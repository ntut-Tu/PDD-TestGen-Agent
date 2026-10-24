"""PDD login JWT verification."""

from __future__ import annotations

import base64

import jwt

from pdd_agent.api.errors import ApiError
from pdd_agent.domain.jobs import Principal


class JwtVerifier:
    """Verifies HS256 tokens signed by the PDD backend."""

    def __init__(self, secret: str) -> None:
        # The legacy backend signs with the base64url text of the secret as raw key bytes.
        self._key = base64.urlsafe_b64encode(secret.encode())

    def verify(self, token: str) -> Principal:
        """Return the caller of ``token`` or raise a 401 ``ApiError``."""
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["HS256"],
                options={"require": ["exp"]},
            )
        except jwt.PyJWTError as exc:
            raise ApiError(401, "unauthorized", f"invalid token: {exc}") from exc
        account = claims.get("sub")
        if not isinstance(account, str) or not account or not claims.get("oauth"):
            raise ApiError(401, "unauthorized", "invalid token: missing sub or oauth claim")
        return Principal(account=account, token=token)


def bearer_token(header: str | None) -> str:
    """Extract the token of an ``Authorization: Bearer`` header or raise a 401 ``ApiError``."""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "missing bearer token")
    return token.strip()
