from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import ProviderSettings


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool


class OIDCClient:
    """Minimal OIDC client for Google, Microsoft and Apple.

    Discovery and JWKS responses are cached briefly in memory. Provider secrets
    are read from environment-backed settings and never included in logs.
    """

    def __init__(self, settings: ProviderSettings):
        self.settings = settings
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None

    async def discovery(self) -> dict[str, Any]:
        if self._discovery and self._discovery[0] > time.time():
            return self._discovery[1]
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(f"{self.settings.issuer}/.well-known/openid-configuration")
            response.raise_for_status()
            data = response.json()
        required = ("authorization_endpoint", "token_endpoint", "jwks_uri")
        missing = [name for name in required if not data.get(name)]
        if missing:
            raise RuntimeError(f"OIDC provider {self.settings.name} is missing discovery fields: {', '.join(missing)}")
        self._discovery = (time.time() + 3600, data)
        return data

    async def authorization_url(self, *, state: str, nonce: str, verifier: str, redirect_uri: str) -> str:
        metadata = await self.discovery()
        params = {
            "client_id": self.settings.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.settings.scopes),
            "state": state,
            "nonce": nonce,
        }
        if self.settings.response_mode:
            params["response_mode"] = self.settings.response_mode
        if self.settings.supports_pkce:
            params["code_challenge"] = _pkce_challenge(verifier)
            params["code_challenge_method"] = "S256"
        return f"{metadata['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        metadata = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
        }
        if self.settings.supports_pkce:
            data["code_verifier"] = verifier
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(metadata["token_endpoint"], data=data)
            response.raise_for_status()
            return response.json()

    async def identity(self, token_response: dict[str, Any], *, nonce: str) -> Identity:
        id_token = token_response.get("id_token")
        if not id_token:
            raise RuntimeError(f"OIDC provider {self.settings.name} did not return an id_token")
        claims = await self._verify_id_token(id_token, nonce=nonce)

        userinfo_endpoint = (await self.discovery()).get("userinfo_endpoint")
        userinfo: dict[str, Any] = {}
        if userinfo_endpoint and token_response.get("access_token"):
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.get(
                    userinfo_endpoint,
                    headers={"Authorization": f"Bearer {token_response['access_token']}"},
                )
                if response.is_success:
                    userinfo = response.json()

        # UserInfo can supplement claims that were omitted from the ID token,
        # but it must refer to the same OIDC subject. Email is profile data,
        # never the identity key.
        subject = str(claims.get("sub", "")).strip()
        if userinfo.get("sub") and str(userinfo["sub"]) != subject:
            raise RuntimeError("OIDC UserInfo subject does not match the ID token")
        email_value = claims.get("email") or userinfo.get("email")
        if not email_value and self.settings.name == "microsoft":
            email_value = claims.get("preferred_username") or userinfo.get("preferred_username")
        email = str(email_value).strip().casefold() if email_value else None
        verified = claims.get("email_verified", userinfo.get("email_verified"))
        if isinstance(verified, str):
            verified = verified.strip().lower() == "true"
        email_verified = bool(verified is True and email and "@" in email)
        if email and ("@" not in email or any(ch.isspace() for ch in email)):
            email = None
            email_verified = False
        if not subject:
            raise RuntimeError(f"OIDC provider {self.settings.name} returned no subject")
        return Identity(provider=self.settings.name, subject=subject, email=email, email_verified=email_verified)

    async def _verify_id_token(self, encoded: str, *, nonce: str) -> dict[str, Any]:
        metadata = await self.discovery()
        if self._jwks and self._jwks[0] > time.time():
            jwks = self._jwks[1]
        else:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                response = await client.get(metadata["jwks_uri"])
                response.raise_for_status()
                jwks = response.json()
            self._jwks = (time.time() + 3600, jwks)

        header = jwt.get_unverified_header(encoded)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            raise RuntimeError(f"Unsupported OIDC signing algorithm for {self.settings.name}")
        key_data = next((key for key in jwks.get("keys", []) if key.get("kid") == header.get("kid")), None)
        if not key_data:
            raise RuntimeError(f"No matching OIDC signing key for {self.settings.name}")
        if key_data.get("kty") == "RSA":
            key = RSAAlgorithm.from_jwk(json.dumps(key_data))
        else:
            # PyJWT handles EC JWKs through its generic key loader.
            key = jwt.PyJWK(key_data).key
        expected_issuer = metadata.get("issuer", self.settings.issuer)
        try:
            claims = jwt.decode(
                encoded,
                key=key,
                algorithms=[algorithm],
                audience=self.settings.client_id,
                issuer=expected_issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
                leeway=30,
            )
        except jwt.InvalidIssuerError:
            # Microsoft’s `common` discovery endpoint issues tenant-specific
            # issuers. Accept only the documented Microsoft v2 issuer shape.
            unverified = jwt.decode(encoded, options={"verify_signature": False})
            issuer = str(unverified.get("iss", ""))
            if self.settings.name != "microsoft" or not issuer.startswith("https://login.microsoftonline.com/") or not issuer.endswith("/v2.0"):
                raise
            claims = jwt.decode(
                encoded,
                key=key,
                algorithms=[algorithm],
                audience=self.settings.client_id,
                options={"require": ["exp", "iat", "iss", "sub"]},
                leeway=30,
            )
        return claims | self._require_nonce(encoded, nonce)

    @staticmethod
    def _require_nonce(encoded: str, expected: str) -> dict[str, Any]:
        claims = jwt.decode(encoded, options={"verify_signature": False})
        if claims.get("nonce") != expected:
            raise RuntimeError("OIDC nonce validation failed")
        return claims


