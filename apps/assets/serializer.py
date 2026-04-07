from rest_framework import serializers
from apps.assets.models import Bird


class BirdSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bird
        fields = "__all__"
        read_only_fields = ['owner']

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user.active_profile
        return super().create(validated_data)
