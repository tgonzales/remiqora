"""The app's own session: a signed cookie set once Adaflow's JWT has been verified.

A cookie, not the Authorization header, because the page plays audio through <audio src="/api/...">, which
cannot send a header, and because every same-origin request then carries it without the upstream frontend
knowing that a login exists. It is this server's cookie on this server's origin; Adaflow's token itself is
never put in a cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie

COOKIE_NAME = "nix_session"


@dataclass(frozen=True)
class SessionUser:
    sub: str
    email: str | None = None
    name: str | None = None
    organization_id: str | None = None
    role: str | None = None

    def public(self) -> dict[str, str | None]:
        return {"id": self.sub, "email": self.email, "name": self.name, "organizationId": self.organization_id, "role": self.role}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, body: str) -> str:
    return _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())


def seal(user: SessionUser, secret: bytes, ttl_sec: int, now: float | None = None) -> str:
    payload = {**asdict(user), "exp": int((now if now is not None else time.time()) + ttl_sec)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(secret, body)}"


def unseal(value: str, secret: bytes, now: float | None = None) -> SessionUser | None:
    body, _, signature = value.partition(".")
    if not body or not signature or not hmac.compare_digest(signature, _sign(secret, body)):
        return None
    try:
        payload = json.loads(_unb64(body))
        if not isinstance(payload, dict) or (now if now is not None else time.time()) > payload.pop("exp"):
            return None
        return SessionUser(**payload)
    except (ValueError, KeyError, TypeError):
        return None


def read_cookie(header: str | None, secret: bytes) -> SessionUser | None:
    if not header:
        return None
    try:
        morsel = SimpleCookie(header).get(COOKIE_NAME)
    except Exception:  # http.cookies raises CookieError on a malformed header
        return None
    return unseal(morsel.value, secret) if morsel else None


def set_cookie(value: str, ttl_sec: int, secure: bool) -> str:
    # Lax: the cookie is not sent on cross-site POSTs, which is the CSRF defence together with the Origin check.
    return f"{COOKIE_NAME}={value}; Path=/; Max-Age={ttl_sec}; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def clear_cookie(secure: bool) -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
