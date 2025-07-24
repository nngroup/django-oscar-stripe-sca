from stripe.error import SignatureVerificationError


class MultipleTaxCodesInBasketError(ValueError):
    pass


class PaymentCaptureError(RuntimeError):
    pass
