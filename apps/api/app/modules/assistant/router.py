"""AI health assistant API.

- `POST /patients/{id}/assistant/ask` answers one question. With `save` it is added to a
  conversation the caller owns; without it (private mode) nothing is stored and the
  client sends the earlier turns itself.
- Conversations are listed, read and deleted only by the person who had them.
- `/me/assistant/preferences`: how long chats are kept, private by default, and whether
  the assistant may read the record at all.

Every question is audited without its content (mode, sections used, checks triggered).
Needs `use_ai_assistant` (patients have it; caregivers only if granted).
"""

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.assistant_model import AssistantUnavailableError, build_assistant_model
from app.core.db import get_session
from app.core.errors import ServiceUnavailableError, ValidationFailedError
from app.modules.access.context import PatientRequest, patient_request
from app.modules.access.dependencies import authenticated
from app.modules.access.permissions import Permission
from app.modules.assistant import service
from app.modules.assistant.models import (
    HISTORY_DAYS,
    AssistantConversation,
    AssistantPreference,
    MessageRole,
)
from app.modules.identity.service import Principal

router = APIRouter(tags=["assistant"])

_use = patient_request(Permission.USE_AI_ASSISTANT)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Turn(_In):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskIn(_In):
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: uuid.UUID | None = None
    save: bool = Field(default=True, description="False: private, nothing is stored")
    history: list[Turn] = Field(
        default_factory=list, max_length=12, description="Private mode only"
    )


class SourceOut(BaseModel):
    id: str
    kind: Literal["record", "library"]
    label: str
    publisher: str | None
    url: str | None
    reviewed_on: str | None


class SegmentOut(BaseModel):
    kind: Literal["record", "general", "uncertain"]
    text: str
    sources: list[SourceOut]


class AnswerOut(BaseModel):
    segments: list[SegmentOut]
    urgent: bool
    emergency: list[str]
    declined: str | None
    questions_for_doctor: list[str]
    mode: Literal["ai", "offline", "emergency"]
    disclaimer: str


class MessageOut(BaseModel):
    id: uuid.UUID | None
    role: Literal["user", "assistant"]
    created_at: datetime | None
    question: str | None = None
    answer: AnswerOut | None = None


class AskOut(BaseModel):
    conversation_id: uuid.UUID | None
    saved: bool
    message: MessageOut


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    last_message_at: datetime | None
    expires_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class PreferencesOut(BaseModel):
    history_days: int
    save_by_default: bool
    use_records: bool
    history_day_choices: list[int]


class PreferencesIn(_In):
    history_days: int | None = None
    save_by_default: bool | None = None
    use_records: bool | None = None


def _answer_out(data: dict[str, Any]) -> AnswerOut:
    """From the stored JSON (service.Reply.to_json) to the API shape."""
    sources: dict[str, dict[str, str | None]] = data.get("sources", {})
    segments = [
        SegmentOut(
            kind=s["kind"],
            text=s["text"],
            sources=[SourceOut(**sources[x]) for x in s["sources"] if x in sources],
        )
        for s in data["segments"]
    ]
    return AnswerOut(
        segments=segments,
        urgent=bool(data["urgent"]),
        emergency=list(data.get("emergency", [])),
        declined=data.get("declined"),
        questions_for_doctor=list(data.get("questions_for_doctor", [])),
        mode=data.get("mode", "ai"),
        disclaimer=service.DISCLAIMER,
    )


def _conversation_out(c: AssistantConversation) -> ConversationOut:
    return ConversationOut(
        id=c.id, title=c.title, last_message_at=c.last_message_at, expires_at=c.expires_at
    )


@router.post("/patients/{patient_id}/assistant/ask", response_model=AskOut)
async def ask(
    patient_id: uuid.UUID, body: AskIn, request: Request, ctx: PatientRequest = _use
) -> AskOut:
    """Ask about this person's medicines, prescriptions, appointments or record, or a
    general health question. Answers say which parts come from the record, which from the
    reviewed library, and what is uncertain. Symptoms that could be an emergency get
    urgent-care guidance straight away."""
    prefs = await service.preferences(ctx.session, ctx.actor_id)
    conversation = None
    history: list[dict[str, str]]
    if body.conversation_id is not None:
        if not body.save:
            raise ValidationFailedError("A saved conversation can't be continued in private mode.")
        conversation = await service.get_conversation(
            ctx.session, ctx.patient_id, ctx.actor_id, body.conversation_id
        )
        history = await service.conversation_history(ctx.session, conversation)
    else:
        history = [t.model_dump() for t in body.history] if not body.save else []
    model = getattr(request.app.state, "assistant_model", None) or build_assistant_model(
        request.app.state.settings
    )
    try:
        reply = await service.ask(
            ctx.session,
            ctx.access,
            actor=ctx.actor_id,
            asker="patient" if "self" in ctx.access.via else "caregiver",
            question=body.question,
            history=history,
            model=model,
            use_records=prefs.use_records,
        )
    except AssistantUnavailableError as exc:
        await ctx.audit(
            "assistant.unavailable", resource_type="assistant", context={"code": exc.code}
        )
        await ctx.session.commit()
        raise ServiceUnavailableError(str(exc)) from exc

    message_id = None
    created_at = None
    if body.save:
        conversation, msg = await service.save_exchange(
            ctx.session,
            patient_id=ctx.patient_id,
            owner=ctx.actor_id,
            conversation=conversation,
            question=body.question,
            reply=reply,
            history_days=prefs.history_days,
        )
        message_id, created_at = msg.id, msg.created_at
    meta = reply.meta
    await ctx.audit(
        "assistant.ask",
        resource_type="assistant_conversation",
        resource_id=conversation.id if conversation else None,
        context={
            "mode": reply.mode,
            "saved": str(body.save),
            "urgent": str(reply.answer.urgent),
            "declined": reply.answer.declined or "",
            "sections": ",".join(meta.get("sections", [])),
            "passages": str(meta.get("passages", 0)),
            "withheld": str(meta.get("withheld", 0)),
            "checks": ",".join(reply.answer.problems),
            "model": str(meta.get("model", "")),
            "signals": ",".join(meta.get("signals", [])),
        },
    )
    if meta.get("withheld"):
        await ctx.audit(
            "assistant.untrusted_text_withheld",
            resource_type="assistant",
            context={"items": str(meta["withheld"])},
        )
    await ctx.session.commit()
    return AskOut(
        conversation_id=conversation.id if conversation else None,
        saved=body.save,
        message=MessageOut(
            id=message_id,
            role="assistant",
            created_at=created_at,
            answer=_answer_out(reply.to_json()),
        ),
    )


@router.get("/patients/{patient_id}/assistant/conversations", response_model=list[ConversationOut])
async def list_conversations(
    patient_id: uuid.UUID, ctx: PatientRequest = _use
) -> list[ConversationOut]:
    """Your own saved chats about this person (never anyone else's)."""
    rows = await service.list_conversations(ctx.session, ctx.patient_id, ctx.actor_id)
    await ctx.audit("assistant.conversation_list", resource_type="assistant_conversation")
    await ctx.session.commit()
    return [_conversation_out(c) for c in rows]


@router.get(
    "/patients/{patient_id}/assistant/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
)
async def get_conversation(
    patient_id: uuid.UUID, conversation_id: uuid.UUID, ctx: PatientRequest = _use
) -> ConversationDetailOut:
    c = await service.get_conversation(ctx.session, ctx.patient_id, ctx.actor_id, conversation_id)
    messages = []
    for m in await service.messages_of(ctx.session, c):
        if m.role == MessageRole.USER:
            messages.append(
                MessageOut(id=m.id, role="user", created_at=m.created_at, question=m.content)
            )
        else:
            messages.append(
                MessageOut(
                    id=m.id,
                    role="assistant",
                    created_at=m.created_at,
                    answer=_answer_out(json.loads(m.content)),
                )
            )
    await ctx.audit(
        "assistant.conversation_view", resource_type="assistant_conversation", resource_id=c.id
    )
    await ctx.session.commit()
    return ConversationDetailOut(**_conversation_out(c).model_dump(), messages=messages)


@router.delete(
    "/patients/{patient_id}/assistant/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    patient_id: uuid.UUID, conversation_id: uuid.UUID, ctx: PatientRequest = _use
) -> Response:
    await service.get_conversation(ctx.session, ctx.patient_id, ctx.actor_id, conversation_id)
    await service.delete_conversations(ctx.session, ctx.patient_id, ctx.actor_id, conversation_id)
    await ctx.audit(
        "assistant.conversation_delete",
        resource_type="assistant_conversation",
        resource_id=conversation_id,
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/patients/{patient_id}/assistant/conversations", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_all_conversations(patient_id: uuid.UUID, ctx: PatientRequest = _use) -> Response:
    count = await service.delete_conversations(ctx.session, ctx.patient_id, ctx.actor_id)
    await ctx.audit(
        "assistant.conversation_delete_all",
        resource_type="assistant_conversation",
        context={"count": str(count)},
    )
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _prefs_out(p: AssistantPreference) -> PreferencesOut:
    return PreferencesOut(
        history_days=p.history_days,
        save_by_default=p.save_by_default,
        use_records=p.use_records,
        history_day_choices=list(HISTORY_DAYS),
    )


@router.get("/me/assistant/preferences", response_model=PreferencesOut)
async def get_preferences(
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> PreferencesOut:
    prefs = await service.preferences(session, principal.user_id)
    await session.commit()
    return _prefs_out(prefs)


@router.put("/me/assistant/preferences", response_model=PreferencesOut)
async def update_preferences(
    body: PreferencesIn,
    principal: Principal = Depends(authenticated()),
    session: AsyncSession = Depends(get_session),
) -> PreferencesOut:
    """Shortening how long chats are kept also applies to chats already saved."""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "history_days" in changes and changes["history_days"] not in HISTORY_DAYS:
        raise ValidationFailedError(f"Choose one of {', '.join(map(str, HISTORY_DAYS))} days.")
    prefs = await service.update_preferences(session, principal.user_id, changes)
    await session.commit()
    return _prefs_out(prefs)
