"""Redact secrets from tool arguments before they are written to mcp_audit_logs (future)."""

from typing import Any

_SENSITIVE = ("token", "secret", "key", "password", "authorization", "credential")


def redact_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if any(s in k.lower() for s in _SENSITIVE) else redact_args(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_args(v) for v in value]
    return value
