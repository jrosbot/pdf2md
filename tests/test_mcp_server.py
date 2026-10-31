import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

import httpx
from mcp.server.auth.provider import (
    AuthorizationCode, AuthorizationParams, OAuthClientInformationFull, RegistrationError, TokenError,
)

from pdf2md_server.auth import PDF2MDOAuthProvider
from pdf2md_server.config import ProviderSettings, Settings
from pdf2md_server.mailer import BrevoMailer
from pdf2md_server.oidc import Identity, OIDCClient
from pdf2md_server.passwords import hash_password, verify_password
from pdf2md_server.storage import SQLiteStore


class McpAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            public_base_url="http://localhost:8000",
            issuer_url="http://localhost:8000",
            resource_url="http://localhost:8000/mcp",
            database_path=Path(self.temp_dir.name) / "state.sqlite3",
            session_secret="local-development-secret-please-change",
        )
        self.store = SQLiteStore(self.settings.database_path)
        self.oauth = PDF2MDOAuthProvider(self.settings, self.store)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def create_account(self, provider="google", subject="google-sub", email="person@example.com"):
        pending = self.store.create_pending_identity(
            auth_state="test-state", provider=provider, provider_sub=subject,
            email=email, email_verified=True, expires_at=int(time.time()) + 600,
        )
        user_id, candidates = self.store.complete_pending_identity(pending)
        self.assertEqual(candidates, [])
        return user_id

    def test_tool_declares_file_parameter_and_account_email_tools(self):
        from pdf2md_server.server import server

        tools = asyncio.run(server.list_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(by_name["convert_pdf_to_markdown"].meta, {"openai/fileParams": ["file"]})
        attachment = by_name["convert_pdf_to_markdown"].input_schema["$defs"]["PdfAttachment"]
        self.assertEqual(set(attachment["properties"]), {"download_url", "file_id", "mime_type", "file_name"})
        self.assertIn("list_account_emails", by_name)
        self.assertIn("set_transactional_email", by_name)
        self.assertIn("echo", by_name)

    def test_authorization_code_and_access_token_lifecycle(self):
        user_id = self.create_account()
        client = OAuthClientInformationFull(
            client_id="client-1", client_secret=None,
            redirect_uris=["http://localhost:6274/oauth/callback"],
            token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
            response_types=["code"], scope="pdf:convert",
        )
        asyncio.run(self.oauth.register_client(client))
        self.assertEqual(asyncio.run(self.oauth.get_client("client-1")).client_id, "client-1")
        bad_client = OAuthClientInformationFull(
            client_id="client-bad-redirect", client_secret=None,
            redirect_uris=["http://evil.example/callback/localhost"],
            token_endpoint_auth_method="none", grant_types=["authorization_code"],
            response_types=["code"], scope="pdf:convert",
        )
        with self.assertRaises(RegistrationError):
            asyncio.run(self.oauth.register_client(bad_client))
        params = AuthorizationParams(
            state="chatgpt-state", scopes=["pdf:convert"], code_challenge="verifier-challenge",
            redirect_uri="http://localhost:6274/oauth/callback", redirect_uri_provided_explicitly=True,
            resource=self.settings.resource_url,
        )
        login_url = asyncio.run(self.oauth.authorize(client, params))
        state = parse_qs(urlparse(login_url).query)["state"][0]
        self.assertEqual(urlparse(login_url).path, "/login")
        self.assertEqual(self.store.get_auth_request(state)["state"], state)

        code = AuthorizationCode(
            code="test-code", scopes=["pdf:convert"], expires_at=4102444800,
            client_id=client.client_id, code_challenge="verifier-challenge",
            redirect_uri="http://localhost:6274/oauth/callback", redirect_uri_provided_explicitly=True,
            resource=self.settings.resource_url, subject=user_id,
        )
        self.store.save_authorization_code(code)
        loaded = asyncio.run(self.oauth.load_authorization_code(client, "test-code"))
        token = asyncio.run(self.oauth.exchange_authorization_code(client, loaded))
        access = asyncio.run(self.oauth.load_access_token(token.access_token))
        self.assertEqual(access.subject, user_id)
        self.assertTrue(access.claims["email_verified"])
        self.assertFalse(self.store.consume_authorization_code("test-code"))
        refresh = asyncio.run(self.oauth.load_refresh_token(client, token.refresh_token))
        with self.assertRaises(TokenError):
            asyncio.run(self.oauth.exchange_refresh_token(client, refresh, ["admin:all"]))

    def test_matching_verified_email_requires_old_account_login_before_link(self):
        user_id = self.create_account()
        pending = self.store.create_pending_identity(
            auth_state="another-state", provider="microsoft", provider_sub="microsoft-sub",
            email="person@example.com", email_verified=True, expires_at=int(time.time()) + 600,
        )
        found_id, candidates = self.store.complete_pending_identity(pending)
        self.assertIsNone(found_id)
        self.assertEqual(candidates, [user_id])
        self.assertIsNone(self.store.get_identity_user_id("microsoft", "microsoft-sub"))

        linked_id, candidates = self.store.complete_pending_identity(pending, target_user_id=user_id)
        self.assertEqual(linked_id, user_id)
        self.assertEqual(candidates, [])
        self.assertEqual(self.store.get_identity_user_id("microsoft", "microsoft-sub"), user_id)
        self.assertEqual(self.store.account_methods(user_id), ["google", "microsoft"])

    def test_oidc_link_flow_requires_existing_method_and_links_after_proof(self):
        from dataclasses import replace
        from pdf2md_server.server import create_server

        providers = (
            ProviderSettings("google", "https://accounts.google.com", "google-client", "google-secret"),
            ProviderSettings("microsoft", "https://login.microsoftonline.com/common/v2.0", "microsoft-client", "microsoft-secret"),
            ProviderSettings("apple", "https://appleid.apple.com", "apple-client", "apple-secret"),
        )
        settings = replace(self.settings, providers=providers)
        mcp_server, store = create_server(settings)
        app = mcp_server.streamable_http_app(streamable_http_path="/mcp", json_response=True)
        user_id = self.create_account(provider="google", subject="google-existing")
        state = "oidc-link-state"
        store.save_auth_request(
            state=state, client_id="test-client", redirect_uri="http://127.0.0.1:8765/callback",
            redirect_uri_explicit=True, client_state="chat-state", scopes=["pdf:convert"],
            code_challenge="test-challenge", resource=settings.resource_url,
            expires_at=int(time.time()) + 1800,
        )

        async def fake_authorization_url(_client, *, state, nonce, verifier, redirect_uri):
            return "https://identity.example/authorize?state=" + state

        async def fake_exchange_code(_client, *, code, verifier, redirect_uri):
            return {"id_token": "test-id-token"}

        async def fake_identity(client, token_response, *, nonce):
            if client.settings.name == "microsoft":
                return Identity("microsoft", "microsoft-new", "person@example.com", True)
            return Identity("google", "google-existing", "person@example.com", True)

        async def exercise_flow():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                start_new = await client.get(f"/oauth/login/microsoft?state={state}", follow_redirects=False)
                self.assertEqual(start_new.status_code, 302)
                auth_request = store.get_auth_request(state)
                microsoft_state = auth_request["provider_state"]

                first_callback = await client.get(
                    f"/oauth/callback/microsoft?state={microsoft_state}&code=provider-code",
                    follow_redirects=False,
                )
                self.assertEqual(first_callback.status_code, 200)
                self.assertIn("already associated", first_callback.text)
                self.assertIsNone(store.get_identity_user_id("microsoft", "microsoft-new"))

                start_existing = await client.get(f"/oauth/login/google?state={state}", follow_redirects=False)
                self.assertEqual(start_existing.status_code, 302)
                auth_request = store.get_auth_request(state)
                google_state = auth_request["provider_state"]

                second_callback = await client.get(
                    f"/oauth/callback/google?state={google_state}&code=provider-code",
                    follow_redirects=False,
                )
                self.assertEqual(second_callback.status_code, 302)
                self.assertEqual(store.get_identity_user_id("microsoft", "microsoft-new"), user_id)
                self.assertEqual(store.account_methods(user_id), ["google", "microsoft"])

        try:
            with patch.object(OIDCClient, "authorization_url", fake_authorization_url), \
                 patch.object(OIDCClient, "exchange_code", fake_exchange_code), \
                 patch.object(OIDCClient, "identity", fake_identity):
                asyncio.run(exercise_flow())
        finally:
            store.close()

    def test_oidc_email_is_optional_and_unverified_fallback_stays_unverified(self):
        client = OIDCClient(ProviderSettings(
            "microsoft", "https://login.microsoftonline.com/common/v2.0", "client", "secret"
        ))
        client.discovery = AsyncMock(return_value={})
        client._verify_id_token = AsyncMock(return_value={
            "sub": "provider-sub", "preferred_username": "person@example.com"
        })
        identity = asyncio.run(client.identity({"id_token": "test-token"}, nonce="nonce"))
        self.assertEqual(identity.email, "person@example.com")
        self.assertFalse(identity.email_verified)

        client._verify_id_token = AsyncMock(return_value={"sub": "provider-sub"})
        identity = asyncio.run(client.identity({"id_token": "test-token"}, nonce="nonce"))
        self.assertIsNone(identity.email)
        self.assertFalse(identity.email_verified)

    def test_apple_oidc_uses_supported_web_flow(self):
        client = OIDCClient(ProviderSettings(
            "apple", "https://appleid.apple.com", "apple-client", "apple-secret",
            scopes=("openid", "email", "name"), supports_pkce=False, response_mode="form_post",
        ))
        client.discovery = AsyncMock(return_value={
            "authorization_endpoint": "https://appleid.apple.com/auth/authorize"
        })
        url = asyncio.run(client.authorization_url(
            state="state", nonce="nonce", verifier="verifier",
            redirect_uri="https://auth.example.com/oauth/callback/apple",
        ))
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params["scope"], ["openid email name"])
        self.assertEqual(params["response_mode"], ["form_post"])
        self.assertNotIn("code_challenge", params)
        self.assertNotIn("code_challenge_method", params)

    def test_apple_form_post_callback_completes_login(self):
        from dataclasses import replace
        from pdf2md_server.server import create_server

        apple = ProviderSettings(
            "apple", "https://appleid.apple.com", "apple-client", "apple-secret",
            scopes=("openid", "email", "name"), supports_pkce=False, response_mode="form_post",
        )
        settings = replace(self.settings, providers=(apple,))
        mcp_server, store = create_server(settings)
        app = mcp_server.streamable_http_app(streamable_http_path="/mcp", json_response=True)
        state = "apple-post-state"
        store.save_auth_request(
            state=state, client_id="test-client", redirect_uri="http://127.0.0.1:8765/callback",
            redirect_uri_explicit=True, client_state="chat-state", scopes=["pdf:convert"],
            code_challenge="test-challenge", resource=settings.resource_url,
            expires_at=int(time.time()) + 1800,
        )
        store.set_provider_state(state, provider_name="apple", provider_state="apple-provider-state",
                                 provider_nonce="apple-nonce", provider_verifier="apple-verifier")

        async def fake_exchange_code(_client, *, code, verifier, redirect_uri):
            self.assertEqual(code, "apple-code")
            self.assertEqual(redirect_uri, f"{settings.public_base_url}/oauth/callback/apple")
            return {"id_token": "apple-id-token"}

        async def fake_identity(_client, token_response, *, nonce):
            self.assertEqual(nonce, "apple-nonce")
            return Identity("apple", "apple-subject", "person@example.com", True)

        async def exercise():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                response = await client.post(
                    "/oauth/callback/apple",
                    data={"state": "apple-provider-state", "code": "apple-code"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 302)
                user_id = store.get_identity_user_id("apple", "apple-subject")
                self.assertIsNotNone(user_id)
                user = store.get_user(user_id)
                self.assertEqual(user.email, "person@example.com")
                self.assertTrue(user.email_verified)

        try:
            with patch.object(OIDCClient, "exchange_code", fake_exchange_code), \
                 patch.object(OIDCClient, "identity", fake_identity):
                asyncio.run(exercise())
        finally:
            store.close()

    def test_user_can_choose_only_verified_emails_linked_to_account(self):
        user_id = self.create_account()
        pending = self.store.create_pending_identity(
            auth_state="email-method", provider="password", provider_sub="second@example.com",
            email="second@example.com", email_verified=True, password_hash="test-hash",
            expires_at=int(time.time()) + 600,
        )
        linked_id, collisions = self.store.complete_pending_identity(pending, target_user_id=user_id)
        self.assertEqual(linked_id, user_id)
        self.assertEqual(collisions, [])
        self.assertEqual(self.store.get_user(user_id).email, "person@example.com")
        self.assertTrue(self.store.set_transactional_email(user_id, "second@example.com"))
        self.assertFalse(self.store.set_transactional_email(user_id, "not-linked@example.com"))
        emails = self.store.account_emails(user_id)
        self.assertEqual(emails["transactional_email"], "second@example.com")
        self.assertTrue(next(item for item in emails["emails"] if item["email"] == "second@example.com")["is_transactional"])

    def test_unverified_identity_email_is_not_used_until_email_challenge(self):
        pending_id = self.store.create_pending_identity(
            auth_state="verify-state", provider="apple", provider_sub="apple-sub",
            email=None, email_verified=False, expires_at=int(time.time()) + 600,
        )
        token = self.store.set_pending_email(pending_id, "New.User@example.com")
        pending = self.store.consume_email_verification(token)
        self.assertTrue(pending.email_verified)
        self.assertEqual(pending.email, "new.user@example.com")
        self.assertIsNone(self.store.consume_email_verification(token))
        user_id, collisions = self.store.complete_pending_identity(pending_id)
        self.assertEqual(collisions, [])
        user = self.store.get_user(user_id)
        self.assertEqual(user.email, "new.user@example.com")
        self.assertTrue(user.email_verified)

    def test_login_refreshes_only_the_method_email_status(self):
        user_id = self.create_account()
        pending = self.store.create_pending_identity(
            auth_state="link-state", provider="microsoft", provider_sub="ms-sub",
            email="alternate@example.com", email_verified=True, expires_at=int(time.time()) + 600,
        )
        self.store.complete_pending_identity(pending, target_user_id=user_id)
        self.store.record_identity_login(provider="google", provider_sub="google-sub",
                                         email="changed@example.com", email_verified=True)
        emails = {item["email"]: item for item in self.store.account_emails(user_id)["emails"]}
        self.assertFalse(emails["person@example.com"]["verified"])
        self.assertTrue(emails["changed@example.com"]["verified"])
        self.assertTrue(emails["alternate@example.com"]["verified"])
        self.assertEqual(self.store.get_user(user_id).email, "person@example.com")
        self.assertFalse(self.store.get_user(user_id).email_verified)

    def test_password_hash_and_reset_token(self):
        hashed = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", hashed))
        self.assertFalse(verify_password("incorrect", hashed))
        with self.assertRaises(ValueError):
            hash_password("short")

        user_id = self.create_account()
        pending = self.store.create_pending_identity(
            auth_state="pw-link", provider="password", provider_sub="person@example.com",
            email="person@example.com", email_verified=True, password_hash=hashed,
            expires_at=int(time.time()) + 600,
        )
        self.store.complete_pending_identity(pending, target_user_id=user_id)
        record = self.store.get_password_identity("person@example.com")
        self.assertEqual(record[0], user_id)
        original_hash = record[1]
        duplicate = self.store.create_pending_identity(
            auth_state="duplicate-password-register", provider="password", provider_sub="person@example.com",
            email="person@example.com", email_verified=True,
            password_hash=hash_password("a different long password"), expires_at=int(time.time()) + 600,
        )
        duplicate_user_id, candidates = self.store.complete_pending_identity(duplicate)
        self.assertIsNone(duplicate_user_id)
        self.assertEqual(candidates, [user_id])
        self.assertEqual(self.store.get_password_identity("person@example.com")[1], original_hash)
        token = self.store.create_password_reset_token("person@example.com")
        replacement = hash_password("a different long password")
        self.assertEqual(self.store.consume_password_reset_token(token, replacement), user_id)
        self.assertIsNone(self.store.consume_password_reset_token(token, hashed))
        self.assertEqual(self.store.get_password_identity("person@example.com")[1], replacement)

    def test_password_registration_verification_login_and_reset_routes(self):
        from dataclasses import replace
        from pdf2md_server.server import create_server

        settings = replace(self.settings, brevo_api_key="test-key", email_from="pdf2md@example.com")
        mcp_server, store = create_server(settings)
        app = mcp_server.streamable_http_app(streamable_http_path="/mcp", json_response=True)
        state = "route-state-register"
        store.save_auth_request(
            state=state, client_id="test-client", redirect_uri="http://127.0.0.1:8765/callback",
            redirect_uri_explicit=True, client_state="chat-state", scopes=["pdf:convert"],
            code_challenge="test-challenge", resource=settings.resource_url,
            expires_at=int(time.time()) + 1800,
        )
        sent: list[tuple[str, str]] = []

        async def fake_send(_mailer, recipient: str, verification_url: str) -> None:
            sent.append((recipient, verification_url))

        async def exercise_routes():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
                register = await client.post(
                    "/auth/password/register",
                    data={"state": state, "email": "New.User@example.com", "password": "correct horse battery staple"},
                )
                self.assertEqual(register.status_code, 200)
                self.assertIn("Check your email", register.text)
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0][0], "new.user@example.com")

                token = parse_qs(urlparse(sent[0][1]).query)["token"][0]
                # Mail scanners can open the confirmation link without consuming it.
                opened = await client.get(f"/auth/email/verify?token={token}")
                self.assertEqual(opened.status_code, 200)
                self.assertIn("Confirm email", opened.text)

                verified = await client.post("/auth/email/verify", data={"token": token}, follow_redirects=False)
                self.assertEqual(verified.status_code, 302)
                self.assertIn("code=", verified.headers["location"])
                user = store.get_password_identity("new.user@example.com")
                self.assertIsNotNone(user)

                # A second registration attempt must not silently sign in or replace the password.
                duplicate_state = "route-state-duplicate"
                store.save_auth_request(
                    state=duplicate_state, client_id="test-client", redirect_uri="http://127.0.0.1:8765/callback",
                    redirect_uri_explicit=True, client_state="duplicate-state", scopes=["pdf:convert"],
                    code_challenge="test-challenge", resource=settings.resource_url,
                    expires_at=int(time.time()) + 1800,
                )
                duplicate = await client.post(
                    "/auth/password/register",
                    data={"state": duplicate_state, "email": "new.user@example.com", "password": "another correct password"},
                )
                self.assertEqual(duplicate.status_code, 409)
                self.assertTrue(verify_password("correct horse battery staple", store.get_password_identity("new.user@example.com")[1]))
                self.assertFalse(verify_password("another correct password", store.get_password_identity("new.user@example.com")[1]))

                login_state = "route-state-login"
                store.save_auth_request(
                    state=login_state, client_id="test-client", redirect_uri="http://127.0.0.1:8765/callback",
                    redirect_uri_explicit=True, client_state="login-state", scopes=["pdf:convert"],
                    code_challenge="test-challenge", resource=settings.resource_url,
                    expires_at=int(time.time()) + 1800,
                )
                login = await client.post(
                    "/auth/password/login",
                    data={"state": login_state, "email": "new.user@example.com", "password": "correct horse battery staple"},
                    follow_redirects=False,
                )
                self.assertEqual(login.status_code, 302)

                user_id = user[0]
                old_access = "active-access-before-reset"
                old_refresh = "active-refresh-before-reset"
                store.save_token(raw_token=old_access, token_type="access", client_id="test-client",
                                 subject=user_id, scopes=["pdf:convert"], resource=settings.resource_url,
                                 expires_at=int(time.time()) + 300, claims={})
                store.save_token(raw_token=old_refresh, token_type="refresh", client_id="test-client",
                                 subject=user_id, scopes=["pdf:convert"], resource=settings.resource_url,
                                 expires_at=int(time.time()) + 3600, claims={})
                reset_token = store.create_password_reset_token("new.user@example.com")
                reset = await client.post(
                    "/auth/password/reset",
                    data={"token": reset_token, "password": "a completely new password"},
                    follow_redirects=False,
                )
                self.assertEqual(reset.status_code, 200)
                self.assertIsNone(store.load_access_token(old_access))
                self.assertIsNone(store.load_refresh_token(old_refresh))
                self.assertTrue(verify_password("a completely new password", store.get_password_identity("new.user@example.com")[1]))

        try:
            with patch.object(BrevoMailer, "send_email_verification", fake_send):
                asyncio.run(exercise_routes())
        finally:
            store.close()

    def test_daily_quota_is_atomic(self):
        self.assertTrue(self.store.try_reserve_usage(user_id="u", amount=4, usage_date="2026-10-17", daily_limit=5))
        self.assertFalse(self.store.try_reserve_usage(user_id="u", amount=2, usage_date="2026-10-17", daily_limit=5))
        self.assertEqual(self.store.usage_bytes(user_id="u", usage_date="2026-10-17"), 4)


if __name__ == "__main__":
    unittest.main()
