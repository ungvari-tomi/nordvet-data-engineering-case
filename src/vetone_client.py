"""Small authenticated client for the VetOne partner API."""

from __future__ import annotations

from collections.abc import Iterator
from time import sleep
from typing import Any
from urllib.parse import urljoin

import requests


class VetOneAPIError(RuntimeError):
    """Raised when the API response does not match its documented shape."""


class VetOneClient:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 60,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._session = session or requests.Session()
        self._access_token: str | None = None

    def __enter__(self) -> VetOneClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._session.close()

    def get_clinics(self) -> list[dict[str, Any]]:
        payload = self._request_json("GET", "v1/clinics")
        if isinstance(payload, dict):
            records = payload.get("data")
        else:
            records = payload
        return self._record_list(records, "clinics")

    def iter_appointments(
        self,
        date_from: str,
        date_to: str,
        include_deleted: bool = True,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "from": date_from,
            "to": date_to,
            "include_deleted": str(include_deleted).lower(),
        }
        seen_cursors: set[str] = set()

        while True:
            payload = self._request_json("GET", "v1/appointments", params=params)
            if not isinstance(payload, dict):
                raise VetOneAPIError("Appointments response must be a JSON object")

            records = self._record_list(payload.get("data"), "appointments")
            yield from records

            cursor = payload.get("next_cursor")
            if cursor in (None, ""):
                break
            if not isinstance(cursor, str):
                raise VetOneAPIError("Appointments next_cursor must be a string or null")
            if cursor in seen_cursors:
                raise VetOneAPIError("Appointments pagination returned a repeated cursor")

            seen_cursors.add(cursor)
            params["cursor"] = cursor

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = urljoin(self._base_url, path)
        refreshed_token = False
        server_retries = 0
        while True:
            headers: dict[str, str] = {}
            if path != "auth/token":
                headers["Authorization"] = f"Bearer {self._get_access_token()}"

            response = self._session.request(
                method,
                url,
                json=(
                    {"client_id": self._client_id, "client_secret": self._client_secret}
                    if path == "auth/token"
                    else None
                ),
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
            if (
                response.status_code == 401
                and path != "auth/token"
                and not refreshed_token
            ):
                self._access_token = None
                refreshed_token = True
                continue
            if (
                response.status_code == 429 or response.status_code >= 500
            ) and server_retries < 3:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else 2**server_retries
                except ValueError:
                    delay = 2**server_retries
                sleep(max(0, min(delay, 60)))
                server_retries += 1
                continue
            response.raise_for_status()
            return response.json()

    def _get_access_token(self) -> str:
        if self._access_token is None:
            response = self._session.request(
                "POST",
                urljoin(self._base_url, "auth/token"),
                json={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(
                payload.get("access_token"), str
            ):
                raise VetOneAPIError(
                    "Token response must contain a string access_token"
                )
            self._access_token = payload["access_token"]
        return self._access_token

    @staticmethod
    def _record_list(value: Any, resource: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(
            not isinstance(record, dict) for record in value
        ):
            raise VetOneAPIError(f"{resource.capitalize()} response must contain a list of objects")
        return value
