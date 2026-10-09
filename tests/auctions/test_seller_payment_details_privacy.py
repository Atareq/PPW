from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.auctions.models import (
    AuctionBidVisibility,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
)
from apps.auctions.services import AuctionService, BiddingService
from apps.authentication.services import build_token_for_user, switch_user_profile
from apps.deals.models import CompletedDeal
from apps.notifications.models import Notification
from apps.payments.models import PaymentRequest
from apps.prizes.models import PrizeAward
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import ProfileDataChangeLog, SellerProfile
from tests.factories import (
    AuctionFactory,
    AuctionItemFactory,
    BidFactory,
    BirdFactory,
    CustomerProfileFactory,
    SellerProfileFactory,
    UserAccountFactory,
)

pytestmark = pytest.mark.django_db

PAYMENT_DETAILS_FIELDS = ("bank_account", "wallet", "transfer_phone")


def make_client(user=None, lang="en"):
    client = APIClient()
    headers = {"HTTP_ACCEPT_LANGUAGE": lang}
    if user is not None:
        refresh = build_token_for_user(user)
        headers["HTTP_AUTHORIZATION"] = f"Bearer {refresh.access_token}"
    client.credentials(**headers)
    return client


def create_ended_won_auction(*, bank_account="EG990001234567890", wallet="VF-CASH-010999", transfer_phone="+201099988877"):
    seller_user = UserAccountFactory(
        phone_number="+201001112233",
        with_profile=ProfileType.SELLER,
    )
    seller_profile = seller_user.active_profile
    seller_profile.bank_account = bank_account
    seller_profile.wallet = wallet
    seller_profile.transfer_phone = transfer_phone
    seller_profile.save(update_fields=["bank_account", "wallet", "transfer_phone"])

    winner_user = UserAccountFactory(
        phone_number="+201002223344",
        with_profile=ProfileType.CUSTOMER,
    )
    losing_user = UserAccountFactory(
        phone_number="+201003334455",
        with_profile=ProfileType.CUSTOMER,
    )

    now = timezone.now()
    auction = AuctionFactory(
        owner=seller_profile,
        status=AuctionStatus.ACTIVE,
        start_time=now - timedelta(hours=3),
        end_time=now - timedelta(minutes=5),
    )
    bird = BirdFactory(owner=seller_profile, is_for_auction=True, is_market_listed=False)
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        status=AuctionItemStatus.ACTIVE,
        starting_price=Decimal("100.00"),
        current_price=Decimal("100.00"),
    )
    BidFactory(
        item=item,
        bidder=losing_user.active_profile,
        amount=Decimal("120.00"),
    )
    BidFactory(
        item=item,
        bidder=winner_user.active_profile,
        amount=Decimal("150.00"),
    )
    AuctionService.close_auction(auction)
    auction.refresh_from_db()
    item.refresh_from_db()
    return auction, item, seller_user, winner_user, losing_user


def assert_no_payment_details_in_payload(payload, seller_user=None):
    serialized = str(payload)
    for field in PAYMENT_DETAILS_FIELDS:
        assert f"'{field}'" not in serialized
        assert f'"{field}"' not in serialized
    if seller_user is not None:
        seller_profile = SellerProfile.objects.get(user=seller_user)
        for secret in (
            seller_profile.bank_account,
            seller_profile.wallet,
            seller_profile.transfer_phone,
            str(seller_user.phone_number),
        ):
            if secret:
                assert secret not in serialized


def test_seller_can_set_and_update_own_payment_details_without_audit_snapshot_leak():
    """Cases 1 & 24: Seller can set/update own payment details; values are not dumped into audit snapshots."""
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = seller_user.active_profile
    client = make_client(seller_user)

    response = client.patch(
        f"/api/users/mobile/sellers/{seller_profile.pk}/",
        {
            "bank_account": "  EG12000999888777666  ",
            "wallet": "  INSTAPAY-SELLER-1  ",
            "transfer_phone": "  +201066677788  ",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["bank_account"] == "EG12000999888777666"
    assert response.data["wallet"] == "INSTAPAY-SELLER-1"
    assert response.data["transfer_phone"] == "+201066677788"

    seller_profile.refresh_from_db()
    assert seller_profile.bank_account == "EG12000999888777666"
    assert seller_profile.wallet == "INSTAPAY-SELLER-1"
    assert seller_profile.transfer_phone == "+201066677788"

    # Update a locked field + payment field together and verify payment fields are excluded from ProfileDataChangeLog
    response_2 = client.patch(
        f"/api/users/mobile/sellers/{seller_profile.pk}/",
        {
            "address": "Cairo Loft 10",
            "bank_account": "EG999999999999",
        },
        format="json",
    )
    assert response_2.status_code == status.HTTP_200_OK
    logs = ProfileDataChangeLog.objects.filter(profile=seller_profile)
    for log in logs:
        for field in PAYMENT_DETAILS_FIELDS:
            assert field not in log.changed_fields
            assert field not in log.old_values
            assert field not in log.new_values


def test_another_seller_cannot_update_or_view_sellers_payment_details():
    """Case 2: Another seller cannot update or view another seller's payment details."""
    seller_1 = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_1_profile = seller_1.active_profile
    seller_1_profile.bank_account = "SECRET-BANK-1"
    seller_1_profile.save(update_fields=["bank_account"])

    seller_2 = UserAccountFactory(with_profile=ProfileType.SELLER)
    client_2 = make_client(seller_2)

    response = client_2.patch(
        f"/api/users/mobile/sellers/{seller_1_profile.pk}/",
        {"bank_account": "HACKED-BANK"},
        format="json",
    )
    # SellerProfileMobileViewSet is strictly scoped to request.user's own SellerProfile
    assert response.data["id"] == seller_2.active_profile.pk
    assert "SECRET-BANK-1" not in str(response.data)
    seller_1_profile.refresh_from_db()
    assert seller_1_profile.bank_account == "SECRET-BANK-1"


def test_customer_cannot_update_seller_payment_details():
    """Case 3: Customer cannot update seller's payment details."""
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = seller_user.active_profile
    seller_profile.wallet = "ORIGINAL-WALLET"
    seller_profile.save(update_fields=["wallet"])

    customer_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    client = make_client(customer_user)

    response = client.patch(
        f"/api/users/mobile/sellers/{seller_profile.pk}/",
        {"wallet": "TAMPERED-WALLET"},
        format="json",
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    seller_profile.refresh_from_db()
    assert seller_profile.wallet == "ORIGINAL-WALLET"


def test_winning_buyer_can_access_seller_payment_details_after_auction_ends():
    """Cases 4, 19, 20: Winning buyer can access seller payment details after auction ends without PaymentRequest or CompletedDeal."""
    auction, item, seller_user, winner_user, _ = create_ended_won_auction()

    assert not PaymentRequest.objects.filter(
        auctionpaymentrequest__auction_item=item
    ).exists()
    assert not CompletedDeal.objects.filter(source_id=item.pk).exists()

    response = make_client(winner_user).get(
        f"/api/auctions/{auction.pk}/seller-payment-details/"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response["Cache-Control"] == "private, no-store"
    assert response.data == {
        "seller_id": seller_user.active_profile.pk,
        "auction_id": auction.pk,
        "bank_account": "EG990001234567890",
        "wallet": "VF-CASH-010999",
        "transfer_phone": "+201099988877",
    }


def test_winning_buyer_can_access_seller_payment_details_when_switched_to_seller_mode():
    """Case 5: Winning buyer account can access even if currently switched to Seller mode."""
    auction, _, seller_user, winner_user, _ = create_ended_won_auction()

    # Give winner_user an activated, valid SellerProfile and switch to Seller mode
    SellerProfile.objects.create(
        user=winner_user,
        nickname=winner_user.username,
        country=CountryOptions.EGYPT,
        currency=CurrencyOptions.EGP,
        activated=True,
        is_valid=True,
        is_active=False,
    )
    switched, new_role = switch_user_profile(
        winner_user, new_profile_type=ProfileType.SELLER
    )
    assert switched is True
    assert new_role == ProfileType.SELLER

    response = make_client(winner_user).get(
        f"/api/auctions/{auction.pk}/seller-payment-details/"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["seller_id"] == seller_user.active_profile.pk
    assert response.data["auction_id"] == auction.pk
    assert response.data["bank_account"] == "EG990001234567890"


def test_non_winners_and_unauthenticated_users_cannot_access_seller_payment_details():
    """Cases 6, 7, 8, 9: Losing bidder, non-bidding customer, another seller, manager, and unauthenticated user cannot access."""
    auction, _, seller_user, _, losing_user = create_ended_won_auction()
    non_bidding_customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    another_seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    manager_user = UserAccountFactory(with_profile=ProfileType.MANAGER)

    url = f"/api/auctions/{auction.pk}/seller-payment-details/"

    # Case 9: Unauthenticated -> 401
    unauth_resp = make_client().get(url)
    assert unauth_resp.status_code == status.HTTP_401_UNAUTHORIZED

    # Case 6: Losing bidder -> 404
    losing_resp = make_client(losing_user).get(url)
    assert losing_resp.status_code == status.HTTP_404_NOT_FOUND
    assert_no_payment_details_in_payload(losing_resp.data, seller_user)

    # Case 7: Non-bidding customer -> 404
    other_cust_resp = make_client(non_bidding_customer).get(url)
    assert other_cust_resp.status_code == status.HTTP_404_NOT_FOUND
    assert_no_payment_details_in_payload(other_cust_resp.data, seller_user)

    # Case 8: Another seller -> 404
    other_seller_resp = make_client(another_seller).get(url)
    assert other_seller_resp.status_code == status.HTTP_404_NOT_FOUND
    assert_no_payment_details_in_payload(other_seller_resp.data, seller_user)

    # Manager -> 403
    manager_resp = make_client(manager_user).get(url)
    assert manager_resp.status_code == status.HTTP_403_FORBIDDEN
    assert_no_payment_details_in_payload(manager_resp.data, seller_user)


def test_winner_cannot_access_before_auction_has_ended():
    """Case 10: Highest bidder / winner cannot access while auction is still active or scheduled."""
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = seller_user.active_profile
    seller_profile.bank_account = "ACTIVE-SECRET-BANK"
    seller_profile.save(update_fields=["bank_account"])

    bidder_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()
    auction = AuctionFactory(
        owner=seller_profile,
        status=AuctionStatus.ACTIVE,
        start_time=now - timedelta(hours=1),
        end_time=now + timedelta(hours=2),
    )
    item = AuctionItemFactory(
        auction=auction,
        bird=BirdFactory(owner=seller_profile),
        status=AuctionItemStatus.ACTIVE,
        starting_price=Decimal("100.00"),
        current_price=Decimal("100.00"),
    )
    BiddingService.place_bid(
        item=item,
        bidder=bidder_user.active_profile,
        amount=Decimal("150.00"),
    )

    response = make_client(bidder_user).get(
        f"/api/auctions/{auction.pk}/seller-payment-details/"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    # Scheduled (not-yet-started) auction is also denied
    scheduled_auction = AuctionFactory(
        owner=seller_profile,
        status=AuctionStatus.ACTIVE,
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=5),
    )
    scheduled_resp = make_client(bidder_user).get(
        f"/api/auctions/{scheduled_auction.pk}/seller-payment-details/"
    )
    assert scheduled_resp.status_code == status.HTTP_404_NOT_FOUND


def test_cancelled_no_winner_and_reserve_not_met_auctions_deny_access():
    """Cases 11, 12, 13: Cancelled auction, ended auction with no winner, and reserve-not-met auction deny access."""
    seller_user = UserAccountFactory(with_profile=ProfileType.SELLER)
    seller_profile = seller_user.active_profile
    seller_profile.bank_account = "NO-LEAK-BANK"
    seller_profile.save(update_fields=["bank_account"])

    bidder_user = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    now = timezone.now()

    # Case 11: Cancelled auction (even if item had winner set)
    cancelled_auction = AuctionFactory(
        owner=seller_profile,
        status=AuctionStatus.CANCELLED,
        start_time=now - timedelta(hours=3),
        end_time=now - timedelta(hours=1),
    )
    AuctionItemFactory(
        auction=cancelled_auction,
        bird=BirdFactory(owner=seller_profile),
        status=AuctionItemStatus.SOLD,
        winner=bidder_user.active_profile,
    )
    resp_cancelled = make_client(bidder_user).get(
        f"/api/auctions/{cancelled_auction.pk}/seller-payment-details/"
    )
    assert resp_cancelled.status_code == status.HTTP_404_NOT_FOUND

    # Case 12: Ended with no bids / no winner
    no_winner_auction = AuctionFactory(
        owner=seller_profile,
        status=AuctionStatus.ACTIVE,
        start_time=now - timedelta(hours=3),
        end_time=now - timedelta(minutes=10),
    )
    AuctionItemFactory(
        auction=no_winner_auction,
        bird=BirdFactory(owner=seller_profile),
        status=AuctionItemStatus.ACTIVE,
    )
    AuctionService.close_auction(no_winner_auction)
    resp_no_winner = make_client(bidder_user).get(
        f"/api/auctions/{no_winner_auction.pk}/seller-payment-details/"
    )
    assert resp_no_winner.status_code == status.HTTP_404_NOT_FOUND

    # Case 13: Reserve not met
    reserve_auction = AuctionFactory(
        owner=seller_profile,
        bid_visibility=AuctionBidVisibility.SEALED,
        status=AuctionStatus.ACTIVE,
        start_time=now - timedelta(hours=3),
        end_time=now - timedelta(minutes=10),
    )
    reserve_item = AuctionItemFactory(
        auction=reserve_auction,
        bird=BirdFactory(owner=seller_profile),
        reserve_price=Decimal("500.00"),
        starting_price=Decimal("100.00"),
        current_price=Decimal("100.00"),
    )
    BidFactory(
        item=reserve_item,
        bidder=bidder_user.active_profile,
        amount=Decimal("200.00"),
    )
    AuctionService.close_auction(reserve_auction)
    resp_reserve = make_client(bidder_user).get(
        f"/api/auctions/{reserve_auction.pk}/seller-payment-details/"
    )
    assert resp_reserve.status_code == status.HTTP_404_NOT_FOUND


def test_public_and_standard_endpoints_never_expose_seller_payment_details(
    django_capture_on_commit_callbacks,
):
    """Cases 14, 15, 16, 17, 18, 22, 24: Public seller profiles, discovery, admin seller list, auction list/detail, events, notifications, and prizes never expose payment details or login phone."""
    with django_capture_on_commit_callbacks(execute=True):
        auction, item, seller_user, winner_user, losing_user = create_ended_won_auction(
            bank_account="EG-PRIVATE-IBAN-777",
            wallet="WALLET-PRIVATE-888",
            transfer_phone="+201555555555",
        )

    viewer_client = make_client(losing_user)
    winner_client = make_client(winner_user)

    # Case 14 & 15: Feed seller discovery & suggestions
    discover_resp = viewer_client.get("/api/feed/sellers/")
    assert discover_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(discover_resp.data, seller_user)

    suggestions_resp = viewer_client.get("/api/feed/people-you-may-know/")
    assert suggestions_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(suggestions_resp.data, seller_user)

    # Case 15: Admin seller list/detail does not automatically expose payment details to managers
    superuser = UserAccountFactory(
        with_profile=ProfileType.MANAGER,
        is_staff=True,
        is_superuser=True,
    )
    admin_resp = make_client(superuser).get(
        f"/api/users/admin/sellers/{seller_user.active_profile.pk}/"
    )
    assert admin_resp.status_code == status.HTTP_200_OK
    for field in PAYMENT_DETAILS_FIELDS:
        assert field not in admin_resp.data

    # Case 16: Auction list endpoint
    list_resp = viewer_client.get("/api/auctions/")
    assert list_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(list_resp.data, seller_user)

    # Case 17: Auction item detail, events, and auction detail serializer for ordinary viewer
    item_viewer_resp = viewer_client.get(f"/api/auctions/items/{item.pk}/")
    assert item_viewer_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(item_viewer_resp.data, seller_user)

    detail_admin_resp = make_client(superuser).get(f"/api/auctions/{auction.pk}/")
    assert detail_admin_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(detail_admin_resp.data, seller_user)
    for event in detail_admin_resp.data.get("events", []):
        assert "user_phone" not in event

    events_resp = viewer_client.get(f"/api/auctions/events/?auction_id={auction.pk}")
    assert events_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(events_resp.data, seller_user)

    # Case 18: Standard auction / item detail endpoints for winning buyer do NOT leak payment details
    item_winner_resp = winner_client.get(f"/api/auctions/items/{item.pk}/")
    assert item_winner_resp.status_code == status.HTTP_200_OK
    assert_no_payment_details_in_payload(item_winner_resp.data, seller_user)
    detail_winner_resp = winner_client.get(f"/api/auctions/{auction.pk}/")
    assert_no_payment_details_in_payload(detail_winner_resp.data, seller_user)

    # Notifications and PrizeAwards metadata never contain seller payment details
    for notif in Notification.objects.all():
        assert_no_payment_details_in_payload(notif.metadata, seller_user)
    for award in PrizeAward.objects.all():
        assert_no_payment_details_in_payload(award.metadata, seller_user)


def test_empty_payment_fields_and_login_phone_isolation():
    """Cases 21 & 22: Seller with empty payment fields returns valid structured empty strings and never falls back to login phone_number."""
    auction, _, seller_user, winner_user, _ = create_ended_won_auction(
        bank_account="",
        wallet="",
        transfer_phone="",
    )

    response = make_client(winner_user).get(
        f"/api/auctions/{auction.pk}/seller-payment-details/"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {
        "seller_id": seller_user.active_profile.pk,
        "auction_id": auction.pk,
        "bank_account": "",
        "wallet": "",
        "transfer_phone": "",
    }
    assert str(seller_user.phone_number) not in str(response.data)


def test_soft_deleted_auction_or_inactive_seller_denies_access():
    """Case 23: Soft-deleted auction or invalid/soft-deleted seller profile is rejected with 404."""
    auction, _, seller_user, winner_user, _ = create_ended_won_auction()
    seller_profile = seller_user.active_profile
    url = f"/api/auctions/{auction.pk}/seller-payment-details/"

    # Invalid seller profile -> 404
    seller_profile.is_valid = False
    seller_profile.save(update_fields=["is_valid"])
    resp_invalid_seller = make_client(winner_user).get(url)
    assert resp_invalid_seller.status_code == status.HTTP_404_NOT_FOUND

    # Restore seller, soft-delete auction -> 404
    seller_profile.is_valid = True
    seller_profile.save(update_fields=["is_valid"])
    auction.delete()
    resp_deleted_auction = make_client(winner_user).get(url)
    assert resp_deleted_auction.status_code == status.HTTP_404_NOT_FOUND


def test_localization_arabic_and_english_error_messages():
    """Case 25: Arabic and English error responses on unauthorized/non-winner access."""
    from django.utils import translation

    auction, _, _, _, losing_user = create_ended_won_auction()
    url = f"/api/auctions/{auction.pk}/seller-payment-details/"

    try:
        en_resp = make_client(losing_user, lang="en").get(url)
        ar_resp = make_client(losing_user, lang="ar").get(url)
    finally:
        translation.activate("en")

    assert en_resp.status_code == status.HTTP_404_NOT_FOUND
    assert ar_resp.status_code == status.HTTP_404_NOT_FOUND
    assert "detail" in en_resp.data
    assert "detail" in ar_resp.data
    assert en_resp.data["detail"] != ar_resp.data["detail"]
