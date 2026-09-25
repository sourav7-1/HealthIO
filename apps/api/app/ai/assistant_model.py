"""Models behind the health assistant. Every call goes through `AssistantModel`, so the
provider can change and tests can script answers.

- `ClaudeAssistantModel` asks Claude for one answer in a fixed JSON schema (structured
  output), with the system prompt cached. It never sees patient identifiers, and its
  answer is checked by app/modules/assistant/safety.py before anyone sees it.
- `OfflineAssistantModel` is used when no AI provider is configured. It answers only by
  quoting record items and library passages that match simple keywords, and says that
  AI answers are switched off.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.config import Settings
from app.modules.assistant.context import RecordItem
from app.modules.assistant.knowledge import Passage
from app.modules.assistant.safety import ModelAnswer, Segment

PROMPT_VERSION = "health_assistant_v1"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(
    encoding="utf-8"
)
MAX_OUTPUT_TOKENS = 8000

# Structured output schema (mirrors safety.ModelAnswer; lengths are enforced after parsing).
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["record", "general", "uncertain"]},
                    "text": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "text", "sources"],
                "additionalProperties": False,
            },
        },
        "urgent": {"type": "boolean"},
        "declined": {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["diagnosis", "prescribing", "dose_change", "stop_medication", "other"],
                },
                {"type": "null"},
            ]
        },
        "questions_for_doctor": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["segments", "urgent", "declined", "questions_for_doctor"],
    "additionalProperties": False,
}


class AssistantUnavailableError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AssistantRequest:
    question: str
    user_content: str  # records + library + question, rendered for the model
    history: list[dict[str, str]]  # earlier turns as plain text, oldest first
    records: list[RecordItem] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)


@dataclass
class ModelResult:
    answer: ModelAnswer
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    attempts: int = 1


class AssistantModel(Protocol):
    name: str

    async def answer(self, request: AssistantRequest) -> ModelResult: ...


class ClaudeAssistantModel:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if settings.anthropic_api_key is None:
            raise AssistantUnavailableError("ai_not_configured", "The assistant is not configured.")
        from anthropic import AsyncAnthropic

        self.model = settings.ai_assistant_model
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.ai_request_timeout_seconds,
            max_retries=2,
        )

    async def answer(self, request: AssistantRequest) -> ModelResult:
        import anthropic

        messages: list[dict[str, Any]] = [
            *request.history,
            {"role": "user", "content": request.user_content},
        ]
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            # Stable prefix: the prompt is identical for every request, so it caches.
            "system": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": messages,
            "output_config": {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        }
        started = time.monotonic()
        for attempt in (1, 2):
            try:
                response = await self.client.messages.create(**params)
            except anthropic.RateLimitError as exc:
                raise AssistantUnavailableError(
                    "rate_limited", "The assistant is busy. Try again shortly."
                ) from exc
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                raise AssistantUnavailableError(
                    "unreachable", "The assistant could not be reached."
                ) from exc
            except anthropic.APIStatusError as exc:
                raise AssistantUnavailableError(
                    "provider_error", f"The assistant failed ({exc.status_code})."
                ) from exc
            if response.stop_reason == "refusal":
                raise AssistantUnavailableError(
                    "refusal", "The assistant can't answer this question."
                )
            if response.stop_reason == "max_tokens":
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                answer = ModelAnswer.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError):
                continue
            usage = response.usage
            return ModelResult(
                answer=answer,
                model=response.model,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
                attempts=attempt,
            )
        raise AssistantUnavailableError(
            "invalid_output", "The assistant gave an answer that could not be used."
        )


# --- offline (no AI provider) ------------------------------------------------------------------

_TOPICS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # topic: (question keywords, record item types)
    "medicines": (
        (
            "medicine",
            "medication",
            "tablet",
            "pill",
            "dose",
            "take",
            "taking",
            "drug",
            "prescri",
            "syrup",
            "capsule",
        ),
        ("medication", "prescription_line"),
    ),
    "appointments": (
        ("appointment", "visit", "follow", "book", "when", "next"),
        ("appointment", "follow_up"),
    ),
    "reports": (("report", "test", "result", "lab", "value", "blood"), ("test_report",)),
    "allergies": (("allerg",), ("allergy",)),
    "conditions": (("condition", "history", "problem"), ("condition",)),
}


def _line(item: RecordItem) -> str:
    return "; ".join(f"{k}: {v}" for k, v in item.fields if v and not v.startswith("rx:"))


OFFLINE_NOTE = (
    "AI answers are switched off on this server, so I can only show matching items from the "
    "record and the reviewed library. For anything else, please ask your doctor or pharmacist."
)


class OfflineAssistantModel:
    name = "offline"

    async def answer(self, request: AssistantRequest) -> ModelResult:
        q = request.question.lower()
        segments: list[Segment] = []
        for keywords, types in _TOPICS.values():
            if any(k in q for k in keywords):
                for item in [r for r in request.records if r.type in types][:6]:
                    segments.append(Segment(kind="record", text=_line(item), sources=[item.id]))
        if request.passages:
            p = request.passages[0]
            text = p.text if len(p.text) <= 700 else p.text[:699] + "…"
            segments.append(Segment(kind="general", text=text, sources=[p.id]))
        segments.append(
            Segment(
                kind="uncertain",
                text=OFFLINE_NOTE,
            )
        )
        return ModelResult(
            answer=ModelAnswer(segments=segments), model="offline-rules", latency_ms=0
        )


def build_assistant_model(settings: Settings) -> AssistantModel:
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key is not None:
        return ClaudeAssistantModel(settings)
    return OfflineAssistantModel()
