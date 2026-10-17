from __future__ import annotations

import html
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    OAuthClientInformationFull,
    OAuthToken,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .config import Settings
from .oidc import OIDCClient
from .storage import SQLiteStore


class PDF2MDOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """OAuth authorization server used by the PDF2MD MCP resource server.

    ChatGPT dynamically registers an OAuth client, sends the user through one
    of the configured OIDC providers, and receives a short-lived authorization
    code. PDF2MD then issues its own bearer token and never forwards provider
    access tokens to the MCP client.
    """

    def __init__(self, settings: Settings, store: SQLiteStore):
        self.settings = settings
        self.store = store
        self.oidc = {
            provider.name: OIDCClient(provider)
            for provider in settings.enabled_providers
        }

    # Provider methods called by the MCP SDK ---------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(record) if record else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_client_metadata", "At least one redirect URI is required")
        for uri in client_info.redirect_uris:
            scheme = str(uri).split(":", 1)[0].lower()
            if scheme not in {"https", "http"}:
                raise RegistrationError("invalid_redirect_uri", "Redirect URIs must use HTTPS")
            if scheme == "http" and "localhost" not in str(uri) and "127.0.0.1" not in str(uri):
                raise RegistrationError("invalid_redirect_uri", "Only localhost may use HTTP")
        self.store.save_client(client_info.model_dump(mode="json"))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.settings.resource_url.rstrip("/"):
            raise AuthorizeError("invalid_target", "The requested resource is not PDF2MD")
        state = secrets.token_urlsafe(32)
        self.store.save_auth_request(
            state=state,
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_explicit=params.redirect_uri_provided_explicitly,
            client_state=params.state,
            scopes=params.scopes or ["pdf:convert"],
            code_challenge=params.code_challenge,
            resource=params.resource or self.settings.resource_url,
            expires_at=int(time.time()) + self.settings.oauth_state_ttl_seconds,
        )
        return f"{self.settings.public_base_url}/login?{urlencode({'state': state})}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        code = self.store.get_authorization_code(authorization_code)
        if not code or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        if not self.store.consume_authorization_code(authorization_code.code):
            raise TokenError("invalid_grant", "Authorization code has already been used or expired")
        return self._issue_tokens(
            client_id=client.client_id,
            subject=authorization_code.subject or "",
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        token = self.store.load_refresh_token(refresh_token)
        if not token or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        self.store.revoke_token(refresh_token.token)
        return self._issue_tokens(
            client_id=client.client_id,
            subject=refresh_token.subject or "",
            scopes=scopes,
            resource=refresh_token.resource,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.store.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.store.revoke_token(token.token)

    def _issue_tokens(self, *, client_id: str, subject: str, scopes: list[str], resource: str | None) -> OAuthToken:
        user = self.store.get_user(subject)
        if not user:
            raise TokenError("invalid_grant", "The PDF2MD account no longer exists")
        resource = resource or self.settings.resource_url
        now = int(time.time())
        access_token = secrets.token_urlsafe(48)
        refresh_token = secrets.token_urlsafe(48)
        claims = {
            "iss": self.settings.issuer_url,
            "sub": subject,
            "email": user.email,
            "provider": user.provider,
        }
        self.store.save_token(
            raw_token=access_token,
            token_type="access",
            client_id=client_id,
            subject=subject,
            scopes=scopes,
            resource=resource,
            expires_at=now + self.settings.access_token_ttl_seconds,
            claims=claims,
        )
        self.store.save_token(
            raw_token=refresh_token,
            token_type="refresh",
            client_id=client_id,
            subject=subject,
            scopes=scopes,
            resource=resource,
            expires_at=now + self.settings.refresh_token_ttl_seconds,
            claims=claims,
        )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.settings.access_token_ttl_seconds,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )

    # Browser-facing OIDC flow ----------------------------------------
    async def login_page(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request(state)
        if not auth_request:
            return HTMLResponse("<h1>Enllaç d’autorització invàlid o caducat</h1>", status_code=400)
        providers = sorted(self.oidc)
        if not providers:
            return HTMLResponse(
                "<h1>PDF2MD encara no té cap proveïdor d’inici de sessió configurat</h1>", status_code=503
            )
        buttons = "".join(
            f'<p><a href="/oauth/login/{html.escape(provider)}?state={html.escape(state)}">'
            f'Continua amb {html.escape(provider.title())}</a></p>'
            for provider in providers
        )
        return HTMLResponse(
            "<!doctype html><html lang=\"ca\"><head><meta charset=\"utf-8\"><title>PDF2MD</title>"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
            "<body><main><h1>Connecta amb PDF2MD</h1><p>Tria el teu compte per continuar.</p>"
            f"{buttons}</main></body></html>",
            headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"},
        )

    async def start_provider_login(self, request: Request) -> Response:
        provider_name = request.path_params["provider"]
        state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request(state)
        oidc = self.oidc.get(provider_name)
        if not auth_request or not oidc:
            return HTMLResponse("<h1>Sol·licitud d’autorització invàlida</h1>", status_code=400)
        provider_state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        self.store.set_provider_state(
            state,
            provider_name=provider_name,
            provider_state=provider_state,
            provider_nonce=nonce,
            provider_verifier=verifier,
        )
        redirect_uri = f"{self.settings.public_base_url}/oauth/callback/{provider_name}"
        try:
            url = await oidc.authorization_url(state=provider_state, nonce=nonce, verifier=verifier, redirect_uri=redirect_uri)
        except Exception:
            return HTMLResponse("<h1>No s’ha pogut iniciar l’autenticació</h1>", status_code=502)
        return RedirectResponse(url, status_code=302)

    async def provider_callback(self, request: Request) -> Response:
        provider_name = request.path_params["provider"]
        provider_state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request_by_provider_state(provider_state)
        if not auth_request or auth_request["provider_name"] != provider_name:
            return HTMLResponse("<h1>Resposta d’autenticació invàlida o caducada</h1>", status_code=400)
        if request.query_params.get("error"):
            return self._redirect_error(auth_request, "access_denied", "L’inici de sessió ha estat cancel·lat")
        code = request.query_params.get("code")
        if not code:
            return self._redirect_error(auth_request, "server_error", "El proveïdor no ha retornat cap codi")
        oidc = self.oidc.get(provider_name)
        if not oidc:
            return self._redirect_error(auth_request, "server_error", "Proveïdor no configurat")
        try:
            token_response = await oidc.exchange_code(
                code=code,
                verifier=auth_request["provider_verifier"],
                redirect_uri=f"{self.settings.public_base_url}/oauth/callback/{provider_name}",
            )
            identity = await oidc.identity(token_response, nonce=auth_request["provider_nonce"])
            user = self.store.upsert_user(
                email=identity.email,
                provider=identity.provider,
                provider_sub=identity.subject,
            )
            authorization_code = AuthorizationCode(
                code=secrets.token_urlsafe(48),
                scopes=json.loads(auth_request["scopes_json"]),
                expires_at=int(time.time()) + 60,
                client_id=auth_request["client_id"],
                code_challenge=auth_request["code_challenge"],
                redirect_uri=auth_request["redirect_uri"],
                redirect_uri_provided_explicitly=bool(auth_request["redirect_uri_explicit"]),
                resource=auth_request["resource"],
                subject=user.user_id,
            )
            self.store.save_authorization_code(authorization_code)
            self.store.delete_auth_request(auth_request["state"])
            return RedirectResponse(
                construct_redirect_uri(
                    auth_request["redirect_uri"],
                    code=authorization_code.code,
                    state=auth_request["client_state"],
                ),
                status_code=302,
            )
        except Exception:
            return self._redirect_error(auth_request, "server_error", "No s’ha pogut validar la identitat")

    @staticmethod
    def _redirect_error(auth_request: Any, error: str, description: str) -> Response:
        location = construct_redirect_uri(
            auth_request["redirect_uri"],
            error=error,
            error_description=description,
            state=auth_request["client_state"],
        )
        return RedirectResponse(location, status_code=302)

