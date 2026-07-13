"""Small stateless helpers shared by the Mythify CLI runtime."""

import re
from datetime import datetime, timezone

REDACTED_SECRET = "[REDACTED]"


def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def timestamp_sort_key(value):
    stamp = parse_iso_timestamp(value)
    if stamp is not None:
        return (1, stamp.timestamp(), str(value or ""))
    return (0, str(value or ""))


def timestamp_at_or_after(value, lower_bound, allow_same_second=False):
    left = parse_iso_timestamp(value)
    right = parse_iso_timestamp(lower_bound)
    if left is not None and right is not None:
        if allow_same_second:
            left = left.replace(microsecond=0)
            right = right.replace(microsecond=0)
        return left >= right
    return str(value or "") >= str(lower_bound or "")


def timestamp_after(value, lower_bound):
    left = parse_iso_timestamp(value)
    right = parse_iso_timestamp(lower_bound)
    if left is not None and right is not None:
        return left > right
    return str(value or "") > str(lower_bound or "")


def now_stamp():
    """Current UTC time as YYYYMMDDHHMMSS, for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def slugify(text):
    """Normalize text to a lowercase, 40-character identifier."""
    chars = []
    for ch in str(text).lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-")[:40]


def tail_text(text, limit=4000):
    return str(text or "")[-limit:]


def redact_sensitive_output(text):
    value = str(text or "")
    if not value:
        return ""
    value = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/\-=]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"(?i)\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)"
        r"[A-Za-z0-9_-]*\s*=\s*)([^\s,;]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"(?i)([\"']?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)"
        r"[A-Za-z0-9_-]*[\"']?\s*:\s*)([\"'])([^\"']+)([\"'])",
        r"\1\2" + REDACTED_SECRET + r"\4",
        value,
    )
    value = re.sub(
        r"(?i)\b((?:authorization|x-api-key|api-key|api_key|token|secret|password|passwd|credential)"
        r"\s*:\s*)([^\s,;}]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"\b("
        r"sk-ant-[A-Za-z0-9_-]{16,}|"
        r"sk-[A-Za-z0-9_-]{16,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|"
        r"gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"npm_[A-Za-z0-9_-]{20,}"
        r")\b",
        REDACTED_SECRET,
        value,
    )
    return value
