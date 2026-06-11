from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.assets.models import AssetCategory
from apps.authentication.services import build_token_for_user
from apps.orders.models import OrderItemStatus
from apps.orders.services import OrderItemService, OrderService
from apps.payments.services import PaymentRequestService
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    CashbackTransactionType,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
)
from apps.promotions.services import (
    CashbackOfferService,
    CashbackService,
    DiscountOfferService,
)
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile
from tests.factories import (
    CashbackOfferFactory,
    CustomerProfileFactory,
    DiscountOfferFactory,
    SellerProfileFactory,
    SupplyFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db


def authenticated_client(user):
    client = APIClient()
    refresh = build_token_for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


def active_customer(user):
    return CustomerProfile.objects.get(pk=user.active_profile.pk)


def active_seller(user):
    return SellerProfile.objects.get(pk=user.active_profile.pk)


def response_results(response):
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data


class TestDiscountOffer:
    def test_valid_percentage_and_fixed_offers(self):
        percentage = DiscountOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("15.00"),
        )
        fixed = DiscountOfferFactory(
            value_type=OfferValueType.FIXED_AMOUNT,
            value=Decimal("10.00"),
        )

        assert percentage.pk
        assert fixed.pk

    def test_invalid_percentage_above_100_is_rejected(self):
        with pytest.raises(DjangoValidationError):
            DiscountOffer.objects.create(
                title="Too much",
                value_type=OfferValueType.PERCENTAGE,
                value=Decimal("101.00"),
                target_type=OfferTargetType.ALL,
            )

    def test_invalid_zero_or_negative_value_is_rejected(self):
        with pytest.raises(DjangoValidationError):
            DiscountOffer.objects.create(
                title="Zero",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("0.00"),
                target_type=OfferTargetType.ALL,
            )

    def test_target_validation(self):
        asset = SupplyFactory()

        with pytest.raises(DjangoValidationError):
            DiscountOffer.objects.create(
                title="Missing asset",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("1.00"),
                target_type=OfferTargetType.ASSET,
            )

        with pytest.raises(DjangoValidationError):
            DiscountOffer.objects.create(
                title="Mixed all",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("1.00"),
                target_type=OfferTargetType.ALL,
                asset=asset,
            )

    def test_immutable_after_creation_except_activation(self):
        offer = DiscountOfferFactory(is_active=True)

        offer.title = "Changed"
        with pytest.raises(DjangoValidationError):
            offer.save()

        DiscountOfferService.deactivate(offer)
        offer.refresh_from_db()
        assert offer.is_active is False

        DiscountOfferService.activate(offer)
        offer.refresh_from_db()
        assert offer.is_active is True

    def test_best_discount_selection_prefers_specificity_on_tie(self):
        asset = SupplyFactory(price=Decimal("100.00"))
        DiscountOfferFactory(
            value=Decimal("10.00"),
            target_type=OfferTargetType.ALL,
            priority=100,
        )
        DiscountOfferFactory(
            value=Decimal("10.00"),
            target_type=OfferTargetType.CATEGORY,
            category=AssetCategory.SUPPLY,
            priority=50,
        )
        asset_offer = DiscountOfferFactory(
            value=Decimal("10.00"),
            target_type=OfferTargetType.ASSET,
            asset=asset,
            priority=0,
        )

        selected = DiscountOfferService.choose_best_offer(
            asset,
            Decimal("100.00"),
        )

        assert selected.pk == asset_offer.pk


class TestCashbackOffer:
    def test_valid_percentage_and_fixed_offers(self):
        percentage = CashbackOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("10.00"),
            allowed_redemption_amount=Decimal("5.00"),
        )
        fixed = CashbackOfferFactory(
            value_type=OfferValueType.FIXED_AMOUNT,
            value=Decimal("3.00"),
        )

        assert percentage.pk
        assert fixed.pk

    def test_redemption_and_minimum_validation(self):
        with pytest.raises(DjangoValidationError):
            CashbackOffer.objects.create(
                title="Bad redemption",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("1.00"),
                target_type=OfferTargetType.ALL,
                allowed_redemption_amount=Decimal("-1.00"),
            )

        with pytest.raises(DjangoValidationError):
            CashbackOffer.objects.create(
                title="Bad minimum",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("1.00"),
                target_type=OfferTargetType.ALL,
                minimum_order_price=Decimal("-1.00"),
            )

    def test_highest_calculated_cashback_wins(self):
        asset = SupplyFactory(price=Decimal("100.00"))
        CashbackOfferFactory(
            value_type=OfferValueType.FIXED_AMOUNT,
            value=Decimal("5.00"),
        )
        winning_offer = CashbackOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("10.00"),
        )

        selected = CashbackOfferService.choose_best_offer(
            asset,
            Decimal("100.00"),
        )

        assert selected.pk == winning_offer.pk

    def test_immutable_after_creation_except_activation(self):
        offer = CashbackOfferFactory(is_active=True)

        offer.value = Decimal("20.00")
        with pytest.raises(DjangoValidationError):
            offer.save()

        CashbackOfferService.deactivate(offer)
        offer.refresh_from_db()
        assert offer.is_active is False


class TestPromotionOrderIntegration:
    def test_discount_is_applied_when_order_item_is_created(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        asset = SupplyFactory(count=5, price=Decimal("20.00"))
        DiscountOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("10.00"),
        )

        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()

        assert item.gross_total == Decimal("40.00")
        assert item.discount_amount == Decimal("4.00")
        assert item.total_price == Decimal("36.00")
        assert order.total_price == Decimal("36.00")
        assert active_customer(customer).cashback_balance == Decimal("0.00")
        assert CashbackTransaction.objects.count() == 0

    def test_cashback_redeems_on_seller_approval(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        customer_profile = active_customer(customer)
        customer_profile.cashback_balance = Decimal("8.00")
        customer_profile.save(update_fields=["cashback_balance", "modified"])
        asset = SupplyFactory(
            owner=active_seller(seller),
            count=5,
            price=Decimal("20.00"),
        )
        offer = CashbackOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("10.00"),
            allowed_redemption_amount=Decimal("5.00"),
        )
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = order.items.get()

        approved_item = OrderItemService.approve_item(
            item,
            seller.active_profile,
        )

        customer_profile.refresh_from_db()
        assert approved_item.status == OrderItemStatus.APPROVED
        assert approved_item.cashback_offer_id == offer.pk
        assert approved_item.cashback_redeemed_amount == Decimal("5.00")
        assert approved_item.cashback_earned_amount == Decimal("4.00")
        assert approved_item.total_price == Decimal("35.00")
        assert customer_profile.cashback_balance == Decimal("3.00")
        transaction = CashbackTransaction.objects.get(
            transaction_type=CashbackTransactionType.REDEEMED
        )
        assert transaction.amount == Decimal("5.00")
        assert transaction.idempotency_key == f"redeemed:order_item:{item.pk}"

    def test_no_eligible_cashback_offer_leaves_fields_empty(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        asset = SupplyFactory(owner=active_seller(seller), count=5)
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        item = order.items.get()

        approved_item = OrderItemService.approve_item(
            item,
            seller.active_profile,
        )

        assert approved_item.cashback_offer_id is None
        assert approved_item.cashback_redeemed_amount == Decimal("0.00")
        assert approved_item.cashback_earned_amount == Decimal("0.00")
        assert CashbackTransaction.objects.count() == 0

    def test_cashback_is_earned_once_on_market_completion(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        customer_profile = active_customer(customer)
        customer_profile.cashback_balance = Decimal("8.00")
        customer_profile.save(update_fields=["cashback_balance", "modified"])
        asset = SupplyFactory(
            owner=active_seller(seller),
            count=5,
            price=Decimal("20.00"),
        )
        CashbackOfferFactory(
            value_type=OfferValueType.PERCENTAGE,
            value=Decimal("10.00"),
            allowed_redemption_amount=Decimal("5.00"),
        )
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 2}],
        )
        item = OrderItemService.approve_item(
            order.items.get(),
            seller.active_profile,
        )
        request = PaymentRequestService.create_for_order_item(
            item.pk,
            customer.active_profile,
        )

        PaymentRequestService.approve(request.pk, seller.active_profile)
        item.refresh_from_db()
        CashbackService.earn_for_completed_order_item(item)

        customer_profile.refresh_from_db()
        assert customer_profile.cashback_balance == Decimal("7.00")
        assert (
            CashbackTransaction.objects.filter(
                transaction_type=CashbackTransactionType.EARNED
            ).count()
            == 1
        )

    def test_refund_redeemed_cashback_is_idempotent(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        customer_profile = active_customer(customer)
        customer_profile.cashback_balance = Decimal("8.00")
        customer_profile.save(update_fields=["cashback_balance", "modified"])
        asset = SupplyFactory(owner=active_seller(seller), count=5)
        CashbackOfferFactory(
            value=Decimal("5.00"),
            allowed_redemption_amount=Decimal("5.00"),
        )
        order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": asset.pk, "quantity": 1}],
        )
        item = OrderItemService.approve_item(
            order.items.get(),
            seller.active_profile,
        )

        first = CashbackService.refund_redeemed_for_order_item(item)
        second = CashbackService.refund_redeemed_for_order_item(item)

        customer_profile.refresh_from_db()
        assert first.pk == second.pk
        assert customer_profile.cashback_balance == Decimal("8.00")
        assert (
            CashbackTransaction.objects.filter(
                transaction_type=CashbackTransactionType.REFUNDED
            ).count()
            == 1
        )

    def test_repeated_redemption_does_not_overspend_balance(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        customer_profile = active_customer(customer)
        customer_profile.cashback_balance = Decimal("8.00")
        customer_profile.save(update_fields=["cashback_balance", "modified"])
        CashbackOfferFactory(
            value=Decimal("2.00"),
            allowed_redemption_amount=Decimal("10.00"),
        )
        first_asset = SupplyFactory(owner=active_seller(seller), count=5)
        second_asset = SupplyFactory(owner=active_seller(seller), count=5)
        first_order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": first_asset.pk, "quantity": 1}],
        )
        second_order = OrderService.create_order(
            customer.active_profile,
            [{"asset_id": second_asset.pk, "quantity": 1}],
        )

        first_item = OrderItemService.approve_item(
            first_order.items.get(),
            seller.active_profile,
        )
        second_item = OrderItemService.approve_item(
            second_order.items.get(),
            seller.active_profile,
        )

        customer_profile.refresh_from_db()
        assert first_item.cashback_redeemed_amount == Decimal("8.00")
        assert second_item.cashback_redeemed_amount == Decimal("0.00")
        assert customer_profile.cashback_balance == Decimal("0.00")


class TestCashbackAdminAdjustment:
    def test_admin_adjustment_increases_and_decreases_balance(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = CustomerProfileFactory(cashback_balance=Decimal("2.00"))

        increase = CashbackService.admin_adjust(
            customer,
            Decimal("10.00"),
            manager.active_profile,
            "bonus",
            idempotency_key="adjust-1",
        )
        duplicate = CashbackService.admin_adjust(
            customer,
            Decimal("10.00"),
            manager.active_profile,
            "bonus",
            idempotency_key="adjust-1",
        )
        decrease = CashbackService.admin_adjust(
            customer,
            Decimal("-3.00"),
            manager.active_profile,
            "correction",
            idempotency_key="adjust-2",
        )

        customer.refresh_from_db()
        assert increase.pk == duplicate.pk
        assert decrease.amount == Decimal("-3.00")
        assert customer.cashback_balance == Decimal("9.00")
        assert CashbackTransaction.objects.count() == 2

    def test_admin_adjustment_cannot_make_balance_negative(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = CustomerProfileFactory(cashback_balance=Decimal("2.00"))

        with pytest.raises(ValidationError):
            CashbackService.admin_adjust(
                customer,
                Decimal("-3.00"),
                manager.active_profile,
                "too much",
            )

    def test_non_manager_cannot_adjust(self):
        seller = UserAccountFactory(with_profile=ProfileType.SELLER)
        customer = CustomerProfileFactory(cashback_balance=Decimal("2.00"))

        with pytest.raises(PermissionDenied):
            CashbackService.admin_adjust(
                customer,
                Decimal("1.00"),
                seller.active_profile,
                "no",
            )


class TestPromotionApi:
    def test_customer_sees_current_offers_and_cashback(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        customer_profile = active_customer(customer)
        customer_profile.cashback_balance = Decimal("12.00")
        customer_profile.save(update_fields=["cashback_balance", "modified"])
        discount = DiscountOfferFactory()
        cashback = CashbackOfferFactory()
        CashbackService.admin_adjust(
            customer_profile,
            Decimal("3.00"),
            UserAccountFactory(with_profile=ProfileType.MANAGER).active_profile,
            "api seed",
            idempotency_key="api-seed",
        )
        client = authenticated_client(customer)

        discount_response = client.get(
            "/api/promotions/discount-offers/current/"
        )
        cashback_response = client.get(
            "/api/promotions/cashback-offers/current/"
        )
        balance_response = client.get("/api/promotions/cashback/balance/")
        transaction_response = client.get("/api/promotions/cashback/my/")

        assert discount_response.status_code == 200
        assert cashback_response.status_code == 200
        assert balance_response.status_code == 200
        assert transaction_response.status_code == 200
        assert response_results(discount_response)[0]["id"] == discount.pk
        assert response_results(cashback_response)[0]["id"] == cashback.pk
        assert balance_response.data["cashback_balance"] == "15.00"
        assert transaction_response.data["count"] == 1

    def test_manager_creates_and_toggles_offers_and_adjusts_cashback(self):
        manager = UserAccountFactory(with_profile=ProfileType.MANAGER)
        customer = CustomerProfileFactory()
        client = authenticated_client(manager)

        discount_response = client.post(
            "/api/promotions/admin/discount-offers/",
            {
                "title": "Manager discount",
                "description": "",
                "value_type": OfferValueType.FIXED_AMOUNT,
                "value": "5.00",
                "target_type": OfferTargetType.ALL,
                "minimum_order_price": "0.00",
                "priority": 0,
            },
            format="json",
        )
        deactivate_response = client.post(
            "/api/promotions/admin/discount-offers/"
            f"{discount_response.data['id']}/deactivate/"
        )
        activate_response = client.post(
            "/api/promotions/admin/discount-offers/"
            f"{discount_response.data['id']}/activate/"
        )
        cashback_response = client.post(
            "/api/promotions/admin/cashback-offers/",
            {
                "title": "Manager cashback",
                "description": "",
                "value_type": OfferValueType.FIXED_AMOUNT,
                "value": "2.00",
                "target_type": OfferTargetType.ALL,
                "minimum_order_price": "0.00",
                "allowed_redemption_amount": "1.00",
                "priority": 0,
            },
            format="json",
        )
        adjustment_response = client.post(
            "/api/promotions/admin/cashback-adjustments/",
            {
                "customer_id": customer.pk,
                "amount": "6.00",
                "note": "manual",
                "idempotency_key": "api-adjust-1",
            },
            format="json",
        )

        assert discount_response.status_code == 201
        assert deactivate_response.status_code == 200
        assert deactivate_response.data["is_active"] is False
        assert activate_response.status_code == 200
        assert activate_response.data["is_active"] is True
        assert cashback_response.status_code == 201
        assert adjustment_response.status_code == 201
        assert adjustment_response.data["amount"] == "6.00"

    def test_non_manager_cannot_manage_promotions(self):
        customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
        client = authenticated_client(customer)

        response = client.post(
            "/api/promotions/admin/discount-offers/",
            {
                "title": "No",
                "value_type": OfferValueType.FIXED_AMOUNT,
                "value": "5.00",
                "target_type": OfferTargetType.ALL,
            },
            format="json",
        )

        assert response.status_code == 403
