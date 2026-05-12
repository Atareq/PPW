from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.packages.models import PackageUsageType
from apps.packages.services import PackageService
from apps.users.constants import ProfileType
from apps.users.models import Profile


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
        if not owner:
            errors["owner"] = "Owner profile is required."
            return

        if owner.type != ProfileType.SELLER:
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
    @staticmethod
    def create_product(model_class, owner, validated_data):
        if owner is None:
            raise serializers.ValidationError("Active profile not found.")

        if owner.type == ProfileType.MANAGER:
            return model_class.objects.create(owner=owner, **validated_data)

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
                **validated_data,
            )
            PackageService.consume_points(
                owner,
                PackageUsageType.PRODUCT_CREATED,
                related_object=product,
            )
            return product
