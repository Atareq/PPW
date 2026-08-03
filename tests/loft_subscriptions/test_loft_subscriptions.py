from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.utils import timezone
import pytest
from rest_framework.test import APIClient

from apps.authentication.services import build_token_for_user
from apps.loft_subscriptions.constants import SubscriptionAddonCode
from apps.loft_subscriptions.models import (
    AIQuotaPackage,
    LoftSubscription,
    LoftSubscriptionStatus,
    LoftSubscriptionTrialUsage,
    PedigreeTemplatePackage,
    SubscriptionAddon,
    SubscriptionAIUsage,
    SubscriptionDurationOption,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
    SubscriptionPaymentRequest,
)
from apps.loft_subscriptions.services import (
    SubscriptionAccessService,
    SubscriptionAIQuotaService,
    SubscriptionCheckoutService,
    SubscriptionPricingService,
)
from apps.users.constants import ProfileType
from tests.factories import (
    PedigreeBirdFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def create_catalog():
    trial = SubscriptionDurationOption.objects.create(
        code="trial_3_days",
        name="3-Day Free Trial",
        duration_days=3,
        price=Decimal("0.00"),
        is_trial=True,
        sort_order=1,
    )
    month_1 = SubscriptionDurationOption.objects.create(
        code="month_1",
        name="One Month",
        duration_months=1,
        price=Decimal("500.00"),
        sort_order=2,
    )
    month_12 = SubscriptionDurationOption.objects.create(
        code="month_12",
        name="One Year",
        duration_months=12,
        price=Decimal("1700.00"),
        sort_order=3,
    )
    shapes_1 = PedigreeTemplatePackage.objects.create(
        code="shapes_1",
        name="One Shape",
        template_limit=1,
        price=Decimal("0.00"),
        sort_order=1,
    )
    shapes_10 = PedigreeTemplatePackage.objects.create(
        code="shapes_10",
        name="Ten Shapes",
        template_limit=10,
        price=Decimal("850.00"),
        sort_order=2,
    )
    unlimited = PedigreeTemplatePackage.objects.create(
        code="shapes_unlimited",
        name="Unlimited Shapes",
        is_unlimited=True,
        price=Decimal("999.00"),
        sort_order=3,
    )
    cloud = SubscriptionAddon.objects.create(
        code=SubscriptionAddonCode.CLOUD_BACKUP,
        name="Cloud Backup",
        price=Decimal("79.00"),
        sort_order=1,
    )
    medical = SubscriptionAddon.objects.create(
        code=SubscriptionAddonCode.MEDICAL_RECORDS,
        name="Medical Records",
        price=Decimal("79.00"),
        sort_order=2,
    )
    qr = SubscriptionAddon.objects.create(
        code=SubscriptionAddonCode.QR_CODE_PEDIGREE,
        name="QR Code Pedigree",
        price=Decimal("99.00"),
        sort_order=3,
    )
    inactive = SubscriptionAddon.objects.create(
        code="future_inactive",
        name="Future Inactive",
        price=Decimal("49.00"),
        is_active=False,
        is_implemented=False,
    )
    unimplemented = SubscriptionAddon.objects.create(
        code="future_active",
        name="Future Active",
        price=Decimal("49.00"),
        is_active=True,
        is_implemented=False,
    )
    ai_300 = AIQuotaPackage.objects.create(
        code="ai_300",
        name="300 AI Operations",
        operation_count=300,
        price=Decimal("249.00"),
    )
    return {
        "trial": trial,
        "month_1": month_1,
        "month_12": month_12,
        "shapes_1": shapes_1,
        "shapes_10": shapes_10,
        "unlimited": unlimited,
        "cloud": cloud,
        "medical": medical,
        "qr": qr,
        "inactive": inactive,
        "unimplemented": unimplemented,
        "ai_300": ai_300,
    }


def selection(catalog, **overrides):
    data = {
        "duration_id": catalog["month_1"].pk,
        "pedigree_package_id": catalog["shapes_1"].pk,
        "addon_ids": [],
        "ai_package_id": None,
        "template_ids": [7],
    }
    data.update(overrides)
    return data


def test_pricing_one_month_with_one_free_shape_is_500():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    result = SubscriptionPricingService.calculate(
        profile=user.active_profile,
        validated_selection=selection(catalog),
    )

    assert result.total == Decimal("500.00")


def test_pricing_year_ten_shapes_all_addons_and_ai_300_is_3056():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)

    result = SubscriptionPricingService.calculate(
        profile=user.active_profile,
        validated_selection=selection(
            catalog,
            duration_id=catalog["month_12"].pk,
            pedigree_package_id=catalog["shapes_10"].pk,
            addon_ids=[
                catalog["cloud"].pk,
                catalog["medical"].pk,
                catalog["qr"].pk,
            ],
            ai_package_id=catalog["ai_300"].pk,
            template_ids=list(range(1, 11)),
        ),
    )

    assert result.total == Decimal("3056.00")


def test_pricing_rejects_duplicate_inactive_and_unimplemented_addons():
    catalog = create_catalog()
    profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile

    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection=selection(
                catalog,
                addon_ids=[catalog["cloud"].pk, catalog["cloud"].pk],
            ),
        )
    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection=selection(catalog, addon_ids=[catalog["inactive"].pk]),
        )
    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection=selection(
                catalog, addon_ids=[catalog["unimplemented"].pk]
            ),
        )


def test_template_limits_and_unlimited_package_are_enforced():
    catalog = create_catalog()
    profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile

    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection=selection(
                catalog,
                pedigree_package_id=catalog["shapes_10"].pk,
                template_ids=list(range(1, 12)),
            ),
        )

    result = SubscriptionPricingService.calculate(
        profile=profile,
        validated_selection=selection(
            catalog,
            pedigree_package_id=catalog["unlimited"].pk,
            template_ids=list(range(1, 20)),
        ),
    )
    assert result.selected_template_ids == list(range(1, 20))


def test_revise_performs_no_writes_and_returns_quote():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    before_counts = (
        SubscriptionInvoice.objects.count(),
        SubscriptionPaymentRequest.objects.count(),
        LoftSubscription.objects.count(),
    )

    result = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=True,
    )

    assert result.quote_token
    assert before_counts == (
        SubscriptionInvoice.objects.count(),
        SubscriptionPaymentRequest.objects.count(),
        LoftSubscription.objects.count(),
    )


def test_paid_checkout_creates_pending_invoice_and_payment_request_without_activation():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    dry_run = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=True,
    )

    result = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=False,
        quote_token=dry_run.quote_token,
        idempotency_key="checkout-1",
    )

    assert result.invoice.status == SubscriptionInvoiceStatus.PENDING
    assert result.payment_request.amount == Decimal("500.00")
    assert result.subscription is None


def test_checkout_idempotency_returns_existing_invoice():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    dry_run = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=True,
    )

    first = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=False,
        quote_token=dry_run.quote_token,
        idempotency_key="checkout-2",
    )
    second = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=selection(catalog),
        dry_run=False,
        quote_token=dry_run.quote_token,
        idempotency_key="checkout-2",
    )

    assert second.invoice.pk == first.invoice.pk
    assert SubscriptionInvoice.objects.count() == 1
    assert SubscriptionPaymentRequest.objects.count() == 1


def test_changed_pricing_returns_409_from_checkout_endpoint():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = authenticated_client(user)
    revise = client.post(
        "/api/loft-subscriptions/revise/",
        selection(catalog),
        format="json",
    )
    catalog["month_1"].price = Decimal("600.00")
    catalog["month_1"].save(update_fields=["price", "modified"])

    response = client.post(
        "/api/loft-subscriptions/checkout/",
        {
            **selection(catalog),
            "quote_token": revise.data["quote_token"],
            "idempotency_key": "changed-price",
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "SUBSCRIPTION_PRICE_CHANGED"
    assert response.data["total"] == Decimal("600.00")


def test_trial_checkout_activates_once_for_user_account_and_last_three_days():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    trial_selection = selection(
        catalog,
        duration_id=catalog["trial"].pk,
        ai_package_id=None,
        template_ids=[7],
    )
    dry_run = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=trial_selection,
        dry_run=True,
    )

    result = SubscriptionCheckoutService.execute(
        profile=user.active_profile,
        validated_selection=trial_selection,
        dry_run=False,
        quote_token=dry_run.quote_token,
        idempotency_key="trial-1",
    )
    seller_profile = SellerProfileFactory(user=user, is_active=False)

    assert result.subscription.status == LoftSubscriptionStatus.ACTIVE
    assert result.subscription.is_trial is True
    assert result.subscription.ends_at == result.subscription.starts_at + timedelta(
        days=3
    )
    assert LoftSubscriptionTrialUsage.objects.filter(user=user).count() == 1
    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=seller_profile,
            validated_selection=trial_selection,
        )


def test_trial_rejects_paid_addons_ai_and_multiple_templates():
    catalog = create_catalog()
    profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    base = selection(
        catalog,
        duration_id=catalog["trial"].pk,
        template_ids=[7],
    )

    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection={**base, "addon_ids": [catalog["cloud"].pk]},
        )
    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection={**base, "ai_package_id": catalog["ai_300"].pk},
        )
    with pytest.raises(Exception):
        SubscriptionPricingService.calculate(
            profile=profile,
            validated_selection={**base, "template_ids": [7, 8]},
        )


def paid_invoice_with_payment(profile, catalog, *, idempotency_key):
    dry_run = SubscriptionCheckoutService.execute(
        profile=profile,
        validated_selection=selection(
            catalog,
            duration_id=catalog["month_12"].pk,
            ai_package_id=catalog["ai_300"].pk,
        ),
        dry_run=True,
    )
    result = SubscriptionCheckoutService.execute(
        profile=profile,
        validated_selection=selection(
            catalog,
            duration_id=catalog["month_12"].pk,
            ai_package_id=catalog["ai_300"].pk,
        ),
        dry_run=False,
        quote_token=dry_run.quote_token,
        idempotency_key=idempotency_key,
    )
    return result.invoice, result.payment_request


def test_payment_confirmation_idempotently_activates_and_schedules_renewal():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
    manager.is_staff = True
    manager.save(update_fields=["is_staff"])
    manager_profile = manager.active_profile

    invoice, payment_request = paid_invoice_with_payment(
        user.active_profile,
        catalog,
        idempotency_key="paid-1",
    )
    response = authenticated_client(manager).post(
        f"/api/loft-subscriptions/payment-requests/{payment_request.pk}/confirm/",
        {},
        format="json",
    )
    repeated = authenticated_client(manager).post(
        f"/api/loft-subscriptions/payment-requests/{payment_request.pk}/confirm/",
        {},
        format="json",
    )

    invoice.refresh_from_db()
    assert response.status_code == 200
    assert repeated.status_code == 200
    assert invoice.status == SubscriptionInvoiceStatus.PAID
    assert LoftSubscription.objects.count() == 1

    second_invoice, second_payment = paid_invoice_with_payment(
        user.active_profile,
        catalog,
        idempotency_key="paid-2",
    )
    from apps.loft_subscriptions.services import SubscriptionActivationService

    second_subscription = SubscriptionActivationService.activate_paid_invoice(
        invoice_id=second_invoice.pk,
        confirmed_by=manager_profile,
    )
    first_subscription = LoftSubscription.objects.order_by("starts_at").first()
    assert second_payment.pk
    assert second_subscription.status == LoftSubscriptionStatus.SCHEDULED
    assert second_subscription.starts_at == first_subscription.ends_at


def test_calendar_month_duration_is_calculated_with_calendar_months():
    catalog = create_catalog()
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    invoice, _payment = paid_invoice_with_payment(
        user.active_profile,
        catalog,
        idempotency_key="calendar-months",
    )
    from apps.loft_subscriptions.services import SubscriptionActivationService

    starts_at = timezone.datetime(2026, 1, 31, 10, tzinfo=timezone.utc)
    subscription = SubscriptionActivationService.activate_paid_invoice(
        invoice_id=invoice.pk,
        confirmed_by=user.active_profile,
        confirmed_at=starts_at,
    )

    assert subscription.ends_at == starts_at + relativedelta(months=12)


def test_ai_quota_consumes_included_then_purchased_and_is_idempotent():
    catalog = create_catalog()
    profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    invoice, _payment = paid_invoice_with_payment(
        profile,
        catalog,
        idempotency_key="ai-paid",
    )
    from apps.loft_subscriptions.services import SubscriptionActivationService

    SubscriptionActivationService.activate_paid_invoice(
        invoice_id=invoice.pk,
        confirmed_by=profile,
    )

    for index in range(10):
        SubscriptionAIQuotaService.consume(
            profile=profile,
            operation_code="pedigree_ai",
            idempotency_key=f"included-{index}",
        )
    balance = SubscriptionAIQuotaService.consume(
        profile=profile,
        operation_code="pedigree_ai",
        idempotency_key="purchased-1",
    )
    same_balance = SubscriptionAIQuotaService.consume(
        profile=profile,
        operation_code="pedigree_ai",
        idempotency_key="purchased-1",
    )

    assert SubscriptionAIUsage.objects.filter(quota_type="included").count() == 10
    assert SubscriptionAIUsage.objects.filter(quota_type="purchased").count() == 1
    assert balance == same_balance
    assert balance["included_remaining"] == 0
    assert balance["purchased_remaining"] == 299


def test_pedigree_generation_requires_subscription_before_provider_call(settings):
    settings.PEDIGREE_REPORT_BASE_URL = "http://provider.test"
    user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    subject = PedigreeBirdFactory(owner=user.active_profile)
    client = authenticated_client(user)

    with patch("requests.post") as post:
        response = client.post(
            "/api/pedigrees/generate/",
            {
                "subject_bird_id": subject.pk,
                "pdf_id": 7,
                "generation_depth": 1,
                "report": {"parameters": {}},
            },
            format="json",
        )

    assert response.status_code == 403
    assert response.data["code"] == "SUBSCRIPTION_REQUIRED"
    post.assert_not_called()


def test_template_entitlement_allows_selected_and_rejects_unselected_template():
    catalog = create_catalog()
    profile = UserAccountFactory(with_profile=ProfileType.CUSTOMER).active_profile
    invoice, _payment = paid_invoice_with_payment(
        profile,
        catalog,
        idempotency_key="template-access",
    )
    from apps.loft_subscriptions.services import SubscriptionActivationService

    SubscriptionActivationService.activate_paid_invoice(
        invoice_id=invoice.pk,
        confirmed_by=profile,
    )

    assert SubscriptionAccessService.require_pedigree_template(profile, 7)
    with pytest.raises(Exception):
        SubscriptionAccessService.require_pedigree_template(profile, 8)
