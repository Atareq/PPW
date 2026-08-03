from django.utils.translation import gettext_lazy as _


LOFT_SUBSCRIPTION_CURRENCY = "EGP"
INCLUDED_AI_OPERATIONS_PER_MONTH = 10
TRIAL_AI_OPERATIONS_TOTAL = 10
SUBSCRIPTION_QUOTE_MAX_AGE_SECONDS = 600
SUBSCRIPTION_QUOTE_SALT = "loft-subscription-quote-v1"


class SubscriptionErrorCode:
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
    SUBSCRIPTION_ADDON_REQUIRED = "SUBSCRIPTION_ADDON_REQUIRED"
    PEDIGREE_TEMPLATE_NOT_ALLOWED = "PEDIGREE_TEMPLATE_NOT_ALLOWED"
    AI_QUOTA_EXHAUSTED = "AI_QUOTA_EXHAUSTED"
    TRIAL_ALREADY_USED = "TRIAL_ALREADY_USED"
    SUBSCRIPTION_PRICE_CHANGED = "SUBSCRIPTION_PRICE_CHANGED"
    INVALID_SUBSCRIPTION_QUOTE = "INVALID_SUBSCRIPTION_QUOTE"


class SubscriptionAddonCode:
    CLOUD_BACKUP = "cloud_backup"
    MEDICAL_RECORDS = "medical_records"
    QR_CODE_PEDIGREE = "qr_code_pedigree"
    CUSTOM_PEDIGREE_TEMPLATE = "custom_pedigree_template"


SUBSCRIPTION_ACCESS_MESSAGES = {
    SubscriptionErrorCode.SUBSCRIPTION_REQUIRED: _(
        "An active subscription is required."
    ),
    SubscriptionErrorCode.SUBSCRIPTION_EXPIRED: _("The subscription is expired."),
    SubscriptionErrorCode.SUBSCRIPTION_ADDON_REQUIRED: _(
        "This subscription add-on is required."
    ),
    SubscriptionErrorCode.PEDIGREE_TEMPLATE_NOT_ALLOWED: _(
        "This pedigree template is not included in the active subscription."
    ),
    SubscriptionErrorCode.AI_QUOTA_EXHAUSTED: _(
        "No AI operations remain for the active subscription."
    ),
}
