"""Tests for M03.2/M03.3 OIDC Authorization Code + PKCE bridge and session/CSRF security."""

from __future__ import annotations

import base64
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from app.api import auth as auth_api
from app.context import RequestContextMiddleware, get_principal
from app.context.providers import OidcSessionRequestContextProvider
from app.services.oidc import OidcBridge, OidcError, validate_oidc_url
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from yonder_configuration import ConfigurationError, validate_settings
from yonder_configuration.models import (
    OidcConfig,
    SecurityConfig,
    SecurityMode,
    YonderEnvironment,
    YonderSettings,
)

SESSION_SECRET = "s" * 40


def _rsa_jwk_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = private_key.public_key().public_numbers()

    def _int_to_b64(val: int) -> str:
        raw = val.to_bytes((val.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    jwk = {
        "kty": "RSA",
        "kid": "test-kid-1",
        "use": "sig",
        "alg": "RS256",
        "n": _int_to_b64(pub_numbers.n),
        "e": _int_to_b64(pub_numbers.e),
    }
    return private_key, jwk


def test_validate_oidc_url_rejects_insecure_or_private_targets() -> None:
    assert validate_oidc_url("https://idp.school.org/application/o/yonder") == (
        "https://idp.school.org/application/o/yonder"
    )
    for bad in (
        "",
        "http://idp.school.org",
        "https://user:pass@idp.school.org",
        "https://idp.school.org/#frag",
        "https://127.0.0.1/auth",
        "https://10.0.0.5/auth",
        "https://localhost/auth",
    ):
        with pytest.raises(OidcError):
            validate_oidc_url(bad, allow_insecure_http=False)
    assert validate_oidc_url("http://localhost:9000/auth", allow_insecure_http=True) == (
        "http://localhost:9000/auth"
    )


def test_oidc_validation_rules() -> None:
    bad = YonderSettings(
        environment=YonderEnvironment.PRODUCTION,
        security=SecurityConfig(
            mode=SecurityMode.OIDC,
            worker_shared_secret="w" * 32,
            oidc=OidcConfig(
                issuer="https://idp.example.org",
                client_id="yonder",
                redirect_uri="https://yonder.example.org/v1/auth/callback",
                session_secret=SESSION_SECRET,
                allow_insecure_http_in_dev=True,
            ),
        ),
        worker_registration={"mode": "allowlist", "allowed_worker_ids": ["w1"]},
    )
    with pytest.raises(ConfigurationError, match="allow_insecure_http_in_dev"):
        validate_settings(bad)


def _make_oidc_app(
    private_key,
    jwk: dict,
    *,
    token_overrides: dict | None = None,
    role_mappings: dict | None = None,
) -> tuple[FastAPI, dict[str, str]]:
    issuer = "https://idp.example.org/application/o/yonder"
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{issuer}/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/token",
                    "jwks_uri": f"{issuer}/jwks",
                },
            )
        if str(request.url) == f"{issuer}/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        if str(request.url) == f"{issuer}/token":
            now = int(time.time())
            claims = {
                "iss": issuer,
                "aud": "yonder-client",
                "sub": "user-123",
                "nonce": captured["nonce"],
                "groups": ["yonder-teachers", "unmapped-group"],
                "iat": now,
                "exp": now + 300,
            }
            if token_overrides:
                claims.update(token_overrides)
            id_token = jwt.encode(
                claims,
                private_key,
                algorithm="RS256",
                headers={"kid": jwk["kid"]},
            )
            return httpx.Response(200, json={"id_token": id_token, "token_type": "Bearer"})
        return httpx.Response(404)

    oidc_cfg = OidcConfig(
        provider_template="authentik",
        issuer=issuer,
        client_id="yonder-client",
        redirect_uri="https://yonder.example.org/v1/auth/callback",
        session_secret=SESSION_SECRET,
        role_mappings=(
            role_mappings if role_mappings is not None else {"yonder-teachers": ("teacher",)}
        ),
        allow_insecure_http_in_dev=True,  # allow TestClient HTTP cookies
    )
    security = SecurityConfig(mode=SecurityMode.OIDC, oidc=oidc_cfg)
    app = FastAPI()
    app.state.settings = YonderSettings(security=security)
    app.state.oidc_bridge = OidcBridge(
        oidc_cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    app.add_middleware(
        RequestContextMiddleware,
        provider=OidcSessionRequestContextProvider(security),
        service_name="test",
    )
    app.include_router(auth_api.router)

    @app.post("/v1/protected-action")
    async def protected_action(request: Request) -> dict[str, str]:
        return {"subject": get_principal(request).subject}

    # Capture nonce from login redirect for the mock IdP token endpoint.
    original_build = app.state.oidc_bridge.build_login_redirect

    async def wrapped_build():
        url, tx = await original_build()
        qs = parse_qs(urlsplit(url).query)
        captured["nonce"] = qs["nonce"][0]
        captured["state"] = qs["state"][0]
        return url, tx

    app.state.oidc_bridge.build_login_redirect = wrapped_build  # type: ignore[method-assign]
    return app, captured


def test_oidc_pkce_login_callback_session_and_csrf() -> None:
    private_key, jwk = _rsa_jwk_pair()
    app, captured = _make_oidc_app(private_key, jwk)
    client = TestClient(app)

    # Unauthenticated session check fails closed.
    assert client.get("/v1/auth/session").status_code == 401

    login = client.get("/v1/auth/login?mode=json")
    assert login.status_code == 200
    auth_url = login.json()["authorization_url"]
    qs = parse_qs(urlsplit(auth_url).query)
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs

    cb = client.get(f"/v1/auth/callback?code=auth-code-1&state={captured['state']}")
    assert cb.status_code == 200
    body = cb.json()
    assert body["authenticated"] is True
    assert body["subject"] == "user-123"
    assert body["roles"] == ["teacher"]
    assert "chat.use" in body["capabilities"]
    csrf = body["csrf_token"]

    sess = client.get("/v1/auth/session")
    assert sess.status_code == 200
    assert sess.json()["subject"] == "user-123"

    # Mutating request without CSRF token must be rejected with 403.
    assert client.post("/v1/protected-action").status_code == 403
    ok = client.post("/v1/protected-action", headers={"X-Yonder-CSRF-Token": csrf})
    assert ok.status_code == 200
    assert ok.json()["subject"] == "user-123"

    # Logout clears session.
    assert client.post("/v1/auth/logout", headers={"X-Yonder-CSRF-Token": csrf}).status_code == 200
    assert client.get("/v1/auth/session").status_code == 401


def test_oidc_rejects_bad_nonce_audience_and_unmapped_groups_default_deny() -> None:
    private_key, jwk = _rsa_jwk_pair()
    bad_nonce_app, captured = _make_oidc_app(
        private_key, jwk, token_overrides={"nonce": "wrong-nonce"}
    )
    c1 = TestClient(bad_nonce_app)
    c1.get("/v1/auth/login?mode=json")
    assert c1.get(f"/v1/auth/callback?code=c&state={captured['state']}").status_code == 401

    bad_aud_app, captured2 = _make_oidc_app(
        private_key, jwk, token_overrides={"aud": "other-client"}
    )
    c2 = TestClient(bad_aud_app)
    c2.get("/v1/auth/login?mode=json")
    assert c2.get(f"/v1/auth/callback?code=c&state={captured2['state']}").status_code == 401

    # Unmapped group -> zero roles & zero capabilities (default-deny).
    unmapped_app, captured3 = _make_oidc_app(private_key, jwk, role_mappings={})
    c3 = TestClient(unmapped_app)
    c3.get("/v1/auth/login?mode=json")
    res = c3.get(f"/v1/auth/callback?code=c&state={captured3['state']}")
    assert res.status_code == 200
    assert res.json()["roles"] == []
    assert res.json()["capabilities"] == []
