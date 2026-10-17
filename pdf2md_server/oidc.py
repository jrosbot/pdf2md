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
    email: str


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
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{metadata['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        metadata = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code_verifier": verifier,
        }
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

        combined = {**claims, **userinfo}
        subject = str(combined.get("sub", "")).strip()
        email = str(combined.get("email", "")).strip().casefold()
        verified = combined.get("email_verified")
        if isinstance(verified, str):
            verified = verified.lower() == "true"
        # Microsoft may omit email_verified; its signed OIDC identity still
        # supplies the account email claim. Google and Apple must explicitly
        # mark the address as verified.
        if self.settings.name in {"google", "apple"} and verified is not True:
            raise RuntimeError(f"The {self.settings.name} identity email is not verified")
        if self.settings.name == "microsoft" and not email:
            email = str(combined.get("preferred_username", "")).strip().casefold()
        if not subject or not email or "@" not in email:
            raise RuntimeError(f"OIDC provider {self.settings.name} returned no usable identity")
        return Identity(provider=self.settings.name, subject=subject, email=email)

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
                algorithms=[header.get("alg", "RS256")],
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
                algorithms=[header.get("alg", "RS256")],
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

