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


logger = logging.getLogger(settings.STRIPE_LOGGER_NAME)

Facade = import_string(settings.STRIPE_FACADE_CLASS_PATH)

Basket = get_model("basket", "Basket")
Line = get_model("basket", "Line")
PaymentEvent = get_model("order", "PaymentEvent")
Selector = get_class("partner.strategy", "Selector")
Source = get_model("payment", "Source")
SourceType = get_model("payment", "SourceType")
try:
    Applicator = get_class("offer.applicator", "Applicator")
except ModuleNotFoundError:
    # fallback for django-oscar<=1.1
    Applicator = get_class("offer.utils", "Applicator")


class StripeSCAPaymentDetailsView(CorePaymentDetailsView):
    template_name = f"{PACKAGE_NAME}/stripe_payment_details.html"

    def get_context_data(self, **kwargs):
        context_data = super().get_context_data(**kwargs)

        basket = context_data["basket"]
        total = context_data["order_total"]
        shipping_method = context_data["shipping_method"]
        customer_email = None
        try:
            customer_email = context_data["basket"].owner.email
        except AttributeError:
            checkout_data = self.request.session[self.checkout_session.SESSION_KEY]
            customer_email = checkout_data["guest"]["email"]

        stripe_session = Facade().create_checkout_session(
            customer_email=customer_email,
            basket=basket,
            total=total,
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


class StripeSCAPreviewView(CorePaymentDetailsView):
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
                self.request, "Your checkout session has expired, please try again"
            )
            raise PermissionDenied
        else:
            context_data["order_total_incl_tax_cents"] = (
                context_data["order_total"].incl_tax * 100
            ).to_integral_value()

        return context_data

    def handle_payment(self, order_number, order_total, **kwargs):
        checkout_session_id = self.request.session["stripe_session_id"]
        payment_intent = Facade().retrieve_payment_intent(
            checkout_session_id=checkout_session_id
        )
        payment_intent.capture()

        source_type, __ = SourceType.objects.get_or_create(name=PAYMENT_METHOD_STRIPE)
        source = Source(
            source_type=source_type,
            currency=order_total.currency,
            amount_allocated=order_total.incl_tax,
            amount_debited=order_total.incl_tax,
            reference=payment_intent_id,
        )
        self.add_payment_source(source)

        self.add_payment_event(
            PAYMENT_EVENT_PURCHASE,
            order_total.incl_tax,
            reference=payment_intent_id,
        )

        del self.request.session["stripe_session_id"]

    def payment_description(self, order_number, total, **kwargs):
        return "Stripe payment for order {0} by {1}".format(
            order_number, self.request.user.get_full_name()
        )

    @staticmethod
    def payment_metadata(order_number, total, **kwargs):
        return {
            "order_number": order_number,
        }

    def load_frozen_basket(self, basket_id):
        # Lookup the frozen basket that this txn corresponds to
        try:
            basket = Basket.objects.get(id=basket_id, status=Basket.FROZEN)
        except Basket.DoesNotExist:
            return None

        # Assign strategy to basket instance
        if Selector:
            basket.strategy = Selector().strategy(self.request)

        # Re-apply any offers
        Applicator().apply(basket, self.request.user, request=self.request)

        return basket

    def get(self, request, *args, **kwargs):
        kwargs["basket"] = self.load_frozen_basket(kwargs["basket_id"])
        if not kwargs["basket"]:
            logger.warning(
                "Unable to load frozen basket with ID %s", kwargs["basket_id"]
            )
            messages.error(
                self.request,
                _("No basket was found that corresponds to your Stripe transaction"),
            )
            return HttpResponseRedirect(reverse("basket:summary"))
        return super(StripeSCAPreviewResponseView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """
        Place an order.
        """
        # Reload frozen basket which is specified in the URL
        basket = self.load_frozen_basket(kwargs["basket_id"])
        if not basket:
            messages.error(
                self.request,
                _("No basket was found that corresponds to your Stripe transaction"),
            )
            return HttpResponseRedirect(reverse("basket:summary"))

        submission = self.build_submission(basket=basket)
        return self.submit(**submission)


class StripeSCAWebhookView(View):

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):

        # TODO: Process incoming webhook

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
        logger.info(f"Checking status of Payment Intent #{payment_intent_id}")

        payment_events = PaymentEvent.objects.filter(
            event_type__name=PAYMENT_EVENT_PURCHASE,
            reference=payment_intent_id,
        )
        num_payment_events = payment_events.count()
        logger.info(f"Found {num_payment_events} matching Payment Event(s)")

        if num_payment_events == 0:
            return False

        order = payment_events.first().order
        logger.info(f"Found matching Order #{order.id}")

        requested_amount = order.total_incl_tax
        logger.info(f"Requested amount: {requested_amount}")

        received_amount = sum([event.amount for event in payment_events])
        logger.info(f"Received amount: {received_amount}")

        return requested_amount == received_amount

    def get(self, request, *args, **kwargs):
        checkout_session_id = self.request.session["stripe_session_id"]
        payment_intent_id = Facade().retrieve_payment_intent_id(
            checkout_session_id=checkout_session_id
        )

        is_payment_successful = self._check_payment_success(payment_intent_id)
        response_data = {
            "payment_intent_id": payment_intent_id,
            "success": is_payment_successful,
        }
        return JsonResponse(response_data)


class StripeSCACancelView(RedirectView):
    permanent = False

    def get(self, request, *args, **kwargs):
        basket = get_object_or_404(Basket, id=kwargs["basket_id"], status=Basket.FROZEN)
        basket.thaw()
        logger.info(
            "Payment cancelled (token %s) - basket #%s thawed",
            request.GET.get("token", "<no token>"),
            basket.id,
        )
        return super(StripeSCACancelResponseView, self).get(request, *args, **kwargs)

    def get_redirect_url(self, **kwargs):
        messages.error(self.request, _("Stripe transaction cancelled"))
        return reverse("basket:summary")
