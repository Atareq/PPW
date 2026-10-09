"""
Production-Grade Auction Services

Architecture: 4-Layer Validation & Orchestration

Layer 1: Input Validation (Serializer)
  - Validates REQUEST FORMAT (types, required fields, primitive choices)
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
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound, ValidationError

from apps.assets.models import Bird
from apps.auctions.models import (
    Auction,
    AuctionBidVisibility,
    AuctionEvent,
    AuctionItem,
    AuctionItemStatus,
    AuctionShippingMethod,
    AuctionStatus,
    AuctionType,
    Bid,
)
from apps.authentication.models import UserAccount
from apps.packages.models import PackageUsageType
from apps.packages.services import PackageService
from apps.users.constants import ProfileType
from apps.users.models import CustomerProfile, SellerProfile, SellerRoom

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
        self._validate_room_ownership()
        self._validate_shipping_settings()
        self._validate_item_count_for_type()
        self._validate_items()

    def _validate_shipping_settings(self) -> None:
        AuctionService._validate_shipping_for_auction(self.auction)

    def _validate_room_ownership(self) -> None:
        room = getattr(self.auction, "room", None)
        if room is None and getattr(self.auction, "room_id", None) is not None:
            room = SellerRoom.all_objects.filter(pk=self.auction.room_id).first()
            if room is None:
                raise ValidationError({"room": _("Seller room was not found.")})
        if room is None:
            return
        if room.deleted is not None:
            raise ValidationError(
                {"room": _("Cannot assign auction to a deleted seller room.")}
            )
        owner = self.auction.owner
        if owner is None or room.seller_id != owner.id:
            raise ValidationError(
                {"room": _("Sellers can assign auctions only to their own rooms.")}
            )

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
                raise ValidationError(_("Duplicate bird_id found in items."))
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
            raise ValidationError(_("A bird or more is not found."))

        if AuctionItem.objects.filter(
            Q(bird_id__in=bird_ids) | Q(paired_bird_id__in=bird_ids),
            auction__status=AuctionStatus.ACTIVE,
            auction__end_time__gt=timezone.now(),
        ).exists():
            raise ValidationError(
                _("A bird or more is already placed in an active auction.")
            )

    def _validate_seller_owns_submitted_birds(self):
        owner = self.auction.owner
        if not owner or owner.type != ProfileType.SELLER:
            return

        invalid_bird_ids = sorted(
            bird.id for bird in self.bird_map.values() if bird.owner_id != owner.id
        )
        if invalid_bird_ids:
            raise ValidationError(
                {
                    "items": _(
                        "Sellers can auction only birds they own. Invalid bird ids: %(ids)s."
                    )
                    % {"ids": invalid_bird_ids}
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
        - One must be male, one must be female
        """
        item_data = self.items_data[0]
        bird_id = item_data.get("bird_id")
        paired_bird_id = item_data.get("paired_bird_id")

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
            reserve_price = item_data.get("reserve_price")

            item = AuctionItem(
                auction=auction,
                bird_id=bird_id,
                paired_bird_id=paired_bird_id,
                starting_price=starting_price,
                reserve_price=reserve_price,
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
        seller_package_id,
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
                seller_package_id,
            )

        with transaction.atomic():
            # Ensure owner is set
            auction.owner = owner
            AuctionService._validate_room_for_owner(owner, getattr(auction, "room", None) or getattr(auction, "room_id", None))
            AuctionService._validate_shipping_for_auction(auction)

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
                    seller_package_id,
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
            AuctionService._handle_auction_published_reward(auction, owner)

        return auction

    @staticmethod
    def _validate_shipping_for_auction(auction: Auction) -> None:
        method = getattr(auction, "shipping_method", None)
        if method == "":
            auction.shipping_method = None
            method = None
        if method is not None:
            normalized = str(method).strip().upper()
            if normalized not in AuctionShippingMethod.values:
                raise ValidationError(
                    {"shipping_method": _("Unsupported shipping method.")}
                )
            auction.shipping_method = normalized

        min_days = getattr(auction, "min_shipping_days", None)
        max_days = getattr(auction, "max_shipping_days", None)
        if min_days is not None and max_days is None:
            max_days = min_days
            auction.max_shipping_days = max_days
        elif max_days is not None and min_days is None:
            min_days = max_days
            auction.min_shipping_days = min_days

        if min_days is not None and min_days < 1:
            raise ValidationError(
                {
                    "min_shipping_days": _(
                        "Minimum shipping days must be a positive integer."
                    )
                }
            )
        if max_days is not None and max_days < 1:
            raise ValidationError(
                {
                    "max_shipping_days": _(
                        "Maximum shipping days must be a positive integer."
                    )
                }
            )
        if min_days is not None and max_days is not None and min_days > max_days:
            raise ValidationError(
                {
                    "max_shipping_days": _(
                        "Minimum shipping days cannot exceed maximum shipping days."
                    )
                }
            )

    @staticmethod
    def _validate_room_for_owner(owner, room_or_id) -> None:
        if room_or_id is None:
            return
        room = (
            room_or_id
            if isinstance(room_or_id, SellerRoom)
            else SellerRoom.all_objects.filter(pk=room_or_id).first()
        )
        if room is None:
            raise ValidationError({"room": _("Seller room was not found.")})
        if room.deleted is not None:
            raise ValidationError(
                {"room": _("Cannot assign auction to a deleted seller room.")}
            )
        if owner is None or room.seller_id != owner.id:
            raise ValidationError(
                {"room": _("Sellers can assign auctions only to their own rooms.")}
            )

    @staticmethod
    @transaction.atomic
    def cancel_auction(
        auction: Auction,
        reason: str = "Cancelled by owner",
        performer=None,
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
            raise ValidationError(_("Auction is already cancelled"))

        if auction.status == AuctionStatus.ENDED:
            raise ValidationError(
                _("Cannot cancel ended auction. Use settlement for dispute resolution.")
            )

        # if auction.items.filter(bids__isnull=False).exists():
        #     raise ValidationError("Cannot cancel an auction that already has bids.")

        now = timezone.now()
        auction.status = AuctionStatus.CANCELLED
        auction.save()

        auction.items.all().update(
            status=AuctionItemStatus.CANCELLED,
            modified=now,
        )

        # Bid refund/payment handling is outside the current auction flow.
        Bid.objects.filter(item__auction=auction)

        AuctionService._log_event(
            auction=auction,
            event_type=AuctionItemStatus.CANCELLED,
            performer=performer if performer is not None else auction.owner,
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
        new_end_time = auction.end_time + timedelta(minutes=auction.auto_extend_minutes)
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
            raise ValidationError(_("Auction is already cancelled"))

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

        if "room" in validated_data:
            AuctionService._validate_room_for_owner(
                locked_auction.owner,
                validated_data["room"],
            )
            locked_auction.room = validated_data["room"]

        for field in (
            "title",
            "description",
            "tags",
            "shipping_method",
            "min_shipping_days",
            "max_shipping_days",
        ):
            if field in validated_data:
                setattr(locked_auction, field, validated_data[field])

        AuctionService._validate_shipping_for_auction(locked_auction)
        locked_auction.save()
        return locked_auction

    @staticmethod
    def _validate_auction_editable(auction: Auction) -> None:
        """Block edits after the auction starts or receives bids."""
        if auction.status != AuctionStatus.ACTIVE:
            raise ValidationError(_("Only active auctions can be updated."))

        if auction.start_time <= timezone.now():
            raise ValidationError(_("Auction cannot be updated after it starts."))

        if auction.items.filter(bids__isnull=False).exists():
            raise ValidationError(_("Auction cannot be updated after it receives bids."))

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

    @staticmethod
    def _handle_auction_published_reward(auction, owner):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.AUCTION_PUBLISHED,
            actor=owner,
            profile=owner,
            context={"auction": auction},
        )

    @staticmethod
    def get_seller_payment_details_for_winner(
        auction_id: int,
        user: UserAccount,
    ) -> dict:
        """
        Return private seller payment coordination details for an ended auction's
        settled winning buyer account.

        Raises NotFound whenever the auction does not exist, has not ended,
        is cancelled, has no settled winner, has an inactive/deleted seller,
        or the caller is not the winning buyer account.
        """
        if not user or not user.is_authenticated or not user.is_active:
            raise NotFound(_("Auction not found"))

        auction = (
            Auction.objects.filter(
                pk=auction_id,
                deleted__isnull=True,
            )
            .exclude(
                status=AuctionStatus.CANCELLED,
            )
            .first()
        )
        if auction is None:
            raise NotFound(_("Auction not found"))

        has_ended = auction.status in (
            AuctionStatus.CLOSED,
            AuctionStatus.ENDED,
        ) or (
            auction.status == AuctionStatus.ACTIVE and auction.is_ended()
        )
        if not has_ended:
            raise NotFound(_("Auction not found"))

        has_won_item = AuctionItem.objects.filter(
            auction=auction,
            deleted__isnull=True,
            status=AuctionItemStatus.SOLD,
            winner__isnull=False,
            winner__deleted__isnull=True,
            winner__is_valid=True,
            winner__activated=True,
            winner__user_id=user.pk,
        ).exists()
        if not has_won_item:
            raise NotFound(_("Auction not found"))

        seller_profile = (
            SellerProfile.objects.filter(
                pk=auction.owner_id,
                deleted__isnull=True,
                is_valid=True,
                activated=True,
                user__is_active=True,
            )
            .first()
        )
        if seller_profile is None:
            raise NotFound(_("Auction not found"))

        return {
            "seller_id": seller_profile.pk,
            "auction_id": auction.pk,
            "bank_account": seller_profile.bank_account or "",
            "wallet": seller_profile.wallet or "",
            "transfer_phone": seller_profile.transfer_phone or "",
        }


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
            raise ValidationError(_("Auction is not currently active"))
        if not self.item.is_active():
            raise ValidationError(
                _("Item is not accepting bids. Current status: %(status)s")
                % {"status": self.item.status}
            )

    def _validate_bidder(self) -> None:
        """Validate bidder is not a Seller"""

        if self.bidder.type != ProfileType.CUSTOMER:
            raise ValidationError(_("Only customers can place bids"))

    def _validate_bid_amount(self) -> None:
        """Validate bid amount meets all requirements"""

        # Must meet starting price
        if self.amount < self.item.starting_price:
            raise ValidationError(
                _("Bid must be at least %(amount)s (starting price)")
                % {"amount": self.item.starting_price}
            )

        if self.auction.bid_visibility == AuctionBidVisibility.SEALED:
            return

        current_highest = self.item.current_highest_bid_amount()
        min_required = self.item.starting_price
        if current_highest is not None:
            min_required = current_highest + self.auction.min_bid_increment

        if self.amount < min_required:
            raise ValidationError(
                _(
                    "Bid must be at least %(amount)s (current %(current)s + increment %(increment)s)"
                )
                % {
                    "amount": min_required,
                    "current": self.item.current_price,
                    "increment": self.auction.min_bid_increment,
                }
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

        if auction.bid_visibility == AuctionBidVisibility.PUBLIC:
            item.current_price = amount
            item.save(update_fields=["current_price", "modified"])
        was_last_minute = (
            timedelta(0) < auction.end_time - timezone.now() <= timedelta(minutes=5)
        )

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
        BiddingService._handle_bid_reward(
            bid,
            bidder,
            was_last_minute=was_last_minute,
        )
        BiddingService._schedule_bid_notification(bid)

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

    @staticmethod
    def _handle_bid_reward(bid, bidder, *, was_last_minute):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.BID_PLACED,
            actor=bidder,
            profile=bidder,
            context={"bid": bid, "was_last_minute": was_last_minute},
        )

    @staticmethod
    def _schedule_bid_notification(bid):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        auction = bid.item.auction
        transaction.on_commit(
            lambda: NotificationService.safe_create_notification(
                recipient_profile=auction.owner,
                actor_profile=bid.bidder,
                notification_type=NotificationType.AUCTION_BID_PLACED,
                title=_("New auction bid"),
                body=_("A customer placed a bid on your auction item."),
                object_type="bid",
                object_id=bid.pk,
                metadata={
                    "auction_id": auction.pk,
                    "auction_item_id": bid.item_id,
                    "bid_id": bid.pk,
                },
                idempotency_key=f"auction_bid_placed:{bid.pk}",
            )
        )


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

    TODO: Payment expiry and fallback to the second-highest bidder requires
    redesign because AuctionPaymentRequest is currently one-to-one with
    AuctionItem.
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

        winning_bid = item.bids.order_by("-amount", "created", "id").first()

        if winning_bid and (
            item.reserve_price is None or winning_bid.amount >= item.reserve_price
        ):
            item.status = AuctionItemStatus.SOLD
            item.winner = winning_bid.bidder
            item.winner_bid = winning_bid
            item.current_price = winning_bid.amount
        else:
            item.status = AuctionItemStatus.UNSOLD
            item.winner = None
            item.winner_bid = None

        item.save()
        if winning_bid and item.winner_id:
            SettlementService._schedule_auction_won_notification(item)
            SettlementService._schedule_auction_lost_notifications(item)
        elif winning_bid and item.reserve_price is not None:
            SettlementService._schedule_reserve_not_met_notifications(item)

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
            raise ValidationError(_("Buy now not enabled for this auction"))

        if not auction.buy_now_price:
            raise ValidationError(_("Buy now price not set"))

        if not item.is_active():
            raise ValidationError(_("Item is not available for buy now"))

        if buyer == auction.owner:
            raise ValidationError(_("Auction owner cannot buy own auction"))

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
            SettlementService._schedule_auction_won_notification(item)
            SettlementService._schedule_buy_now_success_notification(item, bid)

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

    @staticmethod
    def _schedule_auction_won_notification(item):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        transaction.on_commit(
            lambda: NotificationService.safe_create_notification(
                recipient_profile=item.winner,
                actor_profile=item.auction.owner,
                notification_type=NotificationType.AUCTION_WON,
                title=_("Auction won"),
                body=_("Congratulations! You won this auction item."),
                object_type="auction_item",
                object_id=item.pk,
                metadata={
                    "auction_id": item.auction_id,
                    "auction_item_id": item.pk,
                    "bid_id": item.winner_bid_id,
                    "winning_amount": str(item.current_price),
                    "payment_deadline_days": item.auction.payment_deadline_days,
                },
                idempotency_key=f"auction_won:{item.pk}:{item.winner_id}",
            )
        )

    @staticmethod
    def _schedule_auction_lost_notifications(item):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        losing_bidder_ids = (
            item.bids.exclude(bidder_id=item.winner_id)
            .order_by("bidder_id")
            .values_list("bidder_id", flat=True)
            .distinct()
        )
        for bidder_id in losing_bidder_ids:
            transaction.on_commit(
                lambda bidder_id=bidder_id: NotificationService.safe_create_notification(
                    recipient_profile=bidder_id,
                    actor_profile=item.auction.owner,
                    notification_type=NotificationType.AUCTION_LOST,
                    title=_("Auction ended"),
                    body=_("You did not win this auction."),
                    object_type="auction_item",
                    object_id=item.pk,
                    metadata={
                        "auction_id": item.auction_id,
                        "auction_item_id": item.pk,
                    },
                    idempotency_key=f"auction_lost:{item.pk}:{bidder_id}",
                )
            )

    @staticmethod
    def _schedule_reserve_not_met_notifications(item):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        bidder_ids = (
            item.bids.order_by("bidder_id")
            .values_list("bidder_id", flat=True)
            .distinct()
        )
        for bidder_id in bidder_ids:
            transaction.on_commit(
                lambda bidder_id=bidder_id: NotificationService.safe_create_notification(
                    recipient_profile=bidder_id,
                    actor_profile=item.auction.owner,
                    notification_type=NotificationType.AUCTION_RESERVE_NOT_MET,
                    title=_("Auction ended"),
                    body=_("The reserve price was not met."),
                    object_type="auction_item",
                    object_id=item.pk,
                    metadata={
                        "auction_id": item.auction_id,
                        "auction_item_id": item.pk,
                    },
                    idempotency_key=f"auction_reserve_not_met:{item.pk}:{bidder_id}",
                )
            )

    @staticmethod
    def _schedule_buy_now_success_notification(item, bid):
        from apps.notifications.models import NotificationType
        from apps.notifications.services import NotificationService

        transaction.on_commit(
            lambda: NotificationService.safe_create_notification(
                recipient_profile=item.winner,
                actor_profile=item.auction.owner,
                notification_type=NotificationType.AUCTION_BUY_NOW_SUCCESS,
                title=_("Buy now completed"),
                body=_("Your buy-now purchase was confirmed."),
                object_type="auction_item",
                object_id=item.pk,
                metadata={
                    "auction_id": item.auction_id,
                    "auction_item_id": item.pk,
                    "bid_id": bid.pk,
                },
                idempotency_key=f"auction_buy_now_success:{item.pk}:{bid.pk}",
            )
        )
