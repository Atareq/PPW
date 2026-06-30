from django.db import models
from django.utils.translation import gettext_lazy as _


class ProfileType(models.TextChoices):
    SELLER = "Seller", _("Seller")
    CUSTOMER = "Customer", _("Customer")
    MANAGER = "Manager", _("Manager")


class CountryOptions(models.TextChoices):
    EGYPT = "EG", _("Egypt")
    UAE = "AE", _("United Arab Emirates")
    SAUDI = "SA", _("Saudi Arabia")
    KUWAIT = "KW", _("Kuwait")
    QATAR = "QA", _("Qatar")
    BAHRAIN = "BH", _("Bahrain")
    OMAN = "OM", _("Oman")
    JORDAN = "JO", _("Jordan")
    LEBANON = "LB", _("Lebanon")
    IRAQ = "IQ", _("Iraq")
    PALESTINE = "PS", _("Palestine")
    MOROCCO = "MA", _("Morocco")
    TUNISIA = "TN", _("Tunisia")
    ALGERIA = "DZ", _("Algeria")
    LIBYA = "LY", _("Libya")
    SUDAN = "SD", _("Sudan")
    YEMEN = "YE", _("Yemen")
    SYRIA = "SY", _("Syria")
    USA = "US", _("United States")
    EUROPE = "EU", _("European Union")


class CurrencyOptions(models.TextChoices):
    EGP = "EGP", _("Egyptian Pound")
    AED = "AED", _("UAE Dirham")
    SAR = "SAR", _("Saudi Riyal")
    KWD = "KWD", _("Kuwaiti Dinar")
    QAR = "QAR", _("Qatari Riyal")
    BHD = "BHD", _("Bahraini Dinar")
    OMR = "OMR", _("Omani Rial")
    JOD = "JOD", _("Jordanian Dinar")
    LBP = "LBP", _("Lebanese Pound")
    IQD = "IQD", _("Iraqi Dinar")
    ILS = "ILS", _("Israeli Shekel")
    MAD = "MAD", _("Moroccan Dirham")
    TND = "TND", _("Tunisian Dinar")
    DZD = "DZD", _("Algerian Dinar")
    LYD = "LYD", _("Libyan Dinar")
    SDG = "SDG", _("Sudanese Pound")
    YER = "YER", _("Yemeni Rial")
    SYP = "SYP", _("Syrian Pound")
    USD = "USD", _("US Dollar")
    EUR = "EUR", _("Euro")
