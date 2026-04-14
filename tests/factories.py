from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from apps.assets.models import Bird
from apps.auctions.models import (
    Auction,
    AuctionItem,
    AuctionItemStatus,
    AuctionStatus,
    AuctionType,
    Bid,
)
from apps.authentication.models import UserAccount
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile

faker = Faker()


# ============================================================================
# USER & AUTH FACTORIES
# ============================================================================


class UserAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserAccount

    username = factory.Sequence(lambda n: f"user{n}")
    phone_number = factory.Sequence(
        lambda n: PhoneNumber.from_string(f"+201000000{n:03d}")
    )
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    permissions = factory.LazyFunction(list)

    @factory.post_generation
    def with_profile(obj, create, extracted, **kwargs):
        if not create or extracted is False:
            return

        profile_type = ProfileType.CUSTOMER if extracted in (None, True) else extracted

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

    user = factory.SubFactory(UserAccountFactory)
    activated = True
    is_active = False


class CustomerProfileFactory(BaseProfileFactory):
    class Meta:
        model = CustomerProfile

    type = ProfileType.CUSTOMER
    balance = Decimal("0.00")
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


# ============================================================================
# BIRD / ASSET FACTORIES
# ============================================================================


class BirdFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Bird

    name = factory.Faker("first_name")
    colour = factory.Faker("color_name")
    owner = factory.SubFactory(SellerProfileFactory)
    count = 1
    created = factory.LazyFunction(timezone.now)


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
    start_time = factory.LazyFunction(lambda: timezone.now() - timedelta(hours=1))
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
        }
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
        "manager_profile": {}
    }