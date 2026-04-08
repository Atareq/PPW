from rest_framework import serializers

from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile


# ---------------- Dashboard Serializer ----------------
class SellerProfileAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = SellerProfile
        fields = "__all__"  # admins can update everything


class CustomerProfileAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        fields = "__all__"


# ---------------- Mobile Serializer ----------------
class SellerProfileMobileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SellerProfile
        fields = ["country", "currency", "recoded_voice", "nickname"]
        read_only_fields = ["activated", "is_active"]


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        exclude = ["type", "is_active", "user"]
        read_only_fields = ["activated"]


class ManagerProfileadminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerProfile
        exclude = ["type", "user"]
