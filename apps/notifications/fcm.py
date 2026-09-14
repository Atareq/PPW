import json
import logging
import os

from django.conf import settings
import firebase_admin
from firebase_admin import credentials, exceptions as fb_exceptions, messaging

from apps.notifications.payload import NotificationPayload

logger = logging.getLogger(__name__)


class TransientPushError(Exception):
    """Raised when FCM encounters temporary delivery errors that should be retried."""
    pass


def _firebase_app():
    """Resolve or initialize Firebase App from settings, or return None if unconfigured."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    # Option 1: Individual environment variables
    project_id = getattr(settings, "FIREBASE_PROJECT_ID", "")
    client_email = getattr(settings, "FIREBASE_CLIENT_EMAIL", "")
    private_key = getattr(settings, "FIREBASE_PRIVATE_KEY", "")

    if project_id and client_email and private_key:
        try:
            formatted_key = private_key.replace("\\n", "\n")
            cred = credentials.Certificate(
                {
                    "type": "service_account",
                    "project_id": project_id,
                    "private_key": formatted_key,
                    "client_email": client_email,
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            )
            return firebase_admin.initialize_app(cred)
        except Exception as exc:
            logger.warning(
                "Firebase initialization with service account fields failed: %s",
                exc,
            )
            return None

    # Option 2: Full credentials JSON string or file path
    credentials_json = getattr(settings, "FIREBASE_CREDENTIALS_JSON", "").strip()
    if credentials_json:
        try:
            if credentials_json.startswith("{"):
                payload = json.loads(credentials_json)
                cred = credentials.Certificate(payload)
            elif os.path.exists(credentials_json):
                cred = credentials.Certificate(credentials_json)
            else:
                logger.warning(
                    "FIREBASE_CREDENTIALS_JSON was provided but is neither valid JSON nor a file."
                )
                return None
            return firebase_admin.initialize_app(cred)
        except Exception as exc:
            logger.warning(
                "Firebase initialization with JSON credentials failed: %s",
                exc,
            )
            return None

    logger.debug("Firebase credentials not configured; skipping FCM delivery.")
    return None


def _is_permanent_error(exc) -> bool:
    """Determine whether an FCM exception indicates an invalid/unregistered token."""
    if exc is None:
        return False

    if isinstance(
        exc,
        (
            messaging.UnregisteredError,
            messaging.SenderIdMismatchError,
            fb_exceptions.InvalidArgumentError,
        ),
    ):
        return True

    code = getattr(exc, "code", "")
    if code in ("NOT_FOUND", "UNREGISTERED", "INVALID_ARGUMENT"):
        return True

    message = str(exc).lower()
    if "registration-token-not-registered" in message or "invalid-argument" in message:
        return True

    return False


def build_fcm_message(notification, tokens: list[str]) -> messaging.MulticastMessage:
    """Construct an FCM MulticastMessage using the canonical NotificationPayload."""
    payload = NotificationPayload.build(notification)
    data = {
        "notification_id": str(payload["id"]),
        "notification_type": str(payload["notification_type"]),
        "object_type": str(payload["object_type"] or ""),
        "object_id": str(payload["object_id"] or ""),
        "target_url": str(payload["target_url"] or ""),
        "metadata": json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
        "created_at": str(payload["created"] or ""),
    }
    return messaging.MulticastMessage(
        notification=messaging.Notification(
            title=payload["title"],
            body=payload["body"],
        ),
        data=data,
        tokens=tokens,
    )


def send_notification_to_devices(notification, devices) -> int:
    """Send FCM push to recipient devices.

    Deactivates devices on permanent token errors.
    Raises TransientPushError on transient failures so Celery can retry.
    """
    app = _firebase_app()
    if app is None or not devices:
        return 0

    active_devices = [d for d in devices if d.is_active]
    if not active_devices:
        return 0

    tokens = [d.token for d in active_devices]
    message = build_fcm_message(notification, tokens)

    try:
        response = messaging.send_each_for_multicast(message, app=app)
    except (fb_exceptions.UnavailableError, fb_exceptions.DeadlineExceededError) as exc:
        logger.warning(
            "Transient top-level FCM failure for notification %s: %s",
            notification.pk,
            exc,
        )
        raise TransientPushError(str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected FCM error for notification %s: %s",
            notification.pk,
            exc,
        )
        return 0

    has_transient_error = False
    for device, result in zip(active_devices, response.responses):
        if not result.success:
            exc = result.exception
            if _is_permanent_error(exc):
                device.is_active = False
                device.save(update_fields=["is_active", "modified"])
                logger.info(
                    "Deactivated PushDevice #%s due to permanent token error: %s",
                    device.pk,
                    exc,
                )
            else:
                has_transient_error = True
                logger.warning(
                    "Transient FCM error for PushDevice #%s: %s",
                    device.pk,
                    exc,
                )

    if has_transient_error:
        raise TransientPushError(
            f"Transient FCM delivery failures occurred for notification {notification.pk}"
        )

    return response.success_count
