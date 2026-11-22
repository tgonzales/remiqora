"""Server-side check of a JWT issued by Adaflow: a port of `createJwtVerifier` from @adaflow/sdk (jwt-verifier.ts).

Adaflow signs with EdDSA (Ed25519) and publishes its public keys as a JWKS. Only identity is established here:
signature, validity and, when configured, issuer and audience.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class TokenError(Exception):
    """The token was refused. `code` says why without exposing the token."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JwksUnavailable(TokenError):
    """Adaflow's keys could not be fetched. This is an outage (503), never a dead session (401): the SDK's
    guide warns that a false 401 here signs the user out and feeds a redirect loop."""

    def __init__(self, message: str):
        super().__init__("jwks_unavailable", message)


@dataclass(frozen=True)
class Identity:
    sub: str
    expires_at: float
    email: str | None = None
    name: str | None = None
    organization_id: str | None = None
    role: str | None = None


def _b64url(segment: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise TokenError("malformed", "Malformed token.") from exc


def _json_segment(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_b64url(segment))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TokenError("malformed", "Malformed token.") from exc
    if not isinstance(value, dict):
        raise TokenError("malformed", "Malformed token.")
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


async def _fetch_jwks(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers={"accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


class JwtVerifier:
    """Caches the JWKS. One per process."""

    def __init__(
        self,
        jwks_url: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        clock_tolerance_sec: float = 5,
        cache_ttl_sec: float = 600,
        refetch_cooldown_sec: float = 30,
        fetch: Callable[[str], Awaitable[dict[str, Any]]] = _fetch_jwks,
        now: Callable[[], float] = time.time,
    ):
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._tolerance = clock_tolerance_sec
        self._cache_ttl = cache_ttl_sec
        self._cooldown = refetch_cooldown_sec
        self._fetch = fetch
        self._now = now
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def _refresh(self, seen_at: float) -> None:
        async with self._lock:
            if self._fetched_at != seen_at:
                return  # another request refreshed while this one waited
            try:
                body = await self._fetch(self._jwks_url)
            except Exception as exc:  # network, HTTP status, bad JSON: all the same outage
                raise JwksUnavailable("Adaflow's JWKS is unavailable.") from exc
            keys: dict[str, Ed25519PublicKey] = {}
            for jwk in (body.get("keys") if isinstance(body, dict) else None) or []:
                # Ed25519 only: a token with any other algorithm never finds a key to verify against.
                if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not jwk.get("x"):
                    continue
                try:
                    keys[jwk.get("kid") or ""] = Ed25519PublicKey.from_public_bytes(_b64url(jwk["x"]))
                except (TokenError, ValueError):
                    continue
            self._keys = keys
            self._fetched_at = self._now()

    async def _key_for(self, kid: str) -> Ed25519PublicKey:
        if self._now() - self._fetched_at > self._cache_ttl:
            await self._refresh(self._fetched_at)
        key = self._keys.get(kid)
        # Key rotation: a new kid is not in the cache yet. The cooldown keeps forged tokens with random
        # kids from turning into a burst of requests to Adaflow.
        if key is None and self._now() - self._fetched_at > self._cooldown:
            await self._refresh(self._fetched_at)
            key = self._keys.get(kid)
        if key is None:
            raise TokenError("unknown_key", "The token's key is not published in the JWKS.")
        return key

    async def verify(self, token: str) -> Identity:
        parts = token.split(".") if isinstance(token, str) else []
        if len(parts) != 3 or not all(parts):
            raise TokenError("malformed", "Malformed token.")
        header_seg, payload_seg, signature_seg = parts
        header = _json_segment(header_seg)
        # A fixed algorithm: otherwise an HS256 token signed with a guessable secret would pass.
        if header.get("alg") != "EdDSA":
            raise TokenError("unsupported_alg", "Token algorithm not accepted (EdDSA expected).")
        key = await self._key_for(_text(header.get("kid")) or "")
        try:
            key.verify(_b64url(signature_seg), f"{header_seg}.{payload_seg}".encode())
        except InvalidSignature as exc:
            raise TokenError("invalid_signature", "Invalid token signature.") from exc

        claims = _json_segment(payload_seg)
        now = self._now()
        exp = _number(claims.get("exp"))
        if exp is None or now > exp + self._tolerance:
            raise TokenError("expired", "Token expired.")
        nbf = _number(claims.get("nbf"))
        if nbf is not None and now < nbf - self._tolerance:
            raise TokenError("not_yet_valid", "Token not valid yet.")
        if self._issuer is not None and claims.get("iss") != self._issuer:
            raise TokenError("invalid_issuer", "Token issuer does not match.")
        if self._audience is not None:
            aud = claims.get("aud")
            if self._audience not in (aud if isinstance(aud, list) else [aud]):
                raise TokenError("invalid_audience", "Token audience does not match.")
        sub = _text(claims.get("sub"))
        if not sub:
            raise TokenError("malformed", "Token without sub.")
        email = _text(claims.get("email"))
        return Identity(
            sub=sub,
            expires_at=exp,
            email=email.lower() if email else None,
            name=_text(claims.get("name")),
            organization_id=_text(claims.get("organizationId")),
            role=_text(claims.get("role")),
        )
