from rest_framework import serializers

from apps.announcements.models import Announcement


class AnnouncementSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    created_by_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Announcement
        fields = [
            "id",
            "title",
            "subtitle",
            "details",
            "image",
            "image_url",
            "target",
            "is_active",
            "created_by_id",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "image_url",
            "created_by_id",
            "created",
            "modified",
        ]

    def get_image_url(self, obj):
        return obj.image or ""
