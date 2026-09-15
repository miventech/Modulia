from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException

from modulai.bootstrap import Application
from modulai.core.messages import InboundMessage
from modulai.web import FinanceCreateRequest, FinanceUpdateRequest, create_web_app
from tests.test_application import make_config
from tests.test_web import endpoint_for


class FinanceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_store_monthly_entries_and_keep_users_private(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()

            expense = await application.execute_text(
                "/gasto 25.50 supermercado --categoria Alimentación --fecha 2026-09-10"
            )
            income = await application.execute_text(
                "/ingreso 1000 sueldo --categoria Trabajo --fecha 2026-09-01"
            )
            invoice = await application.execute_text(
                "/factura 80 internet --fecha 2026-09-11 --vencimiento 2026-09-20"
            )
            monthly = await application.execute_text(
                "/gasto 45 internet --mensual --fecha 2026-09-10"
            )
            summary = await application.execute_text("/finanzas 2026-09")
            entries = application.audit.list_finance_entries("local-user", "2026-09")
            next_month = application.audit.list_finance_entries("local-user", "2026-10")
            private_entries = application.audit.list_finance_entries("another-user", "2026-09")
            monthly_entry = next(item for item in next_month if item.recurring_parent_id is not None)
            paid_monthly = await application.execute_text(f"/pagar_gasto {monthly_entry.id}")
            paid = await application.execute_text(f"/pagar_factura {entries[0].id}")
            await application.stop()

            self.assertTrue(expense.ok)
            self.assertTrue(income.ok)
            self.assertTrue(invoice.ok)
            self.assertTrue(monthly.ok)
            self.assertIn("PEN 1000.00", summary.text)
            self.assertIn("PEN 25.50", summary.text)
            self.assertIn("Gastos pendientes: PEN 45.00", summary.text)
            self.assertEqual(len(entries), 4)
            self.assertEqual(monthly_entry.status, "pending")
            self.assertTrue(paid_monthly.ok)
            self.assertEqual(private_entries, ())
            self.assertTrue(paid.ok)

    async def test_telegram_and_local_use_shared_finance_identity(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            local = await application.execute_text(
                "/gasto 12.00 café --fecha 2026-09-12"
            )
            telegram_message = replace(
                InboundMessage.local("/finanzas 2026-09"),
                channel="telegram",
                conversation_id="telegram-chat",
                principal_id="987654",
            )
            telegram = await application.execute_message(telegram_message)
            await application.stop()

            self.assertTrue(local.ok)
            self.assertTrue(telegram.ok)
            self.assertIn("PEN 12.00", telegram.text)

    async def test_api_creates_filters_updates_and_deletes_entries(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            application = Application.create(project_root, make_config(project_root, directory))
            await application.start()
            web_app = create_web_app(application)
            create = endpoint_for(web_app, "/api/v1/finance", "POST")
            list_entries = endpoint_for(web_app, "/api/v1/finance", "GET")
            update = endpoint_for(web_app, "/api/v1/finance/{entry_id}", "PUT")
            delete = endpoint_for(web_app, "/api/v1/finance/{entry_id}", "DELETE")

            created = await create(
                FinanceCreateRequest(
                    kind="invoice",
                    description="Luz",
                    amount="42.30",
                    category="Servicios",
                    occurred_on="2026-09-03",
                    due_on="2026-09-15",
                )
            )
            entry_id = created["data"]["id"]
            listed = await list_entries(month="2026-09")
            updated = await update(entry_id, FinanceUpdateRequest(status="paid"))
            deleted = await delete(entry_id)
            with self.assertRaises(HTTPException) as raised:
                await list_entries(month="septiembre")
            await application.stop()

            self.assertEqual(listed["data"]["summary"]["pending_invoices"], "42.30")
            self.assertEqual(updated["data"]["status"], "paid")
            self.assertEqual(deleted["data"]["deleted"], entry_id)
            self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
