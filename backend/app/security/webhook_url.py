"""Strict Feishu/Lark webhook validation shared by settings and provider layers."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

DEFAULT_FEISHU_WEBHOOK_HOSTS = ("open.feishu.cn", "open.larksuite.com")


def validate_feishu_webhook_url(
    value: object, *, allowed_hosts: Iterable[str] = DEFAULT_FEISHU_WEBHOOK_HOSTS
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid webhook URL")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    hosts = {str(item).strip().lower() for item in allowed_hosts if str(item).strip()}
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or (parsed.port not in (None, 443))
    ):
        raise ValueError("invalid webhook URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 5 or parts[:4] != ["open-apis", "bot", "v2", "hook"] or not parts[4]:
        raise ValueError("invalid webhook URL")
    return candidate
