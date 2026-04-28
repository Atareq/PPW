from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.settings import api_settings


class ProfileSyncJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = (
        "apps.authentication.authentication.ProfileSyncJWTAuthentication"
    )
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }


class InternalSchemaAPIView(SpectacularAPIView):
    authentication_classes = api_settings.DEFAULT_AUTHENTICATION_CLASSES

    def get_permissions(self):
        if settings.DEBUG:
            return [AllowAny()]
        return [IsAuthenticated()]


class InternalSwaggerUIView(SpectacularSwaggerView):
    permission_classes = [AllowAny]
