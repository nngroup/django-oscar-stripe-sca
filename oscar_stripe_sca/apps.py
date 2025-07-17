from oscar.apps.checkout.apps import CheckoutConfig
from oscar.core.loading import get_class
from django.urls import path


class StripeSCACheckoutConfig(CheckoutConfig):
    def ready(self):
        self.payment_details_view = get_class(
            "oscar_stripe_sca.views", "StripeSCACheckoutView"
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
        self.stripe_payment_status_view = get_class(
            "oscar_stripe_sca.views", "StripeSCAPaymentStatusView"
        )
        self.stripe_cancel_view = get_class(
            "oscar_stripe_sca.views", "StripeSCACancelView"
        )
        super().ready()

    def get_urls(self):
        urls = super().get_urls()
        urls += [
            path(
                "stripe/preview/<int:basket_id>/",
                self.stripe_preview_view.as_view(preview=True),
                name="stripe-preview",
            ),
            path(
                "stripe/webhook/",
                self.stripe_webhook_view.as_view(),
                name="stripe-webhook",
            ),
            path(
                "stripe/waiting/",
                self.stripe_waiting_view.as_view(),
                name="stripe-waiting",
            ),
            path(
                "stripe/payment-status/",
                self.stripe_payment_status_view.as_view(),
                name="stripe-payment-status",
            ),
            path(
                "stripe/cancel/<int:basket_id>/",
                self.stripe_cancel_view.as_view(),
                name="stripe-cancel",
            ),
        ]
        return urls
