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


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 254 or email.count("@") != 1 or any(ch.isspace() for ch in email):
        raise ValueError("Enter a valid email address")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Enter a valid email address")
    return email


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class User:
    user_id: str
    email: str | None
    email_verified: bool


@dataclass(frozen=True)
class PendingIdentity:
    pending_id: str
    auth_state: str
    provider: str
    provider_sub: str
    email: str | None
    email_verified: bool
    password_hash: str | None
    expires_at: int


class QuotaExceeded(Exception):
    """Raised when a user would exceed the daily byte quota."""


class SQLiteStore:
    """SQLite persistence for accounts, sign-in identities, OAuth and quota."""

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
                CREATE TABLE IF NOT EXISTS pdf2md_users (
                    user_id TEXT PRIMARY KEY,
                    transactional_email TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_identities (
                    identity_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES pdf2md_users(user_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    provider_sub TEXT NOT NULL,
                    password_hash TEXT,
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER NOT NULL,
                    UNIQUE(provider, provider_sub),
                    UNIQUE(user_id, provider)
                );
                CREATE TABLE IF NOT EXISTS identity_emails (
                    identity_id TEXT NOT NULL REFERENCES user_identities(identity_id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(identity_id, email)
                );
                CREATE INDEX IF NOT EXISTS idx_identity_emails_email
                    ON identity_emails(email, verified);
                CREATE TABLE IF NOT EXISTS pending_identities (
                    pending_id TEXT PRIMARY KEY,
                    auth_state TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_sub TEXT NOT NULL,
                    password_hash TEXT,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_verification_tokens (
                    token_hash TEXT PRIMARY KEY,
                    pending_id TEXT NOT NULL REFERENCES pending_identities(pending_id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token_hash TEXT PRIMARY KEY,
                    identity_id TEXT NOT NULL REFERENCES user_identities(identity_id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
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
                    pending_identity_id TEXT,
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
                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    bucket_hash TEXT PRIMARY KEY,
                    window_started_at INTEGER NOT NULL,
                    attempts INTEGER NOT NULL
                );
                """
            )
            # Add this column to databases created by earlier prototype versions.
            request_columns = {row[1] for row in self._connection.execute("PRAGMA table_info(auth_requests)")}
            if "pending_identity_id" not in request_columns:
                self._connection.execute("ALTER TABLE auth_requests ADD COLUMN pending_identity_id TEXT")
            self._migrate_legacy_users()

    def _migrate_legacy_users(self) -> None:
        """Preserve prototype account IDs without trusting unrecorded email status."""
        tables = {row[0] for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" not in tables:
            return
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(users)")}
        if not {"user_id", "email", "provider", "provider_sub"}.issubset(columns):
            return
        now = int(time.time())
        rows = self._connection.execute("SELECT user_id, email, provider, provider_sub FROM users").fetchall()
        for row in rows:
            user_id = row["user_id"]
            self._connection.execute(
                "INSERT OR IGNORE INTO pdf2md_users(user_id, transactional_email, created_at, updated_at) VALUES (?, NULL, ?, ?)",
                (user_id, now, now),
            )
            identity = self._connection.execute(
                "SELECT identity_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                (row["provider"], row["provider_sub"]),
            ).fetchone()
            if not identity:
                identity_id = secrets.token_urlsafe(18)
                self._connection.execute(
                    "INSERT OR IGNORE INTO user_identities(identity_id, user_id, provider, provider_sub, password_hash, created_at, last_login_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
                    (identity_id, user_id, row["provider"], row["provider_sub"], now, now),
                )
                identity = self._connection.execute(
                    "SELECT identity_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                    (row["provider"], row["provider_sub"]),
                ).fetchone()
            if identity and row["email"]:
                try:
                    email = normalize_email(row["email"])
                except ValueError:
                    continue
                self._connection.execute(
                    "INSERT OR IGNORE INTO identity_emails(identity_id, email, verified, updated_at) VALUES (?, ?, 0, ?)",
                    (identity["identity_id"], email, now),
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

    # Accounts and identities -------------------------------------------
    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id, transactional_email FROM pdf2md_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                return None
            email = row["transactional_email"]
            verified = bool(email and self._connection.execute(
                """SELECT 1 FROM identity_emails e JOIN user_identities i USING(identity_id)
                WHERE i.user_id = ? AND e.email = ? AND e.verified = 1 LIMIT 1""",
                (user_id, email),
            ).fetchone())
        return User(user_id=user_id, email=email, email_verified=verified)

    def has_verified_email(self, user_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM identity_emails e JOIN user_identities i USING(identity_id) WHERE i.user_id = ? AND e.verified = 1 LIMIT 1",
                (user_id,),
            ).fetchone()
        return bool(row)

    def get_identity_user_id(self, provider: str, provider_sub: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                (provider, provider_sub),
            ).fetchone()
        return str(row["user_id"]) if row else None

    def get_password_identity(self, email: str) -> tuple[str, str] | None:
        email = normalize_email(email)
        with self._lock:
            row = self._connection.execute(
                "SELECT user_id, password_hash FROM user_identities WHERE provider = 'password' AND provider_sub = ?",
                (email,),
            ).fetchone()
        if not row or not row["password_hash"]:
            return None
        return str(row["user_id"]), str(row["password_hash"])

    def record_identity_login(self, *, provider: str, provider_sub: str, email: str | None,
                              email_verified: bool | None) -> str | None:
        now = int(time.time())
        with self._lock, self._connection:
            identity = self._connection.execute(
                "SELECT identity_id, user_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                (provider, provider_sub),
            ).fetchone()
            if not identity:
                return None
            self._connection.execute(
                "UPDATE user_identities SET last_login_at = ? WHERE identity_id = ?",
                (now, identity["identity_id"]),
            )
            if email:
                email = normalize_email(email)
                self._connection.execute(
                    "UPDATE identity_emails SET verified = 0, updated_at = ? WHERE identity_id = ? AND email <> ?",
                    (now, identity["identity_id"], email),
                )
                self._connection.execute(
                    """INSERT INTO identity_emails(identity_id, email, verified, updated_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(identity_id, email) DO UPDATE SET verified = excluded.verified, updated_at = excluded.updated_at""",
                    (identity["identity_id"], email, int(email_verified is True), now),
                )
                if email_verified is True:
                    self._connection.execute(
                        "UPDATE pdf2md_users SET transactional_email = ?, updated_at = ? WHERE user_id = ? AND transactional_email IS NULL",
                        (email, now, identity["user_id"]),
                    )
            return str(identity["user_id"])

    def accounts_with_verified_email(self, email: str) -> list[str]:
        email = normalize_email(email)
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT i.user_id FROM identity_emails e JOIN user_identities i USING(identity_id) WHERE e.email = ? AND e.verified = 1",
                (email,),
            ).fetchall()
        return [str(row["user_id"]) for row in rows]

    def account_methods(self, user_id: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT provider FROM user_identities WHERE user_id = ? ORDER BY provider", (user_id,)
            ).fetchall()
        return [str(row["provider"]) for row in rows]

    def account_emails(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT e.email, MAX(e.verified) AS verified, GROUP_CONCAT(DISTINCT i.provider) AS methods
                FROM identity_emails e JOIN user_identities i USING(identity_id)
                WHERE i.user_id = ? GROUP BY e.email ORDER BY e.email""",
                (user_id,),
            ).fetchall()
            account = self._connection.execute(
                "SELECT transactional_email FROM pdf2md_users WHERE user_id = ?", (user_id,)
            ).fetchone()
        primary = account["transactional_email"] if account else None
        return {
            "transactional_email": primary,
            "emails": [
                {"email": row["email"], "verified": bool(row["verified"]), "methods": sorted((row["methods"] or "").split(",")), "is_transactional": row["email"] == primary}
                for row in rows
            ],
        }

    def set_transactional_email(self, user_id: str, email: str) -> bool:
        email = normalize_email(email)
        now = int(time.time())
        with self._lock, self._connection:
            available = self._connection.execute(
                """SELECT 1 FROM identity_emails e JOIN user_identities i USING(identity_id)
                WHERE i.user_id = ? AND e.email = ? AND e.verified = 1 LIMIT 1""",
                (user_id, email),
            ).fetchone()
            if not available:
                return False
            self._connection.execute(
                "UPDATE pdf2md_users SET transactional_email = ?, updated_at = ? WHERE user_id = ?",
                (email, now, user_id),
            )
            return True

    def create_pending_identity(self, *, auth_state: str, provider: str, provider_sub: str,
                                email: str | None, email_verified: bool, password_hash: str | None = None,
                                expires_at: int) -> str:
        if email:
            email = normalize_email(email)
        pending_id = secrets.token_urlsafe(24)
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO pending_identities(pending_id, auth_state, provider, provider_sub, password_hash,
                email, email_verified, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pending_id, auth_state, provider, provider_sub, password_hash, email, int(email_verified), expires_at, now),
            )
            self._connection.execute(
                "UPDATE auth_requests SET pending_identity_id = ? WHERE state = ?",
                (pending_id, auth_state),
            )
        return pending_id

    def get_pending_identity(self, pending_id: str) -> PendingIdentity | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM pending_identities WHERE pending_id = ? AND expires_at >= ?",
                (pending_id, int(time.time())),
            ).fetchone()
        if not row:
            return None
        return PendingIdentity(
            pending_id=row["pending_id"], auth_state=row["auth_state"], provider=row["provider"],
            provider_sub=row["provider_sub"], email=row["email"], email_verified=bool(row["email_verified"]),
            password_hash=row["password_hash"], expires_at=int(row["expires_at"]),
        )

    def set_pending_email(self, pending_id: str, email: str) -> str:
        email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        pending = self.get_pending_identity(pending_id)
        if not pending:
            raise ValueError("This sign-in request has expired")
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE pending_identities SET email = ?, email_verified = 0 WHERE pending_id = ?",
                (email, pending_id),
            )
            self._connection.execute("DELETE FROM email_verification_tokens WHERE pending_id = ?", (pending_id,))
            self._connection.execute(
                "INSERT INTO email_verification_tokens(token_hash, pending_id, expires_at) VALUES (?, ?, ?)",
                (_hash(token), pending_id, min(now + 3600, int(pending.expires_at))),
            )
        return token

    def consume_email_verification(self, token: str) -> PendingIdentity | None:
        now = int(time.time())
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT t.pending_id FROM email_verification_tokens t JOIN pending_identities p USING(pending_id)
                WHERE t.token_hash = ? AND t.used_at IS NULL AND t.expires_at >= ? AND p.expires_at >= ?""",
                (_hash(token), now, now),
            ).fetchone()
            if not row:
                return None
            pending_id = row["pending_id"]
            self._connection.execute("UPDATE email_verification_tokens SET used_at = ? WHERE token_hash = ?", (now, _hash(token)))
            self._connection.execute("UPDATE pending_identities SET email_verified = 1 WHERE pending_id = ?", (pending_id,))
        return self.get_pending_identity(pending_id)

    def complete_pending_identity(self, pending_id: str, target_user_id: str | None = None) -> tuple[str | None, list[str]]:
        """Complete a verified identity, or return account IDs that require proof first."""
        pending = self.get_pending_identity(pending_id)
        if not pending or not pending.email or not pending.email_verified:
            raise ValueError("A verified email address is required")
        now = int(time.time())
        user_id: str | None = None
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT identity_id, user_id FROM user_identities WHERE provider = ? AND provider_sub = ?",
                (pending.provider, pending.provider_sub),
            ).fetchone()
            candidates = self._connection.execute(
                "SELECT DISTINCT i.user_id FROM identity_emails e JOIN user_identities i USING(identity_id) WHERE e.email = ? AND e.verified = 1",
                (pending.email,),
            ).fetchall()
            candidate_ids = sorted({str(row["user_id"]) for row in candidates})
            if existing:
                user_id = str(existing["user_id"])
                # Email verification alone must never turn a duplicate password
                # registration into a password login or reset. Require the user
                # to prove the already-linked password before proceeding.
                if pending.provider == "password" and pending.password_hash:
                    return None, [user_id]
                if target_user_id and user_id != target_user_id:
                    raise ValueError("This sign-in method is already linked to a different account")
                if candidate_ids and user_id not in candidate_ids:
                    raise ValueError("This email is verified on another PDF2MD account")
                self._connection.execute(
                    "INSERT INTO identity_emails(identity_id, email, verified, updated_at) VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(identity_id, email) DO UPDATE SET verified = 1, updated_at = excluded.updated_at",
                    (existing["identity_id"], pending.email, now),
                )
                account = self._connection.execute(
                    "SELECT transactional_email FROM pdf2md_users WHERE user_id = ?", (user_id,)
                ).fetchone()
                if account and not account["transactional_email"]:
                    self._connection.execute(
                        "UPDATE pdf2md_users SET transactional_email = ?, updated_at = ? WHERE user_id = ?",
                        (pending.email, now, user_id),
                    )
            elif target_user_id:
                if candidate_ids and target_user_id not in candidate_ids:
                    raise ValueError("This email is verified on another PDF2MD account")
                account = self._connection.execute(
                    "SELECT user_id, transactional_email FROM pdf2md_users WHERE user_id = ?", (target_user_id,)
                ).fetchone()
                if not account:
                    raise ValueError("The existing PDF2MD account could not be found")
                user_id = target_user_id
                self._insert_identity(user_id, pending, now)
                if not account["transactional_email"]:
                    self._connection.execute(
                        "UPDATE pdf2md_users SET transactional_email = ?, updated_at = ? WHERE user_id = ?",
                        (pending.email, now, user_id),
                    )
            elif candidate_ids:
                # Do not link by email alone. The caller must authenticate with
                # a method already linked to one of these accounts.
                return None, candidate_ids
            else:
                user_id = secrets.token_urlsafe(24)
                self._connection.execute(
                    "INSERT INTO pdf2md_users(user_id, transactional_email, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, pending.email, now, now),
                )
                self._insert_identity(user_id, pending, now)
            self._connection.execute("DELETE FROM pending_identities WHERE pending_id = ?", (pending_id,))
        return user_id, []

    def _insert_identity(self, user_id: str, pending: PendingIdentity, now: int) -> None:
        identity_id = secrets.token_urlsafe(18)
        self._connection.execute(
            """INSERT INTO user_identities(identity_id, user_id, provider, provider_sub, password_hash, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (identity_id, user_id, pending.provider, pending.provider_sub, pending.password_hash, now, now),
        )
        self._connection.execute(
            "INSERT INTO identity_emails(identity_id, email, verified, updated_at) VALUES (?, ?, 1, ?)",
            (identity_id, pending.email, now),
        )

    def create_password_reset_token(self, email: str) -> str:
        email = normalize_email(email)
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT identity_id FROM user_identities WHERE provider = 'password' AND provider_sub = ?",
                (email,),
            ).fetchone()
            if not row:
                raise ValueError("No email/password identity")
            self._connection.execute(
                "INSERT INTO password_reset_tokens(token_hash, identity_id, expires_at) VALUES (?, ?, ?)",
                (_hash(token), row["identity_id"], now + 3600),
            )
        return token

    def consume_password_reset_token(self, token: str, password_hash: str) -> str | None:
        now = int(time.time())
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT identity_id FROM password_reset_tokens WHERE token_hash = ? AND used_at IS NULL AND expires_at >= ?",
                (_hash(token), now),
            ).fetchone()
            if not row:
                return None
            identity_id = row["identity_id"]
            identity = self._connection.execute(
                "SELECT user_id FROM user_identities WHERE identity_id = ?", (identity_id,)
            ).fetchone()
            if not identity:
                return None
            self._connection.execute(
                "UPDATE user_identities SET password_hash = ? WHERE identity_id = ?",
                (password_hash, identity_id),
            )
            self._connection.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?", (now, _hash(token))
            )
            self._connection.execute(
                "DELETE FROM password_reset_tokens WHERE identity_id = ? AND token_hash <> ?",
                (identity_id, _hash(token)),
            )
        return str(identity["user_id"])

    def delete_pending_identity(self, pending_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM pending_identities WHERE pending_id = ?", (pending_id,))

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
                """UPDATE auth_requests SET provider_name = ?, provider_state = ?, provider_nonce = ?, provider_verifier = ?
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
                 code.resource, int(code.expires_at)),
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
            code=row["code"], scopes=json.loads(row["scopes_json"]), expires_at=row["expires_at"],
            client_id=row["client_id"], code_challenge=row["code_challenge"], redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]), resource=row["resource"], subject=row["subject"],
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
                """INSERT INTO tokens(token_hash, token_type, client_id, subject, scopes_json, resource,
                expires_at, revoked, claims_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
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
        return AccessToken(token=raw_token, client_id=row["client_id"], scopes=json.loads(row["scopes_json"]),
                           expires_at=row["expires_at"], resource=row["resource"], subject=row["subject"],
                           claims=json.loads(row["claims_json"]))

    def load_refresh_token(self, raw_token: str) -> RefreshToken | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND token_type = 'refresh' AND revoked = 0",
                (_hash(raw_token),),
            ).fetchone()
        if not row:
            return None
        return RefreshToken(token=raw_token, client_id=row["client_id"], scopes=json.loads(row["scopes_json"]),
                            expires_at=row["expires_at"], resource=row["resource"], subject=row["subject"])

    def revoke_token(self, raw_token: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("UPDATE tokens SET revoked = 1 WHERE token_hash = ?", (_hash(raw_token),))

    def revoke_user_tokens(self, user_id: str) -> None:
        """Revoke all active sessions after a password reset."""
        with self._lock, self._connection:
            self._connection.execute("UPDATE tokens SET revoked = 1 WHERE subject = ?", (user_id,))

    # Authentication throttling ----------------------------------------
    def allow_auth_attempt(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Apply a persistent per-identifier throttle without storing raw email addresses."""
        now = int(time.time())
        bucket = _hash(key)
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT window_started_at, attempts FROM auth_rate_limits WHERE bucket_hash = ?", (bucket,)
            ).fetchone()
            if not row or now - int(row["window_started_at"]) >= window_seconds:
                self._connection.execute(
                    "INSERT INTO auth_rate_limits(bucket_hash, window_started_at, attempts) VALUES (?, ?, 1) "
                    "ON CONFLICT(bucket_hash) DO UPDATE SET window_started_at = excluded.window_started_at, attempts = 1",
                    (bucket, now),
                )
                return True
            if int(row["attempts"]) >= limit:
                return False
            self._connection.execute(
                "UPDATE auth_rate_limits SET attempts = attempts + 1 WHERE bucket_hash = ?", (bucket,)
            )
            return True

    # Quota -------------------------------------------------------------
    def usage_bytes(self, *, user_id: str, usage_date: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT bytes FROM usage WHERE user_id = ? AND usage_date = ?", (user_id, usage_date)
            ).fetchone()
        return int(row["bytes"]) if row else 0

    def try_reserve_usage(self, *, user_id: str, amount: int, usage_date: str, daily_limit: int) -> bool:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO usage(user_id, usage_date, bytes) VALUES (?, ?, 0)", (user_id, usage_date)
            )
            result = self._connection.execute(
                "UPDATE usage SET bytes = bytes + ? WHERE user_id = ? AND usage_date = ? AND bytes + ? <= ?",
                (amount, user_id, usage_date, amount, daily_limit),
            )
        return result.rowcount == 1
