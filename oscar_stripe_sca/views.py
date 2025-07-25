import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django.views import generic

from oscar.apps.checkout.views import (
    PaymentDetailsView as DefaultPaymentDetailsView,
    ThankYouView as DefaultThankYouView,
)
from oscar.core.loading import get_class, get_model

from . import settings
from .constants import (
    PACKAGE_NAME,
    PAYMENT_EVENT_PURCHASE,
    PAYMENT_METHOD_STRIPE,
)
from .exceptions import SignatureVerificationError
from .mixins import (
    CSRFExemptMixin,
    OneStepPaymentMixin,
    StripePaymentMixin,
    TwoStepPaymentMixin,
)


logger = logging.getLogger(settings.STRIPE_LOGGER_NAME)

Facade = import_string(settings.STRIPE_FACADE_CLASS_PATH)

Basket = get_model("basket", "Basket")
Line = get_model("basket", "Line")
NoShippingRequired = get_class("shipping.methods", "NoShippingRequired")
OrderPlacementMixin = get_class("checkout.mixins", "OrderPlacementMixin")
PaymentEvent = get_model("order", "PaymentEvent")


class StripeSCAZeroView(OneStepPaymentMixin, OrderPlacementMixin, generic.View):

    def _get_regular_checkout_url(self, request, *args, **kwargs):
        return reverse_lazy("checkout:index")

    def _get_order_confirmation_url(self, request, *args, **kwargs):
        return Facade()._get_order_confirmation_url()

    def _fulfill_order(self, request, *args, **kwargs):
        logger.debug("*** Configuring basket...")
        basket = request.basket
        self.checkout_session.use_shipping_method(NoShippingRequired().code)
        shipping_method = self.get_shipping_method(basket)

        logger.debug("*** Submitting basket...")
        order_number = self.submit_basket(  # from OneStepPaymentMixin
            basket, shipping_method
        )
        logger.debug(f"*** New order number: {order_number}")

        return order_number

    def _should_fulfill_order(self, request, *args, **kwargs):
        return (not self.is_payment_required()) and (not self.is_shipping_required())

    def post(self, request, *args, **kwargs):
        logger.info("*** Entering Stripe checkout funnel...")

        if self._should_fulfill_order(request, *args, **kwargs):
            logger.info("*** Order should be fulfilled right away")

            order_number = self._fulfill_order(request, *args, **kwargs)
            self.request.session["checkout_order_number"] = order_number

            redirect_url = self._get_order_confirmation_url(request, *args, **kwargs)

        else:
            logger.info("*** Order should be processed as usual")

            redirect_url = self._get_regular_checkout_url(request, *args, **kwargs)

        logger.info(f"*** Redirecting to {redirect_url}...")
        return HttpResponseRedirect(redirect_url)


class StripeSCACheckoutView(StripePaymentMixin, DefaultPaymentDetailsView):
    preview = False
    template_name = f"{PACKAGE_NAME}/stripe_checkout.html"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        basket = context_data["basket"]
        shipping_method = context_data["shipping_method"]
        order_total = context_data["order_total"]
        customer_email = None
        try:
            customer_email = basket.owner.email
        except AttributeError:
            checkout_data = self.request.session[self.checkout_session.SESSION_KEY]
            customer_email = checkout_data["guest"]["email"]

        stripe_session = Facade().create_checkout_session(
            basket=basket,
            order_total=order_total,
            shipping_method=shipping_method,
            customer_email=customer_email,
        )
        self.request.session["stripe_session_id"] = stripe_session.id

        context_data.update(
            {
                "stripe_checkout_url": stripe_session.url,
                "stripe_publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
                "stripe_session_id": stripe_session.id,
            }
        )
        return context_data

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)

        if settings.STRIPE_REDIRECT_FROM_BACKEND:
            stripe_checkout_url = context.get("stripe_checkout_url")
            return HttpResponseRedirect(stripe_checkout_url)

        return self.render_to_response(context)


class StripeSCAPreviewView(
    CSRFExemptMixin, TwoStepPaymentMixin, DefaultPaymentDetailsView
):
    preview = True
    template_name_preview = f"{PACKAGE_NAME}/stripe_preview.html"

    @property
    def pre_conditions(self):
        return []

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        if context_data["order_total"] is None:
            messages.error(
                self.request,
                "Your checkout session has expired, please try again",
            )
            raise PermissionDenied

        else:
            context_data["order_total_incl_tax_cents"] = (
                context_data["order_total"].incl_tax * 100
            ).to_integral_value()

        return context_data

    def get(self, request, *args, **kwargs):
        basket_id = kwargs["basket_id"]
        basket = self.load_frozen_basket(basket_id, request.user, request)
        if not basket:
            return HttpResponseRedirect(reverse("basket:summary"))

        kwargs["basket"] = basket
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        basket_id = kwargs["basket_id"]
        basket = self.load_frozen_basket(basket_id, request.user, request)
        if not basket:
            return HttpResponseRedirect(reverse("basket:summary"))

        return self.submit_basket(basket)  # from TwoStepPaymentMixin


class StripeSCAWebhookView(
    CSRFExemptMixin, OneStepPaymentMixin, OrderPlacementMixin, generic.View
):
    http_method_names = ["post"]
    pre_conditions = None
    skip_conditions = None

    def post(self, request, *args, **kwargs):
        payload = request.body
        logger.info(f"*** Received Stripe webhook payload: {payload}")

        signature = request.headers.get("stripe-signature")
        try:
            event = Facade().construct_event(payload, signature)
        except SignatureVerificationError:
            logger.error(f"*** Stripe signature verification error!")
            return HttpResponse(status=400)
        else:
            event_type = event.type
            logger.info(f"*** Stripe event: [{event_type}] --> {event}")

        if event.type == "payment_intent.succeeded":
            payment_intent_data = event.data.object
            metadata = payment_intent_data["metadata"]
            basket_id = metadata["basket_id"]
            basket = self.load_frozen_basket(basket_id)
            shipping_code = metadata["shipping_method"]
            shipping_method = self.get_shipping_method_by_code(shipping_code, basket)
            payment_intent_id = payment_intent_data["id"]

            self.submit_basket(
                basket, shipping_method, payment_intent_id
            )  # from OneStepPaymentMixin

        logger.info("*** Stripe webhook processing complete")
        return HttpResponse(status=200)


class StripeSCAPaymentStatusView(generic.View):

    def _check_payment_status(self, payment_intent_id):
        logger.debug(f"*** Checking status of Payment Intent #{payment_intent_id}")

        is_successful, order_id = False, -1

        payment_events = PaymentEvent.objects.filter(
            event_type__name=PAYMENT_EVENT_PURCHASE,
            reference=payment_intent_id,
        )
        if payment_events.exists():
            order = payment_events.first().order
            logger.debug(f"*** Found matching Order #{order.number} (ID: {order.id})")

            requested_amount = order.total_incl_tax
            logger.debug(f"*** Requested amount: {requested_amount}")

            received_amount = sum([event.amount for event in payment_events])
            logger.debug(f"*** Received amount: {received_amount}")

            is_successful = requested_amount == received_amount
            order_id = order.id

        return is_successful, order_id

    def get(self, request, *args, **kwargs):
        session = self.request.session

        checkout_session_id = session["stripe_session_id"]
        payment_intent_id = Facade().retrieve_payment_intent_id(
            checkout_session_id=checkout_session_id
        )
        is_successful, order_id = self._check_payment_status(payment_intent_id)
        if is_successful:
            session["checkout_order_id"] = order_id  # for the ThankYou view

        response_data = {
            "paymentIntentID": payment_intent_id,
            "isSuccessful": is_successful,
            "orderId": order_id,
        }
        return JsonResponse(response_data)


class StripeSCAWaitingView(generic.TemplateView):
    template_name = f"{PACKAGE_NAME}/stripe_waiting.html"

    def _get_payment_status_url(self):
        return Facade()._get_payment_status_url()

    def _get_payment_success_url(self):
        return Facade()._get_order_confirmation_url()

    def _get_payment_polling_interval(self):
        return settings.STRIPE_PAYMENT_POLLING_INTERVAL

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        payment_status_url = self._get_payment_status_url()
        payment_success_url = self._get_payment_success_url()
        polling_interval = self._get_payment_polling_interval()

        context_data.update(
            {
                "payment_status_url": payment_status_url,
                "payment_success_url": payment_success_url,
                "polling_interval": polling_interval,
            }
        )
        return context_data


class StripeSCACancelView(StripePaymentMixin, generic.RedirectView):
    permanent = False

    def get_redirect_url(self, **kwargs):
        return reverse("basket:summary")

    def get(self, request, *args, **kwargs):
        basket_id = kwargs["basket_id"]
        basket = self.load_frozen_basket(basket_id, request.user, request)
        if basket:
            basket.thaw()
            logger.info(
                "*** Stripe transaction cancelled, basket #%s thawed",
                basket.id,
            )

        messages.error(self.request, _("Stripe transaction cancelled"))

        return super().get(request, *args, **kwargs)


class StripeSCAThankYouView(DefaultThankYouView):
    template_name = "oscar/checkout/thank_you.html"
    context_object_name = "order"

    def get_object(self, queryset=None):
        order = None
        session = self.request.session

        if self.request.user.is_superuser:
            kwargs = {}
            if "order_number" in self.request.GET:
                kwargs["number"] = self.request.GET["order_number"]
            elif "order_id" in self.request.GET:
                kwargs["id"] = self.request.GET["order_id"]
            order = Order._default_manager.filter(**kwargs).first()

        if not order:
            if "checkout_order_id" in session:
                order_id = session["checkout_order_id"]
                logger.debug(f"*** order_id: {order_id}")
                order = Order._default_manager.filter(pk=order_id).first()
            elif "checkout_order_number" in session:
                order_number = session["checkout_order_number"]
                logger.debug(f"*** order_number: {order_number}")
                order = Order._default_manager.filter(number=order_number).first()

        return order
