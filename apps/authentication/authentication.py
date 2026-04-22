from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.authentication.services import switch_user_profile


class ProfileSyncJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None

        user, validated_token = authenticated
        token_profile = validated_token.get("profile")

        if not token_profile:
            raise AuthenticationFailed(
                "Token profile claim is required.",
                code="token_profile_missing",
            )
        if user.role != token_profile:
            switch_user_profile(user, new_profile_type=token_profile)

        return user, validated_token
