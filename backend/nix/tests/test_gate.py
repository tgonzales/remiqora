"""python -m unittest discover -s nix/tests -t .   (from backend/, with nix/requirements.txt installed)"""
from __future__ import annotations

import base64
import json
import time
import unittest

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from nix.gate import SsoGate
from nix.jwt_verifier import JwksUnavailable, JwtVerifier, TokenError
from nix.sessions import COOKIE_NAME, SessionUser, seal, unseal
from nix.settings import Settings


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Issuer:
    """A stand-in for Adaflow: signs tokens and publishes the matching JWKS."""

    def __init__(self, kid: str = "k1"):
        self.kid = kid
        self.key = Ed25519PrivateKey.generate()
        self.fetches = 0
        self.down = False

    def jwks(self) -> dict:
        x = b64(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        return {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": self.kid, "x": x}, {"kty": "RSA", "kid": "rsa", "n": "x", "e": "AQAB"}]}

    async def fetch(self, _url: str) -> dict:
        self.fetches += 1
        if self.down:
            raise httpx.ConnectError("down")
        return self.jwks()

    def token(self, *, alg: str = "EdDSA", kid: str | None = None, **claims) -> str:
        payload = {"sub": "user-1", "email": "Ana@Example.com", "name": "Ana", "organizationId": "org-1", "role": "ADMIN", "exp": time.time() + 900, **claims}
        head = b64(json.dumps({"alg": alg, "kid": kid or self.kid}).encode())
        body = b64(json.dumps(payload).encode())
        return f"{head}.{body}.{b64(self.key.sign(f'{head}.{body}'.encode()))}"


class VerifierTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.issuer = Issuer()
        self.verifier = JwtVerifier("https://idp/jwks", fetch=self.issuer.fetch)

    async def code(self, token: str, verifier: JwtVerifier | None = None) -> str:
        with self.assertRaises(TokenError) as ctx:
            await (verifier or self.verifier).verify(token)
        return ctx.exception.code

    async def test_valid_token_gives_the_identity(self):
        identity = await self.verifier.verify(self.issuer.token())
        self.assertEqual((identity.sub, identity.email, identity.organization_id, identity.role), ("user-1", "ana@example.com", "org-1", "ADMIN"))

    async def test_jwks_is_cached(self):
        for _ in range(3):
            await self.verifier.verify(self.issuer.token())
        self.assertEqual(self.issuer.fetches, 1)

    async def test_refusals(self):
        good = self.issuer.token()
        head, body, sig = good.split(".")
        self.assertEqual(await self.code("not-a-token"), "malformed")
        self.assertEqual(await self.code(self.issuer.token(alg="HS256")), "unsupported_alg")
        self.assertEqual(await self.code(self.issuer.token(alg="none")), "unsupported_alg")
        self.assertEqual(await self.code(self.issuer.token(exp=time.time() - 60)), "expired")
        self.assertEqual(await self.code(self.issuer.token(nbf=time.time() + 600)), "not_yet_valid")
        self.assertEqual(await self.code(self.issuer.token(kid="other")), "unknown_key")
        self.assertEqual(await self.code(Issuer().token()), "invalid_signature")  # right kid, someone else's key
        tampered = b64(json.dumps({**json.loads(base64.urlsafe_b64decode(body + "==")), "role": "OWNER"}).encode())
        self.assertEqual(await self.code(f"{head}.{tampered}.{sig}"), "invalid_signature")

    async def test_issuer_and_audience_when_configured(self):
        strict = JwtVerifier("https://idp/jwks", issuer="adaflow", audience="nix", fetch=self.issuer.fetch)
        self.assertEqual(await self.code(self.issuer.token(iss="evil", aud="nix"), strict), "invalid_issuer")
        self.assertEqual(await self.code(self.issuer.token(iss="adaflow", aud="other"), strict), "invalid_audience")
        await strict.verify(self.issuer.token(iss="adaflow", aud=["x", "nix"]))

    async def test_outage_is_not_a_bad_token(self):
        self.issuer.down = True
        with self.assertRaises(JwksUnavailable):
            await self.verifier.verify(self.issuer.token())

    async def test_unknown_kids_do_not_hammer_the_jwks(self):
        await self.verifier.verify(self.issuer.token())
        for i in range(5):
            await self.code(self.issuer.token(kid=f"forged-{i}"))
        self.assertEqual(self.issuer.fetches, 1)  # inside the cooldown

    async def test_key_rotation_is_picked_up_after_the_cooldown(self):
        clock = [1000.0]
        verifier = JwtVerifier("https://idp/jwks", fetch=self.issuer.fetch, now=lambda: clock[0])
        await verifier.verify(self.issuer.token(exp=5000))
        self.issuer.kid = "k2"
        clock[0] += 31
        await verifier.verify(self.issuer.token(exp=5000))
        self.assertEqual(self.issuer.fetches, 2)


class CookieTest(unittest.TestCase):
    def test_round_trip_tamper_and_expiry(self):
        user = SessionUser("u", "a@b.c", "A", "org", "ADMIN")
        value = seal(user, b"secret", 60, now=100)
        self.assertEqual(unseal(value, b"secret", now=120), user)
        self.assertIsNone(unseal(value, b"secret", now=161))
        self.assertIsNone(unseal(value, b"other", now=120))
        body, sig = value.split(".")
        forged = b64(json.dumps({"sub": "admin", "exp": 10**12}).encode())
        self.assertIsNone(unseal(f"{forged}.{sig}", b"secret", now=120))
        self.assertIsNone(unseal("garbage", b"secret"))


async def upstream(scope, receive, send):
    """What the gate protects: answers 200 with the path, so a test sees whether the request got through."""
    assert scope["type"] == "http"
    body = json.dumps({"path": scope["path"], "user": getattr(scope.get("state", {}).get("nix_user"), "sub", None)}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class GateTest(unittest.IsolatedAsyncioTestCase):
    def make(self, **overrides):
        self.issuer = Issuer()
        self.gateway_calls: list[httpx.Request] = []

        def gateway(request: httpx.Request) -> httpx.Response:
            self.gateway_calls.append(request)
            return httpx.Response(200, json={"token": "fresh"})

        settings = Settings(enabled=True, session_secret=b"s" * 32, gateway_url="https://gw", **overrides)
        verifier = JwtVerifier(settings.jwks_url, fetch=self.issuer.fetch)
        gate = SsoGate(upstream, settings, verifier, httpx.AsyncClient(transport=httpx.MockTransport(gateway)))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="https://nix.adalink.app")

    async def sign_in(self, client: httpx.AsyncClient, **claims) -> httpx.Response:
        return await client.post("/api/nix/auth/session", headers={"authorization": f"Bearer {self.issuer.token(**claims)}"})

    async def test_api_is_closed_and_the_page_is_open(self):
        async with self.make() as client:
            for path in ("/api/tracks", "/api/orchestrator/status", "/openapi.json", "/docs"):
                self.assertEqual((await client.get(path)).status_code, 401, path)
            for path in ("/", "/assets/index.js", "/auth/callback", "/apiary"):
                self.assertEqual((await client.get(path)).status_code, 200, path)
            config = (await client.get("/api/nix/auth/config")).json()
            self.assertEqual(config, {"enabled": True, "adaflowUrl": "https://adaflow.adalink.ai", "user": None})

    async def test_sign_in_opens_the_api(self):
        async with self.make() as client:
            resp = await self.sign_in(client)
            self.assertEqual(resp.status_code, 200)
            cookie = resp.headers["set-cookie"]
            for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
                self.assertIn(flag, cookie)
            self.assertEqual((await client.get("/api/tracks")).json(), {"path": "/api/tracks", "user": "user-1"})
            self.assertEqual((await client.get("/api/nix/auth/config")).json()["user"]["email"], "ana@example.com")
            await client.post("/api/nix/auth/logout")
            client.cookies.clear()
            self.assertEqual((await client.get("/api/tracks")).status_code, 401)

    async def test_bad_tokens_do_not_sign_in(self):
        async with self.make() as client:
            self.assertEqual((await client.post("/api/nix/auth/session")).status_code, 401)
            self.assertEqual((await self.sign_in(client, exp=time.time() - 60)).json(), {"error": "expired"})
            resp = await client.post("/api/nix/auth/session", headers={"authorization": f"Bearer {Issuer().token()}"})
            self.assertEqual((resp.status_code, resp.json()), (401, {"error": "invalid_signature"}))
            self.assertNotIn(COOKIE_NAME, client.cookies)

    async def test_adaflow_outage_is_503_not_401(self):
        async with self.make() as client:
            self.issuer.down = True
            self.assertEqual((await self.sign_in(client)).status_code, 503)

    async def test_organization_allowlist(self):
        async with self.make(allowed_organizations=frozenset({"org-9"})) as client:
            self.assertEqual((await self.sign_in(client)).status_code, 403)
            self.assertEqual((await client.get("/api/tracks")).status_code, 401)
            self.assertEqual((await self.sign_in(client, organizationId="org-9")).status_code, 200)

    async def test_forged_cookie_is_refused(self):
        async with self.make() as client:
            forged = seal(SessionUser("admin"), b"wrong-secret", 600)
            self.assertEqual((await client.get("/api/tracks", headers={"cookie": f"{COOKIE_NAME}={forged}"})).status_code, 401)

    async def test_cross_site_writes_are_refused(self):
        async with self.make() as client:
            await self.sign_in(client)
            self.assertEqual((await client.post("/api/tracks", headers={"origin": "https://evil.example"})).status_code, 403)
            self.assertEqual((await client.post("/api/tracks", headers={"origin": "https://nix.adalink.app"})).status_code, 200)
            self.assertEqual((await client.post("/api/tracks")).status_code, 200)

    async def test_only_the_sdk_session_routes_are_relayed(self):
        async with self.make(public_url="https://nix.adalink.app") as client:
            resp = await client.get("/api/nix/auth/gw/v1/auth/token", headers={"authorization": "Bearer session-token"})
            self.assertEqual(resp.json(), {"token": "fresh"})
            sent = self.gateway_calls[0]
            self.assertEqual((str(sent.url), sent.headers["authorization"], sent.headers["origin"]), ("https://gw/v1/auth/token", "Bearer session-token", "https://nix.adalink.app"))
            self.assertEqual((await client.post("/api/nix/auth/gw/v1/auth/partner/handoff/exchange", json={"code": "c"})).status_code, 200)
            for method, path in (("GET", "v1/autonomous-agents"), ("POST", "v1/auth/token"), ("GET", "v1/auth/../../admin")):
                self.assertEqual((await client.request(method, f"/api/nix/auth/gw/{path}")).status_code, 404, path)
            self.assertEqual(len(self.gateway_calls), 2)

    async def test_switched_off_everything_passes(self):
        gate = SsoGate(upstream, Settings(enabled=False))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://127.0.0.1:9000") as client:
            self.assertEqual((await client.get("/api/tracks")).status_code, 200)
            self.assertEqual((await client.get("/api/nix/auth/config")).json(), {"enabled": False})
            self.assertEqual((await client.get("/api/nix/auth/gw/v1/auth/token")).status_code, 404)

    async def test_local_http_gets_a_cookie_the_browser_will_keep(self):
        gate = SsoGate(upstream, Settings(enabled=True, session_secret=b"s" * 32), JwtVerifier("x", fetch=(issuer := Issuer()).fetch))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://127.0.0.1:9000") as client:
            resp = await client.post("/api/nix/auth/session", headers={"authorization": f"Bearer {issuer.token()}"})
            self.assertNotIn("Secure", resp.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()
