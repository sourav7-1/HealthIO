"""Structured logging with PHI/PII redaction.

Rule: health or personal data must never reach logs. Redaction is applied to every
event as the last processor before rendering, so it covers our code and bound context.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[REDACTED]"

# Keys whose values are always removed, matched case-insensitively as substrings.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "otp",
    "api_key",
    "phone",
    "email",
    "name",
    "dob",
    "birth",
    "address",
    "abha",
    "aadhaar",
    "diagnosis",
    "condition",
    "allerg",
    "medication",
    "drug",
    "prescription",
    "symptom",
    "note",
    "lab",
    "vital",
    "content",
    "message_text",
)

# Values that look like identifiers are masked even under innocent keys.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{2}-\d{4}-\d{4}-\d{4}\b"), "[ABHA]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[ID12]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"), "[PHONE]"),
    (re.compile(r"\bBearer\s+[\w\-.~+/]+=*", re.IGNORECASE), "Bearer [REDACTED]"),
)

# Keys that structlog itself sets; never redact these by key name.
_SAFE_KEYS = frozenset(
    {"event", "level", "timestamp", "logger", "logger_name", "request_id", "path", "method"}
)


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return k not in _SAFE_KEYS and any(part in k for part in SENSITIVE_KEY_PARTS)


def _scrub_str(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def scrub(value: Any) -> Any:
    """Recursively redact sensitive keys and identifier-like strings."""
    if isinstance(value, str):
        return _scrub_str(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and _is_sensitive_key(k) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set):
        return type(value)(scrub(v) for v in value)
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return scrub(dict(event_dict))  # type: ignore[no-any-return]


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Uvicorn's access log prints full URLs, which may carry identifiers.
    # RequestContextMiddleware logs requests instead.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
