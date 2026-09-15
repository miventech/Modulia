from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from modulai.core.commands import CommandResult, ParsedCommand
from modulai.core.messages import InboundMessage


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    text: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: int
    principal_id: str
    text: str
    color: str
    importance: str
    category: str
    completed: bool
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class FinanceRecord:
    id: int
    principal_id: str
    kind: str
    description: str
    amount_cents: int
    category: str
    occurred_on: str
    due_on: str | None
    status: str
    recurring: bool
    recurring_parent_id: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    id: int
    principal_id: str
    channel: str
    conversation_id: str
    kind: str
    payload: str
    next_run_at: str
    recurrence: str | None
    status: str
    created_at: str
    last_run_at: str | None


class AuditStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._connection: sqlite3.Connection | None = None

    def open(self) -> None:
        if self._connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                message_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                command_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                ok INTEGER NOT NULL,
                result_text TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS conversations_one_open
            ON conversations(channel, conversation_id, principal_id)
            WHERE ended_at IS NULL
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                text TEXT NOT NULL,
                color TEXT NOT NULL,
                importance TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'General',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        task_columns = {
            str(row[1]) for row in self._connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "category" not in task_columns:
            self._connection.execute(
                "ALTER TABLE tasks ADD COLUMN category TEXT NOT NULL DEFAULT 'General'"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                description TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                category TEXT NOT NULL,
                occurred_on TEXT NOT NULL,
                due_on TEXT,
                status TEXT NOT NULL,
                recurring INTEGER NOT NULL DEFAULT 0,
                recurring_parent_id INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        finance_columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(finance_entries)").fetchall()
        }
        if "recurring" not in finance_columns:
            self._connection.execute(
                "ALTER TABLE finance_entries ADD COLUMN recurring INTEGER NOT NULL DEFAULT 0"
            )
        if "recurring_parent_id" not in finance_columns:
            self._connection.execute(
                "ALTER TABLE finance_entries ADD COLUMN recurring_parent_id INTEGER"
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS finance_entries_by_month "
            "ON finance_entries(principal_id, occurred_on DESC, id DESC)"
        )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS finance_recurring_occurrence "
            "ON finance_entries(recurring_parent_id, occurred_on) "
            "WHERE recurring_parent_id IS NOT NULL"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                next_run_at TEXT NOT NULL,
                recurrence TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_run_at TEXT
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS schedules_due ON schedules(status, next_run_at)"
        )
        self._connection.commit()

    def record_command(
        self,
        message: InboundMessage,
        command: ParsedCommand,
        result: CommandResult,
    ) -> None:
        if self._connection is None:
            raise RuntimeError("AuditStore no está abierto")
        self._connection.execute(
            """
            INSERT INTO audit_events (
                created_at, message_id, channel, conversation_id, principal_id,
                command_name, arguments_json, ok, result_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.received_at.isoformat(),
                message.id,
                message.channel,
                message.conversation_id,
                message.principal_id,
                command.name,
                json.dumps(command.args, ensure_ascii=False),
                int(result.ok),
                result.text,
            ),
        )
        self._connection.commit()

    def count_events(self) -> int:
        if self._connection is None:
            raise RuntimeError("AuditStore no está abierto")
        row = self._connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
        return int(row[0]) if row else 0

    def start_conversation(self, message: InboundMessage) -> bool:
        connection = self._require_connection()
        if self.is_conversation_active(message):
            return False
        connection.execute(
            """
            INSERT INTO conversations (
                channel, conversation_id, principal_id, started_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                message.channel,
                message.conversation_id,
                message.principal_id,
                _utc_now(),
            ),
        )
        connection.commit()
        return True

    def end_conversation(self, message: InboundMessage) -> bool:
        connection = self._require_connection()
        cursor = connection.execute(
            """
            UPDATE conversations SET ended_at = ?
            WHERE channel = ? AND conversation_id = ? AND principal_id = ?
              AND ended_at IS NULL
            """,
            (
                _utc_now(),
                message.channel,
                message.conversation_id,
                message.principal_id,
            ),
        )
        connection.commit()
        return cursor.rowcount > 0

    def is_conversation_active(self, message: InboundMessage) -> bool:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE channel = ? AND conversation_id = ? AND principal_id = ?
              AND ended_at IS NULL
            LIMIT 1
            """,
            (message.channel, message.conversation_id, message.principal_id),
        ).fetchone()
        return row is not None

    def record_message(self, message: InboundMessage, direction: str, text: str) -> None:
        if direction not in {"inbound", "outbound"}:
            raise ValueError("Dirección de mensaje inválida")
        connection = self._require_connection()
        connection.execute(
            """
            INSERT INTO messages (
                external_id, channel, conversation_id, principal_id,
                direction, text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.channel,
                message.conversation_id,
                message.principal_id,
                direction,
                text,
                _utc_now(),
            ),
        )
        connection.commit()

    def list_messages(
        self, principal_id: str = "local-user", limit: int = 100, conversation_id: str | None = None
    ) -> list[dict[str, object]]:
        connection = self._require_connection()
        bounded = max(1, min(1000, int(limit)))
        query = "SELECT id, external_id, channel, conversation_id, direction, text, created_at FROM messages WHERE principal_id = ?"
        params: list[object] = [principal_id]
        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(bounded)
        rows = connection.execute(query, params).fetchall()
        return [
            {"id": int(row[0]), "external_id": str(row[1]), "channel": str(row[2]), "conversation_id": str(row[3]), "direction": str(row[4]), "text": str(row[5]), "created_at": str(row[6])}
            for row in rows
        ]

    def add_memory(self, principal_id: str, text: str) -> MemoryRecord:
        connection = self._require_connection()
        created_at = _utc_now()
        cursor = connection.execute(
            "INSERT INTO memories (principal_id, text, created_at) VALUES (?, ?, ?)",
            (principal_id, text, created_at),
        )
        connection.commit()
        return MemoryRecord(id=int(cursor.lastrowid), text=text, created_at=created_at)

    def list_memories(self, principal_id: str) -> tuple[MemoryRecord, ...]:
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT id, text, created_at FROM memories
            WHERE principal_id = ? ORDER BY id DESC
            """,
            (principal_id,),
        ).fetchall()
        return tuple(MemoryRecord(int(row[0]), str(row[1]), str(row[2])) for row in rows)

    def delete_memory(self, principal_id: str, memory_id: int) -> bool:
        connection = self._require_connection()
        cursor = connection.execute(
            "DELETE FROM memories WHERE principal_id = ? AND id = ?",
            (principal_id, memory_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def add_task(
        self,
        principal_id: str,
        text: str,
        color: str = "yellow",
        importance: str = "media",
        category: str = "General",
    ) -> TaskRecord:
        connection = self._require_connection()
        created_at = _utc_now()
        cursor = connection.execute(
            """
            INSERT INTO tasks (principal_id, text, color, importance, category, completed, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (principal_id, text, color, importance, category, created_at),
        )
        connection.commit()
        return TaskRecord(
            int(cursor.lastrowid), principal_id, text, color, importance, category, False, created_at, None
        )

    def list_tasks(self, principal_id: str, include_completed: bool = False) -> tuple[TaskRecord, ...]:
        connection = self._require_connection()
        statement = """
            SELECT id, principal_id, text, color, importance, category, completed, created_at, completed_at
            FROM tasks WHERE principal_id = ?
        """
        if not include_completed:
            statement += " AND completed = 0"
        statement += " ORDER BY completed ASC, category COLLATE NOCASE ASC, CASE importance WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, id DESC"
        rows = connection.execute(statement, (principal_id,)).fetchall()
        return tuple(_task_from_row(row) for row in rows)

    def update_task(
        self,
        principal_id: str,
        task_id: int,
        *,
        text: str | None = None,
        color: str | None = None,
        importance: str | None = None,
        category: str | None = None,
        completed: bool | None = None,
    ) -> TaskRecord | None:
        current = self.get_task(principal_id, task_id)
        if current is None:
            return None
        next_text = text if text is not None else current.text
        next_color = color if color is not None else current.color
        next_importance = importance if importance is not None else current.importance
        next_category = category if category is not None else current.category
        next_completed = completed if completed is not None else current.completed
        completed_at = _utc_now() if next_completed and not current.completed else None if not next_completed else current.completed_at
        connection = self._require_connection()
        connection.execute(
            """
            UPDATE tasks SET text = ?, color = ?, importance = ?, category = ?, completed = ?, completed_at = ?
            WHERE id = ? AND principal_id = ?
            """,
            (next_text, next_color, next_importance, next_category, int(next_completed), completed_at, task_id, principal_id),
        )
        connection.commit()
        return self.get_task(principal_id, task_id)

    def get_task(self, principal_id: str, task_id: int) -> TaskRecord | None:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT id, principal_id, text, color, importance, category, completed, created_at, completed_at
            FROM tasks WHERE id = ? AND principal_id = ?
            """,
            (task_id, principal_id),
        ).fetchone()
        return _task_from_row(row) if row else None

    def delete_task(self, principal_id: str, task_id: int) -> bool:
        connection = self._require_connection()
        cursor = connection.execute(
            "DELETE FROM tasks WHERE id = ? AND principal_id = ?",
            (task_id, principal_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def add_finance_entry(
        self,
        principal_id: str,
        kind: str,
        description: str,
        amount_cents: int,
        category: str,
        occurred_on: str,
        due_on: str | None = None,
        status: str = "registered",
        recurring: bool = False,
        recurring_parent_id: int | None = None,
    ) -> FinanceRecord:
        connection = self._require_connection()
        created_at = _utc_now()
        cursor = connection.execute(
            """
            INSERT INTO finance_entries (
                principal_id, kind, description, amount_cents, category,
                occurred_on, due_on, status, recurring, recurring_parent_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                principal_id,
                kind,
                description,
                amount_cents,
                category,
                occurred_on,
                due_on,
                status,
                int(recurring),
                recurring_parent_id,
                created_at,
            ),
        )
        connection.commit()
        return FinanceRecord(
            int(cursor.lastrowid),
            principal_id,
            kind,
            description,
            amount_cents,
            category,
            occurred_on,
            due_on,
            status,
            recurring,
            recurring_parent_id,
            created_at,
        )

    def list_finance_entries(
        self,
        principal_id: str,
        month: str | None = None,
    ) -> tuple[FinanceRecord, ...]:
        connection = self._require_connection()
        if month is not None:
            self._materialize_recurring_expenses(principal_id, month)
        statement = """
            SELECT id, principal_id, kind, description, amount_cents, category,
                   occurred_on, due_on, status, recurring, recurring_parent_id, created_at
            FROM finance_entries WHERE principal_id = ?
        """
        parameters: list[object] = [principal_id]
        if month is not None:
            statement += " AND occurred_on >= ? AND occurred_on < ?"
            year, month_number = (int(part) for part in month.split("-", maxsplit=1))
            next_month = f"{year + 1:04d}-01-01" if month_number == 12 else f"{year:04d}-{month_number + 1:02d}-01"
            parameters.extend((f"{month}-01", next_month))
        statement += " ORDER BY occurred_on DESC, id DESC"
        rows = connection.execute(statement, parameters).fetchall()
        return tuple(_finance_from_row(row) for row in rows)

    def get_finance_entry(self, principal_id: str, entry_id: int) -> FinanceRecord | None:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT id, principal_id, kind, description, amount_cents, category,
                   occurred_on, due_on, status, recurring, recurring_parent_id, created_at
            FROM finance_entries WHERE id = ? AND principal_id = ?
            """,
            (entry_id, principal_id),
        ).fetchone()
        return _finance_from_row(row) if row else None

    def update_finance_entry(
        self,
        principal_id: str,
        entry_id: int,
        *,
        kind: str | None = None,
        description: str | None = None,
        amount_cents: int | None = None,
        category: str | None = None,
        occurred_on: str | None = None,
        due_on: str | None = None,
        status: str | None = None,
        recurring: bool | None = None,
    ) -> FinanceRecord | None:
        current = self.get_finance_entry(principal_id, entry_id)
        if current is None:
            return None
        connection = self._require_connection()
        connection.execute(
            """
            UPDATE finance_entries
            SET kind = ?, description = ?, amount_cents = ?, category = ?, occurred_on = ?, due_on = ?, status = ?, recurring = ?
            WHERE id = ? AND principal_id = ?
            """,
            (
                kind if kind is not None else current.kind,
                description if description is not None else current.description,
                amount_cents if amount_cents is not None else current.amount_cents,
                category if category is not None else current.category,
                occurred_on if occurred_on is not None else current.occurred_on,
                due_on if due_on is not None else current.due_on,
                status if status is not None else current.status,
                int(recurring) if recurring is not None else int(current.recurring),
                entry_id,
                principal_id,
            ),
        )
        connection.commit()
        return self.get_finance_entry(principal_id, entry_id)

    def delete_finance_entry(self, principal_id: str, entry_id: int) -> bool:
        connection = self._require_connection()
        cursor = connection.execute(
            "DELETE FROM finance_entries WHERE id = ? AND principal_id = ?",
            (entry_id, principal_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def _materialize_recurring_expenses(self, principal_id: str, month: str) -> None:
        connection = self._require_connection()
        year, month_number = (int(part) for part in month.split("-", maxsplit=1))
        month_start = f"{month}-01"
        next_month = (
            f"{year + 1:04d}-01-01"
            if month_number == 12
            else f"{year:04d}-{month_number + 1:02d}-01"
        )
        roots = connection.execute(
            """
            SELECT id, description, amount_cents, category, occurred_on, due_on
            FROM finance_entries
            WHERE principal_id = ? AND kind = 'expense' AND recurring = 1
              AND recurring_parent_id IS NULL AND occurred_on < ?
            """,
            (principal_id, month_start),
        ).fetchall()
        for root in roots:
            root_id, description, amount_cents, category, occurred_on, due_on = root
            exists = connection.execute(
                """
                SELECT 1 FROM finance_entries
                WHERE principal_id = ? AND recurring_parent_id = ?
                  AND occurred_on >= ? AND occurred_on < ?
                """,
                (principal_id, root_id, month_start, next_month),
            ).fetchone()
            if exists:
                continue
            day = min(int(str(occurred_on)[8:10]), monthrange(year, month_number)[1])
            occurrence_date = f"{year:04d}-{month_number:02d}-{day:02d}"
            connection.execute(
                """
                INSERT INTO finance_entries (
                    principal_id, kind, description, amount_cents, category,
                    occurred_on, due_on, status, recurring, recurring_parent_id, created_at
                ) VALUES (?, 'expense', ?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                """,
                (
                    principal_id,
                    description,
                    amount_cents,
                    category,
                    occurrence_date,
                    due_on,
                    root_id,
                    _utc_now(),
                ),
            )
        connection.commit()

    def add_schedule(
        self,
        message: InboundMessage,
        kind: str,
        payload: str,
        next_run_at: str,
        recurrence: str | None = None,
    ) -> ScheduleRecord:
        connection = self._require_connection()
        created_at = _utc_now()
        cursor = connection.execute(
            """
            INSERT INTO schedules (
                principal_id, channel, conversation_id, kind, payload, next_run_at,
                recurrence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                message.principal_id,
                message.channel,
                message.conversation_id,
                kind,
                payload,
                next_run_at,
                recurrence,
                created_at,
            ),
        )
        connection.commit()
        return self.get_schedule(int(cursor.lastrowid))

    def get_schedule(self, schedule_id: int) -> ScheduleRecord | None:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT id, principal_id, channel, conversation_id, kind, payload, next_run_at,
                   recurrence, status, created_at, last_run_at
            FROM schedules WHERE id = ?
            """,
            (schedule_id,),
        ).fetchone()
        return _schedule_from_row(row) if row else None

    def list_schedules(self, principal_id: str, include_finished: bool = False) -> tuple[ScheduleRecord, ...]:
        connection = self._require_connection()
        statement = """
            SELECT id, principal_id, channel, conversation_id, kind, payload, next_run_at,
                   recurrence, status, created_at, last_run_at
            FROM schedules WHERE principal_id = ?
        """
        if not include_finished:
            statement += " AND status = 'active'"
        statement += " ORDER BY next_run_at ASC, id ASC"
        return tuple(_schedule_from_row(row) for row in connection.execute(statement, (principal_id,)).fetchall())

    def delete_schedule(self, principal_id: str, schedule_id: int) -> bool:
        connection = self._require_connection()
        cursor = connection.execute(
            "DELETE FROM schedules WHERE id = ? AND principal_id = ? AND status = 'active'",
            (schedule_id, principal_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def claim_due_schedules(self, now: datetime) -> tuple[ScheduleRecord, ...]:
        connection = self._require_connection()
        instant = now.astimezone(timezone.utc).isoformat()
        rows = connection.execute(
            """
            SELECT id, principal_id, channel, conversation_id, kind, payload, next_run_at,
                   recurrence, status, created_at, last_run_at
            FROM schedules WHERE status = 'active' AND next_run_at <= ?
            ORDER BY next_run_at ASC, id ASC
            """,
            (instant,),
        ).fetchall()
        records = tuple(_schedule_from_row(row) for row in rows)
        for record in records:
            if record.recurrence is None:
                connection.execute(
                    "UPDATE schedules SET status = 'completed', last_run_at = ? WHERE id = ?",
                    (instant, record.id),
                )
                continue
            next_run = _next_schedule_time(record, now)
            connection.execute(
                "UPDATE schedules SET next_run_at = ?, last_run_at = ? WHERE id = ?",
                (next_run, instant, record.id),
            )
        connection.commit()
        return records

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("AuditStore no está abierto")
        return self._connection

    def close(self) -> None:
        if self._connection is None:
            return
        self._connection.close()
        self._connection = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_from_row(row: tuple[object, ...]) -> TaskRecord:
    return TaskRecord(
        id=int(row[0]),
        principal_id=str(row[1]),
        text=str(row[2]),
        color=str(row[3]),
        importance=str(row[4]),
        category=str(row[5]),
        completed=bool(row[6]),
        created_at=str(row[7]),
        completed_at=str(row[8]) if row[8] is not None else None,
    )


def _finance_from_row(row: tuple[object, ...]) -> FinanceRecord:
    return FinanceRecord(
        id=int(row[0]),
        principal_id=str(row[1]),
        kind=str(row[2]),
        description=str(row[3]),
        amount_cents=int(row[4]),
        category=str(row[5]),
        occurred_on=str(row[6]),
        due_on=str(row[7]) if row[7] is not None else None,
        status=str(row[8]),
        recurring=bool(row[9]),
        recurring_parent_id=int(row[10]) if row[10] is not None else None,
        created_at=str(row[11]),
    )


def _schedule_from_row(row: tuple[object, ...]) -> ScheduleRecord:
    return ScheduleRecord(
        id=int(row[0]), principal_id=str(row[1]), channel=str(row[2]), conversation_id=str(row[3]),
        kind=str(row[4]), payload=str(row[5]), next_run_at=str(row[6]),
        recurrence=str(row[7]) if row[7] is not None else None, status=str(row[8]),
        created_at=str(row[9]), last_run_at=str(row[10]) if row[10] is not None else None,
    )


def _next_schedule_time(record: ScheduleRecord, now: datetime) -> str:
    previous = datetime.fromisoformat(record.next_run_at)
    if record.recurrence == "daily":
        seconds = 24 * 60 * 60
    elif record.recurrence and record.recurrence.startswith("interval:"):
        seconds = int(record.recurrence.partition(":")[2])
    else:
        raise ValueError(f"Recurrencia inválida: {record.recurrence}")
    next_run = previous
    while next_run <= now.astimezone(timezone.utc):
        next_run = datetime.fromtimestamp(next_run.timestamp() + seconds, timezone.utc)
    return next_run.isoformat()
