import django_filters

from apps.ratings.models import (
    AssetComment,
    AssetRating,
    SellerComment,
    SellerRating,
)


class AssetCommentFilter(django_filters.FilterSet):
    asset_id = django_filters.NumberFilter(field_name="asset_id")
    owner_id = django_filters.NumberFilter(field_name="owner_id")

    class Meta:
        model = AssetComment
        fields = [
            "asset_id",
            "owner_id",
        ]


class SellerCommentFilter(django_filters.FilterSet):
    seller_id = django_filters.NumberFilter(field_name="seller_id")
    owner_id = django_filters.NumberFilter(field_name="owner_id")

    class Meta:
        model = SellerComment
        fields = [
            "seller_id",
            "owner_id",
        ]


class AssetRatingFilter(django_filters.FilterSet):
    asset_id = django_filters.NumberFilter(field_name="asset_id")
    stars = django_filters.NumberFilter(field_name="stars")

    class Meta:
        model = AssetRating
        fields = [
            "asset_id",
            "stars",
        ]


class SellerRatingFilter(django_filters.FilterSet):
    seller_id = django_filters.NumberFilter(field_name="seller_id")
    stars = django_filters.NumberFilter(field_name="stars")

    class Meta:
        model = SellerRating
        fields = [
            "seller_id",
            "stars",
        ]
