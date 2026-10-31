from __future__ import annotations

import html
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, OAuthClientInformationFull, OAuthToken,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .config import Settings
from .mailer import BrevoMailer
from .oidc import OIDCClient
from .passwords import hash_password, verify_password
from .storage import PendingIdentity, SQLiteStore, User, normalize_email


class PDF2MDOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """MCP OAuth server with Google, Microsoft, Apple and verified email/password login."""

    def __init__(self, settings: Settings, store: SQLiteStore):
        self.settings = settings
        self.store = store
        self.oidc = {provider.name: OIDCClient(provider) for provider in settings.enabled_providers}
        self.mailer = BrevoMailer(settings)

    # MCP OAuth authorization-server methods ---------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(record) if record else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError("invalid_client_metadata", "At least one redirect URI is required")
        for uri in client_info.redirect_uris:
            parsed = urlsplit(str(uri))
            scheme = parsed.scheme.lower()
            if scheme not in {"https", "http"}:
                raise RegistrationError("invalid_redirect_uri", "Redirect URIs must use HTTPS")
            if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise RegistrationError("invalid_redirect_uri", "Redirect URI must have a valid host and no credentials or fragment")
            if scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
                raise RegistrationError("invalid_redirect_uri", "Only localhost may use HTTP")
        self.store.save_client(client_info.model_dump(mode="json"))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.settings.resource_url.rstrip("/"):
            raise AuthorizeError("invalid_target", "The requested resource is not PDF2MD")
        state = secrets.token_urlsafe(32)
        self.store.save_auth_request(
            state=state, client_id=client.client_id, redirect_uri=str(params.redirect_uri),
            redirect_uri_explicit=params.redirect_uri_provided_explicitly, client_state=params.state,
            scopes=params.scopes or ["pdf:convert"], code_challenge=params.code_challenge,
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
        return self._issue_tokens(client_id=client.client_id, subject=authorization_code.subject or "",
                                  scopes=authorization_code.scopes, resource=authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        token = self.store.load_refresh_token(refresh_token)
        if not token or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        granted_scopes = list(refresh_token.scopes)
        if scopes and not set(scopes).issubset(set(granted_scopes)):
            raise TokenError("invalid_scope", "The refresh request cannot add scopes")
        effective_scopes = scopes or granted_scopes
        self.store.revoke_token(refresh_token.token)
        return self._issue_tokens(client_id=client.client_id, subject=refresh_token.subject or "",
                                  scopes=effective_scopes, resource=refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.store.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.store.revoke_token(token.token)

    def _issue_tokens(self, *, client_id: str, subject: str, scopes: list[str], resource: str | None) -> OAuthToken:
        user = self.store.get_user(subject)
        if not user:
            raise TokenError("invalid_grant", "The PDF2MD account no longer exists")
        now = int(time.time())
        access_token = secrets.token_urlsafe(48)
        refresh_token = secrets.token_urlsafe(48)
        claims = {"iss": self.settings.issuer_url, "sub": subject, "email": user.email,
                  "email_verified": user.email_verified}
        self.store.save_token(raw_token=access_token, token_type="access", client_id=client_id,
                              subject=subject, scopes=scopes, resource=resource or self.settings.resource_url,
                              expires_at=now + self.settings.access_token_ttl_seconds, claims=claims)
        self.store.save_token(raw_token=refresh_token, token_type="refresh", client_id=client_id,
                              subject=subject, scopes=scopes, resource=resource or self.settings.resource_url,
                              expires_at=now + self.settings.refresh_token_ttl_seconds, claims=claims)
        return OAuthToken(access_token=access_token, token_type="Bearer",
                          expires_in=self.settings.access_token_ttl_seconds, scope=" ".join(scopes),
                          refresh_token=refresh_token)

    # Browser login and account linking -------------------------------
    async def login_page(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request(state)
        if not auth_request:
            return self._page("Authorization link expired", "<p>Start the connection again from ChatGPT.</p>", 400)
        pending_id = auth_request["pending_identity_id"]
        if pending_id:
            pending = self.store.get_pending_identity(pending_id)
            if not pending:
                return self._page("Sign-in expired", "<p>Start the connection again from ChatGPT.</p>", 400)
            if not pending.email_verified:
                return self._email_entry(state, pending, suggested=pending.email)
            user_id, candidates = self.store.complete_pending_identity(pending_id)
            if candidates:
                return self._account_link_page(state, pending, candidates)
            user = self.store.get_user(user_id or "")
            return self._complete_login(auth_request, user) if user else self._page("Sign-in expired", "<p>Start again from ChatGPT.</p>", 400)

        provider_buttons = "".join(
            f'<p><a href="/oauth/login/{html.escape(name)}?state={html.escape(state)}">Continue with {html.escape(name.title())}</a></p>'
            for name in sorted(self.oidc)
        )
        password_form = self._password_login_form(state, show_register=self.settings.email_password_enabled)
        body = (
            "<p>Choose a sign-in method for PDF2MD.</p>" + provider_buttons + password_form +
            ("<p>New here? Create an account with your email address and a password.</p>" if self.settings.email_password_enabled else "")
        )
        return self._page("Sign in to PDF2MD", body)

    def _password_login_form(self, state: str, *, show_register: bool) -> str:
        register = ""
        if show_register:
            register = (
                '<h2>Create an account or add email/password</h2>'
                '<form method="post" action="/auth/password/register">'
                f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
                '<label>Email <input type="email" name="email" required autocomplete="email"></label>'
                '<label>Password <input type="password" name="password" minlength="12" required autocomplete="new-password"></label>'
                '<button type="submit">Verify email and continue</button></form>'
            )
        return (
            '<h2>Continue with email and password</h2>'
            '<form method="post" action="/auth/password/login">'
            f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
            '<label>Email <input type="email" name="email" required autocomplete="email"></label>'
            '<label>Password <input type="password" name="password" required autocomplete="current-password"></label>'
            '<button type="submit">Sign in</button></form>' + register +
            ('<form method="post" action="/auth/password/forgot">'
             f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
             '<label>Email <input type="email" name="email" required autocomplete="email"></label>'
             '<button type="submit">Send password reset link</button></form>' if self.settings.email_password_enabled else "")
        )

    def _email_entry(self, state: str, pending: PendingIdentity, *, suggested: str | None = None) -> Response:
        value = f' value="{html.escape(suggested, quote=True)}"' if suggested else ""
        body = (
            "<p>This sign-in method did not provide a verified email address. Enter an address you can access. "
            "We will send a verification link before creating or linking the account.</p>"
            '<form method="post" action="/auth/email/submit">'
            f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
            f'<input type="hidden" name="pending_id" value="{html.escape(pending.pending_id, quote=True)}">'
            f'<label>Email <input type="email" name="email" required autocomplete="email"{value}></label>'
            '<button type="submit">Send verification link</button></form>'
        )
        return self._page("Verify your email", body)

    def _account_link_page(self, state: str, pending: PendingIdentity, candidates: list[str]) -> Response:
        providers = {provider for candidate in candidates for provider in self.store.account_methods(candidate)}
        buttons = "".join(
            f'<p><a href="/oauth/login/{html.escape(provider)}?state={html.escape(state)}">'
            f'Continue with {html.escape("email and password" if provider == "password" else provider.title())}</a></p>'
            for provider in sorted(providers) if provider in self.oidc
        )
        password = self._password_login_form(state, show_register=False) if "password" in providers else ""
        body = (
            "<p>This verified email is already associated with a PDF2MD account. No new account has been created.</p>"
            "<p>Sign in with a method already linked to that account. After that, PDF2MD will link the new method.</p>"
            + buttons + password
        )
        return self._page("Sign in to your existing account", body)

    async def start_provider_login(self, request: Request) -> Response:
        provider_name = request.path_params["provider"]
        state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request(state)
        oidc = self.oidc.get(provider_name)
        if not auth_request or not oidc:
            return self._page("Invalid sign-in request", "<p>Return to ChatGPT and start again.</p>", 400)
        provider_state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        self.store.set_provider_state(state, provider_name=provider_name, provider_state=provider_state,
                                     provider_nonce=nonce, provider_verifier=verifier)
        redirect_uri = f"{self.settings.public_base_url}/oauth/callback/{provider_name}"
        try:
            url = await oidc.authorization_url(state=provider_state, nonce=nonce, verifier=verifier,
                                               redirect_uri=redirect_uri)
        except Exception:
            return self._page("Sign-in unavailable", "<p>Try again in a moment.</p>", 502)
        return RedirectResponse(url, status_code=302)

    async def provider_callback(self, request: Request) -> Response:
        provider_name = request.path_params["provider"]
        provider_state = request.query_params.get("state", "")
        auth_request = self.store.get_auth_request_by_provider_state(provider_state)
        if not auth_request or auth_request["provider_name"] != provider_name:
            return self._page("Invalid or expired sign-in response", "<p>Start the connection again.</p>", 400)
        if request.query_params.get("error"):
            return self._page("Sign-in cancelled", '<p><a href="/login?state=' + html.escape(auth_request["state"]) + '">Try another method</a></p>')
        code = request.query_params.get("code")
        oidc = self.oidc.get(provider_name)
        if not code or not oidc:
            return self._page("Sign-in failed", '<p><a href="/login?state=' + html.escape(auth_request["state"]) + '">Try again</a></p>', 400)
        try:
            token_response = await oidc.exchange_code(
                code=code, verifier=auth_request["provider_verifier"],
                redirect_uri=f"{self.settings.public_base_url}/oauth/callback/{provider_name}",
            )
            identity = await oidc.identity(token_response, nonce=auth_request["provider_nonce"])
            user_id = self.store.get_identity_user_id(identity.provider, identity.subject)
            pending_id = auth_request["pending_identity_id"]
            if user_id:
                self.store.record_identity_login(provider=identity.provider, provider_sub=identity.subject,
                                                 email=identity.email, email_verified=identity.email_verified)
                if pending_id:
                    # Linking is allowed only after a method already on the target account authenticated.
                    try:
                        user_id, collisions = self.store.complete_pending_identity(pending_id, target_user_id=user_id)
                    except ValueError:
                        return self._page("Cannot link this method", "<p>The identity or email is associated with a different account.</p>", 409)
                    if collisions:
                        return self._account_link_page(auth_request["state"], self.store.get_pending_identity(pending_id), collisions)
                user = self.store.get_user(user_id)
                if not user:
                    raise RuntimeError("The PDF2MD account could not be loaded")
                if not self.store.has_verified_email(user.user_id):
                    pending_id = self.store.create_pending_identity(
                        auth_state=auth_request["state"], provider=identity.provider, provider_sub=identity.subject,
                        email=identity.email, email_verified=False,
                        expires_at=int(auth_request["expires_at"]),
                    )
                    pending = self.store.get_pending_identity(pending_id)
                    return self._email_entry(auth_request["state"], pending, suggested=identity.email)
                return self._complete_login(auth_request, user)

            pending_id = self.store.create_pending_identity(
                auth_state=auth_request["state"], provider=identity.provider, provider_sub=identity.subject,
                email=identity.email, email_verified=identity.email_verified,
                expires_at=int(auth_request["expires_at"]),
            )
            pending = self.store.get_pending_identity(pending_id)
            if identity.email and identity.email_verified:
                user_id, collisions = self.store.complete_pending_identity(pending_id)
                if collisions:
                    return self._account_link_page(auth_request["state"], pending, collisions)
                user = self.store.get_user(user_id or "")
                if not user:
                    raise RuntimeError("The PDF2MD account could not be created")
                return self._complete_login(auth_request, user)
            return self._email_entry(auth_request["state"], pending, suggested=identity.email)
        except Exception:
            return self._page("Identity could not be verified", "<p>Try again or choose another sign-in method.</p>", 400)

    async def password_login(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        state, email, password = fields.get("state", ""), fields.get("email", ""), fields.get("password", "")
        auth_request = self.store.get_auth_request(state)
        if not auth_request:
            return self._page("Sign-in expired", "<p>Start the connection again from ChatGPT.</p>", 400)
        try:
            email = normalize_email(email)
        except ValueError:
            return self._page("Sign-in failed", '<p><a href="/login?state=' + html.escape(state) + '">Try again</a></p>', 400)
        if not self.store.allow_auth_attempt("password-login:" + email, limit=10, window_seconds=900):
            return self._page("Try again later", "<p>Too many sign-in attempts. Wait a while and try again.</p>", 429)
        record = self.store.get_password_identity(email)
        if not record or not verify_password(password, record[1]):
            return self._page("Sign-in failed", '<p>Email or password is incorrect. <a href="/login?state=' + html.escape(state) + '">Try again</a></p>', 401)
        user_id = record[0]
        pending_id = auth_request["pending_identity_id"]
        if pending_id:
            try:
                user_id, collisions = self.store.complete_pending_identity(pending_id, target_user_id=user_id)
            except ValueError:
                return self._page("Cannot link this method", "<p>This email is associated with another PDF2MD account.</p>", 409)
            if collisions:
                pending = self.store.get_pending_identity(pending_id)
                return self._account_link_page(state, pending, collisions)
        user = self.store.get_user(user_id)
        if not user:
            return self._page("Sign-in failed", "<p>Account is unavailable.</p>", 401)
        if not self.store.has_verified_email(user_id):
            pending = self._existing_identity_pending(state, user_id, "password", email)
            return self._email_entry(state, pending, suggested=email)
        return self._complete_login(auth_request, user)

    def _existing_identity_pending(self, state: str, user_id: str, provider: str, sub: str) -> PendingIdentity:
        """Create a short-lived email-verification continuation for an existing login."""
        pending_id = self.store.create_pending_identity(auth_state=state, provider=provider, provider_sub=sub,
                                                        email=None, email_verified=False,
                                                        expires_at=int(time.time()) + self.settings.oauth_state_ttl_seconds)
        pending = self.store.get_pending_identity(pending_id)
        if not pending:
            raise RuntimeError("Could not start email verification")
        return pending

    async def password_register(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        state, raw_email, password = fields.get("state", ""), fields.get("email", ""), fields.get("password", "")
        auth_request = self.store.get_auth_request(state)
        if not auth_request:
            return self._page("Sign-in expired", "<p>Start the connection again from ChatGPT.</p>", 400)
        if not self.settings.email_password_enabled:
            return self._page("Email sign-in unavailable", "<p>Contact PDF2MD support.</p>", 503)
        try:
            email = normalize_email(raw_email)
        except ValueError as exc:
            return self._page("Check your details", f"<p>{html.escape(str(exc))}</p><p><a href=\"/login?state={html.escape(state)}\">Try again</a></p>", 400)
        if not self.store.allow_auth_attempt("password-register:" + email, limit=3, window_seconds=3600):
            return self._page("Try again later", "<p>Too many attempts for this address. Wait and try again.</p>", 429)
        if self.store.get_password_identity(email):
            return self._page(
                "Sign in to your existing account",
                '<p>An email and password sign-in already exists for this address. '
                '<a href="/login?state=' + html.escape(state) + '">Sign in or request a password reset</a>.</p>',
                409,
            )
        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            return self._page("Check your details", f"<p>{html.escape(str(exc))}</p><p><a href=\"/login?state={html.escape(state)}\">Try again</a></p>", 400)
        pending_id = self.store.create_pending_identity(
            auth_state=state, provider="password", provider_sub=email, email=email, email_verified=False,
            password_hash=password_hash, expires_at=int(auth_request["expires_at"]),
        )
        return await self._send_verification_link(state, pending_id, email)

    async def submit_email(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        state, pending_id, raw_email = fields.get("state", ""), fields.get("pending_id", ""), fields.get("email", "")
        auth_request = self.store.get_auth_request(state)
        pending = self.store.get_pending_identity(pending_id)
        if not auth_request or not pending or pending.auth_state != state:
            return self._page("Sign-in expired", "<p>Start the connection again from ChatGPT.</p>", 400)
        try:
            email = normalize_email(raw_email)
        except ValueError as exc:
            return self._email_entry(state, pending, suggested=raw_email)
        return await self._send_verification_link(state, pending_id, email)

    async def _send_verification_link(self, state: str, pending_id: str, email: str) -> Response:
        if not self.settings.email_password_enabled:
            return self._page("Email verification unavailable", "<p>Transactional email is not configured.</p>", 503)
        if not self.store.allow_auth_attempt("email-verification:" + email, limit=5, window_seconds=3600):
            return self._page("Try again later", "<p>Too many verification emails were requested for this address. Try again later.</p>", 429)
        try:
            token = self.store.set_pending_email(pending_id, email)
            link = f"{self.settings.public_base_url}/auth/email/verify?{urlencode({'token': token})}"
            await self.mailer.send_email_verification(email, link)
        except Exception:
            return self._page("Could not send verification email", '<p>Check the address and try again from <a href="/login?state=' + html.escape(state) + '">the sign-in page</a>.</p>', 502)
        return self._page("Check your email", "<p>We sent a verification link. Open it to confirm the address and continue.</p>")

    async def email_verification_page(self, request: Request) -> Response:
        token = request.query_params.get("token", "")
        if not token:
            return self._page("Invalid verification link", "<p>Request a new link from PDF2MD.</p>", 400)
        body = (
            '<p>Confirm that you can access this email address. This will verify the address and continue sign-in.</p>'
            '<form method="post" action="/auth/email/verify">'
            f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
            '<button type="submit">Confirm email</button></form>'
        )
        return self._page("Confirm your email", body)

    async def confirm_email(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        pending = self.store.consume_email_verification(fields.get("token", ""))
        if not pending:
            return self._page("Verification link expired", "<p>Return to PDF2MD and request another verification link.</p>", 400)
        user_id, collisions = self.store.complete_pending_identity(pending.pending_id)
        if collisions:
            return self._account_link_page(pending.auth_state, pending, collisions)
        auth_request = self.store.get_auth_request(pending.auth_state)
        user = self.store.get_user(user_id or "")
        if not auth_request or not user:
            return self._page("Sign-in expired", "<p>Start the connection again from ChatGPT.</p>", 400)
        return self._complete_login(auth_request, user)

    async def forgot_password(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        state = fields.get("state", "")
        email = fields.get("email", "")
        # Keep the response the same whether the account exists or not.
        try:
            email = normalize_email(email)
            record = self.store.get_password_identity(email)
            allowed = self.store.allow_auth_attempt("password-reset:" + email, limit=3, window_seconds=3600)
            if record and allowed and self.settings.email_password_enabled:
                token = self.store.create_password_reset_token(email)
                link = f"{self.settings.public_base_url}/auth/password/reset?{urlencode({'token': token})}"
                await self.mailer.send_password_reset(email, link)
        except Exception:
            pass
        return self._page("Check your email", "<p>If an email/password account exists for that address, we sent a password reset link.</p>"
                          + (f'<p><a href="/login?state={html.escape(state)}">Return to sign in</a></p>' if state else ""))

    async def password_reset_page(self, request: Request) -> Response:
        token = request.query_params.get("token", "")
        body = ('<form method="post" action="/auth/password/reset">'
                f'<input type="hidden" name="token" value="{html.escape(token, quote=True)}">'
                '<label>New password <input type="password" name="password" minlength="12" required autocomplete="new-password"></label>'
                '<button type="submit">Set new password</button></form>')
        return self._page("Reset your password", body)

    async def reset_password(self, request: Request) -> Response:
        fields = await self._form_fields(request)
        try:
            password_hash = hash_password(fields.get("password", ""))
            user_id = self.store.consume_password_reset_token(fields.get("token", ""), password_hash)
            if not user_id:
                raise ValueError("This password reset link has expired")
            self.store.revoke_user_tokens(user_id)
        except ValueError as exc:
            return self._page("Could not reset password", f"<p>{html.escape(str(exc))}</p>", 400)
        return self._page("Password updated", "<p>Your PDF2MD password has been updated. You can sign in now.</p>")

    async def _form_fields(self, request: Request) -> dict[str, str]:
        body = await request.body()
        if len(body) > 16_384:
            return {}
        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError:
            return {}
        return {key: values[0] for key, values in parse_qs(decoded, keep_blank_values=True).items() if values}

    def _complete_login(self, auth_request: Any, user: User) -> Response:
        authorization_code = AuthorizationCode(
            code=secrets.token_urlsafe(48), scopes=json.loads(auth_request["scopes_json"]),
            expires_at=int(time.time()) + 60, client_id=auth_request["client_id"],
            code_challenge=auth_request["code_challenge"], redirect_uri=auth_request["redirect_uri"],
            redirect_uri_provided_explicitly=bool(auth_request["redirect_uri_explicit"]),
            resource=auth_request["resource"], subject=user.user_id,
        )
        self.store.save_authorization_code(authorization_code)
        self.store.delete_auth_request(auth_request["state"])
        return RedirectResponse(construct_redirect_uri(auth_request["redirect_uri"], code=authorization_code.code,
                                                       state=auth_request["client_state"]), status_code=302)

    @staticmethod
    def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
        document = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>PDF2MD</title>'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>'
        )
        return HTMLResponse(document, status_code=status_code, headers={
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        })
