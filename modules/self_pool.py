"""Locally imported Microsoft OAuth/IMAP mailbox pool."""

from __future__ import annotations

import re
import time

from config import (
    LUCKMAIL_IMAP_HOSTS,
    LUCKMAIL_ORDER_TIMEOUT,
    LUCKMAIL_POLL_INTERVAL,
)
from core.db import (
    acquire_mailbox,
    finalize_mailbox,
    update_mailbox_refresh_token,
    upsert_mailboxes,
)
from modules.luckmail import LuckMailProvider


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def import_mailboxes(text: str) -> dict:
    records: dict[str, dict] = {}
    invalid_lines: list[int] = []
    for line_number, raw_line in enumerate((text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) != 4:
            invalid_lines.append(line_number)
            continue
        address, password, client_id, refresh_token = parts
        local = address.split("@", 1)[0]
        if (
            not EMAIL_RE.fullmatch(address)
            or "+" in local
            or not password
            or not client_id
            or not refresh_token
        ):
            invalid_lines.append(line_number)
            continue
        normalized = address.lower()
        records[normalized] = {
            "address": normalized,
            "password": password,
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
    result = upsert_mailboxes(list(records.values()))
    return {**result, "invalid_lines": invalid_lines}


class SelfPoolEmailProvider(LuckMailProvider):
    def __init__(self):
        super().__init__()
        self._mode = "private_inventory"

    def create(self, **_) -> str:
        record = acquire_mailbox()
        if record is None:
            raise RuntimeError("self mailbox pool has no available mailbox")
        self.address = record["address"]
        self._account = {
            "address": record["address"],
            "password": record["password"],
            "client_id": record["client_id"],
            "refresh_token": record["refresh_token"],
        }
        return self.address

    def _refresh_access_token(self) -> str:
        access_token = super()._refresh_access_token()
        if self._account:
            update_mailbox_refresh_token(
                self.address,
                self._account["refresh_token"],
            )
        return access_token

    def wait_for_verification_link(
        self,
        timeout: int | None = None,
        poll_interval: int | None = None,
    ) -> tuple[str, str]:
        if not self._account or not self.address:
            raise RuntimeError("self mailbox has not been acquired")
        wait_seconds = LUCKMAIL_ORDER_TIMEOUT if timeout is None else timeout
        interval = max(
            1,
            LUCKMAIL_POLL_INTERVAL if poll_interval is None else poll_interval,
        )
        deadline = time.time() + max(0, wait_seconds)
        access_token = ""
        inbox_connected = False
        last_error = "unknown"
        while time.time() < deadline:
            try:
                access_token = access_token or self._refresh_access_token()
                errors = []
                for host in LUCKMAIL_IMAP_HOSTS:
                    try:
                        token_id = self._find_token(host, access_token)
                        inbox_connected = True
                        if token_id:
                            return self.address, token_id
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                if errors and len(errors) == len(LUCKMAIL_IMAP_HOSTS):
                    access_token = ""
                    last_error = f"IMAP {errors[-1]}"
            except Exception as exc:
                access_token = ""
                last_error = f"OAuth {type(exc).__name__}"
            time.sleep(interval)
        if inbox_connected:
            raise TimeoutError(
                f"self mailbox received no OreateAI verification link within {wait_seconds}s"
            )
        raise RuntimeError(f"self mailbox OAuth/IMAP unavailable ({last_error})")

    def close(self) -> None:
        if self.address:
            finalize_mailbox(self.address)
        super().close()
