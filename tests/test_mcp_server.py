import asyncio
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, OAuthClientInformationFull

from pdf2md_server.auth import PDF2MDOAuthProvider
from pdf2md_server.config import Settings
from pdf2md_server.storage import SQLiteStore


class McpAuthTests(unittest.TestCase):
    def test_tool_declares_chatgpt_file_parameter(self):
        from pdf2md_server.server import server

        tool = asyncio.run(server.list_tools())[0]
        self.assertEqual(tool.meta, {"openai/fileParams": ["file"]})
        attachment = tool.input_schema["$defs"]["PdfAttachment"]
        self.assertEqual(
            set(attachment["properties"]),
            {"download_url", "file_id", "mime_type", "file_name"},
        )
        self.assertEqual(set(attachment["required"]), {"download_url", "file_id"})

    def test_authorization_code_and_access_token_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                public_base_url="http://localhost:8000",
                issuer_url="http://localhost:8000",
                resource_url="http://localhost:8000/mcp",
                database_path=Path(temp_dir) / "state.sqlite3",
                session_secret="local-development-secret-please-change",
            )
            store = SQLiteStore(settings.database_path)
            provider = PDF2MDOAuthProvider(settings, store)
            client = OAuthClientInformationFull(
                client_id="client-1",
                client_secret=None,
                redirect_uris=["http://localhost:6274/oauth/callback"],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope="pdf:convert",
            )

            asyncio.run(provider.register_client(client))
            saved = asyncio.run(provider.get_client("client-1"))
            self.assertIsNotNone(saved)
            self.assertEqual(saved.client_id, "client-1")

            params = AuthorizationParams(
                state="chatgpt-state",
                scopes=["pdf:convert"],
                code_challenge="verifier-challenge",
                redirect_uri="http://localhost:6274/oauth/callback",
                redirect_uri_provided_explicitly=True,
                resource=settings.resource_url,
            )
            login_url = asyncio.run(provider.authorize(client, params))
            state = parse_qs(urlparse(login_url).query)["state"][0]
            self.assertEqual(urlparse(login_url).path, "/login")

            user = store.upsert_user(email="person@example.com", provider="google", provider_sub="google-sub")
            linked = store.upsert_user(email="person@example.com", provider="microsoft", provider_sub="microsoft-sub")
            self.assertEqual(linked.user_id, user.user_id)
            code = AuthorizationCode(
                code="test-code",
                scopes=["pdf:convert"],
                expires_at=4102444800,
                client_id=client.client_id,
                code_challenge="verifier-challenge",
                redirect_uri="http://localhost:6274/oauth/callback",
                redirect_uri_provided_explicitly=True,
                resource=settings.resource_url,
                subject=user.user_id,
            )
            store.save_authorization_code(code)
            loaded = asyncio.run(provider.load_authorization_code(client, "test-code"))
            self.assertEqual(loaded.subject, user.user_id)
            token = asyncio.run(provider.exchange_authorization_code(client, loaded))
            self.assertTrue(token.access_token)
            access = asyncio.run(provider.load_access_token(token.access_token))
            self.assertEqual(access.subject, user.user_id)
            self.assertEqual(state, store.get_auth_request(state)["state"])

    def test_daily_quota_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "state.sqlite3")
            self.assertTrue(store.try_reserve_usage(user_id="u", amount=4, usage_date="2026-10-17", daily_limit=5))
            self.assertFalse(store.try_reserve_usage(user_id="u", amount=2, usage_date="2026-10-17", daily_limit=5))
            self.assertEqual(store.usage_bytes(user_id="u", usage_date="2026-10-17"), 4)


if __name__ == "__main__":
    unittest.main()
