PACKAGE_NAME = "oscar_stripe_sca"

SESSION_MODE_PAYMENT = "payment"

PAYMENT_EVENT_PURCHASE = "Purchase"

PAYMENT_METHOD_STRIPE = "Stripe"
PAYMENT_METHOD_TYPE_CARD = "card"

CAPTURE_METHOD_MANUAL = "manual"
CAPTURE_METHOD_AUTOMATIC = "automatic"

# See: https://support.stripe.com/questions/which-zero-decimal-currencies-does-stripe-support
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

# See: https://docs.stripe.com/ips#webhook-notifications
STRIPE_TRUSTED_ORIGINS = []
STRIPE_WEBHOOK_ORIGINS = [
    "3.18.12.63",
    "3.130.192.231",
    "13.235.14.237",
    "13.235.122.149",
    "18.211.135.69",
    "35.154.171.200",
    "52.15.183.38",
    "54.88.130.119",
    "54.88.130.237",
    "54.187.174.169",
    "54.187.205.235",
    "54.187.216.72",
]
for origin in STRIPE_WEBHOOK_ORIGINS:
    STRIPE_TRUSTED_ORIGINS.extend(
        [
            f"http://{origin}",
            f"https://{origin}",
        ]
    )

# TODO: Fetch those IP addresses automatically?
# See: https://docs.stripe.com/ips#downloading-ip-address-lists
