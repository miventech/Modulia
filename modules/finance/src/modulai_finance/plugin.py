from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from modulai.core.commands import CommandContext, CommandDefinition, CommandResult, ParsedCommand
from modulai.core.modules import ModuleContext

KINDS = {"gasto": "expense", "ingreso": "income", "factura": "invoice"}


class FinanceModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None
        self._currency = "PEN"
        self._share_telegram_with_local = True
        self._shared_principal_id = "local-user"

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        self._currency = str(context.config.get("currency", "PEN")).upper()
        self._share_telegram_with_local = bool(context.config.get("share_telegram_with_local", True))
        self._shared_principal_id = str(context.config.get("shared_principal_id", "local-user"))
        definitions = (
            CommandDefinition("gasto", "Registra un gasto personal o mensual.", self.add_expense),
            CommandDefinition("ingreso", "Registra un ingreso personal.", self.add_income),
            CommandDefinition("factura", "Registra una factura pendiente de pago.", self.add_invoice),
            CommandDefinition("finanzas", "Muestra el resumen financiero mensual.", self.monthly_summary),
            CommandDefinition("movimientos", "Lista movimientos de un mes.", self.list_entries),
            CommandDefinition("pagar_factura", "Marca una factura como pagada.", self.pay_invoice),
            CommandDefinition("pagar_gasto", "Marca un gasto como pagado.", self.pay_expense),
            CommandDefinition("marcar_gasto", "Marca un gasto como pagado o pendiente.", self.mark_expense),
            CommandDefinition("eliminar_movimiento", "Elimina un movimiento con confirmación.", self.delete_entry),
        )
        for definition in definitions:
            context.commands.register(definition)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def add_expense(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        return self._add(context, command, "gasto")

    def add_income(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        return self._add(context, command, "ingreso")

    def add_invoice(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        return self._add(context, command, "factura")

    def _add(self, context: CommandContext, command: ParsedCommand, label: str) -> CommandResult:
        parsed, error = parse_entry_args(command.args, label == "factura")
        if error:
            return CommandResult.failure(error)
        assert parsed is not None and self._context is not None
        amount_cents, description, category, occurred_on, due_on, recurring, paid = parsed
        if recurring and label != "gasto":
            return CommandResult.failure("Solo los gastos pueden configurarse como mensuales.")
        kind = KINDS[label]
        status = "pending" if kind == "invoice" or (kind == "expense" and not paid) else "paid"
        entry = self._context.store.add_finance_entry(
            self._principal(context),
            kind,
            description,
            amount_cents,
            category,
            occurred_on,
            due_on,
            status,
            recurring=recurring,
        )
        return CommandResult.success(
            f"{label.capitalize()} #{entry.id} registrada: {format_cents(amount_cents, self._currency)} · {category}."
        )

    def monthly_summary(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        month, error = parse_month(command.args, "Uso: /finanzas [AAAA-MM]")
        if error:
            return CommandResult.failure(error)
        assert month is not None and self._context is not None
        entries = self._context.store.list_finance_entries(self._principal(context), month)
        income = sum(item.amount_cents for item in entries if item.kind == "income")
        expenses = sum(
            item.amount_cents
            for item in entries
            if item.kind == "expense" and item.status == "paid"
        )
        pending_expenses = sum(
            item.amount_cents
            for item in entries
            if item.kind == "expense" and item.status == "pending"
        )
        paid_invoices = sum(
            item.amount_cents for item in entries if item.kind == "invoice" and item.status == "paid"
        )
        pending_invoices = sum(
            item.amount_cents for item in entries if item.kind == "invoice" and item.status == "pending"
        )
        balance = income - expenses - paid_invoices
        return CommandResult.success(
            "\n".join(
                (
                    f"Finanzas de {month}:",
                    f"Ingresos: {format_cents(income, self._currency)}",
                    f"Gastos: {format_cents(expenses, self._currency)}",
                    f"Gastos pendientes: {format_cents(pending_expenses, self._currency)}",
                    f"Facturas pagadas: {format_cents(paid_invoices, self._currency)}",
                    f"Facturas pendientes: {format_cents(pending_invoices, self._currency)}",
                    f"Balance: {format_cents(balance, self._currency)}",
                )
            )
        )

    def list_entries(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        month, error = parse_month(command.args, "Uso: /movimientos [AAAA-MM]")
        if error:
            return CommandResult.failure(error)
        assert month is not None and self._context is not None
        entries = self._context.store.list_finance_entries(self._principal(context), month)
        if not entries:
            return CommandResult.success(f"No hay movimientos registrados en {month}.")
        lines = [f"Movimientos de {month}:"]
        for item in entries:
            label = {"expense": "Gasto", "income": "Ingreso", "invoice": "Factura"}[item.kind]
            lines.append(
                f"#{item.id} {item.occurred_on} · {label} · {item.description} · "
                f"{format_cents(item.amount_cents, self._currency)} · {item.status}"
            )
        return CommandResult.success("\n".join(lines))

    def pay_invoice(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        entry_id, error = parse_id(command.args, "Uso: /pagar_factura <id>")
        if error:
            return CommandResult.failure(error)
        assert entry_id is not None and self._context is not None
        entry = self._context.store.get_finance_entry(self._principal(context), entry_id)
        if entry is None or entry.kind != "invoice":
            return CommandResult.failure(f"No existe una factura #{entry_id}.")
        if entry.status == "paid":
            return CommandResult.failure(f"La factura #{entry_id} ya estaba pagada.")
        self._context.store.update_finance_entry(self._principal(context), entry_id, status="paid")
        return CommandResult.success(f"Factura #{entry_id} marcada como pagada.")

    def pay_expense(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        entry_id, error = parse_id(command.args, "Uso: /pagar_gasto <id>")
        if error:
            return CommandResult.failure(error)
        assert entry_id is not None and self._context is not None
        entry = self._context.store.get_finance_entry(self._principal(context), entry_id)
        if entry is None or entry.kind != "expense":
            return CommandResult.failure(f"No existe un gasto #{entry_id}.")
        self._context.store.update_finance_entry(self._principal(context), entry_id, status="paid")
        return CommandResult.success(f"Gasto #{entry_id} marcado como pagado.")

    def mark_expense(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        if len(command.args) != 2 or command.args[1].lower() not in {"pagado", "pendiente"}:
            return CommandResult.failure("Uso: /marcar_gasto <id> pagado|pendiente")
        entry_id, error = parse_id(command.args[:1], "Uso: /marcar_gasto <id> pagado|pendiente")
        if error:
            return CommandResult.failure(error)
        assert entry_id is not None and self._context is not None
        entry = self._context.store.get_finance_entry(self._principal(context), entry_id)
        if entry is None or entry.kind != "expense":
            return CommandResult.failure(f"No existe un gasto #{entry_id}.")
        status = "paid" if command.args[1].lower() == "pagado" else "pending"
        self._context.store.update_finance_entry(self._principal(context), entry_id, status=status)
        return CommandResult.success(f"Gasto #{entry_id} marcado como {command.args[1].lower()}.")

    def delete_entry(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        if len(command.args) != 2 or command.args[1].lower() != "confirmar":
            return CommandResult.failure("Uso: /eliminar_movimiento <id> confirmar")
        entry_id, error = parse_id(command.args[:1], "Uso: /eliminar_movimiento <id> confirmar")
        if error:
            return CommandResult.failure(error)
        assert entry_id is not None and self._context is not None
        if not self._context.store.delete_finance_entry(self._principal(context), entry_id):
            return CommandResult.failure(f"No existe el movimiento #{entry_id}.")
        return CommandResult.success(f"Movimiento #{entry_id} eliminado.")

    def _principal(self, context: CommandContext) -> str:
        if self._share_telegram_with_local and context.message.channel == "telegram":
            return self._shared_principal_id
        return context.message.principal_id


def parse_entry_args(
    args: tuple[str, ...],
    allow_due_on: bool,
) -> tuple[tuple[int, str, str, str, str | None, bool, bool] | None, str | None]:
    usage = (
        "Uso: /gasto <monto> <descripción> [--categoria Nombre] [--fecha AAAA-MM-DD] "
        "[--mensual] [--pendiente]"
    )
    if allow_due_on:
        usage = (
            "Uso: /factura <monto> <descripción> [--categoria Nombre] "
            "[--fecha AAAA-MM-DD] [--vencimiento AAAA-MM-DD]"
        )
    if len(args) < 2:
        return None, usage
    amount_cents, error = parse_amount(args[0])
    if error:
        return None, error
    values = {
        "category": "General",
        "occurred_on": date.today().isoformat(),
        "due_on": None,
        "recurring": False,
        "paid": True,
    }
    words: list[str] = []
    index = 1
    flags = {"--categoria": "category", "--fecha": "occurred_on"}
    if allow_due_on:
        flags["--vencimiento"] = "due_on"
    while index < len(args):
        if args[index].lower() == "--mensual":
            values["recurring"] = True
            values["paid"] = False
            index += 1
            continue
        if args[index].lower() == "--pagado":
            values["paid"] = True
            index += 1
            continue
        if args[index].lower() == "--pendiente":
            values["paid"] = False
            index += 1
            continue
        field = flags.get(args[index].lower())
        if field is None:
            words.append(args[index])
            index += 1
            continue
        if index + 1 >= len(args):
            return None, usage
        values[field] = args[index + 1]
        index += 2
    description = " ".join(words).strip()
    if not description or len(description) > 500:
        return None, "La descripción debe tener entre 1 y 500 caracteres."
    category = " ".join(str(values["category"]).split())
    if not category or len(category) > 60:
        return None, "La categoría debe tener entre 1 y 60 caracteres."
    occurred_on, error = parse_date(str(values["occurred_on"]), "La fecha debe usar AAAA-MM-DD.")
    if error:
        return None, error
    due_on = values["due_on"]
    if due_on is not None:
        due_on, error = parse_date(str(due_on), "El vencimiento debe usar AAAA-MM-DD.")
        if error:
            return None, error
    assert amount_cents is not None and occurred_on is not None
    return (
        amount_cents,
        description,
        category,
        occurred_on,
        due_on,
        bool(values["recurring"]),
        bool(values["paid"]),
    ), None


def parse_amount(value: str) -> tuple[int | None, str | None]:
    try:
        amount = Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None, "El monto debe ser un número, por ejemplo 25.50."
    if amount <= 0 or amount.as_tuple().exponent < -2 or amount > Decimal("999999999.99"):
        return None, "El monto debe ser positivo y tener como máximo dos decimales."
    return int(amount * 100), None


def parse_date(value: str, message: str) -> tuple[str | None, str | None]:
    try:
        return date.fromisoformat(value).isoformat(), None
    except ValueError:
        return None, message


def parse_month(args: tuple[str, ...], usage: str) -> tuple[str | None, str | None]:
    if len(args) > 1:
        return None, usage
    value = args[0] if args else date.today().strftime("%Y-%m")
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError:
        return None, "El mes debe usar AAAA-MM."
    return value, None


def parse_id(args: tuple[str, ...], usage: str) -> tuple[int | None, str | None]:
    if len(args) != 1:
        return None, usage
    try:
        entry_id = int(args[0])
    except ValueError:
        return None, "El identificador debe ser un número positivo."
    if entry_id < 1:
        return None, "El identificador debe ser un número positivo."
    return entry_id, None


def format_cents(value: int, currency: str) -> str:
    sign = "-" if value < 0 else ""
    cents = abs(value)
    return f"{sign}{currency} {cents // 100}.{cents % 100:02d}"


def create_module() -> FinanceModule:
    return FinanceModule()
