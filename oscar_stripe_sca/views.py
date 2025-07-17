import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView, TemplateView, View

from oscar.apps.checkout.views import PaymentDetailsView as CorePaymentDetailsView
from oscar.core.exceptions import ModuleNotFoundError
from oscar.core.loading import get_class, get_model

from . import settings
from .constants import (
    PACKAGE_NAME,
    PAYMENT_EVENT_PURCHASE,
    PAYMENT_METHOD_STRIPE,
)
from .exceptions import SignatureVerificationError
from .mixins import OneStepPaymentMixin, StripePaymentMixin, TwoStepPaymentMixin


logger = logging.getLogger(settings.STRIPE_LOGGER_NAME)

Facade = import_string(settings.STRIPE_FACADE_CLASS_PATH)

Basket = get_model("basket", "Basket")
Line = get_model("basket", "Line")
PaymentEvent = get_model("order", "PaymentEvent")

OrderPlacementMixin = get_class("checkout.mixins", "OrderPlacementMixin")


class StripeSCAPaymentDetailsView(CorePaymentDetailsView):
    template_name = f"{PACKAGE_NAME}/stripe_payment_details.html"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        basket = context_data["basket"]
        shipping_method = context_data["shipping_method"]
        customer_email = None
        try:
            customer_email = basket.owner.email
        except AttributeError:
            checkout_data = self.request.session[self.checkout_session.SESSION_KEY]
            customer_email = checkout_data["guest"]["email"]

        stripe_session = Facade().create_checkout_session(
            basket=basket,
            customer_email=customer_email,
            shipping_method=shipping_method,
        )
        stripe_session_id = stripe_session.id
        self.request.session["stripe_session_id"] = stripe_session_id

        context_data.update(
            {
                "stripe_publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
                "stripe_session_id": stripe_session_id,
            }
        )
        return context_data


class StripeSCAPreviewView(TwoStepPaymentMixin, CorePaymentDetailsView):
    preview = True
    template_name_preview = f"{PACKAGE_NAME}/stripe_preview.html"

    @property
    def pre_conditions(self):
        return []

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

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


class StripeSCAWebhookView(OneStepPaymentMixin, OrderPlacementMixin, View):

    @method_decorator(csrf_exempt)
    def post(self, request, *args, **kwargs):
        logger.info("*** Received Stripe webhook")

        payload = request.body
        logger.debug(f"*** Stripe webhook payload: {payload}")

        signature = request.headers.get("stripe-signature")
        logger.debug(f"*** Stripe webhook signature: {signature}")

        try:
            stripe_event = Facade().construct_event(payload, signature)
        except SignatureVerificationError:
            return HttpResponse(status=400)
        else:
            logger.debug(f"*** Stripe event: {stripe_event}")

        event_type = stripe_event.type
        logger.debug(f"*** Stripe event type: {event_type}")

        if stripe_event.type == "payment_intent.succeeded":
            logger.info("*** Handling successful payment")

            payment_intent = stripe_event.data.object
            logger.debug(f"*** payment_intent: {payment_intent}")

            payment_intent_id = payment_intent["id"]
            logger.debug(f"*** payment_intent_id: {payment_intent_id}")

            basket_id = payment_intent["metadata"]["basket_id"]
            logger.debug(f"*** basket_id: {basket_id}")

            basket = self.load_frozen_basket(basket_id)
            logger.debug(f"*** basket: {basket}")

            self.submit_basket(basket, payment_intent_id)  # from OneStepPaymentMixin

        logger.info("*** Stripe webhook processing complete")
        return HttpResponse(status=200)


class StripeSCAWaitingView(TemplateView):
    template_name = f"{PACKAGE_NAME}/stripe_waiting.html"

    def _get_payment_check_url(self):
        return Facade()._get_payment_check_url()

    def _get_payment_success_url(self):
        return Facade()._get_confirm_url()

    def _get_payment_polling_interval(self):
        return settings.STRIPE_PAYMENT_POLLING_INTERVAL

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        payment_check_url = self._get_payment_check_url()
        payment_success_url = self._get_payment_success_url()
        polling_interval = self._get_payment_polling_interval()

        context_data.update(
            {
                "payment_check_url": payment_check_url,
                "payment_success_url": payment_success_url,
                "polling_interval": polling_interval,
            }
        )
        return context_data


class StripeSCAPaymentCheckView(View):

    def _check_payment_success(self, payment_intent_id):
        logger.debug(f"*** Checking status of Payment Intent #{payment_intent_id}")

        success, order_id = False, -1

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
            logger.debug(f"Received amount: {received_amount}")

            success = requested_amount == received_amount
            order_id = order.id

        return success, order_id

    def get(self, request, *args, **kwargs):
        session = self.request.session

        checkout_session_id = session["stripe_session_id"]
        payment_intent_id = Facade().retrieve_payment_intent_id(
            checkout_session_id=checkout_session_id
        )
        success, order_id = self._check_payment_success(payment_intent_id)
        if success:
            session["checkout_order_id"] = order_id

        response_data = {
            "payment_intent_id": payment_intent_id,
            "order_id": order_id,
            "success": success,
        }
        return JsonResponse(response_data)


class StripeSCACancelView(StripePaymentMixin, RedirectView):
    permanent = False

    def get_redirect_url(self, **kwargs):
        return reverse("basket:summary")

    def get(self, request, *args, **kwargs):
        basket_id = kwargs["basket_id"]
        basket = self.load_frozen_basket(basket_id, request.user, request)
        if basket:
            basket.thaw()
            logger.info(
                "Payment cancelled (token %s) - basket #%s thawed",
                request.GET.get("token", "<no token>"),
                basket.id,
            )

        messages.error(self.request, _("Stripe transaction cancelled"))

        return super().get(request, *args, **kwargs)
