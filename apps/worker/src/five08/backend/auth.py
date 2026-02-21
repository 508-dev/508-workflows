"""OIDC/session and Discord-admin authorization helpers for backend API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import jwt
from jwt import InvalidTokenError
from psycopg.rows import dict_row
from redis import Redis

from five08.queue import get_postgres_connection
from five08.worker.config import WorkerSettings

logger = logging.getLogger(__name__)

DISCORD_API_BASE_URL = "https://discord.com/api/v10"


@dataclass(frozen=True)
class OIDCMetadata:
    """OIDC provider discovery payload used during auth flows."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None


@dataclass(frozen=True)
class PendingOIDCState:
    """Transient login state persisted server-side between login and callback."""

    nonce: str
    code_verifier: str
    next_path: str
    discord_link_token: str | None = None


@dataclass(frozen=True)
class AuthSession:
    """Server-side session payload for authenticated dashboard requests."""

    subject: str
    email: str | None
    display_name: str | None
    groups: list[str]
    is_admin: bool
    id_token: str
    expires_at: int


@dataclass(frozen=True)
class DiscordLinkGrant:
    """One-time link grant created from a Discord command context."""

    discord_user_id: str
    next_path: str


class RedisAuthStore:
    """Redis-backed storage for OIDC states, sessions, and Discord link grants."""

    def __init__(self, redis_conn: Redis) -> None:
        self.redis_conn = redis_conn

    async def save_oidc_state(
        self,
        *,
        state: str,
        payload: PendingOIDCState,
        ttl_seconds: int,
    ) -> None:
        await self._set_json(
            self._oidc_state_key(state), asdict(payload), ttl_seconds=ttl_seconds
        )

    async def pop_oidc_state(self, state: str) -> PendingOIDCState | None:
        value = await self._pop_json(self._oidc_state_key(state))
        if value is None:
            return None
        try:
            return PendingOIDCState(
                nonce=str(value["nonce"]),
                code_verifier=str(value["code_verifier"]),
                next_path=str(value["next_path"]),
                discord_link_token=_to_optional_str(value.get("discord_link_token")),
            )
        except Exception:
            logger.warning("Invalid OIDC state payload in Redis")
            return None

    async def save_session(
        self,
        *,
        session_id: str,
        payload: AuthSession,
        ttl_seconds: int,
    ) -> None:
        await self._set_json(
            self._session_key(session_id), asdict(payload), ttl_seconds=ttl_seconds
        )

    async def get_session(self, session_id: str) -> AuthSession | None:
        value = await self._get_json(self._session_key(session_id))
        if value is None:
            return None

        try:
            parsed = AuthSession(
                subject=str(value["subject"]),
                email=_to_optional_str(value.get("email")),
                display_name=_to_optional_str(value.get("display_name")),
                groups=_to_string_list(value.get("groups")),
                is_admin=bool(value.get("is_admin", False)),
                id_token=str(value["id_token"]),
                expires_at=int(value["expires_at"]),
            )
        except Exception:
            logger.warning("Invalid auth session payload in Redis")
            return None

        now = int(time.time())
        if parsed.expires_at <= now:
            await self.delete_session(session_id)
            return None

        return parsed

    async def delete_session(self, session_id: str) -> None:
        await asyncio.to_thread(self.redis_conn.delete, self._session_key(session_id))

    async def save_discord_link(
        self,
        *,
        token: str,
        payload: DiscordLinkGrant,
        ttl_seconds: int,
    ) -> None:
        await self._set_json(
            self._discord_link_key(token), asdict(payload), ttl_seconds=ttl_seconds
        )

    async def get_discord_link(self, token: str) -> DiscordLinkGrant | None:
        value = await self._get_json(self._discord_link_key(token))
        if value is None:
            return None

        try:
            return DiscordLinkGrant(
                discord_user_id=str(value["discord_user_id"]),
                next_path=str(value["next_path"]),
            )
        except Exception:
            logger.warning("Invalid discord-link payload in Redis")
            return None

    async def delete_discord_link(self, token: str) -> None:
        await asyncio.to_thread(
            self.redis_conn.delete,
            self._discord_link_key(token),
        )

    async def _set_json(
        self, key: str, payload: dict[str, Any], *, ttl_seconds: int
    ) -> None:
        await asyncio.to_thread(
            self.redis_conn.setex,
            key,
            max(1, ttl_seconds),
            json.dumps(payload, separators=(",", ":")),
        )

    async def _get_json(self, key: str) -> dict[str, Any] | None:
        raw = await asyncio.to_thread(self.redis_conn.get, key)
        if raw is None:
            return None
        if not isinstance(raw, (bytes, str)):
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    async def _pop_json(self, key: str) -> dict[str, Any] | None:
        raw = await asyncio.to_thread(_redis_getdel, self.redis_conn, key)
        if raw is None:
            return None
        if not isinstance(raw, (bytes, str)):
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _oidc_state_key(state: str) -> str:
        return f"auth:oidc-state:{state}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"auth:session:{session_id}"

    @staticmethod
    def _discord_link_key(token: str) -> str:
        return f"auth:discord-link:{token}"


class OIDCProviderClient:
    """Small OIDC client for discovery and token exchange."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self._metadata: OIDCMetadata | None = None
        self._metadata_lock = asyncio.Lock()
        self._jwks: dict[str, Any] | None = None
        self._jwks_loaded_at = 0.0
        self._jwks_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.oidc_issuer_url
            and self.settings.oidc_client_id
            and self.settings.oidc_client_secret
        )

    async def get_metadata(self, http_client: httpx.AsyncClient) -> OIDCMetadata:
        cached = self._metadata
        if cached is not None:
            return cached

        async with self._metadata_lock:
            if self._metadata is not None:
                return self._metadata

            issuer = self.settings.oidc_issuer_url.strip().rstrip("/")
            if not issuer:
                raise ValueError("OIDC_ISSUER_URL is not configured")

            url = f"{issuer}/.well-known/openid-configuration"
            response = await http_client.get(
                url, timeout=self.settings.oidc_http_timeout_seconds
            )
            response.raise_for_status()
            payload = response.json()

            metadata = OIDCMetadata(
                issuer=str(payload["issuer"]),
                authorization_endpoint=str(payload["authorization_endpoint"]),
                token_endpoint=str(payload["token_endpoint"]),
                jwks_uri=str(payload["jwks_uri"]),
                end_session_endpoint=_to_optional_str(
                    payload.get("end_session_endpoint")
                ),
            )
            self._metadata = metadata
            return metadata

    async def exchange_code(
        self,
        http_client: httpx.AsyncClient,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        metadata = await self.get_metadata(http_client)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.oidc_client_id,
            "client_secret": self.settings.oidc_client_secret,
            "code_verifier": code_verifier,
        }

        response = await http_client.post(
            metadata.token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.settings.oidc_http_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid token response payload")
        return payload

    async def validate_id_token(
        self,
        http_client: httpx.AsyncClient,
        *,
        id_token: str,
        nonce: str,
    ) -> dict[str, Any]:
        metadata = await self.get_metadata(http_client)
        jwks = await self._get_jwks(http_client, metadata)
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        if not kid:
            raise InvalidTokenError("Missing kid in token header")

        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise InvalidTokenError("Invalid JWKS payload")

        key_data = next(
            (
                item
                for item in keys
                if isinstance(item, dict) and item.get("kid") == kid
            ),
            None,
        )
        if key_data is None:
            raise InvalidTokenError("Signing key not found")

        signing_key = cast(
            Any,
            jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data)),
        )
        claims = jwt.decode(
            id_token,
            key=signing_key,
            algorithms=["RS256", "RS384", "RS512"],
            audience=self.settings.oidc_client_id,
            issuer=metadata.issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )

        token_nonce = str(claims.get("nonce", ""))
        if not secrets.compare_digest(token_nonce, nonce):
            raise InvalidTokenError("Invalid nonce")
        return claims

    async def _get_jwks(
        self,
        http_client: httpx.AsyncClient,
        metadata: OIDCMetadata,
    ) -> dict[str, Any]:
        now = time.time()
        if (
            self._jwks is not None
            and (now - self._jwks_loaded_at) < self.settings.oidc_jwks_cache_seconds
        ):
            return self._jwks

        async with self._jwks_lock:
            now = time.time()
            if (
                self._jwks is not None
                and (now - self._jwks_loaded_at) < self.settings.oidc_jwks_cache_seconds
            ):
                return self._jwks

            response = await http_client.get(
                metadata.jwks_uri,
                timeout=self.settings.oidc_http_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid JWKS payload")

            self._jwks = payload
            self._jwks_loaded_at = time.time()
            return payload


def make_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE verifier/challenge pair."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, challenge


def normalize_next_path(candidate: str | None, *, fallback: str = "/") -> str:
    """Allow only local absolute paths to avoid open redirects."""
    if candidate is None:
        return fallback
    value = candidate.strip()
    if not value.startswith("/"):
        return fallback
    if value.startswith("//"):
        return fallback
    return value


def build_redirect_uri(settings: WorkerSettings, *, request_base_url: str) -> str:
    """Compute callback redirect URI for the current deployment topology."""
    callback_path = settings.oidc_callback_path
    if not callback_path.startswith("/"):
        callback_path = f"/{callback_path}"

    configured = (settings.oidc_redirect_base_url or "").strip().rstrip("/")
    if configured:
        return f"{configured}{callback_path}"

    base = request_base_url.strip().rstrip("/")
    return f"{base}{callback_path}"


def build_authorization_url(
    metadata: OIDCMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build OIDC authorization URL for browser redirect."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


def extract_groups(claims: dict[str, Any], *, claim_name: str) -> list[str]:
    """Extract normalized group names from token claims."""
    value = claims.get(claim_name)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def is_admin_from_groups(
    groups: list[str], *, configured_admin_groups: set[str]
) -> bool:
    """Return whether user has at least one configured admin group."""
    normalized = {group.casefold() for group in groups if group}
    return bool(normalized & configured_admin_groups)


class DiscordAdminVerifier:
    """Resolve whether a Discord user is admin from people cache with API fallback."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings

    async def is_admin_discord_user(
        self,
        *,
        discord_user_id: str,
        http_client: httpx.AsyncClient,
    ) -> bool:
        if await asyncio.to_thread(
            self._is_admin_from_people_db,
            discord_user_id,
        ):
            return True

        return await self._is_admin_from_discord_api(
            discord_user_id=discord_user_id,
            http_client=http_client,
        )

    async def is_admin_email_for_discord_user(
        self,
        *,
        email: str,
        discord_user_id: str,
    ) -> bool:
        """Check if OIDC email maps to an active admin row for this Discord user."""
        normalized_email = email.strip().lower()
        if not normalized_email:
            return False

        return await asyncio.to_thread(
            self._is_admin_email_for_discord_user_sync,
            normalized_email,
            discord_user_id,
        )

    def _is_admin_from_people_db(self, discord_user_id: str) -> bool:
        role_names = self.settings.discord_admin_role_names
        query = """
            SELECT discord_roles
            FROM people
            WHERE sync_status = 'active' AND discord_user_id = %s
            LIMIT 1;
        """
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, (discord_user_id,))
                row = cursor.fetchone()

        if row is None:
            return False

        raw_roles = row.get("discord_roles")
        parsed_roles = {role.casefold() for role in _to_string_list(raw_roles)}
        return bool(parsed_roles & role_names)

    async def _is_admin_from_discord_api(
        self,
        *,
        discord_user_id: str,
        http_client: httpx.AsyncClient,
    ) -> bool:
        bot_token = (self.settings.discord_bot_token or "").strip()
        guild_id = (self.settings.discord_admin_guild_id or "").strip()
        role_names = self.settings.discord_admin_role_names
        if not bot_token or not guild_id or not role_names:
            return False

        headers = {"Authorization": f"Bot {bot_token}"}

        member_response = await http_client.get(
            f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{discord_user_id}",
            headers=headers,
            timeout=self.settings.discord_api_timeout_seconds,
        )
        if member_response.status_code == 404:
            return False
        member_response.raise_for_status()

        member_payload = member_response.json()
        if not isinstance(member_payload, dict):
            return False

        member_role_ids = {
            str(role_id)
            for role_id in member_payload.get("roles", [])
            if isinstance(role_id, str)
        }
        if not member_role_ids:
            return False

        roles_response = await http_client.get(
            f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/roles",
            headers=headers,
            timeout=self.settings.discord_api_timeout_seconds,
        )
        roles_response.raise_for_status()

        roles_payload = roles_response.json()
        if not isinstance(roles_payload, list):
            return False

        member_role_names: set[str] = set()
        for role_obj in roles_payload:
            if not isinstance(role_obj, dict):
                continue
            role_id = role_obj.get("id")
            role_name = role_obj.get("name")
            if not isinstance(role_id, str) or not isinstance(role_name, str):
                continue
            if role_id in member_role_ids:
                member_role_names.add(role_name.casefold())

        return bool(member_role_names & role_names)

    def _is_admin_email_for_discord_user_sync(
        self,
        email: str,
        discord_user_id: str,
    ) -> bool:
        role_names = self.settings.discord_admin_role_names
        query = """
            SELECT discord_roles
            FROM people
            WHERE sync_status = 'active'
              AND discord_user_id = %s
              AND (lower(email) = %s OR lower(email_508) = %s)
            LIMIT 1;
        """
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, (discord_user_id, email, email))
                row = cursor.fetchone()

        if row is None:
            return False

        raw_roles = row.get("discord_roles")
        parsed_roles = {role.casefold() for role in _to_string_list(raw_roles)}
        return bool(parsed_roles & role_names)


def _to_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    return []


def _redis_getdel(redis_conn: Redis, key: str) -> bytes | str | None:
    # GETDEL is atomic in Redis >=6.2 and supported by redis-py.
    return redis_conn.execute_command("GETDEL", key)
