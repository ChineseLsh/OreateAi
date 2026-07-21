"""Configurable LuckMail project-order and private Outlook provider."""

from __future__ import annotations

import email as email_lib
import hashlib
import hmac
import html
import imaplib
import json
import logging
import re
import ssl
import threading
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse

import httpx

from config import (
    DEFAULT_PROXY,
    LUCKMAIL_API_KEY,
    LUCKMAIL_API_SECRET,
    LUCKMAIL_BASE_URL,
    LUCKMAIL_HTTP_RETRIES,
    LUCKMAIL_DOMAIN,
    LUCKMAIL_EMAIL_TYPE,
    LUCKMAIL_IMAP_HOSTS,
    LUCKMAIL_IMAP_LAST_N,
    LUCKMAIL_IMAP_PROXY,
    LUCKMAIL_INVENTORY_CACHE_SECONDS,
    LUCKMAIL_MODE,
    LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS,
    LUCKMAIL_ORDER_POLL_INTERVAL,
    LUCKMAIL_ORDER_TIMEOUT,
    LUCKMAIL_POLL_INTERVAL,
    LUCKMAIL_PROJECT_CODE,
    LUCKMAIL_PROXY,
    LUCKMAIL_RECENT_SECONDS,
    LUCKMAIL_REQUIRE_RECIPIENT_MATCH,
)
from core.db import get_account

log = logging.getLogger(__name__)

TOKEN_ID_RE = re.compile(r"tokenID=([a-fA-F0-9-]{36})")
TOKEN_ID_VALUE_RE = re.compile(r"[a-fA-F0-9-]{36}")
MICROSOFT_TOKEN_ENDPOINTS = (
    (
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
        {"scope": "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"},
    ),
    (
        "https://login.live.com/oauth20_token.srf",
        {"scope": "offline_access https://outlook.office.com/IMAP.AccessAsUser.All"},
    ),
    ("https://login.live.com/oauth20_token.srf", {}),
)


class LuckMailAPIError(RuntimeError):
    def __init__(self, code: int, message: str, http_status: int | None = None):
        self.code = int(code)
        self.http_status = http_status
        super().__init__(f"LuckMail API error {self.code}: {message}")


def _proxy_value(configured: str, fallback: str | None = None) -> str | None:
    value = (configured or "").strip()
    if value.lower() in {"direct", "none", "off"}:
        return None
    return value or fallback


class LuckMailClient:
    def __init__(
        self,
        api_key: str,
        *,
        api_secret: str = "",
        base_url: str = "https://mails.luckyous.com",
        proxy: str | None = None,
        retries: int = 3,
        timeout: float = 20,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key.strip():
            raise ValueError("LUCKMAIL_API_KEY is empty")
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.retries = max(1, int(retries))
        self.http = httpx.Client(
            proxy=proxy,
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
        )

    def _redact(self, value: Any) -> str:
        text = str(value)
        for secret in (self.api_key, self.api_secret):
            if secret:
                text = text.replace(secret, "***")
        return text

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "X-API-Key": self.api_key}
        if body:
            headers["Content-Type"] = "application/json"
        if self.api_secret:
            timestamp = str(int(time.time()))
            payload = f"{method.upper()}{path}{timestamp}{body}".encode()
            headers["X-Timestamp"] = timestamp
            headers["X-Signature"] = hmac.new(
                self.api_secret.encode(), payload, hashlib.sha256
            ).hexdigest()
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retryable: bool = True,
    ) -> Any:
        path = "/" + path.lstrip("/")
        query = urlencode(params or {}, doseq=True)
        signed_path = f"{path}?{query}" if query else path
        body = (
            json.dumps(json_body, ensure_ascii=False, separators=(",", ":"))
            if json_body is not None
            else ""
        )
        last_error: Exception | None = None
        max_attempts = self.retries if retryable else 1
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.http.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    content=body or None,
                    headers=self._headers(method, signed_path, body),
                )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("LuckMail returned non-object JSON")
                code = int(payload.get("code", -1))
                if code != 0:
                    error = LuckMailAPIError(
                        code,
                        self._redact(payload.get("message") or "request failed"),
                        response.status_code,
                    )
                    if (
                        retryable
                        and (code >= 5000 or response.status_code >= 500)
                        and attempt < max_attempts
                    ):
                        last_error = error
                        time.sleep(min(0.5 * attempt, 2))
                        continue
                    raise error
                return payload.get("data")
            except LuckMailAPIError:
                raise
            except Exception as exc:
                last_error = exc
                if retryable and attempt < max_attempts:
                    time.sleep(min(0.5 * attempt, 2))
                    continue
                break
        raise RuntimeError(f"LuckMail request failed after {max_attempts} attempts") from last_error

    def list_private_emails(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/openapi/emails",
            params={"page": page, "page_size": page_size},
        ) or {}

    def create_order(
        self,
        *,
        project_code: str,
        email_type: str,
        domain: str = "",
    ) -> dict[str, Any]:
        body = {"project_code": project_code, "email_type": email_type}
        if domain:
            body["domain"] = domain
        return self._request(
            "POST",
            "/api/v1/openapi/order/create",
            json_body=body,
            retryable=False,
        ) or {}

    def get_order_code(self, order_no: str) -> dict[str, Any]:
        safe_order_no = quote(str(order_no), safe="")
        return self._request(
            "GET",
            f"/api/v1/openapi/order/{safe_order_no}/code",
        ) or {}

    def cancel_order(self, order_no: str) -> dict[str, Any]:
        safe_order_no = quote(str(order_no), safe="")
        return self._request(
            "POST",
            f"/api/v1/openapi/order/{safe_order_no}/cancel",
        ) or {}

    def close(self) -> None:
        self.http.close()


_inventory_lock = threading.RLock()
_inventory_cache: list[dict[str, Any]] | None = None
_inventory_cached_at = 0.0
_reserved: set[str] = set()
_rotated_refresh_tokens: dict[str, str] = {}


def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    enabled = record.get("status") in (1, True, "1", "active", "enabled", "normal")
    address = str(record.get("address") or "").strip()
    client_id = str(record.get("client_id") or "").strip()
    refresh_token = str(record.get("refresh_token") or "").strip()
    if str(record.get("type") or "").lower() != "ms_imap" or not enabled:
        return None
    if not address or "@" not in address or "+" in address or not client_id or not refresh_token:
        return None
    return {
        "address": address,
        "password": str(record.get("password") or "").strip(),
        "client_id": client_id,
        "refresh_token": _rotated_refresh_tokens.get(address.lower(), refresh_token),
    }


def _load_inventory(force: bool = False) -> list[dict[str, Any]]:
    global _inventory_cache, _inventory_cached_at
    now = time.monotonic()
    with _inventory_lock:
        if (
            not force
            and _inventory_cache is not None
            and now - _inventory_cached_at < max(0, LUCKMAIL_INVENTORY_CACHE_SECONDS)
        ):
            return _inventory_cache
        client = LuckMailClient(
            LUCKMAIL_API_KEY,
            api_secret=LUCKMAIL_API_SECRET,
            base_url=LUCKMAIL_BASE_URL,
            proxy=_proxy_value(LUCKMAIL_PROXY, DEFAULT_PROXY),
            retries=LUCKMAIL_HTTP_RETRIES,
        )
        try:
            records: list[dict[str, Any]] = []
            page = 1
            while True:
                payload = client.list_private_emails(page=page, page_size=100)
                rows = payload.get("list") or payload.get("items") or []
                records.extend(
                    normalized
                    for row in rows
                    if isinstance(row, dict)
                    if (normalized := _normalize_record(row)) is not None
                )
                total = int(payload.get("total", len(rows)) or 0)
                if not rows or page * 100 >= total:
                    break
                page += 1
        finally:
            client.close()
        if not records:
            raise RuntimeError("LuckMail has no enabled ms_imap mailbox")
        _inventory_cache = records
        _inventory_cached_at = now
        return records


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


def _message_text(message) -> str:
    def decode_part(part) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")

    if not message.is_multipart():
        return decode_part(message)
    parts = []
    for part in message.walk():
        if part.get_content_type() in {"text/plain", "text/html"}:
            parts.append(decode_part(part))
    return "\n".join(parts)


def _imap_proxy_parts(proxy_url: str) -> dict[str, Any]:
    try:
        import socks
    except ImportError as exc:
        raise RuntimeError("PySocks is required for LUCKMAIL_IMAP_PROXY") from exc
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    proxy_types = {
        "http": socks.HTTP,
        "socks4": socks.SOCKS4,
        "socks4a": socks.SOCKS4,
        "socks5": socks.SOCKS5,
        "socks5h": socks.SOCKS5,
    }
    if parsed.scheme.lower() not in proxy_types or not parsed.hostname:
        raise ValueError(f"unsupported IMAP proxy: {proxy_url}")
    return {
        "proxy_type": proxy_types[parsed.scheme.lower()],
        "proxy_addr": parsed.hostname,
        "proxy_port": parsed.port or (1080 if parsed.scheme.startswith("socks") else 8080),
        "proxy_rdns": parsed.scheme.lower() in {"socks4a", "socks5h"},
        "proxy_username": unquote(parsed.username) if parsed.username else None,
        "proxy_password": unquote(parsed.password) if parsed.password else None,
    }


class _ProxyIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, *, proxy_url: str, timeout: float = 45):
        self._proxy_url = proxy_url
        super().__init__(host, 993, timeout=timeout)

    def _create_socket(self, timeout):
        import socks

        proxy = _imap_proxy_parts(self._proxy_url)
        raw = socks.create_connection((self.host, self.port), timeout=timeout, **proxy)
        return self.ssl_context.wrap_socket(raw, server_hostname=self.host)


class LuckMailProvider:
    def __init__(self):
        self.address = ""
        self._account: dict[str, Any] | None = None
        self._mode = LUCKMAIL_MODE
        self._client: LuckMailClient | None = None
        self._order_no = ""
        self._order_finished = False

    @staticmethod
    def _new_client() -> LuckMailClient:
        return LuckMailClient(
            LUCKMAIL_API_KEY,
            api_secret=LUCKMAIL_API_SECRET,
            base_url=LUCKMAIL_BASE_URL,
            proxy=_proxy_value(LUCKMAIL_PROXY, DEFAULT_PROXY),
            retries=LUCKMAIL_HTTP_RETRIES,
        )

    @staticmethod
    def _cancel_orders(client: LuckMailClient, order_nos: list[str]) -> None:
        for order_no in order_nos:
            try:
                client.cancel_order(order_no)
            except Exception as exc:
                log.warning("LuckMail order cancellation failed: %s", type(exc).__name__)
        order_nos.clear()

    def create(self, **_) -> str:
        if self._mode == "project_order":
            client = self._new_client()
            held_orders: list[str] = []
            try:
                attempts = max(1, LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS)
                for _attempt in range(attempts):
                    try:
                        order = client.create_order(
                            project_code=LUCKMAIL_PROJECT_CODE,
                            email_type=LUCKMAIL_EMAIL_TYPE,
                            domain=LUCKMAIL_DOMAIN,
                        )
                    except LuckMailAPIError as exc:
                        if exc.code == 2003 and held_orders:
                            raise RuntimeError(
                                "LuckMail Grok project has no mailbox beyond existing accounts"
                            ) from exc
                        raise
                    order_no = str(order.get("order_no") or "").strip()
                    address = str(order.get("email_address") or "").strip()
                    if not order_no or not address or "@" not in address:
                        if order_no:
                            self._cancel_orders(client, [order_no])
                        raise RuntimeError(
                            "LuckMail project order returned incomplete mailbox data"
                        )
                    local, domain = address.lower().rsplit("@", 1)
                    if "+" in local or (
                        LUCKMAIL_DOMAIN and domain != LUCKMAIL_DOMAIN.lower()
                    ):
                        self._cancel_orders(client, [order_no])
                        raise RuntimeError(
                            f"LuckMail project order did not return a base {LUCKMAIL_DOMAIN} mailbox"
                        )
                    with _inventory_lock:
                        in_use = address.lower() in _reserved or bool(get_account(address))
                        if not in_use:
                            _reserved.add(address.lower())
                    if in_use:
                        held_orders.append(order_no)
                        continue
                    self._cancel_orders(client, held_orders)
                    self._client = client
                    self._order_no = order_no
                    self.address = address
                    return address
                raise RuntimeError(
                    f"LuckMail allocated only existing account mailboxes after {attempts} attempts"
                )
            except Exception:
                self._cancel_orders(client, held_orders)
                client.close()
                raise
        if self._mode != "private_inventory":
            raise ValueError(f"unsupported LuckMail mode: {self._mode}")
        with _inventory_lock:
            for account in _load_inventory():
                address = account["address"]
                if address.lower() in _reserved or get_account(address):
                    continue
                _reserved.add(address.lower())
                self.address = address
                self._account = dict(account)
                return address
        raise RuntimeError("LuckMail has no unused base mailbox")

    def _refresh_access_token(self) -> str:
        assert self._account is not None
        proxy = _proxy_value(LUCKMAIL_PROXY, DEFAULT_PROXY)
        last_error = "unknown error"
        with httpx.Client(proxy=proxy, timeout=30) as http:
            for url, extra in MICROSOFT_TOKEN_ENDPOINTS:
                response = http.post(
                    url,
                    data={
                        "client_id": self._account["client_id"],
                        "refresh_token": self._account["refresh_token"],
                        "grant_type": "refresh_token",
                        **extra,
                    },
                )
                try:
                    payload = response.json()
                except Exception:
                    payload = {}
                access_token = payload.get("access_token")
                if access_token:
                    refresh_token = payload.get("refresh_token")
                    if refresh_token:
                        self._account["refresh_token"] = refresh_token
                        _rotated_refresh_tokens[self.address.lower()] = refresh_token
                    return access_token
                last_error = str(payload.get("error_description") or payload.get("error") or response.status_code)
        raise RuntimeError(f"LuckMail Microsoft OAuth refresh failed: {last_error}")

    def _find_token(self, host: str, access_token: str) -> str | None:
        imap_proxy = _proxy_value(LUCKMAIL_IMAP_PROXY, DEFAULT_PROXY)
        imap = (
            _ProxyIMAP4SSL(host, proxy_url=imap_proxy)
            if imap_proxy
            else imaplib.IMAP4_SSL(host, 993, timeout=45)
        )
        auth = f"user={self.address}\x01auth=Bearer {access_token}\x01\x01"
        try:
            imap.authenticate("XOAUTH2", lambda _: auth.encode())
            imap.select("INBOX")
            status, data = imap.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                return None
            cutoff = time.time() - max(60, LUCKMAIL_RECENT_SECONDS)
            for message_id in reversed(data[0].split()[-max(1, LUCKMAIL_IMAP_LAST_N) :]):
                _, raw = imap.fetch(message_id, "(RFC822)")
                if not raw or not raw[0] or not isinstance(raw[0][1], bytes):
                    continue
                message = email_lib.message_from_bytes(raw[0][1])
                date_header = message.get("Date")
                if date_header:
                    try:
                        if parsedate_to_datetime(date_header).timestamp() < cutoff:
                            continue
                    except Exception:
                        pass
                recipients = " ".join(
                    _decode_header(message.get(name, ""))
                    for name in ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")
                ).lower()
                if LUCKMAIL_REQUIRE_RECIPIENT_MATCH and self.address.lower() not in recipients:
                    continue
                content = html.unescape(
                    "\n".join(
                        (
                            _decode_header(message.get("Subject", "")),
                            _decode_header(message.get("From", "")),
                            _message_text(message),
                        )
                    )
                )
                if "oreate" not in content.lower():
                    continue
                match = TOKEN_ID_RE.search(content)
                if match:
                    return match.group(1)
            return None
        finally:
            try:
                imap.close()
            except Exception:
                pass
            try:
                imap.logout()
            except Exception:
                pass

    def wait_for_verification_link(
        self, timeout: int | None = None, poll_interval: int | None = None
    ) -> tuple[str, str]:
        if self._mode == "project_order":
            if not self._client or not self._order_no or not self.address:
                raise RuntimeError("LuckMail project order has not been created")
            wait_seconds = LUCKMAIL_ORDER_TIMEOUT if timeout is None else timeout
            interval = max(
                1,
                LUCKMAIL_ORDER_POLL_INTERVAL if poll_interval is None else poll_interval,
            )
            deadline = time.time() + max(0, wait_seconds)
            while time.time() < deadline:
                result = self._client.get_order_code(self._order_no)
                status = str(result.get("status") or "pending").strip().lower()
                if status == "success":
                    token_id = str(result.get("verification_code") or "").strip()
                    if not TOKEN_ID_VALUE_RE.fullmatch(token_id):
                        raise RuntimeError(
                            "LuckMail project order did not return an OreateAI tokenID"
                        )
                    self._order_finished = True
                    return self.address, token_id
                if status in {"timeout", "cancelled"}:
                    self._order_finished = True
                    raise RuntimeError(f"LuckMail project order ended with status {status}")
                time.sleep(interval)
            raise TimeoutError(
                f"LuckMail project order received no OreateAI verification code within {wait_seconds}s"
            )
        if not self._account or not self.address:
            raise RuntimeError("LuckMail mailbox has not been created")
        wait_seconds = 180 if timeout is None else timeout
        deadline = time.time() + wait_seconds
        interval = max(1, LUCKMAIL_POLL_INTERVAL or poll_interval or 4)
        access_token = ""
        while time.time() < deadline:
            try:
                access_token = access_token or self._refresh_access_token()
                errors = []
                for host in LUCKMAIL_IMAP_HOSTS:
                    try:
                        token_id = self._find_token(host, access_token)
                        if token_id:
                            return self.address, token_id
                        break
                    except Exception as exc:
                        errors.append(f"{host}: {type(exc).__name__}")
                if errors and len(errors) == len(LUCKMAIL_IMAP_HOSTS):
                    access_token = ""
            except Exception as exc:
                access_token = ""
                log.warning("LuckMail poll failed: %s", type(exc).__name__)
            time.sleep(interval)
        raise TimeoutError(f"LuckMail received no OreateAI verification link within {wait_seconds}s")

    def close(self) -> None:
        if self._client:
            try:
                if self._order_no and not self._order_finished:
                    self._client.cancel_order(self._order_no)
            except Exception as exc:
                log.warning("LuckMail order cancellation failed: %s", type(exc).__name__)
            finally:
                self._client.close()
                self._client = None
        if self.address:
            with _inventory_lock:
                _reserved.discard(self.address.lower())
