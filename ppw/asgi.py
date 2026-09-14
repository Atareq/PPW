"""ASGI config for PPW HTTP and notification WebSockets."""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ppw.settings")

django_asgi_app = get_asgi_application()

from apps.notifications.routing import websocket_urlpatterns  # noqa: E402
from apps.notifications.websocket import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns),
        ),
    }
)
