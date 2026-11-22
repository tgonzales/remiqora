"""The gate in front of the upstream app, as plain ASGI: upstream's routes are not touched or even imported here.

    /api/nix/auth/*            answered here (config, session, logout, the relay to Adaflow's session routes)
    /api/*, /docs, /openapi…   need the session cookie when NIX_SSO is on
    everything else            the built page (it must load to show the sign-in screen)
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .jwt_verifier import JwksUnavailable, JwtVerifier, TokenError
from .sessions import SessionUser, clear_cookie, read_cookie, seal, set_cookie
from .settings import Settings

log = logging.getLogger("nix.auth")

AUTH_PREFIX = "/api/nix/auth"
PROTECTED = ("/api/", "/docs", "/redoc", "/openapi.json")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
# The only gateway routes the browser SDK (createSsoSession) calls: code → 7-day session, session → fresh JWT, sign-out.
RELAYED = {
    ("POST", "v1/auth/partner/handoff/exchange"),
    ("GET", "v1/auth/token"),
    ("POST", "v1/auth/sign-out"),
}


def _host(request_headers: dict[bytes, bytes]) -> str:
    return (request_headers.get(b"x-forwarded-host") or request_headers.get(b"host") or b"").decode("latin-1").split(",")[0].strip()


def _is_local(host: str) -> bool:
    return host.rsplit(":", 1)[0] in LOCAL_HOSTS or host in LOCAL_HOSTS


def build_auth_app(settings: Settings, verifier: JwtVerifier, http: httpx.AsyncClient | None = None) -> FastAPI:
    auth = FastAPI(title="NiX Studio sign-in", docs_url=None, redoc_url=None, openapi_url=None)

    def secure(request: Request) -> bool:
        # http is only ever right on a developer's machine; everywhere else the cookie is https-only
        return not _is_local(_host(dict(request.scope["headers"])))

    def origin(request: Request) -> str:
        if settings.public_url:
            return settings.public_url
        host = _host(dict(request.scope["headers"]))
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        return f"{proto}://{host}"

    @auth.get("/config")
    async def config(request: Request):
        if not settings.enabled:
            return {"enabled": False}
        user = read_cookie(request.headers.get("cookie"), settings.session_secret)
        return {"enabled": True, "adaflowUrl": settings.adaflow_url, "user": user.public() if user else None}

    @auth.post("/session")
    async def open_session(request: Request):
        """Adaflow's JWT in, this server's session cookie out. Called at sign-in and again to keep the session alive."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse({"error": "missing_token"}, status_code=401)
        try:
            identity = await verifier.verify(token.strip())
        except JwksUnavailable:
            return JSONResponse({"error": "jwks_unavailable"}, status_code=503)
        except TokenError as exc:
            return JSONResponse({"error": exc.code}, status_code=401)
        if settings.allowed_organizations and identity.organization_id not in settings.allowed_organizations:
            log.warning("sign-in refused: organization %s is not allowed", identity.organization_id)
            return JSONResponse({"error": "organization_not_allowed"}, status_code=403, headers={"set-cookie": clear_cookie(secure(request))})
        user = SessionUser(identity.sub, identity.email, identity.name, identity.organization_id, identity.role)
        cookie = set_cookie(seal(user, settings.session_secret, settings.session_ttl_sec), settings.session_ttl_sec, secure(request))
        return JSONResponse({"user": user.public(), "expiresIn": settings.session_ttl_sec}, headers={"set-cookie": cookie})

    @auth.post("/logout")
    async def logout(request: Request):
        return JSONResponse({"ok": True}, headers={"set-cookie": clear_cookie(secure(request))})

    @auth.api_route("/gw/{path:path}", methods=["GET", "POST"])
    async def relay(path: str, request: Request):
        """Relays the SDK's three session routes to the gateway, so the browser needs no cross-origin access to it.
        The guide asks a relaying app to keep the browser's Origin; a same-origin GET has none, so ours is sent."""
        if not settings.enabled or (request.method, path) not in RELAYED:
            return JSONResponse({"error": "not_found"}, status_code=404)
        headers = {"origin": request.headers.get("origin") or origin(request), "accept": "application/json"}
        for name in ("authorization", "content-type"):
            if name in request.headers:
                headers[name] = request.headers[name]
        try:
            client = http or httpx.AsyncClient(timeout=10.0)
            try:
                upstream = await client.request(request.method, f"{settings.gateway_url}/{path}", headers=headers, content=await request.body())
            finally:
                if http is None:
                    await client.aclose()
        except httpx.HTTPError:
            return JSONResponse({"error": "gateway_unavailable"}, status_code=503)
        return Response(upstream.content, status_code=upstream.status_code, media_type=upstream.headers.get("content-type", "application/json"), headers={"cache-control": "no-store"})

    return auth


class SsoGate:
    def __init__(self, app, settings: Settings, verifier: JwtVerifier | None = None, http: httpx.AsyncClient | None = None):
        self.app = app
        self.settings = settings
        verifier = verifier or JwtVerifier(settings.jwks_url, issuer=settings.issuer, audience=settings.audience)
        self.auth = build_auth_app(settings, verifier, http)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path: str = scope["path"]
        if path == AUTH_PREFIX or path.startswith(AUTH_PREFIX + "/"):
            return await self.auth({**scope, "path": path[len(AUTH_PREFIX):] or "/", "root_path": scope.get("root_path", "") + AUTH_PREFIX}, receive, send)
        if not self.settings.enabled or not path.startswith(PROTECTED):
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        user = read_cookie(headers.get(b"cookie", b"").decode("latin-1"), self.settings.session_secret)
        if user is None:
            return await self._refuse(scope, send, 401, "not_signed_in")
        # A browser sends Origin on every cross-site request that can change something; it must be this site.
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin and scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS") and not self._same_site(origin, headers):
            return await self._refuse(scope, send, 403, "cross_site_request")
        scope.setdefault("state", {})["nix_user"] = user
        return await self.app(scope, receive, send)

    def _same_site(self, origin: str, headers: dict[bytes, bytes]) -> bool:
        if self.settings.public_url and origin == self.settings.public_url:
            return True
        return urlsplit(origin).netloc == _host(headers)

    @staticmethod
    async def _refuse(scope, send, status: int, error: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"error": error}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
