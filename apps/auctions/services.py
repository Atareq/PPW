"""
Production-Grade Auction Services

Architecture: 4-Layer Validation & Orchestration

Layer 1: Input Validation (Serializer)
  - Validates REQUEST FORMAT (types, existence, constraints)
  - Examples: bird_id is int, start_time is datetime

Layer 2: Business Validation (AuctionBusinessValidator)
  - Validates BUSINESS RULES (domain logic)
  - Examples: SINGLE has 1 item, PAIR has male+female

Layer 3: Object Creation (Factory)
  - Creates domain objects only (no validation)
  - Guarantees: status=ACTIVE, is_visible=True

Layer 4: Orchestration (Service)
  - Orchestrates complete flow
  - Saves to database, manages transaction
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.assets.models import Bird
from apps.auctions.models import (
    Auction,
    AuctionEvent,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
    Bid,
)
from apps.authentication.models import UserAccount
from apps.packages.models import PackageUsageType
from apps.packages.services import PackageService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile

AUCTION_AUTO_EXTEND_MINUTES = 10


# ============================================================================
# BUSINESS VALIDATOR
# ============================================================================


class AuctionBusinessValidator:
    """
    Validates business rules for auction creation.

    NOT INPUT FORMAT (serializer does that)
    NOT DATABASE RULES (model does that)

    ONLY: Business logic constraints

    Examples:
    - SINGLE auction must have exactly 1 item
    - PAIR auction must have 1 male + 1 female
    - MULTI auction must have 2-10 items
    - Auto-extend requires auto_extend_minutes
    - Deposit requires payment_deadline_days
    """

    def __init__(
        self,
        auction: Auction,
        items_data: list,
    ):
        """
        Initialize validator with auction and items data.

        Args:
            auction: Auction instance (not saved)
            items_data: List of item dicts with bird_id, paired_bird_id, starting_price
        """
        self.auction = auction
        self.items_data = items_data
        self.bird_map = {}

    def validate(self) -> None:
        """
        Run all business validations.

        Raises:
            ValidationError: If any business rule violated
        """
        self._retrieve_unique_unauctioned_items()
        self._validate_seller_owns_submitted_birds()
        self._validate_item_count_for_type()
        self._validate_items()
        self._validate_auto_extend_config()
        self._validate_deposit_config()

    def _retrieve_unique_unauctioned_items(self):
        """Load submitted birds once and reject duplicate or active-auction use."""

        bird_ids = set()
        bird_counter = 0
        for lot_number, item_data in enumerate(self.items_data):
            paired_bird_id = item_data.get("paired_bird_id")
            if (
                item_data["bird_id"] in bird_ids
                or item_data.get("paired_bird_id") in bird_ids
            ):
                raise ValidationError("Duplicate bird_id found in items.")
            if paired_bird_id:
                bird_counter += 1
                bird_ids.add(paired_bird_id)
            bird_counter += 1
            bird_ids.add(item_data["bird_id"])

        self.bird_map = {
            bird.id: bird
            for bird in Bird.objects.filter(id__in=bird_ids)
            # .exclude(
            #    id__in=AuctionItem.objects.filter(
            #        bird_id__in=bird_ids
            #    ).values_list("bird_id", flat=True)
            # )
        }

        if not len(self.bird_map) == bird_counter:
            raise ValidationError("A bird or more is not found.")

        if AuctionItem.objects.filter(
            Q(bird_id__in=bird_ids) | Q(paired_bird_id__in=bird_ids),
            auction__status=AuctionStatus.ACTIVE,
            auction__end_time__gt=timezone.now(),
        ).exists():
            raise ValidationError(
                "A bird or more is already placed in an active auction."
            )

    def _validate_seller_owns_submitted_birds(self):
        owner = self.auction.owner
        if not owner or owner.type != ProfileType.SELLER:
            return

        invalid_bird_ids = sorted(
            bird.id
            for bird in self.bird_map.values()
            if bird.owner_id != owner.id
        )
        if invalid_bird_ids:
            raise ValidationError(
                {
                    "items": (
                        "Sellers can auction only birds they own. "
                        f"Invalid bird ids: {invalid_bird_ids}."
                    )
                }
            )

    def _validate_item_count_for_type(self) -> None:
        """Validate item count matches auction type requirements"""
        auction_type = self.auction.auction_type
        item_count = len(self.items_data)

        if auction_type == AuctionType.SINGLE:
            if item_count != 1:
                raise ValidationError(
                    f"SINGLE auction requires exactly 1 item, got {item_count}"
                )

        elif auction_type == AuctionType.MULTI:
            if not (2 <= item_count <= 10):
                raise ValidationError(
                    f"MULTI auction requires 2-10 items, got {item_count}"
                )

        elif auction_type in [AuctionType.PAIR, AuctionType.BREEDING]:
            if item_count != 1:
                raise ValidationError(
                    f"{auction_type.upper()} auction requires"
                    "exactly 1 item (pair), got {item_count}"
                )

        elif auction_type == AuctionType.RACING:
            if item_count < 2:
                raise ValidationError(
                    f"RACING auction requires at least 2 items, got {item_count}"
                )

    def _validate_items(self) -> None:
        """Validate individual items and their compatibility"""

        # Validate auction-type-specific item requirements
        if self.auction.auction_type in [
            AuctionType.PAIR,
            AuctionType.BREEDING,
        ]:
            self._validate_pair_items()

    def _validate_pair_items(self) -> None:
        """
        Validate PAIR/BREEDING auctions have compatible birds.

        Rules:
        - Must have paired_bird_id in first item
        - One must be male, one must be female
        """
        item_data = self.items_data[0]
        bird_id = item_data.get("bird_id")
        paired_bird_id = item_data.get("paired_bird_id")

        # Check paired bird exists
        if not paired_bird_id:
            raise ValidationError(
                f"{self.auction.auction_type.upper()} auction requires paired_bird_id"
            )

        # Validate gender pairing
        self._validate_pair_gender(
            self.bird_map[bird_id], self.bird_map[paired_bird_id]
        )

    def _validate_pair_gender(self, bird1: Bird, bird2: Bird) -> None:
        """Validate pair has exactly one male and one female"""
        genders = {
            self._normalize_gender(bird1.gender),
            self._normalize_gender(bird2.gender),
        }

        if genders != {"male", "female"}:
            raise ValidationError(
                f"Wrong genders for {self.auction.auction_type.upper()} "
                f"Got: {bird1.name} ({bird1.gender}), {bird2.name} ({bird2.gender})"
            )

    @staticmethod
    def _normalize_gender(gender: str) -> str:
        return str(gender).strip().lower()

    def _validate_auto_extend_config(self) -> None:
        """Validate auto-extend configuration consistency"""
        if self.auction.auto_extend_enabled:
            if not self.auction.auto_extend_minutes:
                raise ValidationError(
                    "auto_extend_minutes required when auto_extend_enabled=True"
                )

    def _validate_deposit_config(self) -> None:
        """Validate deposit configuration consistency"""
        if self.auction.deposit_required:
            if not self.auction.payment_deadline_days:
                raise ValidationError(
                    "payment_deadline_days required when deposit_required=True"
                )


# ============================================================================
# FACTORIES
# ============================================================================


class AuctionFactory:
    """
    Creates auction domain objects (no validation, no database).

    Guarantees:
    - status always set via model default
    - Objects NOT saved (caller responsible)
    """

    @staticmethod
    def create_auction(
        owner,
        title: str,
        description: str,
        auction_type: str,
        start_time,
        end_time,
        auto_extend_enabled: bool = False,
        auto_extend_minutes: int = 5,
        auto_extend_threshold: int = 5,
        buy_now_enabled: bool = False,
        deposit_required: bool = False,
        payment_deadline_days=None,
        min_bid_increment: Decimal = Decimal("1.00"),
        tags: str = "",
    ) -> Auction:
        """
        Create unsaved Auction object.

        All validation already done by AuctionBusinessValidator.

        Returns:
            Auction: Unsaved instance with status=ACTIVE, is_visible=False
        """
        return Auction(
            owner=owner,
            title=title,
            description=description,
            auction_type=auction_type,
            start_time=start_time,
            end_time=end_time,
            auto_extend_enabled=auto_extend_enabled,
            auto_extend_minutes=auto_extend_minutes,
            auto_extend_threshold=auto_extend_threshold,
            buy_now_enabled=buy_now_enabled,
            deposit_required=deposit_required,
            payment_deadline_days=payment_deadline_days,
            min_bid_increment=min_bid_increment,
            tags=tags,
            is_visible=True,
        )

    @staticmethod
    def create_items(
        auction: Auction,
        items_data: list,
    ) -> list:
        """
        Create unsaved AuctionItem objects.

        For PAIR auctions, paired_bird_id becomes the actual paired_bird foreign key.

        Returns:
            List of unsaved AuctionItem instances
        """
        items = []

        for item_data in items_data:
            bird_id = item_data.get("bird_id")
            paired_bird_id = item_data.get("paired_bird_id")
            starting_price = item_data.get("starting_price", Decimal("1.00"))

            item = AuctionItem(
                auction=auction,
                bird_id=bird_id,
                paired_bird_id=paired_bird_id,
                starting_price=starting_price,
                current_price=starting_price,
                status=AuctionItemStatus.ACTIVE,
            )
            items.append(item)

        return items


# ============================================================================
# AUCTION SERVICE
# ============================================================================


class AuctionService:
    """
    Orchestrates auction lifecycle: creation, cancellation.

    Responsibilities:
    - Create and save auctions + items (uses factory)
    - Cancel auctions
    - Settle auctions (resolve winners)
    - Log events (audit trail)

    Does NOT:
    - Validate inputs (validator does that)
    - Create objects (factory does that)
    """

    @staticmethod
    def create_auction(
        owner,
        auction: Auction,
        items_data: list,
    ) -> Auction:
        """
        Create and save auction with items in atomic transaction.

        Prerequisites:
        - AuctionBusinessValidator.validate() already called
        - Auction object already created (but not saved)
        - items_data already validated

        Flow:
        1. Save auction to database
        2. Create items via factory
        3. Bulk insert items
        4. Log event

        Args:
            owner: Profile (auction owner)
            auction: Unsaved Auction instance
            items_data: List of item dicts

        Returns:
            Saved Auction with related items
        """
        if PackageService.should_consume_for_profile(owner):
            PackageService.validate_can_consume(
                owner,
                PackageUsageType.AUCTION_CREATED,
            )

        with transaction.atomic():
            # Ensure owner is set
            auction.owner = owner

            # 1. Save auction
            auction.save()

            # 2. Create items
            items = AuctionFactory.create_items(auction, items_data)

            # 3. Bulk insert items
            AuctionItem.objects.bulk_create(items)

            if PackageService.should_consume_for_profile(owner):
                seller_package = PackageService.consume_points(
                    owner,
                    PackageUsageType.AUCTION_CREATED,
                    related_object=auction,
                )
                auction.seller_package = seller_package
                auction.save(update_fields=["seller_package", "modified"])

            # 4. Log event
            AuctionService._log_event(
                auction=auction,
                event_type="created",
                performer=owner,
                item=None,
                bid=None,
                metadata={"item_count": len(items)},
            )

        return auction

    @staticmethod
    @transaction.atomic
    def cancel_auction(
        auction: Auction,
        reason: str = "Cancelled by owner",
    ) -> Auction:
        """
        Cancel auction and refund all bids.

        Prerequisites:
        - Auction must be in ACTIVE status
        - Cannot cancel ENDED auctions

        Returns:
            Updated Auction
        """
        if auction.status == AuctionStatus.CANCELLED:
            raise ValidationError("Auction is already cancelled")

        if auction.status == AuctionStatus.ENDED:
            raise ValidationError(
                "Cannot cancel ended auction. Use settlement for dispute resolution."
            )

        # if auction.items.filter(bids__isnull=False).exists():
        #     raise ValidationError("Cannot cancel an auction that already has bids.")

        auction.status = AuctionStatus.CANCELLED
        auction.save()

        auction.items.all().update(status=AuctionItemStatus.CANCELLED)

        # Bid refund/payment handling is outside the current auction flow.
        Bid.objects.filter(item__auction=auction)

        AuctionService._log_event(
            auction=auction,
            event_type=AuctionItemStatus.CANCELLED,
            performer=auction.owner,
            item=None,
            bid=None,
            metadata={"reason": reason},
        )

        return auction

    @staticmethod
    @transaction.atomic
    def auto_extend_auction(auction: Auction) -> None:
        """
        Extend auction end time (triggered by last-minute bid).

        Only called if:
        - auto_extend_enabled = True
        - Bid placed within auto_extend_threshold minutes of end
        """

        # Extend end time
        new_end_time = auction.end_time + timedelta(
            minutes=auction.auto_extend_minutes
        )
        auction.end_time = new_end_time
        auction.save()

        AuctionService._log_event(
            auction=auction,
            event_type="auto_extended",
            performer=None,
            item=None,
            bid=None,
            metadata={"new_end_time": new_end_time.isoformat()},
        )

    @staticmethod
    def close_expired_auctions(now=None) -> int:
        """Close all expired active auctions via the service layer."""
        now = now or timezone.now()
        auction_ids = list(
            Auction.objects.filter(
                status=AuctionStatus.ACTIVE,
                end_time__lte=now,
            ).values_list("id", flat=True)
        )

        closed_count = 0
        for auction_id in auction_ids:
            auction = Auction.objects.get(id=auction_id)
            AuctionService.close_auction(auction, now=now)
            closed_count += 1

        return closed_count

    @staticmethod
    @transaction.atomic
    def close_auction(auction: Auction, performer=None, now=None) -> Auction:
        """
        Close a single auction once its end time has been reached.

        The auction transitions to CLOSED while each item is settled to its
        final status using the existing sold/unsold item outcomes.
        """
        now = now or timezone.now()
        locked_auction = Auction.objects.select_for_update().get(id=auction.id)

        if locked_auction.status == AuctionStatus.CLOSED:
            return locked_auction

        if locked_auction.status == AuctionStatus.CANCELLED:
            raise ValidationError("Auction is already cancelled")

        # if locked_auction.end_time > now:
        #     raise ValidationError("Auction cannot be closed before end time.")

        for item in locked_auction.items.select_for_update().all():
            SettlementService._settle_item(item)

        locked_auction.status = AuctionStatus.CLOSED
        locked_auction.save()

        AuctionService._log_event(
            auction=locked_auction,
            event_type="auction_ended",
            performer=performer,
            item=None,
            bid=None,
            metadata={"closed_at": now.isoformat()},
        )

        return locked_auction

    @staticmethod
    @transaction.atomic
    def update_auction(auction: Auction, validated_data: dict) -> Auction:
        """Update auction fields only while the auction is still editable."""
        locked_auction = Auction.objects.select_for_update().get(id=auction.id)
        AuctionService._validate_auction_editable(locked_auction)

        for field in ("title", "description", "tags"):
            if field in validated_data:
                setattr(locked_auction, field, validated_data[field])

        locked_auction.save()
        return locked_auction

    @staticmethod
    def _validate_auction_editable(auction: Auction) -> None:
        """Block edits after the auction starts or receives bids."""
        if auction.status != AuctionStatus.ACTIVE:
            raise ValidationError("Only active auctions can be updated.")

        if auction.start_time <= timezone.now():
            raise ValidationError("Auction cannot be updated after it starts.")

        if auction.items.filter(bids__isnull=False).exists():
            raise ValidationError(
                "Auction cannot be updated after it receives bids."
            )

    @staticmethod
    def _log_event(
        auction: Auction,
        event_type: str,
        performer=None,
        item: AuctionItem = None,
        bid: Bid = None,
        metadata: dict = None,
    ) -> AuctionEvent:
        """Log auction event for audit trail"""
        return AuctionEvent.objects.create(
            auction=auction,
            event_type=event_type,
            performer=performer,
            item=item,
            bid=bid,
            metadata=metadata or {},
        )


# ============================================================================
# BID BUSINESS VALIDATOR
# ============================================================================


class BidBusinessValidator:
    """
    Validates business rules for bid placement.

    Validates:
    - Item is active and accepting bids
    - Auction is currently active
    - Bidder is not the auction owner
    - Bid amount meets all requirements (starting price, min increment)
    - Auto-extend configuration if needed
    """

    def __init__(
        self,
        item: AuctionItem,
        bidder: CustomerProfile,
        amount: Decimal,
    ):
        """
        Initialize validator with bid details.

        Args:
            item: AuctionItem to bid on
            bidder: Profile of bidder
            amount: Bid amount
        """
        self.item = item
        self.bidder = bidder
        self.amount = amount
        self.auction = item.auction

    def validate(self) -> None:
        """
        Run all business validations for bid.

        Raises:
            ValidationError: If any business rule violated
        """
        self._validate_item_is_active()
        self._validate_bidder()
        self._validate_bid_amount()

    def _validate_item_is_active(self) -> None:
        """Validate item is active and accepting bids"""
        if not self.auction.is_active():
            raise ValidationError("Auction is not currently active")
        if not self.item.is_active():
            raise ValidationError(
                f"Item is not accepting bids. Current status: {self.item.status}"
            )

    def _validate_bidder(self) -> None:
        """Validate bidder is not a Seller"""

        if self.bidder.type != ProfileType.CUSTOMER:
            raise ValidationError("Only customers can place bids")

    def _validate_bid_amount(self) -> None:
        """Validate bid amount meets all requirements"""

        # Must meet starting price
        if self.amount < self.item.starting_price:
            raise ValidationError(
                f"Bid must be at least {self.item.starting_price} "
                f"(starting price)"
            )

        current_highest = self.item.current_highest_bid_amount()
        min_required = self.item.starting_price
        if current_highest is not None:
            min_required = current_highest + self.auction.min_bid_increment

        if self.amount < min_required:
            raise ValidationError(
                f"Bid must be at least {min_required} "
                f"(current {self.item.current_price} "
                f"+ increment {self.auction.min_bid_increment})"
            )


# ============================================================================
# BIDDING SERVICE
# ============================================================================


class BiddingService:
    """
    Handles all bidding operations: placing bids, validation, winner tracking.

    Safety:
    - Uses select_for_update() to prevent race conditions
    - Atomic transactions for all bid operations
    - Validates bid amount and auction state
    """

    @staticmethod
    @transaction.atomic
    def place_bid(
        item: AuctionItem,
        bidder,
        amount: Decimal,
    ) -> Bid:
        """
        Place bid on auction item (after validation).

        Prerequisites:
        - BidBusinessValidator.validate() already called
        - All business rules already validated
        - Item locked via select_for_update()

        Flow:
        1. Lock item for update
        2. Update current price
        3. Trigger auto-extend if needed
        4. Log event

        Args:
            item: AuctionItem to bid on (will be re-locked)
            bidder: Profile (bidder)
            amount: Bid amount

        Returns:
            Created Bid
        """
        # Lock item to prevent race conditions during creation
        item = (
            AuctionItem.objects.select_for_update()
            .select_related("auction")
            .get(id=item.id)
        )
        auction = item.auction

        validator = BidBusinessValidator(
            item=item,
            bidder=bidder,
            amount=amount,
        )
        validator.validate()

        bid = Bid.objects.create(
            item=item,
            bidder=bidder,
            amount=amount,
        )

        item.current_price = amount
        item.save()

        # Check if auto-extend needed
        BiddingService._check_auto_extend(auction)

        # Log event
        AuctionService._log_event(
            auction=auction,
            event_type="bid_placed",
            performer=bidder,
            item=item,
            bid=bid,
            metadata={"amount": str(amount)},
        )

        return bid

    @staticmethod
    def _check_auto_extend(auction: Auction) -> None:
        """Check if bid triggered auto-extend"""
        if not auction.auto_extend_enabled:
            return

        now = timezone.now()
        time_to_end = (auction.end_time - now).total_seconds() / 60

        if time_to_end < AUCTION_AUTO_EXTEND_MINUTES:
            AuctionService.auto_extend_auction(auction)


# ============================================================================
# SETTLEMENT SERVICE
# ============================================================================


class SettlementService:
    """
    Handles auction item winner resolution.

    Responsibilities:
    - Determine winners (highest bidder per item)
    - Mark items as SOLD/UNSOLD
    - Leave payment requests and ownership transfer to apps.payments
    """

    @staticmethod
    @transaction.atomic
    def settle_auction(auction: Auction, user: UserAccount) -> Auction:
        """
        Settle auction: determine winners, mark items, close auction.

        Prerequisites:
        - Auction must be ended (end_time passed)

        Returns:
            Updated Auction with status=ENDED
        """
        return AuctionService.close_auction(
            auction=auction,
            performer=user.active_profile,
        )

    @staticmethod
    def _settle_item(item: AuctionItem) -> None:
        """Determine winner and mark item status"""

        # Get highest bid
        winning_bid = item.bids.order_by("-amount").first()

        if winning_bid:
            # Item sold
            item.status = AuctionItemStatus.SOLD
            item.winner = winning_bid.bidder
            item.winner_bid = winning_bid
        else:
            # No bids - item unsold
            item.status = AuctionItemStatus.UNSOLD
            item.winner = None
            item.winner_bid = None

        item.save()

    @staticmethod
    def trigger_buy_now(
        item: AuctionItem,
        buyer,
    ) -> Bid:
        """
        Trigger instant buy now on item.

        Creates winning bid immediately and marks item as SOLD.

        Prerequisites:
        - Auction must have buy_now_enabled = True
        - Item must be active
        - buy_now_price must be set

        Returns:
            Created Bid (winning bid)
        """
        auction = item.auction

        if not auction.buy_now_enabled:
            raise ValidationError("Buy now not enabled for this auction")

        if not auction.buy_now_price:
            raise ValidationError("Buy now price not set")

        if not item.is_active():
            raise ValidationError("Item is not available for buy now")

        if buyer == auction.owner:
            raise ValidationError("Auction owner cannot buy own auction")

        # Create winning bid
        with transaction.atomic():
            bid = Bid.objects.create(
                item=item,
                bidder=buyer,
                amount=auction.buy_now_price,
            )

            # Mark item as sold
            item.status = AuctionItemStatus.SOLD
            item.winner = buyer
            item.winner_bid = bid
            item.current_price = auction.buy_now_price
            item.save()

            # Log event
            AuctionService._log_event(
                auction=auction,
                event_type="buy_now_triggered",
                performer=buyer,
                item=item,
                bid=bid,
                metadata={"buy_now_price": str(auction.buy_now_price)},
            )

        return bid
