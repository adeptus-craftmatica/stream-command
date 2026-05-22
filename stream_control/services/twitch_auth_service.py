from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from stream_control.core.tls import describe_tls_error, tls_context


class TwitchAuthError(RuntimeError):
    pass


@dataclass(slots=True)
class TwitchDeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    requested_scopes: list[str]


@dataclass(slots=True)
class TwitchTokenBundle:
    access_token: str
    refresh_token: str
    scopes: list[str]
    token_type: str
    expires_in: int


@dataclass(slots=True)
class TwitchTokenValidation:
    client_id: str
    login: str
    user_id: str
    scopes: list[str]
    expires_in: int


class TwitchAuthService:
    DEVICE_CODE_URL = "https://id.twitch.tv/oauth2/device"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

    async def start_device_authorization(
        self,
        client_id: str,
        scopes: list[str],
    ) -> TwitchDeviceAuthorization:
        cleaned_client_id = client_id.strip()
        if not cleaned_client_id:
            raise TwitchAuthError("Enter the Twitch client ID before starting authorization.")
        normalized_scopes = [scope.strip() for scope in scopes if scope.strip()]
        if not normalized_scopes:
            raise TwitchAuthError("No Twitch scopes were requested for authorization.")
        return await asyncio.to_thread(
            self._start_device_authorization_sync,
            cleaned_client_id,
            normalized_scopes,
        )

    async def poll_device_authorization(
        self,
        client_id: str,
        authorization: TwitchDeviceAuthorization,
    ) -> TwitchTokenBundle:
        cleaned_client_id = client_id.strip()
        if not cleaned_client_id:
            raise TwitchAuthError("Enter the Twitch client ID before starting authorization.")

        interval = max(1, int(authorization.interval or 5))
        deadline = time.monotonic() + max(5, int(authorization.expires_in or 1800))

        while True:
            if time.monotonic() >= deadline:
                raise TwitchAuthError("The Twitch authorization window expired. Start authorization again.")
            try:
                return await asyncio.to_thread(
                    self._exchange_device_code_sync,
                    cleaned_client_id,
                    authorization.device_code,
                )
            except TwitchAuthError as exc:
                message = str(exc).strip().lower()
                if "authorization is still pending" in message:
                    await asyncio.sleep(interval)
                    continue
                if "asked us to slow down" in message:
                    interval += 5
                    await asyncio.sleep(interval)
                    continue
                raise

    async def validate_access_token(self, access_token: str) -> TwitchTokenValidation:
        cleaned_token = access_token.strip()
        if not cleaned_token:
            raise TwitchAuthError("Twitch did not return an access token.")
        return await asyncio.to_thread(self._validate_access_token_sync, cleaned_token)

    def _start_device_authorization_sync(
        self,
        client_id: str,
        scopes: list[str],
    ) -> TwitchDeviceAuthorization:
        payload = self._post_form(
            self.DEVICE_CODE_URL,
            {
                "client_id": client_id,
                "scopes": " ".join(scopes),
            },
        )
        device_code = str(payload.get("device_code", "")).strip()
        user_code = str(payload.get("user_code", "")).strip()
        verification_uri = str(payload.get("verification_uri", "")).strip()
        if not device_code or not user_code or not verification_uri:
            raise TwitchAuthError("Twitch returned an incomplete device authorization response.")
        return TwitchDeviceAuthorization(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_in=max(1, int(payload.get("expires_in", 1800) or 1800)),
            interval=max(1, int(payload.get("interval", 5) or 5)),
            requested_scopes=list(scopes),
        )

    def _exchange_device_code_sync(
        self,
        client_id: str,
        device_code: str,
    ) -> TwitchTokenBundle:
        payload = self._post_form(
            self.TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise TwitchAuthError("Twitch did not return an access token.")
        raw_scopes = payload.get("scope", [])
        if isinstance(raw_scopes, list):
            scopes = [str(item).strip() for item in raw_scopes]
        elif isinstance(raw_scopes, str):
            scopes = [part.strip() for part in raw_scopes.split(" ") if part.strip()]
        else:
            scopes = []
        return TwitchTokenBundle(
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token", "")).strip(),
            scopes=[scope for scope in scopes if scope],
            token_type=str(payload.get("token_type", "bearer")).strip() or "bearer",
            expires_in=max(1, int(payload.get("expires_in", 1) or 1)),
        )

    def _validate_access_token_sync(self, access_token: str) -> TwitchTokenValidation:
        req = request.Request(
            self.VALIDATE_URL,
            method="GET",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        try:
            with request.urlopen(req, timeout=10, context=tls_context()) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            message = self._error_message(raw)
            if exc.code == 401:
                raise TwitchAuthError("Twitch rejected the access token. Authorize again and try once more.") from exc
            raise TwitchAuthError(message or f"Twitch validation failed with HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise TwitchAuthError(f"Could not reach Twitch: {describe_tls_error(exc.reason)}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TwitchAuthError("Twitch returned an unreadable token validation response.") from exc

        raw_scopes = payload.get("scopes", [])
        if isinstance(raw_scopes, list):
            scopes = [str(item).strip() for item in raw_scopes]
        elif isinstance(raw_scopes, str):
            scopes = [part.strip() for part in raw_scopes.split(" ") if part.strip()]
        else:
            scopes = []
        return TwitchTokenValidation(
            client_id=str(payload.get("client_id", "")).strip(),
            login=str(payload.get("login", "")).strip(),
            user_id=str(payload.get("user_id", "")).strip(),
            scopes=[scope for scope in scopes if scope],
            expires_in=max(0, int(payload.get("expires_in", 0) or 0)),
        )

    def _post_form(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = parse.urlencode(body, doseq=True).encode("utf-8")
        req = request.Request(
            url,
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with request.urlopen(req, timeout=10, context=tls_context()) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raw = exc.read()
            payload = self._parse_json(raw)
            oauth_error = str(payload.get("message", "") or payload.get("error", "")).strip().lower()
            if oauth_error == "authorization_pending":
                raise TwitchAuthError("Twitch authorization is still pending.") from exc
            if oauth_error == "slow_down":
                raise TwitchAuthError("Twitch asked us to slow down before polling again.") from exc
            if oauth_error == "access_denied":
                raise TwitchAuthError("Twitch authorization was denied.") from exc
            if oauth_error == "expired_token":
                raise TwitchAuthError("The Twitch authorization code expired. Start authorization again.") from exc
            message = self._error_message(raw) or f"Twitch authorization failed with HTTP {exc.code}."
            raise TwitchAuthError(message) from exc
        except error.URLError as exc:
            raise TwitchAuthError(f"Could not reach Twitch: {describe_tls_error(exc.reason)}") from exc

        payload = self._parse_json(raw)
        if not isinstance(payload, dict):
            raise TwitchAuthError("Twitch returned an unreadable authorization response.")
        return payload

    @staticmethod
    def _parse_json(raw: bytes) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _error_message(raw: bytes) -> str:
        payload = TwitchAuthService._parse_json(raw)
        if payload:
            if "message" in payload:
                return str(payload.get("message", "")).strip()
            if "error_description" in payload:
                return str(payload.get("error_description", "")).strip()
            if "error" in payload:
                return str(payload.get("error", "")).strip()
        return raw.decode("utf-8", errors="ignore").strip()
