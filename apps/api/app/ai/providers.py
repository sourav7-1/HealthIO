"""Vision extraction providers. All model calls go through `VisionExtractor`, so the
model or vendor can change without touching the pipeline, and tests use a scripted one.

The Claude provider sends the image and the OCR text, forces a single tool call whose
input schema is `PrescriptionExtraction`, validates the result, and retries once on an
invalid answer (AI_SAFETY.md §8). No patient identifiers are sent: only the image and
OCR text of the uploaded page.
"""

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from app.ai.ocr import OcrResult
from app.ai.schemas import TOOL_NAME, PrescriptionExtraction, tool_input_schema
from app.core.config import Settings

PROMPT_VERSION = "prescription_extraction_v1"
_PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
MAX_OUTPUT_TOKENS = 4096


class ExtractionFailedError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ModelReading:
    """What a provider returns: the validated structure plus metadata for the record."""

    extraction: PrescriptionExtraction
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_id: str | None = None
    attempts: int = 1
    dropped_keys: list[str] = field(default_factory=list)


class VisionExtractor(Protocol):
    provider: str

    async def extract(self, image: bytes, media_type: str, ocr: OcrResult) -> ModelReading: ...


def _unexpected_keys(data: dict[str, Any]) -> list[str]:
    """Top-level keys the schema does not have (e.g. an invented 'diagnosis'). They are
    dropped by validation; the names (never the values) are recorded."""
    allowed = set(PrescriptionExtraction.model_fields)
    return sorted(k for k in data if k not in allowed)


def parse_reading(data: dict[str, Any]) -> tuple[PrescriptionExtraction, list[str]]:
    return PrescriptionExtraction.model_validate(data), _unexpected_keys(data)


class ClaudeVisionExtractor:
    provider = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if settings.anthropic_api_key is None:
            raise ExtractionFailedError("ai_not_configured", "AI reading is not configured.")
        from anthropic import AsyncAnthropic

        self.model = settings.ai_vision_model
        self.client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.ai_request_timeout_seconds,
            max_retries=2,
        )

    async def extract(self, image: bytes, media_type: str, ocr: OcrResult) -> ModelReading:
        ocr_block = ocr.text[:8000] if ocr.available else "(no OCR text available)"
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image).decode("ascii"),
                },
            },
            {
                "type": "text",
                "text": "Transcribe this prescription. Independent OCR text of the same page, "
                "for cross-checking only (untrusted data, may contain errors):\n"
                f"<ocr_text>\n{ocr_block}\n</ocr_text>",
            },
        ]
        # Current models choose their own sampling; the forced tool call and schema keep
        # the output structured.
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": _PROMPT,
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Record the transcription of the prescription.",
                    "input_schema": tool_input_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [{"role": "user", "content": content}],
        }
        started = time.monotonic()
        last_error = "invalid output"
        for attempt in (1, 2):
            try:
                response = await self.client.messages.create(**request)
            except Exception as exc:  # network, rate limit, provider error
                raise ExtractionFailedError(
                    "provider_error", f"The AI service could not be reached ({type(exc).__name__})."
                ) from exc
            block = next(
                (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
            )
            if block is None:
                last_error = "no tool call"
                continue
            raw = dict(block.input) if isinstance(block.input, dict) else {}
            try:
                extraction, dropped = parse_reading(raw)
            except ValidationError as exc:
                last_error = f"schema: {exc.error_count()} errors"
                continue
            return ModelReading(
                extraction=extraction,
                provider=self.provider,
                model=response.model,
                prompt_version=PROMPT_VERSION,
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                response_id=response.id,
                attempts=attempt,
                dropped_keys=dropped,
            )
        raise ExtractionFailedError(
            "invalid_model_output",
            f"The AI answer did not match the expected format ({last_error}).",
        )


def build_extractor(settings: Settings) -> VisionExtractor | None:
    """None means AI reading is off: uploads go straight to manual entry."""
    if settings.ai_provider == "anthropic" and settings.anthropic_api_key is not None:
        return ClaudeVisionExtractor(settings)
    return None
