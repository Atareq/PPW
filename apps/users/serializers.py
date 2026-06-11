from rest_framework import serializers

from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile


# ---------------- Dashboard Serializer ----------------
class SellerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = SellerProfile
        fields = "__all__"


class CustomerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = CustomerProfile
        fields = "__all__"


# ---------------- Mobile Serializer ----------------
class SellerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, obj):
        return obj.user.avatar_url or None

    class Meta:
        model = SellerProfile
        fields = [
            "id", "type", "username", "nickname", "country", "currency",
            "balance", "avg_rating", "ratings_count",
            "activated", "is_active", "is_valid", "created", "avatar_url",
        ]
        read_only_fields = [
            "id", "type", "username", "activated", "is_active", "is_valid",
            "balance", "avg_rating", "ratings_count", "created", "avatar_url",
        ]


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, obj):
        return obj.user.avatar_url or None

    class Meta:
        model = CustomerProfile
        fields = [
            "id", "type", "username", "country", "currency", "balance",
            "activated", "is_active", "is_valid", "created", "avatar_url",
        ]
        read_only_fields = [
            "id", "type", "username", "activated", "is_active", "is_valid",
            "balance", "created", "avatar_url",
        ]


class ManagerProfileadminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerProfile
        exclude = ["type", "user"]


# ---------------- Discover / public listing ----------------
class SellerDiscoverSerializer(serializers.ModelSerializer):
    """Public seller listing — no sensitive fields (balance, currency)."""

    active_auctions_count = serializers.IntegerField(read_only=True)
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, obj):
        return obj.user.avatar_url or None

    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = SellerProfile
        fields = [
            "id", "username", "nickname", "country",
            "avg_rating", "ratings_count",
            "active_auctions_count",
            "avatar_url",
        ]
