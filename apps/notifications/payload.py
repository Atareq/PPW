import re

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext as _

_POINTS_EARNED_BODY_RE = re.compile(
    r"^Congratulations! You earned (?P<points>\d+) PP Coins\.$"
)
_BADGE_AWARDED_BODY_RE = re.compile(
    r"^(?:Congratulations! )?You earned the (?P<badge_name>.+) badge\.$"
)
_PRIZE_AWARDED_BODY_RE = re.compile(
    r"^Congratulations! You won a prize: (?P<prize_title>.+)\.$"
)


def localize_notification_text(text, language=None) -> str:
    """Translate notification text to the target language, defaulting to Arabic.

    PPW is an Arabic-first platform (default_language = 'ar').
    - If text is an English msgid (standard in PPW), it translates to Arabic (or requested lang).
    - If text is already localized Arabic text or custom arbitrary text, gettext returns it as-is.
    - If language is not provided, defaults to 'ar' (Arabic-first platform default).
    """
    if not text:
        return ""

    target_lang = (language or "ar").split("-")[0]
    if target_lang not in {"ar", "en"}:
        target_lang = "ar"

    with translation.override(target_lang):
        translated = str(_(text))
        if translated != text or target_lang == "en":
            return translated

        points_match = _POINTS_EARNED_BODY_RE.match(text)
        if points_match:
            return str(_("Congratulations! You earned %(points)s PP Coins.")) % {
                "points": points_match.group("points"),
            }

        badge_match = _BADGE_AWARDED_BODY_RE.match(text)
        if badge_match:
            badge_name = str(_(badge_match.group("badge_name")))
            return str(
                _("Congratulations! You earned the %(badge_name)s badge.")
            ) % {"badge_name": badge_name}

        prize_match = _PRIZE_AWARDED_BODY_RE.match(text)
        if prize_match:
            prize_title = str(_(prize_match.group("prize_title")))
            return str(
                _("Congratulations! You won a prize: %(prize_title)s.")
            ) % {"prize_title": prize_title}

        return translated


class NotificationPayload:
    """Canonical request-independent representation for WebSocket and FCM payloads."""

    @staticmethod
    def build(notification, language=None) -> dict:
        actor_data = None
        actor = getattr(notification, "actor_profile", None)
        if actor is not None:
            actor_data = {
                "id": actor.pk,
                "type": actor.type,
            }
            nickname = getattr(actor, "nickname", "")
            if nickname:
                actor_data["nickname"] = nickname

        created_iso = (
            notification.created.isoformat() if notification.created else None
        )
        read_at_iso = (
            notification.read_at.isoformat() if notification.read_at else None
        )

        return {
            "id": notification.pk,
            "notification_type": notification.notification_type,
            "title": localize_notification_text(notification.title, language),
            "body": localize_notification_text(notification.body, language),
            "actor_profile": actor_data,
            "object_type": notification.object_type,
            "object_id": notification.object_id,
            "metadata": notification.metadata or {},
            "target_url": notification.target_url,
            "is_read": bool(notification.is_read),
            "read_at": read_at_iso,
            "created": created_iso,
        }
