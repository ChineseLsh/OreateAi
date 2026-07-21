from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from dotenv import dotenv_values, set_key, unset_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

PROVIDER_CHOICES = {
    "auto",
    "luckmail",
    "mailtm",
    "linshiyouxiang",
    "guerrilla",
    "1secmail",
}
LUCKMAIL_MODE_CHOICES = {"project_order", "project_purchase", "private_inventory"}
LUCKMAIL_EMAIL_TYPE_CHOICES = {"ms_graph", "ms_imap", "google_variant", "self_built"}

_SECRET_KEYS = {"LUCKMAIL_API_KEY", "LUCKMAIL_API_SECRET"}
_WRITE_LOCK = threading.RLock()


def _string(default: str = "") -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        result = str(value).strip()
        if "\r" in result or "\n" in result:
            raise ValueError("value must not contain line breaks")
        return result

    validate.default = default  # type: ignore[attr-defined]
    return validate


def _choice(choices: set[str], default: str) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        result = _string()(value).lower()
        if result not in choices:
            raise ValueError(f"must be one of: {', '.join(sorted(choices))}")
        return result

    validate.default = default  # type: ignore[attr-defined]
    return validate


def _nonempty_string(default: str) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        result = _string()(value)
        if not result:
            raise ValueError("must not be empty")
        return result

    validate.default = default  # type: ignore[attr-defined]
    return validate


def _boolean(default: bool) -> Callable[[Any], bool]:
    def validate(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError("must be a boolean")

    validate.default = default  # type: ignore[attr-defined]
    return validate


def _integer(default: int, minimum: int, maximum: int) -> Callable[[Any], int]:
    def validate(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError(f"must be between {minimum} and {maximum}")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"must be between {minimum} and {maximum}") from exc
        if not minimum <= result <= maximum:
            raise ValueError(f"must be between {minimum} and {maximum}")
        return result

    validate.default = default  # type: ignore[attr-defined]
    return validate


_SETTINGS: dict[str, Callable[[Any], Any]] = {
    "THREADAI_PROXY": _string("http://127.0.0.1:7897"),
    "THREADAI_EMAIL_PROVIDER": _choice(PROVIDER_CHOICES, "auto"),
    "THREADAI_ALLOW_PLUS_EMAIL": _boolean(False),
    "THREADAI_BROWSER_CHANNEL": _string("chrome"),
    "THREADAI_BROWSER_HEADLESS": _boolean(True),
    "THREADAI_BROWSER_TIMEOUT_MS": _integer(60_000, 1_000, 600_000),
    "THREADAI_BROWSER_RISK_TIMEOUT_MS": _integer(15_000, 1_000, 300_000),
    "LUCKMAIL_BASE_URL": _string("https://mails.luckyous.com"),
    "LUCKMAIL_PROXY": _string(""),
    "LUCKMAIL_HTTP_RETRIES": _integer(3, 1, 20),
    "LUCKMAIL_MODE": _choice(LUCKMAIL_MODE_CHOICES, "project_purchase"),
    "LUCKMAIL_PROJECT_CODE": _nonempty_string("grok"),
    "LUCKMAIL_EMAIL_TYPE": _choice(LUCKMAIL_EMAIL_TYPE_CHOICES, "ms_imap"),
    "LUCKMAIL_DOMAIN": _nonempty_string("outlook.com"),
    "LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS": _integer(10, 1, 50),
    "LUCKMAIL_ORDER_TIMEOUT": _integer(300, 10, 1_800),
    "LUCKMAIL_ORDER_POLL_INTERVAL": _integer(3, 1, 60),
    "LUCKMAIL_INVENTORY_CACHE_SECONDS": _integer(60, 0, 86_400),
    "LUCKMAIL_POLL_INTERVAL": _integer(5, 1, 300),
    "LUCKMAIL_RECENT_SECONDS": _integer(900, 60, 86_400),
    "LUCKMAIL_IMAP_HOSTS": _string("outlook.office365.com,imap-mail.outlook.com"),
    "LUCKMAIL_IMAP_LAST_N": _integer(30, 1, 500),
    "LUCKMAIL_REQUIRE_RECIPIENT_MATCH": _boolean(True),
    "LUCKMAIL_IMAP_PROXY": _string(""),
}


def _validated_value(key: str, value: Any) -> Any:
    try:
        return _SETTINGS[key](value)
    except ValueError as exc:
        raise ValueError(f"{key}: {exc}") from exc


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_settings(path: Path | str | None = None) -> dict[str, Any]:
    env_path = Path(path) if path is not None else ENV_PATH
    with _WRITE_LOCK:
        raw = dotenv_values(env_path) if env_path.exists() else {}
    values: dict[str, Any] = {}
    for key, validator in _SETTINGS.items():
        current = raw.get(key)
        if current is None:
            current = validator.default  # type: ignore[attr-defined]
        try:
            values[key] = _validated_value(key, current)
        except ValueError:
            values[key] = validator.default  # type: ignore[attr-defined]

    return {
        "values": values,
        "secret_status": {key: bool(raw.get(key)) for key in sorted(_SECRET_KEYS)},
        "restart_required": False,
    }


def update_settings(
    values: dict[str, Any],
    secrets: dict[str, Any],
    clear_secrets: list[str],
    path: Path | str | None = None,
) -> dict[str, Any]:
    env_path = Path(path) if path is not None else ENV_PATH
    unknown_values = set(values) - set(_SETTINGS)
    if unknown_values:
        raise ValueError(f"unsupported setting: {sorted(unknown_values)[0]}")
    unknown_secrets = (set(secrets) | set(clear_secrets)) - _SECRET_KEYS
    if unknown_secrets:
        submitted = sorted(set(secrets) | set(clear_secrets))
        raise ValueError(f"unsupported secret in: {', '.join(submitted)}")

    validated = {key: _validated_value(key, value) for key, value in values.items()}
    clean_secrets: dict[str, str] = {}
    for key, value in secrets.items():
        try:
            clean = _string()(value)
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
        if clean:
            clean_secrets[key] = clean

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        if not env_path.exists():
            env_path.touch()
        before = env_path.read_text(encoding="utf-8")
        for key, value in validated.items():
            set_key(str(env_path), key, _serialize(value), quote_mode="auto")
        for key, value in clean_secrets.items():
            set_key(str(env_path), key, value, quote_mode="always")
        for key in set(clear_secrets):
            unset_key(str(env_path), key)
        changed = env_path.read_text(encoding="utf-8") != before
        result = read_settings(env_path)
        result["restart_required"] = changed
    return result
