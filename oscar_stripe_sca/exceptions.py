from stripe.error import SignatureVerificationError


class MultipleTaxCodesInBasket(ValueError):
    pass


class PaymentCaptureError(RuntimeError):
    pass
