from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from modulai.core.commands import CommandRegistry
from modulai.core.jobs import JobManager
from modulai.infrastructure.audit import AuditStore


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    schema_version: int
    id: str
    name: str
    version: str
    entrypoint: str
    requires_core: str
    capabilities: tuple[str, ...]
    config_schema: str | None = None


@dataclass(frozen=True, slots=True)
class ModuleContext:
    commands: CommandRegistry
    root: Path
    data_dir: Path
    logger: logging.Logger
    jobs: JobManager
    store: AuditStore
    config: dict[str, object] = field(default_factory=dict)


class Module(Protocol):
    async def setup(self, context: ModuleContext) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
