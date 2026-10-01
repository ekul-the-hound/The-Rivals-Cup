"""Structured audit log to stdout/stderr (logging). It does NOT write to Supabase.

Records metadata only: request id, principal, method, tool, a hash of the arguments, status,
duration and size. Never tokens, argument values, or response content.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("mcp.audit")
if not log.handlers:  # one JSON line per call on stderr; redirect to a file to feed the dashboard
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


def args_hash(args: Any) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


def audit(**fields: Any) -> None:
    fields.setdefault("ts", datetime.now(UTC).isoformat(timespec="seconds"))
    log.info(json.dumps(fields, default=str, separators=(",", ":")))
