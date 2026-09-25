"""AI health assistant: conversations, messages, per-user privacy preferences, and the
trusted knowledge library the assistant may cite.

Privacy (docs/phases/13-ai-assistant.md):
- A conversation belongs to the person who had it (the patient, or a caregiver asking
  about the person they look after). Nobody else can read it, doctors included.
- Question and answer text is encrypted. Audit events never contain it.
- Conversations are kept for the owner's chosen number of days (or not at all: private
  mode stores nothing) and can be deleted at any time. Deletion removes the rows; chats
  are not clinical records.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.models import (
    Base,
    Entity,
    PatientOwned,
    patient_scope_key,
    patient_scoped_fk,
    str_enum,
    user_fk,
)

HISTORY_DAYS = (1, 7, 30, 90)


class AssistantPreference(Base, Entity):
    """One row per user (not per patient): how their own chats are kept."""

    __tablename__ = "assistant_preferences"
    __table_args__ = (
        Index("uq_assistant_preferences_user", "user_id", unique=True),
        CheckConstraint(f"history_days IN {HISTORY_DAYS}", name="history_days_choice"),
    )

    user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    # Keep saved chats for this many days after the last message.
    history_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30, server_default="30"
    )
    # Off: new chats are private by default (nothing is stored).
    save_by_default: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    # Off: the assistant answers general questions only and never reads the record.
    use_records: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")


class AssistantConversation(Base, Entity, PatientOwned):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        patient_scope_key(),
        Index("ix_assistant_conversations_patient_owner", "patient_id", "owner_user_id"),
        Index("ix_assistant_conversations_expires", "expires_at"),
    )

    owner_user_id: Mapped[uuid.UUID] = user_fk(nullable=False)
    title: Mapped[str | None] = mapped_column(EncryptedString("assistant_conversations.title"))
    last_message_at: Mapped[datetime | None]
    expires_at: Mapped[datetime] = mapped_column(nullable=False)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AssistantMessage(Base, Entity, PatientOwned):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        patient_scoped_fk(["conversation_id"], "assistant_conversations", ondelete="CASCADE"),
        Index("ix_assistant_messages_patient_conversation", "patient_id", "conversation_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole), nullable=False)
    # User: the question. Assistant: the checked answer as JSON (segments, sources, flags).
    content: Mapped[str] = mapped_column(
        EncryptedString("assistant_messages.content"), nullable=False
    )
    # Non-content metadata for quality review and cost: kind of answer, model, tokens.
    meta: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


# --- trusted knowledge library ---------------------------------------------------------


class KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class KnowledgeDocument(Base, Entity):
    """A reviewed health-education text from an allow-listed publisher. Loaded only by
    `scripts/import_knowledge.py`; never from arbitrary web content."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("uq_knowledge_documents_source_url", "publisher", "url", unique=True),
        CheckConstraint("review_due >= reviewed_on", name="review_due_after_review"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
    )

    publisher: Mapped[str] = mapped_column(String(64), nullable=False)  # allow-list key
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    category: Mapped[str] = mapped_column(String(32), nullable=False)  # medication, condition…
    # Medicine names (generic, lower case) this text is about, for matching the patient's list.
    medicines: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reviewed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewed_on: Mapped[date] = mapped_column(Date, nullable=False)
    review_due: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        str_enum(KnowledgeStatus), nullable=False, default=KnowledgeStatus.ACTIVE
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class KnowledgeChunk(Base, Entity):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_search", "search", postgresql_using="gin"),
        Index("uq_knowledge_chunks_document_seq", "document_id", "seq", unique=True),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
            "setweight(to_tsvector('english', body), 'B')",
            persisted=True,
        ),
    )
