from oscar.apps.checkout.apps import CheckoutConfig
from oscar.core.loading import get_class
from django.urls import path


class StripeSCACheckoutConfig(CheckoutConfig):
    def ready(self):
        self.stripe_payment_details_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAPaymentDetailsView"
        )
        self.stripe_preview_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAPreviewView"
        )
        self.stripe_webhook_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAWebhookView"
        )
        self.stripe_waiting_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAWaitingView"
        )
        self.stripe_payment_check_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAPaymentCheckView"
        )
        self.stripe_cancel_view = get_class(
            "oscar_stripe_sca.views", "StripeSCACancelView"
        )
        super().ready()

    def get_urls(self):
        urls = super().get_urls()
        urls += [
            path(
                "stripe/payment-details/",
                self.stripe_payment_details_view.as_view(),
                name="stripe-payment-details",
            ),
            path(
                "stripe/preview/<int:basket_id>/",
                self.stripe_preview_view.as_view(preview=True),
                name="stripe-preview",
            ),
            path(
                "stripe/webhook/",
                self.stripe_webhook_view.as_view(),
                name="stripe-payment-record",
            ),
            path(
                "stripe/waiting/",
                self.stripe_waiting_view.as_view(),
                name="stripe-waiting",
            ),
            path(
                "stripe/payment-check/",
                self.stripe_payment_check_view.as_view(),
                name="stripe-payment-check",
            ),
            path(
                "stripe/cancel/<int:basket_id>/",
                self.stripe_cancel_view.as_view(),
                name="stripe-cancel",
            ),
        ]
        return urls
