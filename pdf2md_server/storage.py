from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    provider: str
    provider_sub: str


class QuotaExceeded(Exception):
    """Raised when a user would exceed the daily byte quota."""


class SQLiteStore:
    """Small SQLite persistence layer for OAuth state, tokens, users and quota."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    provider TEXT NOT NULL,
                    provider_sub TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(provider, provider_sub)
                );
                CREATE TABLE IF NOT EXISTS user_identities (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_sub TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER NOT NULL,
                    PRIMARY KEY(provider, provider_sub),
                    UNIQUE(user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS auth_requests (
                    state TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_explicit INTEGER NOT NULL,
                    client_state TEXT,
                    scopes_json TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    resource TEXT,
                    provider_state TEXT,
                    provider_name TEXT,
                    provider_nonce TEXT,
                    provider_verifier TEXT,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorization_codes (
                    code_hash TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    redirect_uri_explicit INTEGER NOT NULL,
                    resource TEXT,
                    expires_at INTEGER NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    token_type TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    resource TEXT,
                    expires_at INTEGER,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    claims_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    user_id TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    PRIMARY KEY (user_id, usage_date)
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # OAuth clients -----------------------------------------------------
    def save_client(self, metadata: dict[str, Any]) -> None:
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO clients(client_id, metadata_json, created_at) VALUES (?, ?, ?)",
                (metadata["client_id"], _json(metadata), now),
            )

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT metadata_json FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return json.loads(row["metadata_json"]) if row else None

    # Users -------------------------------------------------------------
    def upsert_user(self, *, email: str, provider: str, provider_sub: str) -> User:
        email = email.strip().casefold()
        if not email or "@" not in email:
            raise ValueError("The identity provider did not return a usable email address")
        now = int(time.time())
        with self._lock, self._connection:
            identity = self._connection.execute(
                "SELECT user_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                (provider, provider_sub),
            ).fetchone()
            email_row = self._connection.execute(
                "SELECT user_id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if identity:
                user_id = identity["user_id"]
                if email_row and email_row["user_id"] != user_id:
                    raise ValueError("This provider identity is already linked to another PDF2MD account")
            elif email_row:
                # One verified email is one PDF2MD account. A second provider
                # identity with the same email is linked to the existing user.
                user_id = email_row["user_id"]
            else:
                user_id = secrets.token_urlsafe(24)
                self._connection.execute(
                    "INSERT INTO users(user_id, email, provider, provider_sub, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, email, provider, provider_sub, now, now),
                )
            self._connection.execute(
                "UPDATE users SET provider = ?, provider_sub = ?, updated_at = ? WHERE user_id = ?",
                (provider, provider_sub, now, user_id),
            )
            self._connection.execute(
                """INSERT INTO user_identities(user_id, provider, provider_sub, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_sub) DO UPDATE SET last_login_at = excluded.last_login_at""",
                (user_id, provider, provider_sub, now, now),
            )
        return User(user_id=user_id, email=email, provider=provider, provider_sub=provider_sub)

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id, email, provider, provider_sub FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return User(**dict(row)) if row else None

    # OAuth request and code lifecycle ---------------------------------
    def save_auth_request(self, *, state: str, client_id: str, redirect_uri: str, redirect_uri_explicit: bool,
                          client_state: str | None, scopes: list[str], code_challenge: str, resource: str | None,
                          expires_at: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO auth_requests
                (state, client_id, redirect_uri, redirect_uri_explicit, client_state, scopes_json,
                 code_challenge, resource, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (state, client_id, redirect_uri, int(redirect_uri_explicit), client_state, _json(scopes),
                 code_challenge, resource, expires_at),
            )

    def get_auth_request(self, state: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM auth_requests WHERE state = ? AND expires_at >= ?", (state, int(time.time()))
            ).fetchone()
        return row

    def get_auth_request_by_provider_state(self, provider_state: str) -> sqlite3.Row | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM auth_requests WHERE provider_state = ? AND expires_at >= ?",
                (provider_state, int(time.time())),
            ).fetchone()
        return row

    def set_provider_state(self, state: str, *, provider_name: str, provider_state: str, provider_nonce: str,
                           provider_verifier: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE auth_requests
                SET provider_name = ?, provider_state = ?, provider_nonce = ?, provider_verifier = ?
                WHERE state = ?""",
                (provider_name, provider_state, provider_nonce, provider_verifier, state),
            )

    def delete_auth_request(self, state: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM auth_requests WHERE state = ?", (state,))

    def save_authorization_code(self, code: AuthorizationCode) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO authorization_codes
                (code_hash, code, client_id, subject, scopes_json, code_challenge, redirect_uri,
                 redirect_uri_explicit, resource, expires_at, used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (_hash(code.code), code.code, code.client_id, code.subject or "", _json(code.scopes),
                 code.code_challenge, str(code.redirect_uri), int(code.redirect_uri_provided_explicitly),
                 code.resource, int(code.expires_at),),
            )

    def get_authorization_code(self, code: str) -> AuthorizationCode | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM authorization_codes WHERE code_hash = ? AND used = 0 AND expires_at >= ?",
                (_hash(code), int(time.time())),
            ).fetchone()
        if not row:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]),
            resource=row["resource"],
            subject=row["subject"],
        )

    def consume_authorization_code(self, code: str) -> bool:
        with self._lock, self._connection:
            result = self._connection.execute(
                "UPDATE authorization_codes SET used = 1 WHERE code_hash = ? AND used = 0 AND expires_at >= ?",
                (_hash(code), int(time.time())),
            )
        return result.rowcount == 1

    # Tokens ------------------------------------------------------------
    def save_token(self, *, raw_token: str, token_type: str, client_id: str, subject: str,
                   scopes: list[str], resource: str | None, expires_at: int | None,
                   claims: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO tokens
                (token_hash, token_type, client_id, subject, scopes_json, resource, expires_at,
                 revoked, claims_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (_hash(raw_token), token_type, client_id, subject, _json(scopes), resource, expires_at,
                 _json(claims), int(time.time())),
            )

    def load_access_token(self, raw_token: str) -> AccessToken | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND token_type = 'access' AND revoked = 0",
                (_hash(raw_token),),
            ).fetchone()
        if not row:
            return None
        return AccessToken(
            token=raw_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
            claims=json.loads(row["claims_json"]),
        )

    def load_refresh_token(self, raw_token: str) -> RefreshToken | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND token_type = 'refresh' AND revoked = 0",
                (_hash(raw_token),),
            ).fetchone()
        if not row:
            return None
        return RefreshToken(
            token=raw_token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    def revoke_token(self, raw_token: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE tokens SET revoked = 1 WHERE token_hash = ?", (_hash(raw_token),))

    # Quota -------------------------------------------------------------
    def reserve_usage(self, *, user_id: str, amount: int, usage_date: str) -> bool:
        """Atomically reserve bytes; the caller supplies the daily limit separately."""

        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO usage(user_id, usage_date, bytes) VALUES (?, ?, 0)",
                (user_id, usage_date),
            )
            self._connection.execute(
                "UPDATE usage SET bytes = bytes + ? WHERE user_id = ? AND usage_date = ?",
                (amount, user_id, usage_date),
            )
            row = self._connection.execute(
                "SELECT bytes FROM usage WHERE user_id = ? AND usage_date = ?", (user_id, usage_date)
            ).fetchone()
        return bool(row and row["bytes"] >= 0)

    def usage_bytes(self, *, user_id: str, usage_date: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT bytes FROM usage WHERE user_id = ? AND usage_date = ?", (user_id, usage_date)
            ).fetchone()
        return int(row["bytes"]) if row else 0

    def try_reserve_usage(self, *, user_id: str, amount: int, usage_date: str, daily_limit: int) -> bool:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO usage(user_id, usage_date, bytes) VALUES (?, ?, 0)",
                (user_id, usage_date),
            )
            result = self._connection.execute(
                "UPDATE usage SET bytes = bytes + ? WHERE user_id = ? AND usage_date = ? AND bytes + ? <= ?",
                (amount, user_id, usage_date, amount, daily_limit),
            )
        return result.rowcount == 1

