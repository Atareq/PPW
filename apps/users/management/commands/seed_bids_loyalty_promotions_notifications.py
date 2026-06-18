"""
python manage.py seed_bids_loyalty_promotions_notifications

Seeds:
  Bids       — extra bids from customer on active auction items
  Loyalty    — PointWallet + PointTransactions + BadgeAwards for both users
  Promotions — 2 DiscountOffers + 2 CashbackOffers
  Notifications — varied notifications for customer and seller profiles

Depends on seed_all + seed_assets + seed_auctions_orders.
Safe to rerun.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assets.models import AssetCategory
from apps.auctions.models import Auction, AuctionEvent, AuctionItem, Bid
from apps.core.models import Notification, NotificationKind
from apps.loyalty.constants import BadgeType, PointTransactionType
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.promotions.models import (
    CashbackOffer,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
)
from apps.users.models import CustomerProfile, SellerProfile

SELLER_PHONE = "+201000000007"
CUSTOMER_PHONE = "+201000000006"


class Command(BaseCommand):
    help = "Seed bids, loyalty, promotions, and notifications."

    def handle(self, *args, **options):
        seller = SellerProfile.objects.filter(user__phone_number=SELLER_PHONE).first()
        customer = CustomerProfile.objects.filter(user__phone_number=CUSTOMER_PHONE).first()

        if not seller or not customer:
            self.stdout.write(self.style.ERROR("Seller/customer not found. Run seed_all first."))
            return

        self._seed_bids(customer)
        self._seed_loyalty(seller, customer)
        self._seed_promotions(seller)
        self._seed_notifications(seller, customer)
        self.stdout.write(self.style.SUCCESS("Bids, loyalty, promotions, notifications seeded."))

    # ── Bids ──────────────────────────────────────────────────────────────────

    def _seed_bids(self, customer):
        active_auctions = Auction.objects.filter(status="active")

        for auction in active_auctions:
            for item in auction.items.filter(status="active"):
                existing_count = item.bids.filter(bidder=customer).count()
                if existing_count >= 3:
                    self.stdout.write(f"  Bids on item #{item.pk}: already have {existing_count}")
                    continue

                # Place bids incrementally above current price
                current = item.current_price
                increment = auction.min_bid_increment or Decimal("50.00")
                bids_to_place = 3 - existing_count

                with transaction.atomic():
                    for i in range(bids_to_place):
                        current = current + increment * (i + 1)
                        bid = Bid.objects.create(
                            item=item,
                            bidder=customer,
                            amount=current,
                        )
                        AuctionEvent.objects.create(
                            auction=auction,
                            event_type="bid_placed",
                            item=item,
                            bid=bid,
                            performer=customer,
                            metadata={"amount": str(current)},
                        )

                    item.current_price = current
                    item.save(update_fields=["current_price"])

                self.stdout.write(f"  Bids on item #{item.pk} (auction #{auction.pk}): {bids_to_place} placed, current={current}")

    # ── Loyalty ───────────────────────────────────────────────────────────────

    def _seed_loyalty(self, seller, customer):
        for profile, label, earn_pts, reason in [
            (customer, "customer", 250, "Points earned from completed order and auction activity."),
            (seller, "seller",   150, "Points earned from first sale and seller activity."),
        ]:
            user = profile.user
            wallet, created = PointWallet.objects.get_or_create(user=user, defaults={"balance": 0})
            w_status = "created" if created else "exists"

            txns_exist = PointTransaction.objects.filter(wallet=wallet).count()
            if txns_exist >= 2:
                self.stdout.write(f"  Loyalty ({label}): wallet={w_status}, transactions already seeded")
            else:
                with transaction.atomic():
                    # Earn transaction
                    wallet.balance += earn_pts
                    wallet.save(update_fields=["balance"])
                    PointTransaction.objects.get_or_create(
                        idempotency_key=f"seed-earn-{profile.pk}",
                        defaults=dict(
                            wallet=wallet,
                            user=user,
                            profile=profile,
                            transaction_type=PointTransactionType.EARN,
                            points=earn_pts,
                            balance_after=wallet.balance,
                            reason=reason,
                            source_type="seed",
                            source_id=str(profile.pk),
                        ),
                    )
                    # Small spend transaction
                    spend_pts = 25
                    wallet.balance -= spend_pts
                    wallet.save(update_fields=["balance"])
                    PointTransaction.objects.get_or_create(
                        idempotency_key=f"seed-spend-{profile.pk}",
                        defaults=dict(
                            wallet=wallet,
                            user=user,
                            profile=profile,
                            transaction_type=PointTransactionType.SPEND,
                            points=spend_pts,
                            balance_after=wallet.balance,
                            reason="Points redeemed for activity reward.",
                            source_type="seed",
                            source_id=str(profile.pk),
                        ),
                    )
                self.stdout.write(f"  Loyalty ({label}): wallet={w_status}, transactions created, balance={wallet.balance}")

            # Badges
            badge_map = {
                "customer": [BadgeType.FIRST_ORDER, BadgeType.POINTS_STARTER],
                "seller": [BadgeType.SELLER_STARTER, BadgeType.POINTS_STARTER],
            }
            for badge in badge_map[label]:
                _, bc = BadgeAward.objects.get_or_create(
                    profile=profile,
                    badge_type=badge,
                    defaults={"source_type": "seed", "source_id": str(profile.pk)},
                )
                self.stdout.write(f"  Badge {badge} ({label}): {'created' if bc else 'exists'}")

    # ── Promotions ────────────────────────────────────────────────────────────

    def _seed_promotions(self, manager_profile):
        DISCOUNTS = [
            dict(
                title="10% Off All Birds",
                description="Flat 10% discount on all bird listings sitewide.",
                value_type=OfferValueType.PERCENTAGE,
                value=Decimal("10.00"),
                target_type=OfferTargetType.CATEGORY,
                category=AssetCategory.BIRD,
                minimum_order_price=Decimal("0.00"),
                priority=10,
            ),
            dict(
                title="Fixed 50 EGP Off Supplies",
                description="50 EGP discount on supplies and accessories.",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("50.00"),
                target_type=OfferTargetType.CATEGORY,
                category=AssetCategory.SUPPLY,
                minimum_order_price=Decimal("100.00"),
                priority=5,
            ),
        ]
        CASHBACKS = [
            dict(
                title="5% Cashback on Feeds",
                description="Earn 5% cashback on all feed purchases.",
                value_type=OfferValueType.PERCENTAGE,
                value=Decimal("5.00"),
                target_type=OfferTargetType.CATEGORY,
                category=AssetCategory.FEED,
                minimum_order_price=Decimal("0.00"),
                allowed_redemption_amount=Decimal("200.00"),
                priority=8,
            ),
            dict(
                title="20 EGP Cashback All Products",
                description="20 EGP flat cashback on any order above 200 EGP.",
                value_type=OfferValueType.FIXED_AMOUNT,
                value=Decimal("20.00"),
                target_type=OfferTargetType.ALL,
                minimum_order_price=Decimal("200.00"),
                allowed_redemption_amount=Decimal("100.00"),
                priority=3,
            ),
        ]

        for d in DISCOUNTS:
            if DiscountOffer.objects.filter(title=d["title"]).exists():
                self.stdout.write(f"  DiscountOffer '{d['title']}': already exists")
                continue
            d["created_by"] = manager_profile
            DiscountOffer.objects.create(**d)
            self.stdout.write(f"  DiscountOffer '{d['title']}': created")

        for c in CASHBACKS:
            if CashbackOffer.objects.filter(title=c["title"]).exists():
                self.stdout.write(f"  CashbackOffer '{c['title']}': already exists")
                continue
            c["created_by"] = manager_profile
            CashbackOffer.objects.create(**c)
            self.stdout.write(f"  CashbackOffer '{c['title']}': created")

    # ── Notifications ─────────────────────────────────────────────────────────

    def _seed_notifications(self, seller, customer):
        now = timezone.now()

        NOTIFS = [
            # Customer notifications
            dict(
                profile=customer,
                kind=NotificationKind.NEW_BID,
                title="Your bid on Blaze was placed",
                body="You placed a bid of 1,300 EGP on Blaze. You are currently the highest bidder.",
                is_read=True,
                read_at=now - timezone.timedelta(hours=2),
            ),
            dict(
                profile=customer,
                kind=NotificationKind.ORDER,
                title="Order completed",
                body="Your order has been completed successfully. Items are ready for handoff.",
                is_read=True,
                read_at=now - timezone.timedelta(days=1),
            ),
            dict(
                profile=customer,
                kind=NotificationKind.PAYMENT,
                title="Payment approved",
                body="Your market payment request has been approved by the seller.",
                is_read=False,
            ),
            dict(
                profile=customer,
                kind=NotificationKind.NEW_BADGE,
                title="Badge earned: First Order",
                body="Congratulations! You earned the First Order badge for completing your first purchase.",
                is_read=False,
            ),
            # Seller notifications
            dict(
                profile=seller,
                kind=NotificationKind.NEW_BID,
                title="New bid on Blaze",
                body="A customer placed a bid of 1,300 EGP on your auction item Blaze.",
                is_read=True,
                read_at=now - timezone.timedelta(hours=3),
            ),
            dict(
                profile=seller,
                kind=NotificationKind.ORDER,
                title="New order received",
                body="You have a new order pending approval. Please review and approve the items.",
                is_read=False,
            ),
            dict(
                profile=seller,
                kind=NotificationKind.PAYMENT,
                title="Payment request received",
                body="A buyer has submitted a payment request for an auction item.",
                is_read=False,
            ),
        ]

        for n in NOTIFS:
            # Use title+profile as uniqueness check
            if Notification.objects.filter(profile=n["profile"], title=n["title"]).exists():
                self.stdout.write(f"  Notification '{n['title']}': already exists")
                continue
            Notification.objects.create(**n)
            self.stdout.write(f"  Notification '{n['title']}': created")
