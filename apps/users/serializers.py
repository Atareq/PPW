from rest_framework import serializers

from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile


# ---------------- Dashboard Serializer ----------------
class SellerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = SellerProfile
        fields = "__all__"


class CustomerProfileAdminSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = CustomerProfile
        fields = "__all__"


# ---------------- Mobile Serializer ----------------
class SellerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = SellerProfile
        fields = [
            'id', 'type', 'nickname', 'username', 'phone_number',
            'country', 'currency', 'recoded_voice',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
        ]
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'avg_rating', 'ratings_count',
            'activated', 'is_active', 'is_valid', 'created',
        ]


class CustomerProfileMobileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)

    class Meta:
        model = CustomerProfile
        exclude = ['user', 'balance']
        read_only_fields = [
            'id', 'type', 'username', 'phone_number',
            'activated', 'is_active', 'is_valid',
            'cashback_balance', 'created', 'modified', 'deleted',
        ]


class ManagerProfileadminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagerProfile
        exclude = ["type", "user"]
