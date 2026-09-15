from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class JobOutcome:
    text: str
    artifacts: tuple[str, ...] = ()


@dataclass(slots=True)
class JobRecord:
    id: str
    label: str
    status: str = "queued"
    progress: float = 0.0
    detail: str = "En cola"
    result: str | None = None
    error: str | None = None
    artifacts: tuple[str, ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class JobCancelled(Exception):
    """Señal interna para finalizar un trabajo cancelado de forma cooperativa."""


class JobContext:
    def __init__(self, record: JobRecord, cancel_event: asyncio.Event) -> None:
        self.record = record
        self._cancel_event = cancel_event
        self._loop = asyncio.get_running_loop()

    @property
    def id(self) -> str:
        return self.record.id

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelled()

    def report_progress(self, progress: float, detail: str) -> None:
        bounded = max(0.0, min(100.0, progress))
        self._loop.call_soon_threadsafe(self._apply_progress, bounded, detail)

    def _apply_progress(self, progress: float, detail: str) -> None:
        self.record.progress = progress
        self.record.detail = detail


JobRunner = Callable[[JobContext], Awaitable[JobOutcome]]
JobListener = Callable[[JobRecord], Awaitable[None]]


class JobManager:
    def __init__(self, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency debe ser positivo")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._records: dict[str, JobRecord] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._listeners: list[JobListener] = []

    @property
    def records(self) -> tuple[JobRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: item.created_at, reverse=True))

    def get(self, job_id: str) -> JobRecord | None:
        return self._records.get(job_id)

    def add_listener(self, listener: JobListener) -> None:
        self._listeners.append(listener)

    def submit(
        self,
        label: str,
        runner: JobRunner,
        metadata: Mapping[str, object] | None = None,
    ) -> JobRecord:
        record = JobRecord(
            id=uuid4().hex[:12],
            label=label,
            metadata=dict(metadata or {}),
        )
        cancel_event = asyncio.Event()
        self._records[record.id] = record
        self._cancel_events[record.id] = cancel_event
        self._tasks[record.id] = asyncio.create_task(self._run(record, cancel_event, runner))
        return record

    def cancel(self, job_id: str) -> bool:
        record = self._records.get(job_id)
        if record is None or record.status in {"succeeded", "failed", "cancelled"}:
            return False
        self._cancel_events[job_id].set()
        record.detail = "Cancelación solicitada"
        return True

    async def shutdown(self) -> None:
        for job_id in tuple(self._tasks):
            self.cancel(job_id)
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(
        self,
        record: JobRecord,
        cancel_event: asyncio.Event,
        runner: JobRunner,
    ) -> None:
        try:
            async with self._semaphore:
                if cancel_event.is_set():
                    record.status = "cancelled"
                    record.detail = "Cancelado antes de iniciar"
                    return
                record.status = "running"
                record.started_at = datetime.now(timezone.utc).isoformat()
                context = JobContext(record, cancel_event)
                outcome = await runner(context)
                if cancel_event.is_set():
                    record.status = "cancelled"
                    record.detail = "Cancelado"
                else:
                    record.status = "succeeded"
                    record.progress = 100.0
                    record.detail = "Completado"
                    record.result = outcome.text
                    record.artifacts = outcome.artifacts
        except JobCancelled:
            record.status = "cancelled"
            record.detail = "Cancelado"
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.detail = "Cancelado"
        except Exception as error:
            record.status = "failed"
            record.error = f"{type(error).__name__}: {error}"
            record.detail = "Falló"
        finally:
            record.finished_at = datetime.now(timezone.utc).isoformat()
            await self._notify(record)
            self._tasks.pop(record.id, None)
            self._cancel_events.pop(record.id, None)

    async def _notify(self, record: JobRecord) -> None:
        for listener in tuple(self._listeners):
            try:
                await listener(record)
            except Exception:
                # Un listener de notificaciones nunca debe cambiar el estado del trabajo.
                continue
