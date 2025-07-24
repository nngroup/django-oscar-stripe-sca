import logging

from django.contrib import messages
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from oscar.core.loading import get_class, get_model
from oscar.core.exceptions import ModuleNotFoundError

from . import settings
from .constants import PAYMENT_EVENT_PURCHASE, PAYMENT_METHOD_STRIPE


logger = logging.getLogger(settings.STRIPE_LOGGER_NAME)

Facade = import_string(settings.STRIPE_FACADE_CLASS_PATH)

Basket = get_model("basket", "Basket")
OfferApplicator = get_class("offer.applicator", "Applicator")
PaymentSource = get_model("payment", "Source")
PaymentSourceType = get_model("payment", "SourceType")
ShippingRepository = get_class("shipping.repository", "Repository")
StrategySelector = get_class("partner.strategy", "Selector")
SurchargeApplicator = get_class("checkout.applicator", "SurchargeApplicator")
TaxInclusiveFixedPrice = get_class("partner.prices", "TaxInclusiveFixedPrice")
UnableToPlaceOrder = get_class("order.exceptions", "UnableToPlaceOrder")


class StripePaymentMixin(object):

    def __init__(self, *args, **kwargs):
        self.facade = Facade()

    def load_frozen_basket(self, basket_id, user=None, request=None):
        try:
            basket = Basket.objects.get(id=basket_id, status=Basket.FROZEN)
        except Basket.DoesNotExist:
            logger.warning("Unable to load frozen basket with ID %s", basket_id)
            if request:
                messages.error(
                    request,
                    _("No basket was found for your Stripe transaction"),
                )
            return None

        # Assign strategy to basket instance
        if StrategySelector:
            basket.strategy = StrategySelector().strategy(request)

        # Re-apply any offers
        OfferApplicator().apply(basket, user=user, request=request)

        return basket

    def build_submission(self, basket, **kwargs):
        user = kwargs.pop("user", basket.owner)
        shipping_address = self.get_shipping_address(basket)
        shipping_method = kwargs.pop(
            "shipping_method", self.get_shipping_method(basket, shipping_address)
        )
        billing_address = self.get_billing_address(shipping_address)

        submission = {
            "user": user,
            "basket": basket,
            "shipping_address": shipping_address,
            "shipping_method": shipping_method,
            "billing_address": billing_address,
            "order_kwargs": {},
            "payment_kwargs": {},
        }

        if not shipping_method:
            order_total = shipping_charge = surcharges = None
        else:
            request = kwargs.pop("request", None)
            shipping_charge = shipping_method.calculate(basket)
            surcharges = SurchargeApplicator(
                request, submission
            ).get_applicable_surcharges(basket, shipping_charge=shipping_charge)
            order_total = self.get_order_totals(
                basket,
                shipping_charge=shipping_charge,
                surcharges=surcharges,
                **kwargs,
            )

        submission.update(
            {
                "shipping_charge": shipping_charge,
                "surcharges": surcharges,
                "order_total": order_total,
            }
        )
        if billing_address:
            submission["payment_kwargs"]["billing_address"] = billing_address

        # Allow overrides to be passed in
        submission.update(kwargs)
        logger.debug(f"*** submission: {submission}")

        return submission

    def add_payment_details(self, order_total, payment_intent_id):
        payment_source_type, __ = PaymentSourceType.objects.get_or_create(
            name=PAYMENT_METHOD_STRIPE
        )
        payment_source = PaymentSource(
            source_type=payment_source_type,
            amount_allocated=order_total.incl_tax,
            amount_debited=order_total.incl_tax,
            currency=order_total.currency,
            reference=payment_intent_id,
        )
        self.add_payment_source(payment_source)
        self.add_payment_event(
            PAYMENT_EVENT_PURCHASE,
            order_total.incl_tax,
            reference=payment_intent_id,
        )


class TwoStepPaymentMixin(StripePaymentMixin):

    def submit_basket(self, basket):
        submission = self.build_submission(basket)
        return self.submit(**submission)

    def handle_payment(self, order_number, order_total, **kwargs):
        checkout_session_id = self.request.session["stripe_session_id"]

        payment_intent_id = self.facade.retrieve_payment_intent_id(
            checkout_session_id=checkout_session_id
        )
        self.facade.capture_payment_intent(payment_intent_id=payment_intent_id)
        self.add_payment_details(
            order_total=order_total,
            payment_intent_id=payment_intent_id,
        )

        del self.request.session["stripe_session_id"]


class OneStepPaymentMixin(StripePaymentMixin):

    def _get_shipping_method(self, basket, code):
        shipping_methods = ShippingRepository().get_shipping_methods(basket)
        for shipping_method in shipping_methods:
            if shipping_method.code == code:
                return shipping_method

    def _retrieve_shipping_method(self, basket, payment_intent_data):
        try:
            code = payment_intent_data["metadata"]["shipping_method"]
        except KeyError:
            return None
        else:
            return self._get_shipping_method(basket, code)

    def build_submission(self, basket, payment_intent_data, **kwargs):
        shipping_method = self._retrieve_shipping_method(basket, payment_intent_data)
        logger.debug(f"*** shipping_method: {shipping_method}")

        return super().build_submission(basket=basket, shipping_method=shipping_method)

    def submit_basket(self, basket, payment_intent_data):
        submission = self.build_submission(basket, payment_intent_data)
        self.add_payment_details(
            order_total=submission["order_total"],
            payment_intent_id=payment_intent_data["id"],
        )
        self.submit(**submission)

    def submit(
        self,
        user,
        basket,
        shipping_address,
        shipping_method,
        shipping_charge,
        billing_address,
        order_total,
        order_kwargs=None,
        payment_kwargs=None,
        surcharges=None,
    ):
        order_number = self.generate_order_number(basket)
        try:
            self.handle_order_placement(
                order_number,
                user,
                basket,
                shipping_address,
                shipping_method,
                shipping_charge,
                billing_address,
                order_total,
                surcharges=surcharges,
                **order_kwargs,
            )

        except UnableToPlaceOrder as ex:

            # It's possible that something will go wrong while trying to
            # actually place an order. Not a good situation to be in as a
            # payment transaction may already have taken place, but needs
            # to be handled gracefully.
            msg = str(ex)
            logger.error(
                "*** Order #%s: unable to place order - %s",
                order_number,
                msg,
                exc_info=True,
            )

        except Exception as ex:

            # Hopefully you only ever reach this in development...
            msg = str(ex)
            logger.exception(
                "*** Order #%s: unhandled exception while placing order (%s)",
                order_number,
                msg,
                exc_info=True,
            )
