import asyncio
import logging

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.authentication.models import UserAccount
from apps.notifications.payload import NotificationPayload
from apps.users.models import Profile

logger = logging.getLogger(__name__)


def resolve_concrete_profile(profile):
    """Safely resolve concrete Profile subclass without mutating active_profile.

    Directly queries/resolves the concrete subclass (CustomerProfile, SellerProfile,
    or ManagerProfile) without calling switch_user_profile() or mutating state.
    """
    if profile is None:
        return None
    from apps.users.constants import ProfileType
    from apps.users.models import CustomerProfile, ManagerProfile, SellerProfile

    if isinstance(profile, (CustomerProfile, SellerProfile, ManagerProfile)):
        return profile

    if profile.type == ProfileType.CUSTOMER:
        try:
            return profile.customerprofile
        except (CustomerProfile.DoesNotExist, AttributeError):
            return CustomerProfile.objects.filter(pk=profile.pk).first() or profile
    elif profile.type == ProfileType.SELLER:
        try:
            return profile.sellerprofile
        except (SellerProfile.DoesNotExist, AttributeError):
            return SellerProfile.objects.filter(pk=profile.pk).first() or profile
    elif profile.type == ProfileType.MANAGER:
        try:
            return profile.managerprofile
        except (ManagerProfile.DoesNotExist, AttributeError):
            return ManagerProfile.objects.filter(pk=profile.pk).first() or profile

    return profile


class JWTAuthMiddleware:
    """Authenticate notification WebSockets strictly with Authorization: Bearer <token>."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        raw_token = self._get_bearer_token(scope.get("headers", []))
        user, profile = await self._authenticate_token(raw_token)
        scope["user"] = user
        scope["notification_profile"] = profile
        return await self.app(scope, receive, send)

    @staticmethod
    def _get_bearer_token(headers):
        for key, value in headers:
            if key.lower() == b"authorization":
                parts = value.decode("utf-8").split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    return parts[1]
        return None

    @database_sync_to_async
    def _authenticate_token(self, raw_token):
        if not raw_token:
            return None, None
        try:
            token = AccessToken(raw_token)
            user_id = token.get("user_id")
            profile_type = token.get("profile")
            if not user_id or not profile_type:
                return None, None
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
                return None, None
            concrete_profile = getattr(
                profile,
                f"{profile_type.lower()}profile",
                profile,
            )
            concrete_profile = resolve_concrete_profile(profile)
            return user, concrete_profile
        except (TokenError, UserAccount.DoesNotExist):
            return None, None


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        profile = self.scope.get("notification_profile")
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


async def async_broadcast_notification(notification):
    """Asynchronously broadcast a persisted Notification to its recipient profile."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = NotificationPayload.build(notification)
    group_name = f"notifications.profile.{notification.recipient_profile_id}"
    await channel_layer.group_send(
        group_name,
        {
            "type": "notification_created",
            "notification": {
                "event": "notification.created",
                "notification": payload,
            },
        },
    )


def broadcast_notification(notification):
    """Synchronously broadcast a persisted Notification to its recipient profile."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        return loop.create_task(async_broadcast_notification(notification))

    payload = NotificationPayload.build(notification)
    group_name = f"notifications.profile.{notification.recipient_profile_id}"
    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "notification_created",
                "notification": {
                    "event": "notification.created",
                    "notification": payload,
                },
            },
        )
    except Exception:
        logger.exception(
            "Failed to dispatch notification %s to channel layer group %s.",
            notification.pk,
            group_name,
        )
