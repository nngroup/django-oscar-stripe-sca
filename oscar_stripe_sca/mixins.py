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
PaymentSource = get_model("payment", "Source")
PaymentSourceType = get_model("payment", "SourceType")
StrategySelector = get_class("partner.strategy", "Selector")
OfferApplicator = get_class("offer.applicator", "Applicator")


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

    def build_submission(self, **kwargs):

        basket = kwargs.pop("basket", request.basket)
        user = kwargs.pop("user", basket.owner)

        shipping_address = self.get_shipping_address(basket)
        shipping_method = self.get_shipping_method(basket, shipping_address)
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
            shipping_charge = shipping_method.calculate(basket)
            surcharges = SurchargeApplicator(
                request, submission
            ).get_applicable_surcharges(
                basket, shipping_charge=shipping_charge
            )
            order_total = self.get_order_totals(
                basket,
                shipping_charge=shipping_charge,
                surcharges=surcharges,
                **kwargs,
            )

        submission.update({
            "order_total": order_total,
            "shipping_charge": shipping_charge,
            "surcharges": surcharges,
        })
        if billing_address:
            submission["payment_kwargs"]["billing_address"] = billing_address

        # Allow overrides to be passed in
        submission.update(kwargs)

        return submission

    def add_payment_details(self, order_total, payment_intent_id):
        payment_source_type, __ = PaymentSourceType.objects.get_or_create(
            name=PAYMENT_METHOD_STRIPE
        )
        payment_source = PaymentSource(
            source_type=payment_source_type,
            currency=order_total.currency,
            amount_allocated=order_total.incl_tax,
            amount_debited=order_total.incl_tax,
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
        submission = self.build_submission(basket=basket)
        return self.submit(**submission)

    def handle_payment(self, order_number, order_total, **kwargs):
        checkout_session_id = self.request.session["stripe_session_id"]

        payment_intent_id = self.facade.retrieve_payment_intent_id(
            checkout_session_id=checkout_session_id
        )
        self.facade.capture_payment_intent(
            payment_intent_id=payment_intent_id
        )
        self.add_payment_details(order_total, payment_intent_id)

        del self.request.session["stripe_session_id"]


class OneStepPaymentMixin(StripePaymentMixin):

    def submit_basket(self, basket, payment_intent_id):
        submission = self.build_submission(basket=basket)
        order_total = submission.get("order_total")
        self.add_payment_details(order_total, payment_intent_id)
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
                "Order #%s: unable to place order - %s",
                order_number,
                msg,
                exc_info=True,
            )

        except Exception as ex:

            # Hopefully you only ever reach this in development...
            msg = str(ex)
            logger.exception(
                "Order #%s: unhandled exception while placing order (%s)",
                order_number,
                msg,
                exc_info=True,
            )
