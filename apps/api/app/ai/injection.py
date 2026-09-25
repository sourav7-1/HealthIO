"""Prompt-injection protection for text that did not come from Health Io.

Uploaded documents, OCR text, report conclusions, notes and titles typed by people are
**data**. Before any of it reaches a model it goes through `untrusted_block()`:

1. `scan()` looks for text written to steer an AI (instructions to ignore rules, role
   markers, fake system tags, requests to reveal prompts, tool-call syntax, links that
   could exfiltrate data). Flagged text is **withheld**: the model sees only a note that
   the item exists and was withheld, and the event is audited.
2. Clean text is neutralised: markup characters that could close or open our own tags are
   replaced, whitespace is collapsed, and length is capped.
3. It is wrapped in `<untrusted_document id=…>` tags. The system prompt tells the model
   that anything inside such tags is quoted material, never instructions.

This is defence in depth: the model's output is independently checked
(app/modules/assistant/safety.py), so a missed injection still cannot make the assistant
diagnose, prescribe or change a dose.
"""

import re
import unicodedata
from dataclasses import dataclass

MAX_CHARS = 2000

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b"
            r"(instructions?|rules?|guidelines?|prompts?|polic(y|ies)|above|previous|prior)\b",
            re.I,
        ),
    ),
    (
        "new_role",
        re.compile(r"\b(you are now|act as|pretend to be|roleplay as|from now on,? you)\b", re.I),
    ),
    ("jailbreak", re.compile(r"\b(developer|debug|god|dan|jailbreak)\s*mode\b", re.I)),
    (
        "prompt_leak",
        re.compile(r"\b(system|hidden|initial)\s+(prompt|message|instructions?)\b", re.I),
    ),
    ("role_marker", re.compile(r"(^|\n)\s*(system|assistant|human|user)\s*:", re.I)),
    (
        "tag",
        re.compile(
            r"</?\s*(system|assistant|instructions?|untrusted_document|patient_records|"
            r"trusted_reference|question|tool_use|tool_result)\b",
            re.I,
        ),
    ),
    ("tool_syntax", re.compile(r"\b(tool_use|function_call|tool_choice)\b", re.I)),
    ("exfiltration", re.compile(r"!\[[^\]]*\]\(\s*https?://|https?://\S+\?\S*=", re.I)),
    (
        "clinical_command",
        re.compile(
            r"\b(tell|instruct|advise)\s+(the\s+)?(patient|user|them|him|her)\s+to\s+"
            r"(stop|double|increase|decrease|skip|take)\b",
            re.I,
        ),
    ),
]


@dataclass(frozen=True)
class ScanResult:
    flagged: bool
    reasons: tuple[str, ...]


def _normalise(text: str) -> str:
    # Fold look-alike characters (full-width letters, etc.) so patterns can't be dodged.
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")  # zero-width


def scan(text: str) -> ScanResult:
    norm = _normalise(text)
    reasons = tuple(name for name, pattern in _PATTERNS if pattern.search(norm))
    return ScanResult(bool(reasons), reasons)


def neutralise(text: str, limit: int = MAX_CHARS) -> str:
    clean = _normalise(text).replace("<", "\u2039").replace(">", "\u203a").replace("`", "'")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


@dataclass(frozen=True)
class UntrustedBlock:
    text: str  # what the model sees
    withheld: bool
    reasons: tuple[str, ...]


def untrusted_block(item_id: str, label: str, text: str | None) -> UntrustedBlock | None:
    """Wrap one piece of uploaded or typed text for a prompt, or withhold it."""
    if not text or not text.strip():
        return None
    result = scan(text)
    if result.flagged:
        return UntrustedBlock(
            f'<untrusted_document id="{item_id}" label="{neutralise(label, 80)}" withheld="true">'
            "[This text was withheld because it contained instructions aimed at an AI.]"
            "</untrusted_document>",
            True,
            result.reasons,
        )
    return UntrustedBlock(
        f'<untrusted_document id="{item_id}" label="{neutralise(label, 80)}">'
        f"{neutralise(text)}</untrusted_document>",
        False,
        (),
    )
