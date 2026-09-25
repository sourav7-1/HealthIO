"""The health assistant: one question in, one checked and sourced answer out.

question
  │ triage()            red flags → fixed urgent-care answer, no model call
  ▼
record context         only what the caller may see; no identifiers; untrusted text
  │                    filtered for prompt injection (context.py)
trusted library        reviewed passages from allow-listed publishers (knowledge.py)
  ▼
model                  one structured answer (app/ai/assistant_model.py)
  ▼
check_answer()         sources verified, numbers traced to the record, forbidden
  │                    advice replaced by a decline (safety.py)
  ▼
saved (optional)       encrypted, owner-only, deleted after the owner's retention
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.assistant_model import (
    PROMPT_VERSION,
    AssistantModel,
    AssistantRequest,
    ModelResult,
)
from app.ai.injection import neutralise
from app.core.errors import NotFoundError, RateLimitedError
from app.modules.access.service import PatientAccess
from app.modules.assistant import context as record_context
from app.modules.assistant import knowledge
from app.modules.assistant.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantPreference,
    MessageRole,
)
from app.modules.assistant.safety import (
    CheckedAnswer,
    Segment,
    check_answer,
    triage,
    urgent_text,
)
from app.modules.audit.models import AuditLog

DISCLAIMER = (
    "Health Io's assistant explains what is in your record and gives general information. "
    "It can't diagnose, prescribe or change your treatment. For medical decisions, talk to "
    "your doctor or pharmacist."
)
DAILY_QUESTION_LIMIT = 60
HISTORY_TURNS = 6


# --- preferences -------------------------------------------------------------------------


async def preferences(session: AsyncSession, user_id: uuid.UUID) -> AssistantPreference:
    row = await session.scalar(
        select(AssistantPreference).where(AssistantPreference.user_id == user_id)
    )
    if row is None:
        row = AssistantPreference(user_id=user_id, created_by=user_id, updated_by=user_id)
        session.add(row)
        await session.flush()
    return row


async def update_preferences(
    session: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any]
) -> AssistantPreference:
    row = await preferences(session, user_id)
    for key, value in changes.items():
        setattr(row, key, value)
    row.updated_by = user_id
    await session.flush()
    if "history_days" in changes:
        # Shortening retention applies to chats already saved.
        await session.execute(
            update(AssistantConversation)
            .where(AssistantConversation.owner_user_id == user_id)
            .values(
                expires_at=func.least(
                    AssistantConversation.expires_at,
                    func.coalesce(
                        AssistantConversation.last_message_at, AssistantConversation.created_at
                    )
                    + timedelta(days=row.history_days),
                )
            )
        )
    return row


# --- conversations -----------------------------------------------------------------------


async def list_conversations(
    session: AsyncSession, patient_id: uuid.UUID, owner: uuid.UUID
) -> list[AssistantConversation]:
    rows = await session.scalars(
        select(AssistantConversation)
        .where(
            AssistantConversation.patient_id == patient_id,
            AssistantConversation.owner_user_id == owner,
            AssistantConversation.expires_at > datetime.now(UTC),
        )
        .order_by(AssistantConversation.last_message_at.desc().nulls_last())
    )
    return list(rows.all())


async def get_conversation(
    session: AsyncSession, patient_id: uuid.UUID, owner: uuid.UUID, conversation_id: uuid.UUID
) -> AssistantConversation:
    """Owner only: another person's chat about the same patient is 404."""
    row = await session.scalar(
        select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.patient_id == patient_id,
            AssistantConversation.owner_user_id == owner,
            AssistantConversation.expires_at > datetime.now(UTC),
        )
    )
    if row is None:
        raise NotFoundError()
    return row


async def messages_of(
    session: AsyncSession, conversation: AssistantConversation
) -> list[AssistantMessage]:
    rows = await session.scalars(
        select(AssistantMessage)
        .where(
            AssistantMessage.patient_id == conversation.patient_id,
            AssistantMessage.conversation_id == conversation.id,
        )
        .order_by(AssistantMessage.created_at, AssistantMessage.id)
    )
    return list(rows.all())


async def delete_conversations(
    session: AsyncSession,
    patient_id: uuid.UUID,
    owner: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
) -> int:
    """Hard delete (messages cascade). Chats are the owner's, not clinical records."""
    stmt = delete(AssistantConversation).where(
        AssistantConversation.patient_id == patient_id,
        AssistantConversation.owner_user_id == owner,
    )
    if conversation_id is not None:
        stmt = stmt.where(AssistantConversation.id == conversation_id)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def purge_expired(session: AsyncSession, now: datetime | None = None) -> int:
    result = await session.execute(
        delete(AssistantConversation).where(
            AssistantConversation.expires_at <= (now or datetime.now(UTC))
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- asking ------------------------------------------------------------------------------


@dataclass
class SourceRef:
    id: str
    kind: str  # record | library
    label: str
    publisher: str | None = None
    url: str | None = None
    reviewed_on: str | None = None


@dataclass
class Reply:
    answer: CheckedAnswer
    emergency: list[str]
    sources: dict[str, SourceRef]
    mode: str  # ai | offline | emergency
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "segments": [s.model_dump() for s in self.answer.segments],
            "urgent": self.answer.urgent,
            "emergency": self.emergency,
            "declined": self.answer.declined,
            "questions_for_doctor": self.answer.questions_for_doctor,
            "sources": {k: v.__dict__ for k, v in self.sources.items()},
            "mode": self.mode,
        }


async def questions_today(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.actor_user_id == user_id,
                AuditLog.action == "assistant.ask",
                AuditLog.occurred_at > datetime.now(UTC) - timedelta(days=1),
            )
        )
        or 0
    )


def _history_from_messages(rows: list[AssistantMessage]) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for m in rows[-HISTORY_TURNS * 2 :]:
        if m.role == MessageRole.USER:
            turns.append({"role": "user", "content": neutralise(m.content, 2000)})
        else:
            data = json.loads(m.content)
            text = " ".join(s["text"] for s in data.get("segments", []))
            turns.append({"role": "assistant", "content": neutralise(text, 3000)})
    return _alternate(turns)


def _alternate(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop anything that would break user/assistant alternation (the new question is
    appended as a user turn, so history must end with an assistant turn)."""
    out: list[dict[str, str]] = []
    for t in turns:
        if t["role"] not in ("user", "assistant") or not t["content"].strip():
            continue
        if out and out[-1]["role"] == t["role"]:
            out[-1] = t
        else:
            out.append(t)
    while out and out[0]["role"] != "user":
        out.pop(0)
    while out and out[-1]["role"] != "assistant":
        out.pop()
    return out


def _render_library(passages: list[knowledge.Passage]) -> str:
    return "\n".join(
        f'<passage id="{p.id}" publisher="{neutralise(p.publisher, 120)}" '
        f'title="{neutralise(p.title, 200)}" reviewed="{p.reviewed_on}">'
        f"{neutralise((p.heading + ': ' if p.heading else '') + p.text, 1600)}</passage>"
        for p in passages
    )


def _source_refs(
    ctx: record_context.RecordContext, passages: list[knowledge.Passage]
) -> dict[str, SourceRef]:
    refs: dict[str, SourceRef] = {}
    for item in ctx.items:
        first = next((v for _, v in item.fields if v and not v.startswith("rx:")), "")
        refs[item.id] = SourceRef(
            item.id, "record", f"{item.type.replace('_', ' ').capitalize()}: {first}"[:160]
        )
    for p in passages:
        refs[p.id] = SourceRef(
            p.id, "library", p.title, p.publisher, p.url, p.reviewed_on.isoformat()
        )
    return refs


async def ask(
    session: AsyncSession,
    access: PatientAccess,
    *,
    actor: uuid.UUID,
    asker: str,  # patient | caregiver
    question: str,
    history: list[dict[str, str]],
    model: AssistantModel,
    use_records: bool,
) -> Reply:
    if await questions_today(session, actor) >= DAILY_QUESTION_LIMIT:
        raise RateLimitedError(
            "You've reached today's limit for the assistant. Please try again tomorrow."
        )

    t = triage(question)
    if t.urgent:
        # No model call: the answer is fixed and immediate.
        answer = CheckedAnswer(
            segments=[
                Segment(
                    kind="uncertain",
                    text="I can't assess symptoms. Because what you describe could be serious, "
                    "please get medical help now rather than waiting for an answer here.",
                )
            ],
            urgent=True,
            declined=None,
            questions_for_doctor=[],
        )
        return Reply(answer, urgent_text(t), {}, "emergency", {"signals": list(t.signals)})

    ctx = (
        await record_context.build(session, access)
        if use_records
        else record_context.RecordContext()
    )
    passages = await knowledge.search(session, question, medicines=ctx.medicine_names)
    user_content = (
        f"<asker>{asker}</asker>\n"
        "<patient_records>\n"
        + (ctx.rendered or "(No record items were shared with the assistant for this question.)")
        + "\n</patient_records>\n<trusted_reference>\n"
        + (_render_library(passages) or "(No reviewed passage matched this question.)")
        + f"\n</trusted_reference>\n<question>\n{neutralise(question, 2000)}\n</question>"
    )
    result: ModelResult = await model.answer(
        AssistantRequest(
            question=question,
            user_content=user_content,
            history=_alternate(history),
            records=ctx.items,
            passages=passages,
        )
    )
    checked = check_answer(
        result.answer,
        record_texts=ctx.texts,
        library_urls={p.id: p.url for p in passages},
        triage_result=t,
    )
    refs = _source_refs(ctx, passages)
    used = {s for seg in checked.segments for s in seg.sources}
    return Reply(
        checked,
        urgent_text(t) if checked.urgent else [],
        {k: v for k, v in refs.items() if k in used},
        "offline" if result.model == "offline-rules" else "ai",
        {
            "model": result.model,
            "prompt_version": PROMPT_VERSION,
            "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cache_read_tokens": result.cache_read_tokens,
            "attempts": result.attempts,
            "sections": ctx.sections,
            "records_used": use_records,
            "passages": len(passages),
            "withheld": len(ctx.withheld),
            "problems": checked.problems,
            "kinds": [s.kind for s in checked.segments],
        },
    )


async def save_exchange(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    owner: uuid.UUID,
    conversation: AssistantConversation | None,
    question: str,
    reply: Reply,
    history_days: int,
) -> tuple[AssistantConversation, AssistantMessage]:
    now = datetime.now(UTC)
    if conversation is None:
        conversation = AssistantConversation(
            patient_id=patient_id,
            owner_user_id=owner,
            title=question.strip()[:80],
            expires_at=now + timedelta(days=history_days),
            created_by=owner,
            updated_by=owner,
        )
        session.add(conversation)
        await session.flush()
    conversation.last_message_at = now
    conversation.expires_at = now + timedelta(days=history_days)
    conversation.updated_by = owner
    user_msg = AssistantMessage(
        patient_id=patient_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=question.strip(),
        meta={},
        created_by=owner,
        updated_by=owner,
    )
    session.add(user_msg)
    await session.flush()  # keeps the question ordered before the answer
    answer_msg = AssistantMessage(
        patient_id=patient_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=json.dumps(reply.to_json()),
        meta={k: v for k, v in reply.meta.items() if k != "problems"} | {"mode": reply.mode},
        created_by=owner,
        updated_by=owner,
    )
    session.add(answer_msg)
    await session.flush()
    return conversation, answer_msg


async def conversation_history(
    session: AsyncSession, conversation: AssistantConversation
) -> list[dict[str, str]]:
    return _history_from_messages(await messages_of(session, conversation))
