from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
import factory
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from apps.assets.models import AssetStatus, Bird, Supply
from apps.auctions.models import (
    Auction,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
    Bid,
)
from apps.authentication.models import UserAccount
from apps.deals.models import CompletedDeal, CompletedDealSourceType, CompletedDealType
from apps.general.models import AssetMedia
from apps.loyalty.constants import BadgeType, PointTransactionType
from apps.loyalty.models import BadgeAward, PointTransaction, PointWallet
from apps.notifications.models import Notification, NotificationType
from apps.orders.models import (
    Cart,
    CartItem,
    CartStatus,
    Order,
    OrderEvent,
    OrderEventType,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)
from apps.packages.models import (
    AssetSellerPackage,
    Package,
    SellerPackageStatus,
)
from apps.pedigrees.models import (
    PedigreeBird,
    PedigreeBirdColour,
    PedigreeBirdSex,
    PedigreeDocument,
    PedigreeDocumentStatus,
)
from apps.promotions.models import (
    CashbackOffer,
    CashbackTransaction,
    CashbackTransactionType,
    DiscountOffer,
    OfferTargetType,
    OfferValueType,
)
from apps.races.models import Race, RaceResult
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.manager_permissions import get_manager_permission_codes
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile

faker = Faker()


def default_manager_permissions():
    return sorted(get_manager_permission_codes())


# ============================================================================
# USER & AUTH FACTORIES
# ============================================================================


class UserAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserAccount

    username = factory.Sequence(lambda n: f"user{n}")
    phone_number = factory.Sequence(
        lambda n: PhoneNumber.from_string(f"+201000000{n:03d}")  # noqa
    )
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Sequence(lambda n: f"user{n}@example.com")

    @factory.post_generation
    def with_profile(obj, create, extracted, **kwargs):
        if not create or extracted is False:
            return

        profile_type = (
            ProfileType.CUSTOMER if extracted in (None, True) else extracted
        )

        if profile_type == ProfileType.CUSTOMER:
            CustomerProfileFactory(user=obj, is_active=True)
        elif profile_type == ProfileType.SELLER:
            SellerProfileFactory(user=obj, is_active=True)
        elif profile_type == ProfileType.MANAGER:
            ManagerProfileFactory(user=obj, is_active=True)
        else:
            raise ValueError(f"Unsupported profile type: {profile_type}")


class BaseProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        abstract = True

    user = factory.SubFactory(UserAccountFactory, with_profile=False)
    activated = True
    is_active = False


class CustomerProfileFactory(BaseProfileFactory):
    class Meta:
        model = CustomerProfile

    type = ProfileType.CUSTOMER
    balance = Decimal("0.00")
    cashback_balance = Decimal("0.00")
    country = CountryOptions.EGYPT
    currency = CurrencyOptions.EGP


class SellerProfileFactory(BaseProfileFactory):
    class Meta:
        model = SellerProfile

    type = ProfileType.SELLER
    balance = Decimal("0.00")
    nickname = factory.Faker("user_name")
    recoded_voice = ""
    country = CountryOptions.EGYPT
    currency = CurrencyOptions.EGP


class ManagerProfileFactory(BaseProfileFactory):
    class Meta:
        model = ManagerProfile

    type = ProfileType.MANAGER
    permissions = factory.LazyFunction(default_manager_permissions)


# ============================================================================
# BIRD / ASSET FACTORIES
# ============================================================================


class BirdFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Bird

    title = factory.Sequence(lambda n: f"Bird Asset {n}")
    description = ""
    status = AssetStatus.AVAILABLE
    price = Decimal("100.00")
    is_market_listed = True
    is_for_auction = False
    name = factory.Faker("first_name")
    ring_number = factory.Sequence(lambda n: f"RING-{n:05d}")  # noqa
    gender = "male"
    colour = factory.Faker("color_name")
    owner = factory.SubFactory(SellerProfileFactory)
    count = 1
    achievements = ""
    stamina_ability = "good"
    created = factory.LazyFunction(timezone.now)


class SupplyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supply

    title = factory.Sequence(lambda n: f"Supply Asset {n}")
    description = ""
    owner = factory.SubFactory(SellerProfileFactory)
    status = AssetStatus.AVAILABLE
    count = 10
    price = Decimal("25.00")
    is_market_listed = True
    is_for_auction = False
    created = factory.LazyFunction(timezone.now)


class AssetMediaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AssetMedia

    asset = factory.SubFactory(SupplyFactory)
    media_url = factory.Sequence(
        lambda n: f"https://example.com/media-{n}.jpg"
    )
    alt_text = ""
    is_primary = False
    order = 0


class PedigreeDocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PedigreeDocument

    owner = factory.SubFactory(SellerProfileFactory)

    @factory.lazy_attribute
    def uploaded_by(self):
        return self.owner.user

    bird = None
    original_file = "pedigree_documents/test-pedigree.jpg"
    status = PedigreeDocumentStatus.PROCESSED
    raw_ocr_text = "EG-12345-2024\nFather: Blue Champion"
    extracted_bird_ring_number = "EG-12345-2024"
    extracted_description = "Father: Blue Champion"
    reviewed_bird_ring_number = ""
    reviewed_description = ""
    processing_error = ""


class PedigreeBirdFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PedigreeBird

    owner = factory.SubFactory(CustomerProfileFactory)
    ring_number = factory.Sequence(lambda n: f"EG 2026 {n:04d}")
    ring_code = "EG"
    name = factory.Sequence(lambda n: f"Pedigree Bird {n}")
    description = ""
    sex = PedigreeBirdSex.UNKNOWN
    colour = PedigreeBirdColour.UNKNOWN
    birthday = None
    father = None
    mother = None


# ============================================================================
# LOYALTY FACTORIES
# ============================================================================


class PointWalletFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PointWallet

    user = factory.SubFactory(UserAccountFactory)
    balance = 0


class PointTransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PointTransaction

    wallet = factory.SubFactory(PointWalletFactory)

    @factory.lazy_attribute
    def user(self):
        return self.wallet.user

    profile = None
    transaction_type = PointTransactionType.EARN
    points = 10
    balance_after = 10
    reason = "Test transaction"
    source_type = ""
    source_id = ""
    idempotency_key = None
    metadata = factory.LazyFunction(dict)


class BadgeAwardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BadgeAward

    profile = factory.SubFactory(CustomerProfileFactory)
    badge_type = BadgeType.FIRST_ORDER
    source_type = ""
    source_id = ""
    metadata = factory.LazyFunction(dict)


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    recipient_profile = factory.SubFactory(CustomerProfileFactory)
    actor_profile = None
    notification_type = NotificationType.CHAT_MESSAGE_RECEIVED
    title = "Test notification"
    body = "Notification body"
    object_type = ""
    object_id = None
    target_url = ""
    is_read = False
    read_at = None
    idempotency_key = None


# ============================================================================
# ORDER FACTORIES
# ============================================================================


class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Order

    owner = factory.SubFactory(CustomerProfileFactory)
    status = OrderStatus.PENDING
    total_price = Decimal("25.00")


class OrderItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrderItem

    order = factory.SubFactory(OrderFactory)
    asset = factory.SubFactory(SupplyFactory)

    @factory.lazy_attribute
    def seller(self):
        return SellerProfile.objects.get(pk=self.asset.owner_id)

    quantity = 1
    unit_price_snapshot = Decimal("25.00")
    status = OrderItemStatus.PENDING_SELLER

    @factory.lazy_attribute
    def total_price(self):
        return self.quantity * self.unit_price_snapshot


class CartFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Cart

    customer = factory.SubFactory(CustomerProfileFactory)
    status = CartStatus.ACTIVE


class CartItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CartItem

    cart = factory.SubFactory(CartFactory)
    asset = factory.SubFactory(SupplyFactory)

    @factory.lazy_attribute
    def seller(self):
        return SellerProfile.objects.get(pk=self.asset.owner_id)

    quantity = 1
    unit_price_snapshot = Decimal("25.00")


class OrderEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrderEvent

    order = factory.SubFactory(OrderFactory)
    item = None

    @factory.lazy_attribute
    def recipient(self):
        return self.order.owner

    event_type = OrderEventType.ORDER_CREATED
    message = "Order created."
    metadata = factory.LazyFunction(dict)


# ============================================================================
# PROMOTION FACTORIES
# ============================================================================


class DiscountOfferFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DiscountOffer

    title = factory.Sequence(lambda n: f"Discount Offer {n}")
    description = ""
    value_type = OfferValueType.FIXED_AMOUNT
    value = Decimal("5.00")
    target_type = OfferTargetType.ALL
    asset = None
    category = None
    minimum_order_price = Decimal("0.00")
    is_active = True
    priority = 0
    created_by = factory.SubFactory(ManagerProfileFactory)


class CashbackOfferFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CashbackOffer

    title = factory.Sequence(lambda n: f"Cashback Offer {n}")
    description = ""
    value_type = OfferValueType.FIXED_AMOUNT
    value = Decimal("5.00")
    target_type = OfferTargetType.ALL
    asset = None
    category = None
    minimum_order_price = Decimal("0.00")
    allowed_redemption_amount = Decimal("0.00")
    is_active = True
    priority = 0
    created_by = factory.SubFactory(ManagerProfileFactory)


class CashbackTransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CashbackTransaction

    customer = factory.SubFactory(CustomerProfileFactory)
    cashback_offer = factory.SubFactory(CashbackOfferFactory)
    order_item = None
    transaction_type = CashbackTransactionType.EARNED
    amount = Decimal("5.00")
    balance_after = Decimal("5.00")
    adjusted_by = None
    note = ""
    idempotency_key = factory.Sequence(lambda n: f"cashback-tx-{n}")


# ============================================================================
# PACKAGE FACTORIES
# ============================================================================


class PackageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Package

    name = factory.Sequence(lambda n: f"Seller Package {n}")
    description = ""
    points = 100
    price = Decimal("50.00")
    activation_period_days = 30
    auction_cost = 10
    product_cost = 5
    is_available = True


class AssetSellerPackageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AssetSellerPackage

    owner = factory.SubFactory(SellerProfileFactory)
    package = factory.SubFactory(PackageFactory)

    @factory.lazy_attribute
    def remaining_points(self):
        return self.package.points

    activated = False
    activated_at = None
    expires_at = None
    invalidated_at = None
    status = SellerPackageStatus.PENDING

    @classmethod
    def _adjust_kwargs(cls, **kwargs):
        owner = kwargs.get("owner")
        if (
            owner is not None
            and not isinstance(owner, SellerProfile)
            and getattr(owner, "type", None) == ProfileType.SELLER
        ):
            kwargs["owner"] = SellerProfile.objects.get(pk=owner.pk)
        return super()._adjust_kwargs(**kwargs)


class ActiveAssetSellerPackageFactory(AssetSellerPackageFactory):
    status = SellerPackageStatus.ACTIVE
    activated = True
    activated_at = factory.LazyFunction(timezone.now)
    expires_at = factory.LazyFunction(
        lambda: timezone.now() + timedelta(days=30)
    )


# ============================================================================
# RACE FACTORIES
# ============================================================================


class RaceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Race

    title = factory.Sequence(lambda n: f"{500 + n}KM")
    season_year = 2026
    station_name = factory.Sequence(lambda n: f"Station {n}")
    release_datetime = factory.LazyFunction(timezone.now)
    weather_condition = ""
    total_birds = 284
    competitors_count = 22
    planned_distance_km = Decimal("500.000")
    coordinate_x = ""
    coordinate_y = ""
    notes = ""


class RaceResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RaceResult

    race = factory.SubFactory(RaceFactory)
    rank = factory.Sequence(lambda n: n + 1)
    competitor_name = factory.Sequence(lambda n: f"Competitor {n}")
    bird_ring_number = factory.Sequence(lambda n: f"RING-{n:05d}")  # noqa
    distance_km = Decimal("490.832")
    speed = factory.Sequence(lambda n: Decimal("1400.000") + Decimal(n))
    arrival_datetime = factory.LazyFunction(
        lambda: timezone.now() + timedelta(hours=1)
    )
    basket_number = None


# ============================================================================
# AUCTION FACTORIES
# ============================================================================


class AuctionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Auction

    owner = factory.SubFactory(SellerProfileFactory)
    title = factory.Faker("sentence", nb_words=5)
    description = factory.Faker("text", max_nb_chars=200)
    auction_type = AuctionType.SINGLE
    start_time = factory.LazyFunction(
        lambda: timezone.now() - timedelta(hours=1)
    )
    end_time = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7))
    status = AuctionStatus.ACTIVE
    is_visible = True
    auto_extend_enabled = False
    auto_extend_minutes = 5
    buy_now_enabled = False
    buy_now_price = None
    deposit_required = False
    deposit = None
    payment_deadline_days = None
    min_bid_increment = Decimal("1.00")
    tags = factory.LazyFunction(lambda: "test,auction")


class AuctionItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuctionItem

    auction = factory.SubFactory(AuctionFactory)

    @factory.lazy_attribute
    def bird(self):
        # keep bird ownership consistent with auction owner by default
        return BirdFactory(owner=self.auction.owner)

    paired_bird = None
    status = AuctionItemStatus.ACTIVE
    starting_price = Decimal("100.00")
    current_price = Decimal("100.00")
    winner = None
    winner_bid = None

    @factory.post_generation
    def with_paired_bird(obj, create, extracted, **kwargs):
        if not create or extracted is None:
            return
        obj.paired_bird = extracted
        obj.save(update_fields=["paired_bird"])


class BidFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Bid

    item = factory.SubFactory(AuctionItemFactory)
    bidder = factory.SubFactory(CustomerProfileFactory)
    amount = Decimal("150.00")

    @factory.lazy_attribute
    def bidder(self):
        # customer is the valid default bidder role
        return CustomerProfileFactory()

    @factory.lazy_attribute
    def amount(self):
        base_amount = self.item.current_price or self.item.starting_price
        increment = self.item.auction.min_bid_increment or Decimal("1.00")
        return Decimal(base_amount) + Decimal(increment)


class CompletedDealFactory(factory.django.DjangoModelFactory):
    """A recorded market deal; source ids are synthetic unless given."""

    class Meta:
        model = CompletedDeal

    deal_type = CompletedDealType.MARKET
    source_type = CompletedDealSourceType.ORDER_ITEM
    source_id = factory.Sequence(lambda n: 100000 + n)
    buyer_profile = factory.SubFactory(CustomerProfileFactory)
    buyer_user = factory.SelfAttribute("buyer_profile.user")
    seller_profile = factory.SubFactory(SellerProfileFactory)
    seller_user = factory.SelfAttribute("seller_profile.user")
    asset = factory.SubFactory(
        SupplyFactory,
        owner=factory.SelfAttribute("..seller_profile"),
    )
    amount = Decimal("10.00")
    completed_at = factory.LazyFunction(timezone.now)


# ============================================================================
# CONVENIENCE FACTORIES
# ============================================================================


def create_seller_with_bird():
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    bird = BirdFactory(owner=seller.active_profile)
    return seller, bird


def create_customer():
    customer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return customer


def create_active_auction_with_item():
    seller, bird = create_seller_with_bird()
    auction = AuctionFactory(
        owner=seller.active_profile,
        start_time=timezone.now() - timedelta(hours=1),
        end_time=timezone.now() + timedelta(days=7),
        status=AuctionStatus.ACTIVE,
        is_visible=True,
    )
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        status=AuctionItemStatus.ACTIVE,
        current_price=Decimal("100.00"),
    )
    return auction, item, seller


# ============================================================================
# REGISTRATION PAYLOAD FIXTURES
# ============================================================================


def customer_registration_payload(phone_number="+201001234567", region="EG"):
    """
    Build a valid customer registration payload.

    Use to create new customers via the registration endpoint.
    """
    return {
        "phone_number": phone_number,
        "region": region,
        "password": "SecurePassword123!@",
        "password_confirm": "SecurePassword123!@",
        "customer_profile": {
            "country": CountryOptions.EGYPT,
            "currency": CurrencyOptions.EGP,
        },
    }


def manager_registration_payload(phone_number="+201001234567", region="EG"):
    """
    Build a valid manager registration payload.

    Use to create new managers via the registration endpoint.
    """
    return {
        "phone_number": phone_number,
        "region": region,
        "password": "SecurePassword123!@",
        "password_confirm": "SecurePassword123!@",
        "manager_profile": {},
    }
