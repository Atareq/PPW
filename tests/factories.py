"""
Test data factories using factory_boy.

Factories create consistent test data for all tests.
Much better than hardcoded fixtures.
"""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from apps.assets.models import Bird
from apps.auctions.models import Auction, AuctionItem, AuctionType, Bid
from apps.authentication.models import UserAccount
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile

faker = Faker()


# ============================================================================
# USER & AUTH FACTORIES
# ============================================================================


class UserAccountFactory(factory.django.DjangoModelFactory):
    """Factory for creating UserAccount instances."""

    class Meta:
        model = UserAccount

    phone_number = factory.Sequence(
        lambda n: PhoneNumber.from_string(f"+201000000{n:03d}")
    )
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    permissions = factory.LazyFunction(list)

    @factory.post_generation
    def with_profile(obj, create, extracted, **kwargs):
        """
        Optionally create a profile after user creation.

        Usage:
            UserAccountFactory()  # creates only user
            UserAccountFactory(with_profile=True)  # creates active customer profile
            UserAccountFactory(with_profile=ProfileType.CUSTOMER)
            UserAccountFactory(with_profile=ProfileType.SELLER)
            UserAccountFactory(with_profile=ProfileType.MANAGER)

        Note:
            'extracted' is the value passed to with_profile=...
        """
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
    """Base factory for all profile types."""

    class Meta:
        abstract = True

    user = factory.SubFactory(UserAccountFactory)
    activated = True
    is_active = False


class CustomerProfileFactory(BaseProfileFactory):
    """Factory for creating CustomerProfile instances."""

    class Meta:
        model = CustomerProfile

    type = ProfileType.CUSTOMER
    balance = Decimal("0.00")
    country = CountryOptions.EG
    currency = CurrencyOptions.EGP


class SellerProfileFactory(BaseProfileFactory):
    """Factory for creating SellerProfile instances."""

    class Meta:
        model = SellerProfile

    type = ProfileType.SELLER
    balance = Decimal("0.00")
    nickname = factory.Faker("user_name")
    recoded_voice = ""
    country = CountryOptions.EG
    currency = CurrencyOptions.EGP


class ManagerProfileFactory(BaseProfileFactory):
    """Factory for creating ManagerProfile instances."""

    class Meta:
        model = ManagerProfile

    type = ProfileType.MANAGER


# ============================================================================
# BIRD/ASSET FACTORIES
# ============================================================================


class BirdFactory(factory.django.DjangoModelFactory):
    """Factory for creating Bird instances."""

    class Meta:
        model = Bird

    name = factory.Faker("first_name")
    species = "Pigeon"
    age_months = factory.Faker("random_int", min=1, max=120)
    color = factory.Faker("color_name")
    owner = factory.SubFactory(SellerProfileFactory)
    created = factory.LazyFunction(timezone.now)


# ============================================================================
# AUCTION FACTORIES
# ============================================================================


class AuctionFactory(factory.django.DjangoModelFactory):
    """Factory for creating Auction instances."""

    class Meta:
        model = Auction

    owner = factory.SubFactory(SellerProfileFactory)
    title = factory.Faker("sentence", nb_words=5)
    description = factory.Faker("text", max_nb_chars=200)
    auction_type = AuctionType.SINGLE
    start_time = factory.LazyFunction(timezone.now)
    end_time = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7))
    status = "active"
    is_visible = True
    auto_extend_enabled = False
    auto_extend_minutes = 5
    buy_now_enabled = False
    buy_now_price = None
    deposit_required = False
    payment_deadline_days = None
    min_bid_increment = Decimal("1.00")
    tags = factory.Faker("words", nb=3)


class AuctionItemFactory(factory.django.DjangoModelFactory):
    """Factory for creating AuctionItem instances."""

    class Meta:
        model = AuctionItem

    auction = factory.SubFactory(AuctionFactory)
    bird = factory.SubFactory(BirdFactory)
    status = "active"
    starting_price = Decimal("100.00")
    current_highest_bid = Decimal("100.00")
    created = factory.LazyFunction(timezone.now)

    @factory.post_generation
    def with_paired_bird(obj, create, extracted, **kwargs):
        """Optional paired bird for PAIR auctions."""
        if not create or extracted is None:
            return
        obj.paired_bird = extracted
        obj.save(update_fields=["paired_bird"])


class BidFactory(factory.django.DjangoModelFactory):
    """Factory for creating Bid instances."""

    class Meta:
        model = Bid

    item = factory.SubFactory(AuctionItemFactory)
    bidder = factory.SubFactory(SellerProfileFactory)
    amount = Decimal("150.00")
    created = factory.LazyFunction(timezone.now)


# ============================================================================
# CONVENIENCE FACTORIES
# ============================================================================


def create_seller_with_bird():
    """Create a seller user with a bird."""
    seller = UserAccountFactory(with_profile=ProfileType.SELLER)
    bird = BirdFactory(owner=seller.active_profile)
    return seller, bird


def create_buyer():
    """Create a buyer user."""
    buyer = UserAccountFactory(with_profile=ProfileType.CUSTOMER)
    return buyer


def create_active_auction_with_item():
    """Create an active auction with an item."""
    seller, bird = create_seller_with_bird()
    auction = AuctionFactory(
        owner=seller.active_profile,
        start_time=timezone.now() - timedelta(hours=1),
        end_time=timezone.now() + timedelta(days=7),
        status="active",
        is_visible=True,
    )
    item = AuctionItemFactory(
        auction=auction,
        bird=bird,
        status="active",
    )
    return auction, item, seller