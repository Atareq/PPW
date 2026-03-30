from rest_framework import serializers
from apps.users.models import SellerProfile, CustomerProfile
from apps.authentication.services import switch_user_profile


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
        exclude = ["type", "is_active", "user"]
        read_only_fields = ["activated"]

    def update(self, instance, validated_data):
        if validated_data.pop("is_active", False):
            switch_user_profile(instance.user, "Seller")
        return super().update(instance, validated_data)


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerProfile
        exclude = ["type", "is_active", "user"]
        read_only_fields = ["activated"]
