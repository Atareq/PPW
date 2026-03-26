from django.db import models


class ProfileType(models.TextChoices):
    SELLER = "Seller", "Seller"
    CUSTOMER = "Customer", "Customer"


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
