from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.general.models import BaseModel, OwnerBaseModel
from apps.users.models import Profile

# ============================================================================
# PHASE 1: CORE DOMAIN MODELS
# ============================================================================


class AuctionType(models.TextChoices):
    """Auction type classification - drives behavior and validation"""

    SINGLE = "single", _("Single Bird")
    MULTI = "multi", _("Multi-Bird Independent")
    PAIR = "pair", _("Pair (Male + Female)")
    BREEDING = "breeding", _("Breeding/Squab Pair")
    RACING = "racing", _("Racing/Loft Group")


class AuctionBidVisibility(models.TextChoices):
    """Bid visibility mode for an auction."""

    PUBLIC = "public", _("Public Auction")
    SEALED = "sealed", _("Silent Auction")


class AuctionShippingMethod(models.TextChoices):
    """Seller-defined shipping/handoff option for an auction (BE-SHIP-01)."""

    PERSONAL_PICKUP = "PERSONAL_PICKUP", _("Personal Pickup")
    SHIPPING_COMPANY = "SHIPPING_COMPANY", _("Shipping Company")
    INTERNATIONAL_SHIPPING = "INTERNATIONAL_SHIPPING", _("International Shipping")
    HAND_DELIVERY = "HAND_DELIVERY", _("Hand Delivery")


class AuctionItemStatus(models.TextChoices):
    """Individual item states within an auction"""

    ACTIVE = "active", _("Active")
    SOLD = "sold", _("Sold")
    UNSOLD = "unsold", _("Unsold")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    CANCELLED = "cancelled", _("Cancelled")


class AuctionStatus(models.TextChoices):
    """Individual item states within an auction"""

    ACTIVE = "active", _("Active")
    ENDED = "ended", _("Ended")
    CANCELLED = "cancelled", _("Cancelled")  # By owner
    CLOSED = "closed", _("Closed")  # By admin


class Auction(OwnerBaseModel):
    """
    Core Auction Aggregate

    Responsibilities:
    - Container for one or multiple auction items
    - Lifecycle management (active → ended)
    - Global timing and rules
    - Seller context
    - Visibility and publication
    """

    title = models.CharField(max_length=255)
    description = models.TextField()

    status = models.CharField(
        choices=AuctionStatus.choices,
        default=AuctionStatus.ACTIVE,
        max_length=20,
    )
    # Auction type determines behavior
    auction_type = models.CharField(
        max_length=20, choices=AuctionType.choices, default=AuctionType.SINGLE
    )
    bid_visibility = models.CharField(
        max_length=20,
        choices=AuctionBidVisibility.choices,
        default=AuctionBidVisibility.PUBLIC,
        db_index=True,
    )

    # Timing - Global for all items in this auction
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    # Optional auto-extend on last-minute bids
    auto_extend_enabled = models.BooleanField(default=False)
    auto_extend_minutes = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1)],
        help_text=_("Minutes to extend auction if bid placed in final window"),
    )

    # Payment rules
    deposit_required = models.BooleanField(default=False)
    deposit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )
    payment_deadline_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Days after auction end to complete payment"),
    )

    # Bid
    min_bid_increment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1.00,
        validators=[MinValueValidator(0)],
        help_text=_("Minimum amount next bid must exceed current"),
    )

    buy_now_enabled = models.BooleanField(default=False)
    buy_now_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Instant buy price for this item"),
    )

    # Visibility
    is_visible = models.BooleanField(
        default=True, help_text=_("Whether this auction is visible to the public")
    )

    # Metadata
    tags = models.CharField(max_length=500, blank=True)
    seller_package = models.ForeignKey(
        "packages.SellerPackage",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="created_auctions",
    )
    room = models.ForeignKey(
        "users.SellerRoom",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auctions",
    )
    shipping_method = models.CharField(
        max_length=32,
        choices=AuctionShippingMethod.choices,
        null=True,
        blank=True,
    )
    min_shipping_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Minimum expected shipping duration in days"),
    )
    max_shipping_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Maximum expected shipping duration in days"),
    )

    class Meta:
        indexes = [
            models.Index(fields=["end_time"]),
            models.Index(fields=["owner"]),
            models.Index(fields=["room"]),
            models.Index(fields=["auction_type"]),
            models.Index(fields=["bid_visibility"]),
            models.Index(fields=["is_visible"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(min_shipping_days__isnull=True) | Q(min_shipping_days__gte=1),
                name="auctions_min_shipping_days_gte_1",
            ),
            models.CheckConstraint(
                check=Q(max_shipping_days__isnull=True) | Q(max_shipping_days__gte=1),
                name="auctions_max_shipping_days_gte_1",
            ),
            models.CheckConstraint(
                check=(
                    Q(min_shipping_days__isnull=True)
                    | Q(max_shipping_days__isnull=True)
                    | Q(min_shipping_days__lte=F("max_shipping_days"))
                ),
                name="auctions_min_lte_max_shipping_days",
            ),
        ]
        ordering = ["-created"]

    def clean(self):
        super().clean()
        errors = {}
        if self.shipping_method is not None and self.shipping_method != "":
            if self.shipping_method not in AuctionShippingMethod.values:
                errors["shipping_method"] = _("Unsupported shipping method.")
        if self.min_shipping_days is not None and self.min_shipping_days < 1:
            errors["min_shipping_days"] = _(
                "Minimum shipping days must be a positive integer."
            )
        if self.max_shipping_days is not None and self.max_shipping_days < 1:
            errors["max_shipping_days"] = _(
                "Maximum shipping days must be a positive integer."
            )
        if (
            self.min_shipping_days is not None
            and self.max_shipping_days is not None
            and self.min_shipping_days > self.max_shipping_days
        ):
            errors["max_shipping_days"] = _(
                "Minimum shipping days cannot exceed maximum shipping days."
            )
        if errors:
            raise DjangoValidationError(errors)

    def __str__(self):
        return f"  #{self.pk} Auction ({self.get_auction_type_display()})"

    def is_active(self):
        """Check if auction is currently active"""
        now = timezone.now()
        return self.start_time <= now <= self.end_time

    def is_ended(self):
        """Check if auction has ended"""
        return timezone.now() > self.end_time

    def time_remaining(self):
        """Return timedelta of remaining time, or None if ended"""
        now = timezone.now()
        if now >= self.end_time:
            return None
        return self.end_time - now


class AuctionItem(BaseModel):
    """
    CRITICAL: Unit of Bidding and Sale

    Each auction contains 1 → N items
    Each item has:
    - One bird reference
    - Independent bid stream
    - Independent price/winner
    - Status tracking

    This layer removes ALL complexity around multi-bird auctions:
    - Single bird → 1 item
    - Multi-bird → N items
    - Pair → 1 item (representing both birds)
    """

    auction = models.ForeignKey(Auction, on_delete=models.CASCADE, related_name="items")

    # Bidding target: Primary subject
    bird = models.ForeignKey(
        "assets.Bird", on_delete=models.CASCADE, related_name="auction_items"
    )

    # For pair auctions: second bird (if exists)
    paired_bird = models.ForeignKey(
        "assets.Bird",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="paired_in_auctions",
    )

    # Item state
    status = models.CharField(
        max_length=20,
        choices=AuctionItemStatus.choices,
        default=AuctionItemStatus.ACTIVE,
    )

    # Pricing
    starting_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text=_("Minimum starting price for bids"),
    )
    reserve_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )

    current_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        default=0,
    )

    # Winner
    winner = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_auction_items",
    )

    # The bid that won the item
    winner_bid = models.OneToOneField(
        "Bid",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["auction"]),
            models.Index(fields=["status", "winner"]),
            models.Index(fields=["auction", "status"]),
        ]

    def __str__(self):
        return f" #{self.pk} AuctionItem (Auction: {self.auction.pk})"

    def is_active(self):
        """Check if item is actively accepting bids"""
        return self.status == AuctionItemStatus.ACTIVE and self.auction.is_active()

    def current_highest_bid(self):
        """Get the current highest bid for this item"""
        return self.bids.order_by("-amount", "created", "id").first()

    def current_highest_bid_amount(self):
        """Get the current highest bid for this item"""
        return (
            self.current_highest_bid().amount if self.bids.exists() else None
        )

    def current_highest_bidder(self):
        """Get the current highest bidder"""
        bid = self.current_highest_bid()
        return bid.bidder if bid else None


class Bid(BaseModel):
    """
    Bidding Unit

    Relationships:
    - Always belongs to AuctionItem (never direct to Auction)
    - Bidder is a buyer (Profile)

    Enforces:
    - Bid > current price
    - Auction is active
    - Item is active
    """

    item = models.ForeignKey(AuctionItem, on_delete=models.CASCADE, related_name="bids")

    bidder = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="bids_placed"
    )

    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )

    class Meta:
        indexes = [
            models.Index(fields=["item", "-amount"]),
            models.Index(fields=["bidder", "item"]),
            models.Index(fields=["item", "-created"]),
        ]
        ordering = ["-created"]

    def __str__(self):
        return (
            f" #{self.pk} Bid: {self.amount} on {self.item} by "
            "{self.bidder.user.username}"
        )

    def is_winning_bid(self):
        """Check if this is currently the winning bid"""
        return self.item.current_highest_bid() == self


# ============================================================================
# PHASE 2: BUSINESS LOGIC CONFIGURATION
# ============================================================================


class AuctionEvent(BaseModel):
    """
    Event Log for Auction Lifecycle

    Enables:
    - Audit trail
    - Analytics
    - Triggering async operations
    """

    EVENT_TYPES = [
        ("created", _("Auction Created")),
        ("published", _("Auction Published")),
        ("started", _("Auction Started")),
        ("bid_placed", _("Bid Placed")),
        ("auto_extended", _("Auto-Extended")),
        ("buy_now_triggered", _("Buy Now Triggered")),
        ("item_sold", _("Item Sold")),
        ("auction_ended", _("Auction Ended")),
        ("cancelled", _("Auction Cancelled")),
    ]

    auction = models.ForeignKey(
        Auction, on_delete=models.CASCADE, related_name="events"
    )

    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)

    # Context (item, bid, user involved)
    item = models.ForeignKey(
        AuctionItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    bid = models.ForeignKey(
        Bid,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    performer = models.ForeignKey(
        Profile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auction_events",
    )

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["auction", "-created"]),
            models.Index(fields=["event_type", "-created"]),
        ]
        ordering = ["-created"]

    def __str__(self):
        return f" #{self.pk} AuctionEvent {self.get_event_type_display()}"
        " - {self.auction.title}"
