"""Entry point of the cloud server: `uvicorn nix.main:app` in place of upstream's `app.main:app`.

With NIX_SSO unset this serves exactly what upstream serves (plus /api/nix/auth/config answering
{"enabled": false}), so it is safe to start by mistake; the desktop app and local installs keep upstream's entry.
"""
from __future__ import annotations

import logging

from app.main import app as upstream_app

from .gate import SsoGate
from .settings import Settings

settings = Settings.from_env()
if settings.enabled:
    logging.getLogger("nix.auth").warning(
        "sign-in with Adaflow is ON (%s)%s", settings.adaflow_url,
        "" if settings.allowed_organizations else "; any Adaflow user may enter (NIX_SSO_ALLOWED_ORGS is empty)",
    )

app = SsoGate(upstream_app, settings)
