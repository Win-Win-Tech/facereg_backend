from typing import Optional, Tuple

from rest_framework import authentication, exceptions

from .models import AuthToken, User


class SimpleTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Token"

    def authenticate(self, request) -> Optional[Tuple[User, AuthToken]]:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Invalid token header.")

        token_key = parts[1]
        try:
            token = AuthToken.objects.select_related("user").get(key=token_key)
        except AuthToken.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid or expired token.")

        user = token.user
        if not user.is_active or user.is_deleted:
            raise exceptions.AuthenticationFailed("User inactive or deleted.")

        return user, token

