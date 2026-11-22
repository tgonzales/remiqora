from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str) -> frozenset[str]:
    return frozenset(part.strip() for part in os.getenv(name, "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Read once from the environment; every NIX_SSO_* variable is optional except NIX_SSO itself."""

    enabled: bool = False
    # Adaflow, the identity provider: where the browser is sent to sign in.
    adaflow_url: str = "https://adaflow.adalink.ai"
    # The platform's API gateway: the session routes (/v1/auth/*) are relayed to it.
    gateway_url: str = "https://zeus.adalink.app"
    jwks_url: str = "https://adaflow.adalink.ai/v1/auth/jwks"
    issuer: str | None = None
    audience: str | None = None
    # Empty: any Adaflow user may enter. Otherwise only members of these organizations.
    allowed_organizations: frozenset[str] = frozenset()
    # This server's public address (https://nix-studio.adalink.app). Used as the Origin of relayed requests.
    public_url: str | None = None
    session_ttl_sec: int = 30 * 60
    # Signs the session cookie. Without NIX_SSO_SESSION_SECRET a random one is made at start, which signs
    # everybody out on a restart and does not work with more than one server process.
    session_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        adaflow = os.getenv("NIX_SSO_ADAFLOW_URL", cls.adaflow_url).rstrip("/")
        secret = os.getenv("NIX_SSO_SESSION_SECRET", "")
        return cls(
            enabled=_flag("NIX_SSO"),
            adaflow_url=adaflow,
            gateway_url=os.getenv("NIX_SSO_GATEWAY_URL", cls.gateway_url).rstrip("/"),
            jwks_url=os.getenv("NIX_SSO_JWKS_URL", f"{adaflow}/v1/auth/jwks"),
            issuer=os.getenv("NIX_SSO_ISSUER") or None,
            audience=os.getenv("NIX_SSO_AUDIENCE") or None,
            allowed_organizations=_list("NIX_SSO_ALLOWED_ORGS"),
            public_url=(os.getenv("NIX_PUBLIC_URL") or "").rstrip("/") or None,
            session_ttl_sec=int(os.getenv("NIX_SSO_SESSION_TTL", str(cls.session_ttl_sec))),
            session_secret=secret.encode() if secret else secrets.token_bytes(32),
        )
