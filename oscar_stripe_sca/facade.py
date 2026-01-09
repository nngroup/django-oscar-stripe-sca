from datetime import datetime as dt, timezone as tz
from decimal import Decimal, ROUND_HALF_UP
import logging

from django.apps import apps
from django.urls import reverse_lazy
from django.utils import timezone

import stripe

from . import settings
from .constants import (
    CAPTURE_METHOD_AUTOMATIC,
    CAPTURE_METHOD_MANUAL,
    INVOICE_NUMBERING_AUTOMATIC,
    INVOICE_NUMBERING_MANUAL,
    INVOICE_SENDING_AUTOMATIC,
    INVOICE_SENDING_MANUAL,
    PAYMENT_METHOD_TYPE_CARD,
    SESSION_MODE_PAYMENT,
    ZERO_DECIMAL_CURRENCIES,
)
from .exceptions import MultipleTaxCodesInBasketError, PaymentCaptureError


Basket = apps.get_model("basket", "Basket")
Order = apps.get_model("order", "Order")
PaymentSource = apps.get_model("payment", "Source")


class PaymentItem:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title")
        self.price_incl_tax = kwargs.get("price_incl_tax")
        self.price_currency = kwargs.get("price_currency")
        self.quantity = kwargs.get("quantity")
        self.tax_code = kwargs.get("tax_code")


class Facade:

    stripe_client = None

    def __init__(self, api_key=None, api_version=None):
        api_key = api_key or settings.STRIPE_SECRET_KEY
        api_version = api_version or settings.STRIPE_API_VERSION
        self.stripe_client = stripe.StripeClient(
            api_key=api_key,
            stripe_version=api_version,
        )
        self.logger = logging.getLogger(settings.STRIPE_LOGGER_NAME)

    def _get_extra_session_params(self, session_params, session_line_items):
        return {}  # Customize at will!

    def _get_invoice_account_tax_ids(self, session_params, session_line_items):
        return None  # Customize at will!

    def _get_invoice_custom_fields(self, session_params, session_line_items):
        return None  # Customize at will!

    def _get_invoice_description(self, session_params, session_line_items):
        return settings.STRIPE_INVOICE_DESCRIPTION  # Customize at will!

    def _get_invoice_footer(self, session_params, session_line_items):
        return settings.STRIPE_INVOICE_FOOTER  # Customize at will!

    def _get_invoice_issuer(self, session_params, session_line_items):
        return None  # Customize at will!

    def _get_invoice_metadata(self, session_params, session_line_items):
        return {
            "numbering": settings.STRIPE_INVOICE_NUMBERING
        }

    def _get_invoice_rendering_options(self, session_params, session_line_items):
        display_tax_amounts = settings.STRIPE_INVOICE_DISPLAY_TAX_AMOUNTS
        amount_tax_display = (
            "include_inclusive_tax" if display_tax_amounts else "exclude_tax"
        )
        return {"amount_tax_display": amount_tax_display}  # Customize at will!

    def _get_invoice_data(self, session_params, session_line_items):
        return {
            "account_tax_ids": self._get_invoice_account_tax_ids(
                session_params, session_line_items
            ),
            "custom_fields": self._get_invoice_custom_fields(
                session_params, session_line_items
            ),
            "description": self._get_invoice_description(
                session_params, session_line_items
            ),
            "footer": self._get_invoice_footer(session_params, session_line_items),
            "issuer": self._get_invoice_issuer(session_params, session_line_items),
            "metadata": self._get_invoice_metadata(session_params, session_line_items),
            "rendering_options": self._get_invoice_rendering_options(
                session_params, session_line_items
            ),
        }

    def _get_invoice_session_params(self, session_params, session_line_items):
        invoice_data = self._get_invoice_data(session_params, session_line_items)

        # Fully-automatic invoice creation may only be enabled
        # if Stripe is in charge of numbering.
        enabled = invoice_data["metadata"]["numbering"] == INVOICE_NUMBERING_AUTOMATIC

        invoice_session_params = {
            "invoice_creation": {
                "enabled": enabled,
                "invoice_data": invoice_data,
            }
        }
        return invoice_session_params

    def _get_tax_session_params(self, session_params, session_line_items):
        tax_session_params = {
            "automatic_tax": {
                "enabled": True,
            },
        }
        return tax_session_params

    def _get_checkout_step_url(self, base_url, step_name, **reverse_kwargs):
        step_url = base_url or (
            "{0}{1}".format(
                settings.STRIPE_RETURN_URL_BASE,
                reverse_lazy(
                    f"checkout:{step_name}",
                    kwargs=reverse_kwargs,
                ),
            )
        )
        return step_url

    def _get_cancel_url(self, basket):
        return self._get_checkout_step_url(
            settings.STRIPE_CANCEL_URL,
            "cancel",
            basket_id=basket.id,
        )

    def _get_order_confirmation_url(self):
        return self._get_checkout_step_url(
            settings.STRIPE_ORDER_CONFIRMATION_URL,
            "thank-you",
        )

    def _get_payment_status_url(self):
        return self._get_checkout_step_url(
            settings.STRIPE_PAYMENT_STATUS_URL,
            "payment-status",
        )

    def _get_waiting_for_payment_url(self):
        return self._get_checkout_step_url(
            settings.STRIPE_WAITING_FOR_PAYMENT_URL,
            "waiting",
        )

    def _get_order_preview_url(self, basket):
        return self._get_checkout_step_url(
            settings.STRIPE_ORDER_PREVIEW_URL,
            "preview",
            basket_id=basket.id,
        )

    def _get_success_url(self, basket):
        if not settings.STRIPE_BYPASS_ORDER_PREVIEW:
            return self._get_order_preview_url(basket)
        if settings.STRIPE_WAIT_FOR_PAYMENT_CONFIRMATION:
            return self._get_waiting_for_payment_url()

        return self._get_order_confirmation_url()

    def _get_capture_method(self):
        if settings.STRIPE_BYPASS_ORDER_PREVIEW:
            return CAPTURE_METHOD_AUTOMATIC
        else:
            return CAPTURE_METHOD_MANUAL

    def _get_session_mode(self):
        return SESSION_MODE_PAYMENT

    def build_session_params(
        self, basket, customer_email, session_line_items, session_metadata
    ):

        session_mode = self._get_session_mode()
        capture_method = self._get_capture_method()
        success_url = self._get_success_url(basket)
        cancel_url = self._get_cancel_url(basket)

        session_params = {
            "mode": session_mode,
            "customer_creation": "always",
            "customer_email": customer_email,
            "payment_method_types": [PAYMENT_METHOD_TYPE_CARD],
            "line_items": session_line_items,
            "metadata": session_metadata,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "payment_intent_data": {
                "capture_method": capture_method,
                "metadata": session_metadata,
            },
        }

        if settings.STRIPE_ENABLE_TAX_COMPUTATION:
            tax_session_params = self._get_tax_session_params(
                session_params, session_line_items
            )
            session_params.update(tax_session_params)

        if settings.STRIPE_ENABLE_INVOICE_GENERATION:
            invoice_session_params = self._get_invoice_session_params(
                session_params, session_line_items
            )
            session_params.update(invoice_session_params)

        extra_session_params = self._get_extra_session_params(
            session_params, session_line_items
        )
        session_params.update(extra_session_params)

        return session_params

    def _get_extra_session_metadata(self, session_metadata, session_line_items):
        return {}  # Customize at will!

    def _get_discount_metadata(self, basket):
        discounts = []

        # TODO: add site-wide offers data

        for voucher in basket.grouped_voucher_discounts:
            voucher_name = voucher["voucher"].name
            voucher_discount = voucher["discount"]
            discounts.append(f"{voucher_name}:{voucher_discount}")

        return ",".join(discounts)

    def build_session_metadata(self, basket, shipping_method, session_line_items):
        session_metadata = {
            "scs": "oscar",
            "basket_id": basket.id,
            "shipping_method": shipping_method.code,
        }

        discount_metadata = self._get_discount_metadata(basket)
        session_metadata.update(
            {
                "discounts": discount_metadata,
            }
        )

        extra_session_metadata = self._get_extra_session_metadata(
            session_metadata, session_line_items
        )
        session_metadata.update(extra_session_metadata)

        return session_metadata

    def _prepare_line_item(self, name, amount, currency, quantity, tax_code=None):
        prepared_line_item = {}

        if settings.STRIPE_USE_PRICES_API:
            product_data = {"name": name}
            if tax_code and settings.STRIPE_ENABLE_TAX_COMPUTATION:
                product_data.update({"tax_code": tax_code})

            prepared_line_item = {
                "price_data": {
                    "product_data": product_data,
                    "currency": currency,
                    "unit_amount": amount,
                },
                "quantity": quantity,
            }
        else:
            prepared_line_item = {
                "name": name,
                "amount": amount,
                "currency": currency,
                "quantity": quantity,
            }

        return prepared_line_item

    def _get_default_product_tax_code(self):
        return settings.STRIPE_DEFAULT_PRODUCT_TAX_CODE

    def _choose_tax_code(self, raw_line_items):
        """Choose the singular tax code that should be applied to a
        compressed basket line, based on the passed `raw_line_items`.

        The default behavior is to refuse to choose, i.e. to raise
        an exception if different tax codes are found in the basket.

        Customize at will!

        """
        unique_tax_codes = list(set([item.tax_code for item in raw_line_items]))
        unique_tax_codes_count = len(unique_tax_codes)
        if unique_tax_codes_count == 0:
            return self._get_default_product_tax_code()
        elif unique_tax_codes_count == 1:
            return unique_tax_codes[0]
        else:
            raise MultipleTaxCodesInBasketError(
                "Basket contains products with different tax codes."
            )

    def _convert_to_cents(self, price, currency):
        """
        Convert price to cents with proper rounding, handling zero-decimal currencies.

        """
        if currency.upper() in ZERO_DECIMAL_CURRENCIES:
            return int(Decimal(str(price)).quantize(
                Decimal("1"), ROUND_HALF_UP
            ))
        else:
            return int(Decimal(str(price)).quantize(
                Decimal("0.01"), ROUND_HALF_UP
            ) * 100)

    def prepare_line_items(self, raw_line_items, order_total):
        prepared_line_items = []

        if settings.STRIPE_COMPRESS_TO_ONE_LINE_ITEM:
            name = ", ".join(
                [
                    f"{raw_line_item.quantity}x{raw_line_item.title}"
                    for raw_line_item in raw_line_items
                ]
            )
            amount = self._convert_to_cents(order_total.incl_tax, order_total.currency)
            currency = order_total.currency
            quantity = 1
            tax_code = self._choose_tax_code(raw_line_items)

            prepared_line_item = self._prepare_line_item(
                name, amount, currency, quantity, tax_code
            )
            prepared_line_items.append(prepared_line_item)

        else:
            for raw_line_item in raw_line_items:

                name = raw_line_item.title
                amount = self._convert_to_cents(
                    raw_line_item.price_incl_tax, raw_line_item.price_currency
                )
                currency = raw_line_item.price_currency
                quantity = raw_line_item.quantity
                tax_code = raw_line_item.tax_code

                prepared_line_item = self._prepare_line_item(
                    name, amount, currency, quantity
                )
                prepared_line_items.append(prepared_line_item)

        return prepared_line_items

    def _get_shipping_tax_code(self):
        return settings.STRIPE_DEFAULT_SHIPPING_TAX_CODE

    def _get_product_tax_code(self, product):
        return settings.STRIPE_DEFAULT_PRODUCT_TAX_CODE  # Customize at will!

    def get_raw_line_items(self, basket, shipping_method):
        raw_line_items = []

        for line in basket.all_lines():
            # This loop splits line into discounted and non-discounted ones
            for prices in line.get_price_breakdown():
                price_incl_tax, _, quantity = prices
                raw_line_items.append(
                    PaymentItem(
                        title=line.product.get_title(),
                        price_incl_tax=price_incl_tax,
                        price_currency=line.price_currency,
                        quantity=quantity,
                        tax_code=self._get_product_tax_code(line.product),
                    )
                )

        if basket.is_shipping_required() and shipping_method:
            shipping_price = shipping_method.calculate(basket)
            raw_line_items.append(
                PaymentItem(
                    title=self.shipping_method.name,
                    price_incl_tax=shipping_price.incl_tax,
                    price_currency=shipping_price.currency,
                    quantity=1,
                    tax_code=self._get_shipping_tax_code(),
                )
            )

        return raw_line_items

    def create_checkout_session(
        self, basket, order_total, shipping_method, customer_email
    ):
        self.logger.info(
            "*** Creating Stripe checkout session for "
            f"basket: {basket.id}, "
            f"order_total: {order_total}, "
            f"shipping_method: {shipping_method}, and "
            f"customer_email: {customer_email} ..."
        )

        raw_line_items = self.get_raw_line_items(basket, shipping_method)
        session_line_items = self.prepare_line_items(raw_line_items, order_total)
        session_metadata = self.build_session_metadata(
            basket, shipping_method, session_line_items
        )
        session_params = self.build_session_params(
            basket, customer_email, session_line_items, session_metadata
        )
        self.logger.info(f"*** Stripe session parameters: {session_params}")

        basket.freeze()

        session = self.stripe_client.checkout.sessions.create(params=session_params)
        self.logger.info(f"*** Stripe session: {session}")

        return session

    def retrieve_checkout_session(self, checkout_session_id=None, payment_intent_id=None):
        if not checkout_session_id:
            if not payment_intent_id:
                raise ValueError()

            params = {"payment_intent": payment_intent_id}
            return self.stripe_client.checkout.sessions.list(params=params).data[0]

        return self.stripe_client.checkout.sessions.retrieve(checkout_session_id)

    def retrieve_checkout_session_lines(self, checkout_session):
        return self.stripe_client.checkout.sessions.line_items.list(checkout_session.id)

    def retrieve_payment_intent_id(self, checkout_session_id):
        checkout_session = self.retrieve_checkout_session(
            checkout_session_id=checkout_session_id
        )
        return checkout_session.get("payment_intent")

    def retrieve_payment_intent(self, payment_intent_id=None, checkout_session_id=None):
        if not payment_intent_id:
            if not checkout_session_id:
                raise ValueError()

            payment_intent_id = self.retrieve_payment_intent_id(checkout_session_id)

        return self.stripe_client.payment_intents.retrieve(payment_intent_id)

    def capture_payment_intent(self, payment_intent_id=None, checkout_session_id=None):
        payment_intent = self.retrieve_payment_intent(
            payment_intent_id, checkout_session_id
        )
        payment_intent.capture()

    def _raise_order_payment_capture_error(self, error_reason, original_exception=None):
        error_message = f"Payment capture failed: {error_reason}"
        self.logger.exception(error_message)

        new_exception = PaymentCaptureError(error_message)
        if original_exception:
            raise new_exception from original_exception
        else:
            raise new_exception

    def capture_order_payment(self, order_number, **kwargs):
        self.logger.info(
            f"*** Initiating Stripe payment capture for order #{order_number}"
        )

        # Fetch the Order and its Payment Source
        try:
            order = Order.objects.get(number=order_number)
            payment_source = PaymentSource.objects.get(order=order)

        except Order.DoesNotExist as ex:
            reason = f"Order #{order_number} does not exist"
            self._raise_order_payment_capture_error(reason, ex)

        except PaymentSource.DoesNotExist as ex:
            reason = f"No Payment Source for Order #{order_number}"
            self._raise_order_payment_capture_error(reason, ex)

        # Fetch the Payment Intent
        payment_intent_id = payment_source.reference
        payment_intent = self.retrieve_payment_intent(
            payment_intent_id=payment_intent_id
        )

        # Capture the Payment Intent
        payment_intent.modify(
            params={"receipt_email": order.user.email},
        )
        payment_intent.capture()

        # Update the Payment Source
        payment_source.date_captured = timezone.now()
        payment_source.save()

        self.logger.info(
            f"Payment for Order #{order.number} (ID: {order.id}) "
            f"was captured via Stripe (ref: {payment_intent_id})"
        )

    def is_manual_invoicing_required(self):
        return (
            settings.STRIPE_ENABLE_INVOICE_GENERATION
            and settings.STRIPE_INVOICE_NUMBERING == INVOICE_NUMBERING_MANUAL
        )

    def _get_next_invoice_number(self):
        raise NotImplementedError  # Implement before calling!

    def _get_invoice_product_type(self, product):
        return product.get_product_class().name  # Customize at will!

    def _get_invoice_product_title(self, product):
        return product.get_title()  # Customize at will!

    def create_invoice(self, payment_intent_id, invoice_number=None):
        invoicer = self.stripe_client.invoices

        # TODO: Refactor and test this!

        self.logger.info("*** Fetching Stripe data...")
        payment_intent = self.retrieve_payment_intent(
            payment_intent_id=payment_intent_id
        )
        self.logger.debug(f"*** payment_intent_id: {payment_intent_id}")

        # We retrieve the checkout session and payment metadata.
        checkout_session = self.retrieve_checkout_session(
            payment_intent_id=payment_intent_id
        )
        checkout_session_id = checkout_session.id
        self.logger.debug(f"*** checkout_session_id: {checkout_session_id}")

        self.logger.info(
            f"*** Extracting and computing adjacent data..."
        )
        payment_metadata = payment_intent.metadata

        # From that metadata, we retrieve the basket and the order.
        basket_id = int(payment_metadata.basket_id)
        self.logger.debug(f"*** basket_id: {basket_id}")

        basket = Basket.objects.get(pk=basket_id)
        order = basket.order_set.first()
        order_id = order.id
        order_number = order.number
        self.logger.debug(f"*** order_id: {order_id}")
        self.logger.debug(f"*** order_number: {order_number}")

        # We can then retrieve the discounts that were applied...
        coupons = []
        raw_discount_data = payment_metadata.discounts
        self.logger.debug(f"*** raw_discount_data: {raw_discount_data}")

        discounts = raw_discount_data.split(",")
        for discount in discounts:
            try:
                discount_name, discount_amount = discount.split(":")
            except ValueError:
                continue
            else:
                # ... and create ad-hoc Stripe coupons for them.
                amount_off = int(Decimal(discount_amount) * 100)
                coupon = self.stripe_client.coupons.create({
                    "name": discount_name,
                    "amount_off": amount_off,
                    "currency": payment_intent.currency,
                    "metadata": {
                        "checkout_session_id": checkout_session_id,
                        "payment_intent_id": payment_intent_id,
                        "basket_id": basket_id,
                        "order_id": order_id,
                        "order_number": order_number,
                    }
                })
                coupons.append(coupon)

        # And we finally create the invoice.
        self.logger.info(
            f"*** Creating invoice for checkout session: {checkout_session.id}"
        )
        today = round(dt.now(tz.utc).timestamp())
        number = invoice_number or self._get_next_invoice_number()  # overridden in the project
        customer = checkout_session.customer
        params = {
            "number": number,
            "customer": customer,
            "collection_method": "send_invoice",
            "due_date": today,
            "automatic_tax": {
                "enabled": settings.STRIPE_ENABLE_TAX_COMPUTATION,
            },
        }
        if coupons:
            params.update(
                {"discounts": [
                    {"coupon": coupon.id} for coupon in coupons
                ]}
            )
        invoice = invoicer.create(params=params)

        invoice_id = invoice.id
        invoice_number = invoice.number
        self.logger.debug(f"*** invoice_id: {invoice_id}")
        self.logger.debug(f"*** invoice_number: {invoice_number}")

        # We now add lines to the invoice, for each one of the order.
        invoice_info = f"{invoice_id} (number: {invoice.number})"
        self.logger.info(f"*** Adding lines to invoice: {invoice_info}")
        invoice_lines = []
        order_lines = order.lines.all()
        for order_line in order_lines:
            product = order_line.product
            product_type = self._get_invoice_product_type(product)
            product_title = self._get_invoice_product_title(product)
            description = f"[{product_type}] {product_title}"
            amount = int(order_line.unit_price_excl_tax * 100)
            invoice_lines.append({
                "description": description,
                "amount": amount,
            })
        params={"lines": invoice_lines}
        invoicer.add_lines(invoice=invoice_id, params=params)

        # The invoice may now be finalized...
        self.logger.info(f"*** Finalizing invoice: {invoice_info}")
        invoicer.finalize_invoice(invoice=invoice_id)

        # ... linked to its payment...
        self.logger.info(f"*** Attaching payment to invoice: {invoice_info}")
        params = {"payment_intent": payment_intent_id}
        invoicer.attach_payment(invoice_id, params=params)

        # ... and sent, *if* that should be done through Stripe.
        if settings.STRIPE_INVOICE_SENDING == INVOICE_SENDING_AUTOMATIC:
            self.logger.info(f"*** Sending invoice: {invoice_info}")
            invoicer.send_invoice(invoice_id)

        return invoice_id

    def retrieve_invoice(self, invoice_id):
        return self.stripe_client.invoices.retrieve(invoice_id)

    def record_invoice(self, invoice_id, payment_intent_id):
        raise NotImplementedError  # Implement before calling!

    def construct_event(self, payload, sig_header):
        params = {
            "payload": payload,
            "sig_header": sig_header,
        }
        secret = settings.STRIPE_WEBHOOK_ENDPOINT_SECRET
        if secret:
            params.update({"secret": secret})

        return self.stripe_client.construct_event(**params)
