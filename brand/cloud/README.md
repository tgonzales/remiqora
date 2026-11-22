# NiX Studio as a cloud server: sign-in with Adaflow

The desktop app and local installs have no login and are not affected by anything here. A cloud server starts
another entry point, `nix.main:app`, which puts a gate in front of the upstream app: nothing under `/api/` answers
without a session, and a session is opened only with a valid Adaflow token.

```sh
./setup_linux.sh                         # upstream: engines, models, backend/.venv (once)
NIX_PUBLIC_URL=https://nix-studio.adalink.app \
NIX_SSO_SESSION_SECRET="$(openssl rand -hex 32)" \
brand/cloud/serve.sh                     # keep the secret: a new one signs everybody out
```

Serve it over https on a `*.adalink.app` / `*.adalink.ai` host: Adaflow only hands a token to those (and to
`http://localhost` / `127.0.0.1`, for development). No app registration is needed.

## How it works

1. The page loads `brand/auth/gate.ts` before the app. It asks `GET /api/nix/auth/config`; with sign-in on and
   no session, it shows "Sign in with Adaflow" and the app is not loaded at all.
2. The button sends the browser to Adaflow (`/sso/handoff?redirect-url=…/auth/callback`). Adaflow returns with
   the token in the URL fragment; `@adaflow/sdk` (`createSsoSession`) reads it, clears the URL and keeps it in
   `sessionStorage`.
3. The page posts the token once to `POST /api/nix/auth/session`. The server verifies it against Adaflow's
   published keys (EdDSA, `backend/nix/jwt_verifier.py`) and answers with its own cookie: `HttpOnly`,
   `SameSite=Lax`, `Secure`, 30 minutes.
4. Every request of the app carries that cookie by itself, audio players included, which is why no upstream file
   had to learn about tokens. The page renews the cookie every 10 minutes with a fresh Adaflow token; the SDK
   gets that token from its 7-day session without leaving the page.
5. When Adaflow no longer renews (signed out there, 7 days over), the user is back at the sign-in screen.

## Settings

| Variable | Default | |
|---|---|---|
| `NIX_SSO` | off | `1` turns sign-in on. `serve.sh` sets it. |
| `NIX_PUBLIC_URL` | from the request | This server's public address. Set it behind a proxy. |
| `NIX_SSO_SESSION_SECRET` | random at start | Signs the cookie. Set it, or a restart signs everybody out. |
| `NIX_SSO_ALLOWED_ORGS` | empty: any Adaflow user | Comma-separated Adaflow organization ids that may enter. |
| `NIX_SSO_ADAFLOW_URL` | `https://adaflow.adalink.ai` | The identity provider. |
| `NIX_SSO_GATEWAY_URL` | `https://zeus.adalink.app` | The gateway the session routes are relayed to. |
| `NIX_SSO_JWKS_URL` | `<adaflow>/v1/auth/jwks` | Adaflow's public keys. |
| `NIX_SSO_ISSUER`, `NIX_SSO_AUDIENCE` | not checked | Set them once Adalink confirms the values. |
| `NIX_SSO_SESSION_TTL` | `1800` | Cookie lifetime in seconds. |
| `NIX_TRUSTED_PROXIES` | `127.0.0.1` | Addresses whose `X-Forwarded-*` headers are trusted. |

## What sign-in does not do

Sign-in decides who may enter; it does not separate people. The upstream app is single-user: everyone who signs
in shares one library, one set of projects and settings, and one GPU queue, and can use every feature, including
the ones that read folders on the server (LoRA datasets) and that download or delete models. Run one server per
customer or team, and use `NIX_SSO_ALLOWED_ORGS` to keep it to them.

## Tests

```sh
cd backend && .venv/bin/python3 -m unittest discover -s nix/tests -t .
```

They cover the token checks (algorithm, signature, expiry, key rotation, an Adaflow outage answering 503 and not
401), the cookie, the gate and the relay. The flow against the real Adaflow has to be tried on the deployed host.
