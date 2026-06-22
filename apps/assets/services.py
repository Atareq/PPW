from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.assets.models import AssetSaleChannel, Supply
from apps.authentication.models import UserAccount
from apps.general.models import AssetMedia
from apps.packages.models import PackageUsageType
from apps.packages.services import PackageService
from apps.users.constants import CountryOptions, CurrencyOptions, ProfileType
from apps.users.models import Profile, SellerProfile


PLATFORM_SUPPLY_USERNAME = "ppw-platform-supplies"
PLATFORM_SUPPLY_PHONE_NUMBER = "+201099999999"
PLATFORM_SUPPLY_NICKNAME = "PPW Platform"


class BirdValidator:
    """
    Centralized validator for Bird create/update flows.
    Use in serializers and optionally in model.clean().
    """

    @classmethod
    def validate(cls, attrs, owner: Profile, instance=None):
        # Get the data from `attr` if create and `instance` in case of update
        father = attrs.get("father", getattr(instance, "father", None))
        mother = attrs.get("mother", getattr(instance, "mother", None))
        birthday = attrs.get("birthday", getattr(instance, "birthday", None))
        flying_speed = attrs.get(
            "flying_speed", getattr(instance, "flying_speed", None)
        )

        errors = {}

        cls._validate_owner_profile(errors, owner)
        cls._validate_birthday(errors, birthday)
        cls._validate_flying_speed(errors, flying_speed)
        cls._validate_parents(errors, instance, father, mother, birthday)

        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    @staticmethod
    def _validate_owner_profile(errors, owner):
        if owner is None or owner.type != ProfileType.SELLER:
            errors["owner"] = "Only SELLER profiles can own birds."

    @staticmethod
    def _validate_birthday(errors, birthday):
        if birthday and birthday > timezone.now().date():
            errors["birthday"] = "Birthday cannot be in the future."

    @staticmethod
    def _validate_flying_speed(errors, flying_speed):
        if flying_speed is not None and flying_speed < 0:
            errors["flying_speed"] = "Flying speed cannot be negative."

    @classmethod
    def _validate_parents(cls, errors, instance, father, mother, birthday):
        cls._validate_father(errors, instance, father, birthday)
        cls._validate_mother(errors, instance, mother, birthday)

        if father and mother and father.id == mother.id:
            errors["mother"] = "Father and mother cannot be the same bird."

    @staticmethod
    def _validate_father(errors, instance, father, birthday):
        if not father:
            return

        if father.gender != "male":
            errors["father"] = "Father must be male."
            return

        if father.gender == "young":
            errors["father"] = "Father cannot be young."

        if instance and father.id == instance.id:
            errors["father"] = "Bird cannot be its own father."

        if birthday and father.birthday and father.birthday >= birthday:
            errors["father"] = "Father must be older than the bird."

    @staticmethod
    def _validate_mother(errors, instance, mother, birthday):
        if not mother:
            return

        if mother.gender != "female":
            errors["mother"] = "Mother must be female."
            return

        if mother.gender == "young":
            errors["mother"] = "Mother cannot be young."

        if instance and mother.id == instance.id:
            errors["mother"] = "Bird cannot be its own mother."

        if birthday and mother.birthday and mother.birthday >= birthday:
            errors["mother"] = "Mother must be older than the bird."


class ProductAssetCreationService:
    """Create product assets and consume seller package points when required."""

    @staticmethod
    def create_product(
        model_class,
        owner,
        validated_data,
        media_data=None,
    ):
        if model_class is Supply:
            return ProductAssetCreationService.create_platform_supply(
                validated_data,
                media_data=media_data,
                actor_profile=owner,
            )

        if owner.type != ProfileType.SELLER:
            raise serializers.ValidationError(
                "Only seller profiles can create product assets."
            )

        PackageService.validate_can_consume(
            owner,
            PackageUsageType.PRODUCT_CREATED,
        )
        with transaction.atomic():
            product = model_class.objects.create(
                owner=owner,
                sale_channel=AssetSaleChannel.SELLER_MARKET,
                **validated_data,
            )
            AssetMediaWriteService.create_media(product, media_data)
            PackageService.consume_points(
                owner,
                PackageUsageType.PRODUCT_CREATED,
                related_object=product,
            )
            ProductAssetCreationService._handle_market_item_reward(
                product,
                owner,
            )
            return product

    @staticmethod
    def create_platform_supply(
        validated_data,
        media_data=None,
        actor_profile=None,
    ):
        if actor_profile and actor_profile.type not in {ProfileType.MANAGER}:
            raise serializers.ValidationError(
                "Supply products are platform-controlled and cannot be created by sellers."
            )

        platform_seller = ProductAssetCreationService.get_platform_seller_profile()
        validated_data.pop("sale_channel", None)
        validated_data.pop("managed_by", None)

        with transaction.atomic():
            product = Supply.objects.create(
                owner=platform_seller,
                sale_channel=AssetSaleChannel.PLATFORM,
                managed_by=actor_profile if actor_profile else None,
                **validated_data,
            )
            AssetMediaWriteService.create_media(product, media_data)
            return product

    @staticmethod
    def get_platform_seller_profile():
        user = UserAccount.objects.filter(
            phone_number=PLATFORM_SUPPLY_PHONE_NUMBER
        ).first()
        if user is None:
            user = UserAccount.objects.create_user(
                username=PLATFORM_SUPPLY_USERNAME,
                phone_number=PLATFORM_SUPPLY_PHONE_NUMBER,
                password=None,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])

        seller = SellerProfile.objects.filter(user=user).first()
        if seller is None:
            seller = SellerProfile.objects.create(
                user=user,
                is_active=False,
                activated=True,
                is_valid=True,
                nickname=PLATFORM_SUPPLY_NICKNAME,
                country=CountryOptions.EGYPT,
                currency=CurrencyOptions.EGP,
            )
        return seller

    @staticmethod
    def _handle_market_item_reward(product, owner):
        from apps.loyalty.constants import RewardEventType
        from apps.loyalty.reward_engine import RewardEngine

        RewardEngine.handle_event(
            RewardEventType.MARKET_ITEM_LISTED,
            actor=owner,
            profile=owner,
            context={"asset": product},
        )


class AssetMediaWriteService:
    """Maintain nested asset media as replace-all serializer children."""

    @staticmethod
    def create_media(asset, media_data=None):
        for media_item_data in media_data or []:
            AssetMedia.objects.create(asset=asset, **media_item_data)
        AssetMediaWriteService._clear_media_cache(asset)

    @staticmethod
    def replace_media(asset, media_data):
        """Replace the full media set; omitted serializer field preserves it."""

        asset.media.all().delete()
        AssetMediaWriteService.create_media(asset, media_data)

    @staticmethod
    def _clear_media_cache(asset):
        cache = getattr(asset, "_prefetched_objects_cache", None)
        if cache:
            cache.pop("media", None)
