"""
python manage.py seed_auctions_orders

Seeds demo auctions (using existing birds) and orders (using existing
market-listed assets). Depends on seed_all + seed_assets having run first.

Auctions created:
  - 1 active single-bird auction  (Blaze)
  - 1 active pair auction         (Storm + Luna)
  - 1 ended multi-bird auction    (Nova + Ash)

Orders created:
  - 1 completed order  (2 items from demo customer)
  - 1 pending order    (1 item from demo customer)

Safe to rerun — skips if auction/order title already exists for that seller/customer.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.assets.models import Asset, Bird
from apps.auctions.models import (
    Auction,
    AuctionEvent,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
    Bid,
)
from apps.orders.models import (
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)
from apps.users.models import CustomerProfile, SellerProfile

SELLER_PHONE = "+201000000007"
CUSTOMER_PHONE = "+201000000006"


class Command(BaseCommand):
    help = "Seed demo auctions and orders using existing assets."

    def handle(self, *args, **options):
        seller = SellerProfile.objects.filter(
            user__phone_number=SELLER_PHONE
        ).first()
        customer = CustomerProfile.objects.filter(
            user__phone_number=CUSTOMER_PHONE
        ).first()

        if not seller:
            self.stdout.write(self.style.ERROR(
                f"Seller not found ({SELLER_PHONE}). Run seed_all first."
            ))
            return
        if not customer:
            self.stdout.write(self.style.ERROR(
                f"Customer not found ({CUSTOMER_PHONE}). Run seed_all first."
            ))
            return

        self._seed_auctions(seller, customer)
        self._seed_orders(seller, customer)
        self.stdout.write(self.style.SUCCESS("Auction + order seeding complete."))

    # ── Auctions ──────────────────────────────────────────────────────────────

    def _seed_auctions(self, seller, customer):
        now = timezone.now()

        blaze = Bird.objects.filter(ring_number="EG-2024-003").first()
        storm = Bird.objects.filter(ring_number="EG-2024-001").first()
        luna = Bird.objects.filter(ring_number="EG-2024-002").first()
        nova = Bird.objects.filter(ring_number="EG-2025-001").first()
        ash = Bird.objects.filter(ring_number="EG-2025-002").first()

        missing = [n for n, b in [("Blaze", blaze), ("Storm", storm),
                                   ("Luna", luna), ("Nova", nova), ("Ash", ash)] if not b]
        if missing:
            self.stdout.write(self.style.ERROR(
                f"Missing birds: {missing}. Run seed_assets first."
            ))
            return

        # 1. Active single-bird auction — Blaze
        self._create_auction(
            title="Blaze — National-Ranked Racing Pigeon",
            description="Top racing pigeon, 2nd place national race 2023. "
                        "Proven performer with excellent speed and endurance.",
            auction_type=AuctionType.SINGLE,
            owner=seller,
            start_time=now - timezone.timedelta(hours=2),
            end_time=now + timezone.timedelta(days=3),
            status=AuctionStatus.ACTIVE,
            items=[{"bird": blaze, "starting_price": Decimal("1200.00")}],
            buy_now_enabled=True,
            buy_now_price=Decimal("2000.00"),
            min_bid_increment=Decimal("50.00"),
            tags="racing,champion",
            # Seed a bid from the customer
            bids=[{"bird": blaze, "bidder": customer, "amount": Decimal("1300.00")}],
        )

        # 2. Active pair auction — Storm + Luna
        self._create_auction(
            title="Breeding Pair — Storm & Luna",
            description="Proven breeding pair. Storm is a regional champion, "
                        "Luna has produced 3 prize-winning offspring together.",
            auction_type=AuctionType.PAIR,
            owner=seller,
            start_time=now - timezone.timedelta(hours=1),
            end_time=now + timezone.timedelta(days=5),
            status=AuctionStatus.ACTIVE,
            items=[{"bird": storm, "paired_bird": luna, "starting_price": Decimal("1800.00")}],
            buy_now_enabled=False,
            min_bid_increment=Decimal("100.00"),
            tags="breeding,pair",
            bids=[],
        )

        # 3. Ended multi-bird auction — Nova + Ash
        self._create_auction(
            title="Young Birds Lot — Nova & Ash",
            description="Two young birds from champion lineage. "
                        "Nova is offspring of Storm x Luna, Ash from Blaze.",
            auction_type=AuctionType.MULTI,
            owner=seller,
            start_time=now - timezone.timedelta(days=5),
            end_time=now - timezone.timedelta(hours=6),
            status=AuctionStatus.ENDED,
            items=[
                {"bird": nova, "starting_price": Decimal("300.00"), "status": AuctionItemStatus.UNSOLD},
                {"bird": ash, "starting_price": Decimal("280.00"), "status": AuctionItemStatus.UNSOLD},
            ],
            buy_now_enabled=False,
            min_bid_increment=Decimal("25.00"),
            tags="young,lot",
            bids=[],
        )

    def _create_auction(self, title, description, auction_type, owner,
                        start_time, end_time, status, items, bids,
                        buy_now_enabled=False, buy_now_price=None,
                        min_bid_increment=Decimal("1.00"), tags=""):
        if Auction.objects.filter(owner=owner, title=title).exists():
            self.stdout.write(f"  Auction '{title}': already exists")
            return

        with transaction.atomic():
            auction = Auction.objects.create(
                owner=owner,
                title=title,
                description=description,
                auction_type=auction_type,
                start_time=start_time,
                end_time=end_time,
                status=status,
                buy_now_enabled=buy_now_enabled,
                buy_now_price=buy_now_price,
                min_bid_increment=min_bid_increment,
                is_visible=True,
                tags=tags,
            )
            AuctionEvent.objects.create(
                auction=auction,
                event_type="created",
                performer=owner,
            )

            item_objs = []
            for item_data in items:
                item_status = item_data.pop("status", AuctionItemStatus.ACTIVE)
                paired = item_data.pop("paired_bird", None)
                bird = item_data["bird"]
                starting_price = item_data["starting_price"]

                item = AuctionItem.objects.create(
                    auction=auction,
                    bird=bird,
                    paired_bird=paired,
                    starting_price=starting_price,
                    current_price=starting_price,
                    status=item_status,
                )
                item_objs.append(item)

            # Seed bids
            for bid_data in bids:
                target_bird = bid_data["bird"]
                item = next((i for i in item_objs if i.bird == target_bird), None)
                if not item:
                    continue
                bid = Bid.objects.create(
                    item=item,
                    bidder=bid_data["bidder"],
                    amount=bid_data["amount"],
                )
                item.current_price = bid_data["amount"]
                item.save(update_fields=["current_price"])
                AuctionEvent.objects.create(
                    auction=auction,
                    event_type="bid_placed",
                    item=item,
                    bid=bid,
                    performer=bid_data["bidder"],
                    metadata={"amount": str(bid_data["amount"])},
                )

        self.stdout.write(f"  Auction '{title}': created")

    # ── Orders ────────────────────────────────────────────────────────────────

    def _seed_orders(self, seller, customer):
        # Pull market-listed non-bird assets
        listed = list(
            Asset.objects.filter(
                owner=seller,
                is_market_listed=True,
            ).exclude(category="bird").order_by("created")
        )

        if len(listed) < 2:
            self.stdout.write(self.style.WARNING(
                "Not enough market-listed non-bird assets for orders. "
                "Run seed_assets first."
            ))
            return

        # 1. Completed order — 2 items
        self._create_order(
            customer=customer,
            seller=seller,
            assets=[listed[0], listed[1]],
            quantities=[2, 1],
            status=OrderStatus.COMPLETED,
            item_status=OrderItemStatus.COMPLETED,
        )

        # 2. Pending order — 1 item
        self._create_order(
            customer=customer,
            seller=seller,
            assets=[listed[2]] if len(listed) > 2 else [listed[0]],
            quantities=[1],
            status=OrderStatus.PENDING,
            item_status=OrderItemStatus.PENDING_SELLER,
        )

    def _create_order(self, customer, seller, assets, quantities,
                      status, item_status):
        label = f"Order({status}) for {customer.user.phone_number}"

        if Order.objects.filter(owner=customer, status=status).exists():
            self.stdout.write(f"  {label}: already exists")
            return

        with transaction.atomic():
            order = Order.objects.create(
                owner=customer,
                status=status,
                total_price=Decimal("0.00"),
            )

            total = Decimal("0.00")
            for asset, qty in zip(assets, quantities):
                unit_price = asset.price
                item = OrderItem.objects.create(
                    order=order,
                    asset=asset,
                    seller=seller,
                    quantity=qty,
                    unit_price_snapshot=unit_price,
                    status=item_status,
                )
                total += item.total_price

            order.total_price = total
            order.save(update_fields=["total_price"])

            OrderEvent.objects.create(
                order=order,
                recipient=customer,
                event_type=OrderEventType.ORDER_CREATED,
                message="Order created via seed.",
            )

        self.stdout.write(f"  {label}: created (total={total})")
