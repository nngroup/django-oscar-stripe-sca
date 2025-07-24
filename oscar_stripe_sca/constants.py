PACKAGE_NAME = "oscar_stripe_sca"

SESSION_MODE_PAYMENT = "payment"

PAYMENT_EVENT_PURCHASE = "Purchase"

PAYMENT_METHOD_STRIPE = "Stripe"
PAYMENT_METHOD_TYPE_CARD = "card"

CAPTURE_METHOD_MANUAL = "manual"
CAPTURE_METHOD_AUTOMATIC = "automatic"

# https://support.stripe.com/questions/which-zero-decimal-currencies-does-stripe-support
ZERO_DECIMAL_CURRENCIES = (
    "BIF",  # Burundian Franc
    "CLP",  # Chilean Peso
    "DJF",  # Djiboutian Franc
    "GNF",  # Guinean Franc
    "JPY",  # Japanese Yen
    "KMF",  # Comorian Franc
    "KRW",  # South Korean Won
    "MGA",  # Malagasy Ariary
    "PYG",  # Paraguayan Guaraní
    "RWF",  # Rwandan Franc
    "VND",  # Vietnamese Đồng
    "VUV",  # Vanuatu Vatu
    "XAF",  # Central African Cfa Franc
    "XOF",  # West African Cfa Franc
    "XPF",  # Cfp Franc
)
