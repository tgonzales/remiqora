"""NiX Studio's cloud mode: sign-in with Adaflow (SSO) in front of the upstream app.

Nothing here is used by the desktop app or a local install: those start `app.main:app` and have no login.
A cloud server starts `nix.main:app` with NIX_SSO=1 (see brand/README.md).
"""
