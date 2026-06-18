from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.chat.models import Conversation, Message
from apps.chat.services import ChatService


class ProfileSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(read_only=True)
    nickname = serializers.CharField(read_only=True, allow_blank=True)

    def to_representation(self, instance):
        data = {
            "id": instance.pk,
            "type": instance.type,
        }
        nickname = getattr(instance, "nickname", "")
        if nickname:
            data["nickname"] = nickname
        return data


class MessageSummarySerializer(serializers.ModelSerializer):
    sender = ProfileSummarySerializer(read_only=True)
    receiver = ProfileSummarySerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "sender",
            "receiver",
            "body",
            "created",
        ]
        read_only_fields = fields


class ConversationListSerializer(serializers.ModelSerializer):
    customer = ProfileSummarySerializer(read_only=True)
    seller = ProfileSummarySerializer(read_only=True)
    other_participant = serializers.SerializerMethodField()
    last_message = MessageSummarySerializer(read_only=True)
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "customer",
            "seller",
            "other_participant",
            "last_message",
            "last_message_at",
            "unread_count",
            "is_active",
            "created",
        ]
        read_only_fields = fields

    @extend_schema_field(ProfileSummarySerializer)
    def get_other_participant(self, obj):
        active_profile = self.context.get("active_profile")
        if active_profile and obj.customer_id == active_profile.pk:
            return ProfileSummarySerializer(obj.seller).data
        return ProfileSummarySerializer(obj.customer).data

    @extend_schema_field(serializers.IntegerField())
    def get_unread_count(self, obj):
        active_profile = self.context.get("active_profile")
        if active_profile is None:
            return 0
        return Message.objects.filter(
            conversation=obj, receiver=active_profile, is_read=False
        ).count()


class MessageSerializer(serializers.ModelSerializer):
    conversation_id = serializers.IntegerField(read_only=True)
    sender = ProfileSummarySerializer(read_only=True)
    receiver = ProfileSummarySerializer(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation_id",
            "sender",
            "receiver",
            "body",
            "is_read",
            "read_at",
            "created",
        ]
        read_only_fields = fields


class CreateConversationSerializer(serializers.Serializer):
    receiver_profile_id = serializers.IntegerField()

    def create(self, validated_data):
        active_profile = self.context["active_profile"]
        return ChatService.get_or_create_conversation(
            active_profile,
            validated_data["receiver_profile_id"],
        )


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField(
        max_length=Message.MAX_BODY_LENGTH,
        allow_blank=False,
        trim_whitespace=True,
    )


class MessageListQuerySerializer(serializers.Serializer):
    after_id = serializers.IntegerField(required=False, min_value=1)
