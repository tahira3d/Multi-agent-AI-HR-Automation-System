"""Backend-only transport; tokens stay in each Streamlit user's session."""

from __future__ import annotations

import os
from typing import Any
import requests
import streamlit as st


class APIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class APIClient:
    def __init__(self, token: str | None = None):
        self.base_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.token = token

    def request(self, method: str, path: str, **kwargs: Any):
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.request(
                method,
                self.base_url + path,
                headers=headers,
                timeout=kwargs.pop("timeout", (10, 180)),
                **kwargs,
            )
        except requests.Timeout:
            raise APIError(
                "The request timed out. Refresh the application status before retrying an evaluation or decision."
            ) from None
        except requests.RequestException:
            raise APIError("The backend is unavailable. Check FastAPI and API_BASE_URL.") from None
        if not response.ok:
            try:
                detail = response.json().get("detail", "Request failed")
                if isinstance(detail, list):
                    detail = "; ".join(
                        str(item.get("msg", "Invalid input")) for item in detail if isinstance(item, dict)
                    )
                if isinstance(detail, dict):
                    detail = detail.get("message", "Request failed")
            except ValueError:
                detail = f"Backend returned HTTP {response.status_code}"
            raise APIError(str(detail)[:2000], response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise APIError("The backend returned an unreadable response.") from None

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def download(self, path: str, **kwargs: Any) -> bytes:
        """Fetch a private binary report without ever putting its URL in browser state."""
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.get(
                self.base_url + path, headers=headers, timeout=kwargs.pop("timeout", (10, 180)), **kwargs
            )
        except requests.Timeout:
            raise APIError("The report request timed out. Refresh before retrying.") from None
        except requests.RequestException:
            raise APIError("The backend is unavailable. Check FastAPI and API_BASE_URL.") from None
        if not response.ok:
            try:
                detail = response.json().get("detail", "Report download failed")
            except ValueError:
                detail = f"Backend returned HTTP {response.status_code}"
            raise APIError(str(detail)[:2000], response.status_code)
        return response.content


def client():
    return APIClient(st.session_state.get("access_token"))


def show_error(exc):
    if exc.status_code == 401:
        st.session_state.clear()
        st.session_state["login_notice"] = "Your session expired. Please sign in again."
        st.rerun()
    st.error(str(exc))


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "applications", "rankings", "events"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def fetch_all(path):
    rows = []
    for offset in range(0, 10000, 100):
        batch = as_list(client().get(path, params={"offset": offset, "limit": 100}))
        rows.extend(batch)
        if len(batch) < 100:
            break
    return rows
