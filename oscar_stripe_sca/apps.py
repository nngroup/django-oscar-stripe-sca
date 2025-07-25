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
        self.zero_view = views.StripeSCAZeroView
        self.checkout_view = views.StripeSCACheckoutView
        self.cancel_view = views.StripeSCACancelView
        self.preview_view = views.StripeSCAPreviewView
        self.webhook_view = views.StripeSCAWebhookView
        self.waiting_view = views.StripeSCAWaitingView
        self.payment_status_view = views.StripeSCAPaymentStatusView
        self.thank_you_view = views.StripeSCAThankYouView

    def get_urls(self):
        return [
            path("zero/", self.zero_view.as_view(), name="zero"),
            path("", self.index_view.as_view(), name="index"),
            path(
                "shipping-address/",
                self.shipping_address_view.as_view(),
                name="shipping-address",
            ),
            path(
                "user-address/edit/<int:pk>/",
                self.user_address_update_view.as_view(),
                name="user-address-update",
            ),
            path(
                "user-address/delete/<int:pk>/",
                self.user_address_delete_view.as_view(),
                name="user-address-delete",
            ),
            path(
                "shipping-method/",
                self.shipping_method_view.as_view(),
                name="shipping-method",
            ),
            path(
                "payment-method/",
                self.checkout_view.as_view(),
                name="payment-method",
            ),
            path(
                "payment-details/",
                self.checkout_view.as_view(),
                name="payment-details",
            ),
            path(
                "cancel/<int:basket_id>/",
                self.cancel_view.as_view(),
                name="cancel",
            ),
            path(
                "preview/",
                self.preview_view.as_view(),
                name="preview",
            ),
            path(
                "webhook/",
                self.webhook_view.as_view(),
                name="stripe-webhook",
            ),
            path(
                "waiting/",
                self.waiting_view.as_view(),
                name="waiting",
            ),
            path(
                "payment-status/",
                self.payment_status_view.as_view(),
                name="payment-status",
            ),
            path("thank-you/", self.thankyou_view.as_view(), name="thank-you"),
        ]
