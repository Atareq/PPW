from django.db import models


class ProfileType(models.TextChoices):
    SELLER = "Seller", "Seller"
    CUSTOMER = "Customer", "Customer"
    MANAGER = "Manager", "Manager"


class StaffRole(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    ADMIN = "admin", "Admin"
    MODERATOR = "moderator", "Moderator"
    FINANCE_ADMIN = "finance_admin", "Finance Admin"
    SUPPORT_AGENT = "support_agent", "Support Agent"


# Actions allowed per staff role per permission_resource.
# "__all__" means every resource/action is permitted.
# For ADMIN, "__all__" applies but destroy is blocked globally in StaffPermissions.
# Resources match the permission_resource strings set on each ViewSet.
STAFF_ROLE_PERMISSIONS: dict = {
    StaffRole.SUPER_ADMIN: "__all__",
    StaffRole.ADMIN: "__all__",  # destroy blocked in StaffPermissions.can_access()
    StaffRole.MODERATOR: {
        "SELLER_PROFILE_ADMIN": ["list", "retrieve", "partial_update", "bulk_suspend", "bulk_restore", "bulk_activate", "stats"],
        "CUSTOMER_PROFILE_ADMIN": ["list", "retrieve", "partial_update", "bulk_suspend", "bulk_restore", "bulk_activate"],
        "AUCTION": ["list", "retrieve", "partial_update", "close", "cancel"],
        "COMPLAINT": ["list", "retrieve", "mark_in_review", "resolve", "reject"],
        "BIRD": ["list", "retrieve"],
        "PRODUCT_ASSET": ["list", "retrieve"],
        "PEDIGREE_DOCUMENT": ["list", "retrieve", "review"],
        "ASSET_COMMENT": ["list", "retrieve"],
        "SELLER_COMMENT": ["list", "retrieve"],
        "ASSET_RATING": ["list", "retrieve"],
        "SELLER_RATING": ["list", "retrieve"],
    },
    StaffRole.FINANCE_ADMIN: {
        "PACKAGE": ["list", "retrieve", "create", "partial_update"],
        "SELLER_PACKAGE": ["list", "retrieve", "partial_update"],
        "MY_SELLER_PACKAGE": ["list", "retrieve"],
        "DISCOUNT_OFFER_ADMIN": ["list", "retrieve", "create", "activate", "deactivate"],
        "CASHBACK_OFFER_ADMIN": ["list", "retrieve", "create", "activate", "deactivate"],
        "CASHBACK_TRANSACTION_ADMIN": ["list"],
        "CASHBACK_ADJUSTMENT_ADMIN": ["create"],
        "DISCOUNT_OFFER": ["list", "retrieve"],
        "CASHBACK_OFFER": ["list", "retrieve"],
        "CASHBACK": ["list", "retrieve"],
        "LOYALTY_BADGE": ["list", "retrieve", "create"],
        "LOYALTY_POINT": ["list", "retrieve"],
        "PAYMENT_REQUEST": ["list", "retrieve"],
    },
    StaffRole.SUPPORT_AGENT: {
        "COMPLAINT": ["list", "retrieve", "mark_in_review", "resolve", "reject"],
        "SELLER_PROFILE_ADMIN": ["list", "retrieve"],
        "CUSTOMER_PROFILE_ADMIN": ["list", "retrieve"],
        "PAYMENT_REQUEST": ["list", "retrieve"],
        "ASSET_COMMENT": ["list", "retrieve"],
        "SELLER_COMMENT": ["list", "retrieve"],
    },
}


class CountryOptions(models.TextChoices):
    EGYPT = "EG", "Egypt"
    UAE = "AE", "United Arab Emirates"
    SAUDI = "SA", "Saudi Arabia"
    KUWAIT = "KW", "Kuwait"
    QATAR = "QA", "Qatar"
    BAHRAIN = "BH", "Bahrain"
    OMAN = "OM", "Oman"
    JORDAN = "JO", "Jordan"
    LEBANON = "LB", "Lebanon"
    IRAQ = "IQ", "Iraq"
    PALESTINE = "PS", "Palestine"
    MOROCCO = "MA", "Morocco"
    TUNISIA = "TN", "Tunisia"
    ALGERIA = "DZ", "Algeria"
    LIBYA = "LY", "Libya"
    SUDAN = "SD", "Sudan"
    YEMEN = "YE", "Yemen"
    SYRIA = "SY", "Syria"
    USA = "US", "United States"
    EUROPE = "EU", "European Union"


class CurrencyOptions(models.TextChoices):
    EGP = "EGP", "Egyptian Pound"
    AED = "AED", "UAE Dirham"
    SAR = "SAR", "Saudi Riyal"
    KWD = "KWD", "Kuwaiti Dinar"
    QAR = "QAR", "Qatari Riyal"
    BHD = "BHD", "Bahraini Dinar"
    OMR = "OMR", "Omani Rial"
    JOD = "JOD", "Jordanian Dinar"
    LBP = "LBP", "Lebanese Pound"
    IQD = "IQD", "Iraqi Dinar"
    ILS = "ILS", "Israeli Shekel"
    MAD = "MAD", "Moroccan Dirham"
    TND = "TND", "Tunisian Dinar"
    DZD = "DZD", "Algerian Dinar"
    LYD = "LYD", "Libyan Dinar"
    SDG = "SDG", "Sudanese Pound"
    YER = "YER", "Yemeni Rial"
    SYP = "SYP", "Syrian Pound"
    USD = "USD", "US Dollar"
    EUR = "EUR", "Euro"
