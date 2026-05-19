from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from PySide6.QtCore import QObject


@dataclass(slots=True)
class TwitchCredentials:
    client_id: str = ""
    access_token: str = ""
    broadcaster_id: str = ""


@dataclass(slots=True)
class TwitchCategory:
    id: str
    name: str


@dataclass(slots=True)
class TwitchChannelInfo:
    broadcaster_id: str
    broadcaster_name: str
    title: str
    category_id: str
    category_name: str


class TwitchApiError(RuntimeError):
    pass


class TwitchService(QObject):
    BASE_URL = "https://api.twitch.tv/helix"

    def has_credentials(self, credentials: TwitchCredentials) -> bool:
        return bool(credentials.client_id.strip() and self._normalize_token(credentials.access_token))

    async def get_channel_info(self, credentials: TwitchCredentials) -> TwitchChannelInfo:
        return await asyncio.to_thread(self._get_channel_info_sync, credentials)

    async def search_categories(
        self,
        credentials: TwitchCredentials,
        query: str,
        limit: int = 10,
    ) -> list[TwitchCategory]:
        query = query.strip()
        if not query:
            return []
        return await asyncio.to_thread(self._search_categories_sync, credentials, query, limit)

    async def update_channel_info(
        self,
        credentials: TwitchCredentials,
        *,
        title: str = "",
        category_id: str = "",
    ) -> TwitchChannelInfo:
        return await asyncio.to_thread(self._update_channel_info_sync, credentials, title, category_id)

    def _get_channel_info_sync(self, credentials: TwitchCredentials) -> TwitchChannelInfo:
        client_id, access_token = self._validated_auth(credentials)
        broadcaster_id = self._resolve_broadcaster_id_sync(credentials, client_id, access_token)
        response = self._request_json(
            "GET",
            "/channels",
            client_id,
            access_token,
            query={"broadcaster_id": broadcaster_id},
        )
        entries = response.get("data", [])
        if not entries:
            raise TwitchApiError("Twitch did not return channel information for this broadcaster.")
        channel = entries[0]
        return TwitchChannelInfo(
            broadcaster_id=str(channel.get("broadcaster_id", broadcaster_id)),
            broadcaster_name=str(channel.get("broadcaster_name", "")).strip(),
            title=str(channel.get("title", "")).strip(),
            category_id=str(channel.get("game_id", "")).strip(),
            category_name=str(channel.get("game_name", "")).strip(),
        )

    def _search_categories_sync(
        self,
        credentials: TwitchCredentials,
        query: str,
        limit: int,
    ) -> list[TwitchCategory]:
        client_id, access_token = self._validated_auth(credentials)
        response = self._request_json(
            "GET",
            "/search/categories",
            client_id,
            access_token,
            query={"query": query, "first": max(1, min(limit, 25))},
        )
        categories: list[TwitchCategory] = []
        for item in response.get("data", []):
            category_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            if category_id and name:
                categories.append(TwitchCategory(id=category_id, name=name))
        return categories

    def _update_channel_info_sync(
        self,
        credentials: TwitchCredentials,
        title: str,
        category_id: str,
    ) -> TwitchChannelInfo:
        client_id, access_token = self._validated_auth(credentials)
        broadcaster_id = self._resolve_broadcaster_id_sync(credentials, client_id, access_token)

        payload: dict[str, str] = {}
        cleaned_title = title.strip()
        cleaned_category = category_id.strip()
        if cleaned_title:
            payload["title"] = cleaned_title
        if cleaned_category:
            payload["game_id"] = cleaned_category
        if not payload:
            raise TwitchApiError("Provide a title or category before updating Twitch channel information.")

        self._request_json(
            "PATCH",
            "/channels",
            client_id,
            access_token,
            query={"broadcaster_id": broadcaster_id},
            body=payload,
        )
        updated_credentials = TwitchCredentials(
            client_id=credentials.client_id,
            access_token=credentials.access_token,
            broadcaster_id=broadcaster_id,
        )
        return self._get_channel_info_sync(updated_credentials)

    def _resolve_broadcaster_id_sync(
        self,
        credentials: TwitchCredentials,
        client_id: str,
        access_token: str,
    ) -> str:
        broadcaster_id = credentials.broadcaster_id.strip()
        if broadcaster_id:
            return broadcaster_id
        response = self._request_json("GET", "/users", client_id, access_token)
        users = response.get("data", [])
        if not users:
            raise TwitchApiError("Twitch did not return a broadcaster ID for the current access token.")
        broadcaster_id = str(users[0].get("id", "")).strip()
        if not broadcaster_id:
            raise TwitchApiError("Twitch returned an empty broadcaster ID.")
        return broadcaster_id

    def _validated_auth(self, credentials: TwitchCredentials) -> tuple[str, str]:
        client_id = credentials.client_id.strip()
        access_token = self._normalize_token(credentials.access_token)
        if not client_id:
            raise TwitchApiError("Enter your Twitch client ID first.")
        if not access_token:
            raise TwitchApiError("Enter a Twitch user access token first.")
        return client_id, access_token

    @staticmethod
    def _normalize_token(token: str) -> str:
        cleaned = token.strip()
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        if cleaned.lower().startswith("oauth:"):
            cleaned = cleaned[6:].strip()
        return cleaned

    def _request_json(
        self,
        method: str,
        path: str,
        client_id: str,
        access_token: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        if query:
            encoded_query = parse.urlencode(query, doseq=True)
            url = f"{url}?{encoded_query}"

        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")

        req = request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Client-Id": client_id,
                "Content-Type": "application/json",
                "User-Agent": "StreamControl/0.1",
            },
        )

        try:
            with request.urlopen(req, timeout=10) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            message = self._error_message(raw) or f"Twitch API request failed with HTTP {exc.code}."
            raise TwitchApiError(message) from exc
        except error.URLError as exc:
            raise TwitchApiError(f"Could not reach Twitch: {exc.reason}") from exc

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TwitchApiError("Twitch returned an unreadable response.") from exc

    @staticmethod
    def _error_message(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="ignore").strip()
        return str(payload.get("message", "")).strip()
