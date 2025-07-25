from django.urls import path
from django.utils.translation import gettext_lazy as _

from oscar.apps.checkout.apps import CheckoutConfig


class StripeSCACheckoutConfig(CheckoutConfig):
    label = "checkout"
    name = "oscar_stripe_sca.apps.checkout"
    verbose_name = _("Checkout")

    namespace = "checkout"

    def ready(self):
        from . import views

        super().ready()
        self.payment_details_view = views.StripeSCACheckoutView
        self.stripe_preview_view = views.StripeSCAPreviewView
        self.stripe_webhook_view = views.StripeSCAWebhookView
        self.stripe_waiting_view = views.StripeSCAWaitingView
        self.stripe_payment_status_view = views.StripeSCAPaymentStatusView
        self.stripe_cancel_view = views.StripeSCACancelView

    def get_urls(self):
        urls = super().get_urls()
        urls += [
            path(
                "preview-stripe/<int:basket_id>/",
                self.stripe_preview_view.as_view(preview=True),
                name="stripe-preview",
            ),
            path(
                "webhook-stripe/",
                self.stripe_webhook_view.as_view(),
                name="stripe-webhook",
            ),
            path(
                "waiting-stripe/",
                self.stripe_waiting_view.as_view(),
                name="stripe-waiting",
            ),
            path(
                "payment-status-stripe/",
                self.stripe_payment_status_view.as_view(),
                name="stripe-payment-status",
            ),
            path(
                "cancel-stripe/<int:basket_id>/",
                self.stripe_cancel_view.as_view(),
                name="stripe-cancel",
            ),
        ]
        return urls
