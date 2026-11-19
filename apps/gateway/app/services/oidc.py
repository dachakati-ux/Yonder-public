"""OIDC Authorization Code + PKCE bridge and signed session helpers (M03.2/M03.3)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from yonder_configuration.models import OidcConfig

ALLOWED_ID_TOKEN_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "EdDSA",
)

PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "generic": {
        "label": "Generic OIDC",
        "scopes": ("openid", "profile", "email", "groups"),
        "groups_claim": "groups",
        "notes": "Standard OIDC discovery (/.well-known/openid-configuration) with PKCE S256.",
    },
    "authentik": {
        "label": "Authentik",
        "scopes": ("openid", "profile", "email", "groups"),
        "groups_claim": "groups",
        "notes": "Create an OAuth2/OpenID Provider in Authentik with PKCE enabled.",
    },
    "keycloak": {
        "label": "Keycloak",
        "scopes": ("openid", "profile", "email", "groups"),
        "groups_claim": "groups",
        "notes": "Add a Group Membership protocol mapper in the Keycloak client scope.",
    },
    "entra": {
        "label": "Microsoft Entra ID",
        "scopes": ("openid", "profile", "email"),
        "groups_claim": "groups",
        "notes": "Configure group claims (Security groups / Group ID) in App Registration.",
    },
    "google_workspace": {
        "label": "Google Workspace",
        "scopes": ("openid", "profile", "email"),
        "groups_claim": "groups",
        "notes": (
            "Bind verified Google Workspace tenant/sub claims; never trust raw email domain alone."
        ),
    },
}


class OidcError(ValueError):
    """Raised when OIDC discovery, token verification, or session validation fails."""


def validate_oidc_url(url: str, *, allow_insecure_http: bool = False) -> str:
    """Validate an OIDC endpoint URL, blocking schemes, userinfo, and private targets."""
    cleaned = url.strip()
    if not cleaned:
        raise OidcError("OIDC URL must not be empty.")
    parsed = urlsplit(cleaned)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise OidcError("OIDC URL must not contain userinfo or fragments.")
    allowed_schemes = {"https", "http"} if allow_insecure_http else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise OidcError("OIDC URL must use HTTPS and include a valid hostname.")
    if not allow_insecure_http:
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"}:
            raise OidcError("Loopback OIDC endpoints require allow_insecure_http_in_dev.")
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None and (
            addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        ):
            raise OidcError("Private or loopback IP OIDC endpoints are not permitted.")
    return cleaned.rstrip("/")


def build_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def sign_session_payload(payload: dict[str, Any], secret: str) -> str:
    """Sign a compact JSON payload with HMAC-SHA256."""
    if len(secret) < 32:
        raise OidcError("Session signing secret must be at least 32 characters.")
    body = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    sig = _b64url_encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"yonder.v1.{body}.{sig}"


def verify_session_payload(token: str, secret: str) -> dict[str, Any]:
    """Verify HMAC-SHA256 signature and expiration; fail closed."""
    if not token or not secret:
        raise OidcError("Missing session token or signing secret.")
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "yonder" or parts[1] != "v1":
        raise OidcError("Malformed session token.")
    _, _, body, supplied_sig = parts
    expected_sig = _b64url_encode(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied_sig, expected_sig):
        raise OidcError("Invalid session signature.")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OidcError("Corrupted session payload.") from exc
    if not isinstance(payload, dict):
        raise OidcError("Session payload must be an object.")
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= float(exp):
        raise OidcError("Session expired.")
    return payload


def map_groups_to_roles_and_capabilities(
    groups: tuple[str, ...],
    config: OidcConfig,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Map verified IdP groups to Yonder roles & capabilities (default-deny)."""
    roles: list[str] = []
    seen_roles: set[str] = set()
    for group in groups:
        for role in config.role_mappings.get(group, ()):
            if role not in seen_roles:
                seen_roles.add(role)
                roles.append(role)
    capabilities: list[str] = []
    seen_caps: set[str] = set()
    for role in roles:
        for cap in config.role_capabilities.get(role, ()):
            if cap not in seen_caps:
                seen_caps.add(cap)
                capabilities.append(cap)
    return tuple(roles), tuple(capabilities)


@dataclass
class OidcDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


class OidcBridge:
    """Stateless-cookie OIDC Authorization Code + PKCE bridge."""

    TX_COOKIE_NAME = "yonder_oidc_tx"
    TX_TTL_SECONDS = 600

    def __init__(self, config: OidcConfig, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._http_client = http_client
        self._discovery: OidcDiscovery | None = None
        self._jwks_by_kid: dict[str, jwt.PyJWK] = {}

    def _session_secret(self) -> str:
        secret = (self._config.session_secret or "").strip()
        if len(secret) < 32:
            raise OidcError("OIDC session_secret must be at least 32 characters.")
        return secret

    async def _get_json(self, url: str) -> dict[str, Any]:
        validated = validate_oidc_url(
            url, allow_insecure_http=self._config.allow_insecure_http_in_dev
        )
        if self._http_client is not None:
            response = await self._http_client.get(
                validated,
                timeout=self._config.discovery_timeout_seconds,
                follow_redirects=False,
            )
        else:
            async with httpx.AsyncClient(
                timeout=self._config.discovery_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.get(validated)
        if response.status_code != 200:
            raise OidcError(f"OIDC endpoint returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OidcError("OIDC JSON response must be an object.")
        return payload

    async def _post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        validated = validate_oidc_url(
            url, allow_insecure_http=self._config.allow_insecure_http_in_dev
        )
        if self._http_client is not None:
            response = await self._http_client.post(
                validated,
                data=data,
                timeout=self._config.discovery_timeout_seconds,
                follow_redirects=False,
            )
        else:
            async with httpx.AsyncClient(
                timeout=self._config.discovery_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.post(validated, data=data)
        if response.status_code != 200:
            raise OidcError(f"OIDC token endpoint returned HTTP {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OidcError("OIDC token response must be an object.")
        return payload

    async def fetch_discovery(self, *, force_refresh: bool = False) -> OidcDiscovery:
        if self._discovery is not None and not force_refresh:
            return self._discovery
        issuer = validate_oidc_url(
            self._config.issuer,
            allow_insecure_http=self._config.allow_insecure_http_in_dev,
        )
        doc = await self._get_json(f"{issuer}/.well-known/openid-configuration")
        doc_issuer = str(doc.get("issuer", "")).rstrip("/")
        if doc_issuer != issuer:
            raise OidcError("OIDC discovery issuer does not match configured issuer.")
        auth_ep = validate_oidc_url(
            str(doc.get("authorization_endpoint", "")),
            allow_insecure_http=self._config.allow_insecure_http_in_dev,
        )
        token_ep = validate_oidc_url(
            str(doc.get("token_endpoint", "")),
            allow_insecure_http=self._config.allow_insecure_http_in_dev,
        )
        jwks_uri = validate_oidc_url(
            str(doc.get("jwks_uri", "")),
            allow_insecure_http=self._config.allow_insecure_http_in_dev,
        )
        self._discovery = OidcDiscovery(
            issuer=issuer,
            authorization_endpoint=auth_ep,
            token_endpoint=token_ep,
            jwks_uri=jwks_uri,
        )
        return self._discovery

    async def _get_signing_key(self, kid: str | None, *, force_refresh: bool = False) -> jwt.PyJWK:
        if not force_refresh and kid and kid in self._jwks_by_kid:
            return self._jwks_by_kid[kid]
        discovery = await self.fetch_discovery()
        jwks_doc = await self._get_json(discovery.jwks_uri)
        keys = jwks_doc.get("keys")
        if not isinstance(keys, list) or not keys:
            raise OidcError("OIDC JWKS document contains no keys.")
        parsed: dict[str, jwt.PyJWK] = {}
        first_key: jwt.PyJWK | None = None
        for entry in keys:
            if not isinstance(entry, dict):
                continue
            jwk = jwt.PyJWK.from_dict(entry)
            if first_key is None:
                first_key = jwk
            entry_kid = entry.get("kid")
            if isinstance(entry_kid, str) and entry_kid:
                parsed[entry_kid] = jwk
        self._jwks_by_kid = parsed
        if kid and kid in parsed:
            return parsed[kid]
        if kid is None and len(keys) == 1 and first_key is not None:
            return first_key
        raise OidcError("No matching JWK found for ID token kid.")

    async def build_login_redirect(self) -> tuple[str, str]:
        """Return (authorization_url, signed_tx_cookie_value)."""
        discovery = await self.fetch_discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = build_pkce_pair()
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": " ".join(self._config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        tx_token = sign_session_payload(
            {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "exp": int(time.time()) + self.TX_TTL_SECONDS,
            },
            self._session_secret(),
        )
        return f"{discovery.authorization_endpoint}?{urlencode(params)}", tx_token

    async def verify_id_token(self, id_token: str, *, expected_nonce: str) -> dict[str, Any]:
        discovery = await self.fetch_discovery()
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OidcError("Malformed ID token header.") from exc
        kid = header.get("kid")
        try:
            jwk = await self._get_signing_key(kid, force_refresh=False)
        except OidcError:
            jwk = await self._get_signing_key(kid, force_refresh=True)
        try:
            claims = jwt.decode(
                id_token,
                key=jwk.key,
                algorithms=list(ALLOWED_ID_TOKEN_ALGORITHMS),
                issuer=discovery.issuer,
                audience=self._config.client_id,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"ID token verification failed: {exc}") from exc
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not secrets.compare_digest(
            token_nonce, expected_nonce
        ):
            raise OidcError("ID token nonce mismatch.")
        return claims

    async def exchange_and_verify(
        self, *, code: str, state: str, tx_cookie: str
    ) -> tuple[str, dict[str, Any]]:
        """Exchange authorization code, verify ID token, return (session_cookie, metadata)."""
        if not code or not state:
            raise OidcError("Missing authorization code or state.")
        secret = self._session_secret()
        tx = verify_session_payload(tx_cookie, secret)
        if not secrets.compare_digest(str(tx.get("state", "")), state):
            raise OidcError("OIDC state mismatch.")
        discovery = await self.fetch_discovery()
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "code_verifier": str(tx.get("verifier", "")),
        }
        if self._config.client_secret:
            form["client_secret"] = self._config.client_secret
        token_response = await self._post_form(discovery.token_endpoint, form)
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("Token response did not include an id_token.")
        claims = await self.verify_id_token(id_token, expected_nonce=str(tx.get("nonce", "")))
        subject = str(claims.get(self._config.subject_claim, "")).strip()
        if not subject:
            raise OidcError("ID token missing configured subject claim.")
        raw_groups = claims.get(self._config.groups_claim)
        groups: tuple[str, ...] = (
            tuple(str(g).strip() for g in raw_groups if str(g).strip())
            if isinstance(raw_groups, list)
            else ()
        )
        roles, capabilities = map_groups_to_roles_and_capabilities(groups, self._config)
        now = int(time.time())
        csrf_token = secrets.token_urlsafe(24)
        session_payload = {
            "sub": subject,
            "roles": list(roles),
            "groups": list(groups),
            "capabilities": list(capabilities),
            "csrf": csrf_token,
            "iat": now,
            "exp": now + self._config.session_ttl_seconds,
            "provider": self._config.provider_template,
        }
        session_cookie = sign_session_payload(session_payload, secret)
        return session_cookie, session_payload
