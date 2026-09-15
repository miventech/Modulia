from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Attachment:
    id: str
    name: str
    path: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    id: str
    channel: str
    conversation_id: str
    principal_id: str
    text: str | None
    attachments: tuple[Attachment, ...]
    received_at: datetime
    metadata: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def local(cls, text: str) -> InboundMessage:
        return cls(
            id=str(uuid4()),
            channel="local",
            conversation_id="local-console",
            principal_id="local-user",
            text=text,
            attachments=(),
            received_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    text: str | None = None
    attachments: tuple[Attachment, ...] = ()
    reply_to: str | None = None
