import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError

from apps.authentication.models import UserAccount
from apps.notifications.serializers import NotificationSerializer
from apps.users.models import Profile


class JWTAuthMiddleware:
    """Authenticate WebSocket connections with the existing JWT access token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        raw_token = query.get("token", [None])[0]
        scope["user"] = await self._get_user(raw_token)
        return await self.app(scope, receive, send)

    @database_sync_to_async
    def _get_user(self, raw_token):
        if not raw_token:
            return None
        try:
            token = AccessToken(raw_token)
            user_id = token.get("user_id")
            profile_type = token.get("profile")
            if not user_id or not profile_type:
                return None
            user = UserAccount.objects.get(pk=user_id, is_active=True)
            profile = Profile.objects.filter(
                user=user,
                type=profile_type,
                is_active=True,
                activated=True,
                is_valid=True,
                deleted__isnull=True,
            ).first()
            if profile is None:
                return None
            user._notification_profile = profile
            return user
        except (TokenError, UserAccount.DoesNotExist):
            return None


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        profile = getattr(user, "_notification_profile", None)
        if not user or not profile:
            await self.close(code=4401)
            return

        self.profile_id = profile.pk
        self.group_name = f"notifications.profile.{self.profile_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        group_name = getattr(self, "group_name", None)
        if group_name:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def notification_created(self, event):
        await self.send_json(event["notification"])


async def broadcast_notification(notification):
    """Broadcast a persisted Notification to its recipient profile."""
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = await _serialize_notification(notification)
    await channel_layer.group_send(
        f"notifications.profile.{notification.recipient_profile_id}",
        {
            "type": "notification_created",
            "notification": {
                "event": "notification.created",
                "notification": payload,
            },
        },
    )


@database_sync_to_async
def _serialize_notification(notification):
    return NotificationSerializer(notification).data
