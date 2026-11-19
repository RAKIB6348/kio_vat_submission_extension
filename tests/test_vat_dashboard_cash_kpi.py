# -*- coding: utf-8 -*-
"""Phase 4 — Current Cash Balance KPI on the VAT Payment Dashboard.

Uses the real GL-derived Cash Deposit calculation; all records are created in
the test transaction of a non-production database.
"""

from pathlib import Path

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestVatDashboardCashKpi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env.user.write({
            "groups_id": [
                Command.link(cls.env.ref("base.group_user").id),
                Command.link(cls.env.ref("account.group_account_invoice").id),
                Command.link(cls.env.ref(
                    "kio_cash_deposit.group_kio_cash_deposit_admin"
                ).id),
            ],
        })
        cls.payable_account = cls.env["account.account"].search([
            ("account_type", "=", "liability_payable"),
            ("company_id", "=", cls.company.id),
            ("deprecated", "=", False),
        ], limit=1)
        cls.vat_account = cls.env["account.payment"]._kio_vat_payable_account(
            cls.company, required=False
        )
        if not cls.vat_account:
            cls.vat_account = cls.env["account.account"].create({
                "name": "VAT Payable",
                "code": "200904",
                "account_type": "liability_non_current",
                "reconcile": True,
                "company_id": cls.company.id,
            })
        cls.general_journal = cls.env["account.journal"].create({
            "name": "VAT Cash KPI Misc",
            "code": "VCKM",
            "type": "general",
            "company_id": cls.company.id,
        })
        cls.journal_a, _, cls.branch_a = cls._make_branch("VAT Cash KPI A", "VCKA")
        cls.journal_b, _, cls.branch_b = cls._make_branch("VAT Cash KPI B", "VCKB")
        cls.bank_journal = cls.env["account.journal"].create({
            "name": "VAT Cash KPI Bank",
            "code": "VCKBNK",
            "type": "bank",
            "company_id": cls.company.id,
            "default_account_id": cls.env["account.account"].create({
                "name": "VAT Cash KPI Bank Account",
                "code": cls.env["account.account"]._search_new_account_code(
                    cls.company, 6, "95"
                ),
                "account_type": "asset_cash",
                "company_id": cls.company.id,
            }).id,
        })
        cls._fund_cash(cls.journal_a, 4000.0)
        cls._fund_cash(cls.journal_b, 2500.0)
        cls._fund_vat(cls.branch_a, 500.0)
        cls._base_all_cash = cls.env[
            "kio.cash.deposit"
        ].get_vat_payment_dashboard_data()["current_cash_balance"]

    @classmethod
    def _make_branch(cls, name, code):
        account = cls.env["account.account"].create({
            "name": name + " Cash",
            "code": cls.env["account.account"]._search_new_account_code(
                cls.company, 6, "94"
            ),
            "account_type": "asset_cash",
            "company_id": cls.company.id,
        })
        journal = cls.env["account.journal"].create({
            "name": name + " Cash",
            "code": code,
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": account.id,
        })
        manual = journal.outbound_payment_method_line_ids.filtered(
            lambda line: line.code == "manual"
        )[:1]
        manual.payment_account_id = account
        branch = cls.env["kio.cash.deposit.branch"].create({
            "name": name,
            "company_id": cls.company.id,
            "branch_cash_account_id": journal.id,
        })
        return journal, account, branch

    @classmethod
    def _post_entry(cls, lines, ref):
        move = cls.env["account.move"].create({
            "move_type": "entry",
            "journal_id": cls.general_journal.id,
            "date": fields.Date.today(),
            "ref": ref,
            "line_ids": [Command.create(line) for line in lines],
        })
        move.action_post()
        return move

    @classmethod
    def _fund_cash(cls, journal, amount):
        cls._post_entry([
            {"account_id": journal.default_account_id.id, "debit": amount,
             "name": "Cash funding"},
            {"account_id": cls.payable_account.id, "credit": amount,
             "name": "Cash funding"},
        ], "Cash KPI funding " + journal.code)

    @classmethod
    def _fund_vat(cls, branch, amount):
        move = cls._post_entry([
            {"account_id": cls.payable_account.id, "debit": amount,
             "name": "VAT payable opening"},
            {"account_id": cls.vat_account.id, "credit": amount,
             "name": "VAT payable opening"},
        ], "VAT KPI funding " + branch.name)
        method = cls.env["pos.payment.method"].create({
            "name": "VAT Cash KPI " + branch.name,
            "journal_id": branch.branch_cash_account_id.id,
            "company_id": cls.company.id,
        })
        config = cls.env["pos.config"].create({
            "name": "VAT Cash KPI Config " + branch.name,
            "company_id": cls.company.id,
            "payment_method_ids": [Command.set(method.ids)],
        })
        cls.env["pos.session"].create({
            "config_id": config.id,
            "user_id": cls.env.user.id,
            "move_id": move.id,
            "state": "closed",
        })

    def _dashboard(self, **kwargs):
        return self.env["kio.cash.deposit"].get_vat_payment_dashboard_data(**kwargs)

    def _wizard(self, branch, amount):
        model = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True
        )
        wizard = model.new({
            "company_id": self.company.id,
            "payment_type": "outbound",
            "kio_vat_branch_id": branch.id,
            "amount": amount,
        })
        wizard._compute_from_lines()
        wizard._compute_available_journal_ids()
        wizard._compute_journal_id()
        wizard._compute_payment_method_line_id()
        wizard._compute_communication()
        return wizard

    # ------------------------------------------------------------------
    # Backend KPI value
    # ------------------------------------------------------------------
    def test_dashboard_exposes_current_cash_balance(self):
        data = self._dashboard()
        self.assertIn("current_cash_balance", data)
        self.assertAlmostEqual(
            data["current_cash_balance"], self._base_all_cash, places=2
        )

    def test_branch_filter_scopes_cash_balance(self):
        self.assertAlmostEqual(
            self._dashboard(branch_id=self.branch_a.id)["current_cash_balance"],
            4000.0, places=2,
        )
        self.assertAlmostEqual(
            self._dashboard(branch_id=self.branch_b.id)["current_cash_balance"],
            2500.0, places=2,
        )

    def test_cash_balance_is_independent_of_vat_date_filters(self):
        future = fields.Date.today().replace(year=fields.Date.today().year + 2)
        data = self._dashboard(date_from=future, date_to=future, search="no-match")
        self.assertAlmostEqual(
            data["current_cash_balance"], self._base_all_cash, places=2
        )
        # VAT figures remain filtered while cash does not.
        self.assertAlmostEqual(data["outstanding"], 0.0, places=2)

    def test_manager_sees_only_assigned_branches(self):
        manager = self.env["res.users"].create({
            "name": "VAT Cash KPI Manager",
            "login": "vat_cash_kpi_manager",
            "groups_id": [
                Command.set([
                    self.env.ref("base.group_user").id,
                    self.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_user"
                    ).id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(self.branch_a.ids)],
            "company_ids": [Command.set(self.company.ids)],
        })
        data = self.env(user=manager)["kio.cash.deposit"].get_vat_payment_dashboard_data()
        self.assertAlmostEqual(data["current_cash_balance"], 4000.0, places=2)

    def test_posted_vat_payment_reduces_cash_exactly_once(self):
        before = self._dashboard()["current_cash_balance"]
        self._wizard(self.branch_a, 100.0).action_create_payments()
        after = self._dashboard()["current_cash_balance"]
        self.assertAlmostEqual(after, before - 100.0, places=2)
        # The KPI equals the underlying GL balance of the branch cash accounts.
        authorized_journals = self.env[
            "kio.cash.deposit"
        ]._get_authorized_vat_branches().mapped("branch_cash_account_id")
        gl = self.env["kio.cash.deposit"]._get_cash_journal_balance(
            authorized_journals, company=self.company
        )
        self.assertAlmostEqual(after, gl, places=2)

    def test_bank_transaction_does_not_reduce_cash_balance(self):
        before = self._dashboard()["current_cash_balance"]
        self._post_entry([
            {"account_id": self.payable_account.id, "debit": 900.0,
             "name": "Bank transfer"},
            {"account_id": self.bank_journal.default_account_id.id,
             "credit": 900.0, "name": "Bank transfer"},
        ], "Bank-only movement")
        self.assertAlmostEqual(
            self._dashboard()["current_cash_balance"], before, places=2
        )

    # ------------------------------------------------------------------
    # Payment History tab data source
    # ------------------------------------------------------------------
    def test_history_returns_only_posted_vat_payments(self):
        self._wizard(self.branch_a, 100.0).action_create_payments()
        self.env["account.payment"].create({
            "payment_type": "outbound",
            "partner_type": "supplier",
            "amount": 10.0,
            "journal_id": self.journal_a.id,
            "company_id": self.company.id,
            "date": fields.Date.today(),
        })
        data = self.env["kio.cash.deposit"].get_vat_payment_history_data(
            branch_id=self.branch_a.id
        )
        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]
        self.assertEqual(row["branch"], "VAT Cash KPI A")
        self.assertEqual(row["journal"], "VCKA")
        self.assertEqual(row["status"], "Posted")
        self.assertTrue(row["reference"])
        symbol = self.company.currency_id.symbol or "BDT"
        self.assertEqual(row["amount"], f"{symbol} 100.00")
        self.assertEqual(row["challan"], "")
        # Journal Entry comes from the payment's existing linked account.move.
        payment = self.env["account.payment"].search([
            ("is_kio_vat_payment", "=", True),
            ("kio_vat_branch_id", "=", self.branch_a.id),
        ], limit=1)
        self.assertTrue(payment.move_id)
        self.assertEqual(row["move_id"], payment.move_id.id)
        self.assertEqual(row["move_name"], payment.move_id.name)

    def test_history_branch_filter_and_manager_scope(self):
        self._fund_vat(self.branch_b, 200.0)
        self._wizard(self.branch_a, 100.0).action_create_payments()
        self._wizard(self.branch_b, 50.0).action_create_payments()
        D = self.env["kio.cash.deposit"]
        rows_a = D.get_vat_payment_history_data(branch_id=self.branch_a.id)["rows"]
        rows_b = D.get_vat_payment_history_data(branch_id=self.branch_b.id)["rows"]
        self.assertEqual([r["branch"] for r in rows_a], ["VAT Cash KPI A"])
        self.assertEqual([r["branch"] for r in rows_b], ["VAT Cash KPI B"])

        manager = self.env["res.users"].create({
            "name": "VAT History Manager",
            "login": "vat_history_manager",
            "groups_id": [
                Command.set([
                    self.env.ref("base.group_user").id,
                    self.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_user"
                    ).id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(self.branch_a.ids)],
            "company_ids": [Command.set(self.company.ids)],
        })
        manager_rows = self.env(user=manager)[
            "kio.cash.deposit"
        ].get_vat_payment_history_data()["rows"]
        self.assertEqual([r["branch"] for r in manager_rows], ["VAT Cash KPI A"])

    # ------------------------------------------------------------------
    # Frontend card definition (source-level regression guard)
    # ------------------------------------------------------------------
    def test_kpi_card_is_declared_in_frontend_assets(self):
        root = Path(__file__).resolve().parents[1] / "static" / "src"
        js = (root / "js" / "vat_payment_page.js").read_text()
        xml = (root / "xml" / "vat_payment_page.xml").read_text()
        scss = (root / "scss" / "vat_payment_page.scss").read_text()
        self.assertIn('label: "Current Cash Balance"', js)
        self.assertIn('icon: "fa fa-money"', js)
        self.assertIn("data.current_cash_balance", js)
        self.assertIn("get_vat_payment_history_data", js)
        self.assertIn("return this.state.paymentHistoryRows;", js)
        self.assertIn("openJournalEntry", js)
        self.assertIn('["name", "=", "view_move_form"]', js)
        self.assertNotIn("<th>Challan Number</th>", xml)
        self.assertNotIn("<th>Payment Journal</th>", xml)
        self.assertNotIn('<td><t t-esc="row.journal"/></td>', xml)
        self.assertIn("<th>Journal Entry</th>", xml)
        self.assertIn("this.openJournalEntry(row.moveId)", xml)
        self.assertIn('<span t-if="!row.moveId">—</span>', xml)
        self.assertIn("repeat(4, minmax(0, 1fr))", scss)
        self.assertIn("kio-vat-payment__kpi-card--cash", scss)
        self.assertIn("border-top: 3px solid var(--kio-vat-blue)", scss)

        # Table template column sequence and bindings
        self.assertIn('<th class="is-money">Current Cash Balance</th>', xml)
        self.assertIn('<td class="is-money"><t t-esc="row.current_cash_balance"/></td>', xml)
        self.assertIn('<td class="is-money"><t t-esc="summaryTotals.cash"/></td>', xml)

        # Verify sequence: Branch -> Total VAT Payable -> Paid VAT -> Outstanding VAT -> Current Cash Balance -> Status
        idx_branch = xml.find("<th>Branch</th>")
        idx_payable = xml.find('<th class="is-money">Total VAT Payable</th>')
        idx_paid = xml.find('<th class="is-money">Paid VAT</th>')
        idx_outstanding = xml.find('<th class="is-money">Outstanding VAT</th>')
        idx_cash = xml.find('<th class="is-money">Current Cash Balance</th>')
        idx_status = xml.find("<th>Status</th>")
        self.assertTrue(
            -1 < idx_branch < idx_payable < idx_paid < idx_outstanding < idx_cash < idx_status,
            "Column sequence must be Branch, Total VAT Payable, Paid VAT, Outstanding VAT, Current Cash Balance, Status",
        )

    # ------------------------------------------------------------------
    # Branch-wise Summary Table: Current Cash Balance
    # ------------------------------------------------------------------
    def test_branch_summary_rows_contain_actual_cash_balance(self):
        data = self._dashboard()
        rows_by_id = {r["id"]: r for r in data["rows"] if r.get("id")}
        self.assertIn(self.branch_a.id, rows_by_id)
        self.assertAlmostEqual(
            rows_by_id[self.branch_a.id]["current_cash_balance"], 4000.0, places=2
        )
        self.assertIn(self.branch_b.id, rows_by_id)
        self.assertAlmostEqual(
            rows_by_id[self.branch_b.id]["current_cash_balance"], 2500.0, places=2
        )

    def test_shared_cash_journal_does_not_duplicate_total_cash(self):
        """When branches share a cash journal, each branch row reflects the
        journal's actual balance, but the total row and KPI deduplicate to match
        the underlying distinct GL accounts."""
        before_total = self._dashboard()["current_cash_balance"]
        branch_c = self.env["kio.cash.deposit.branch"].create({
            "name": "VAT Cash KPI C (Shared with A)",
            "company_id": self.company.id,
            "branch_cash_account_id": self.journal_a.id,
        })
        data = self._dashboard()
        rows_by_id = {r["id"]: r for r in data["rows"] if r.get("id")}
        self.assertAlmostEqual(
            rows_by_id[self.branch_a.id]["current_cash_balance"], 4000.0, places=2
        )
        self.assertAlmostEqual(
            rows_by_id[branch_c.id]["current_cash_balance"], 4000.0, places=2
        )
        # Total cash must NOT duplicate journal_a (total remains exactly before_total, not before + 4000)
        self.assertAlmostEqual(
            data["current_cash_balance"], before_total, places=2
        )

    def test_unallocated_vat_row_has_no_cash_balance(self):
        """Unallocated VAT has no branch cash account; its cash balance must be None."""
        # Create unallocated VAT entry (no POS session/branch)
        self._post_entry([
            {"account_id": self.payable_account.id, "debit": 300.0, "name": "Direct"},
            {"account_id": self.vat_account.id, "credit": 300.0, "name": "Direct"},
        ], "Unallocated VAT entry")
        data = self._dashboard()
        unallocated_rows = [r for r in data["rows"] if r.get("is_unallocated")]
        self.assertTrue(unallocated_rows, "Unallocated VAT row should be present")
        self.assertIsNone(
            unallocated_rows[0]["current_cash_balance"],
            "Unallocated VAT must have no attributable cash balance (displays dash)",
        )

    def test_branch_without_cash_journal_displays_zero(self):
        """A branch with no cash journal configured reports 0.0 cash balance."""
        branch_empty = self.env["kio.cash.deposit.branch"].create({
            "name": "VAT Cash KPI No Cash",
            "company_id": self.company.id,
            "branch_cash_account_id": False,
        })
        data = self._dashboard()
        rows_by_id = {r["id"]: r for r in data["rows"] if r.get("id")}
        self.assertIn(branch_empty.id, rows_by_id)
        self.assertEqual(rows_by_id[branch_empty.id]["current_cash_balance"], 0.0)

    def test_negative_cash_balance_displayed_accurately(self):
        """Negative balances on cash journals are preserved accurately."""
        # Overdraw journal_b by crediting 3500 (balance was 2500 -> net -1000)
        self._post_entry([
            {"account_id": self.payable_account.id, "debit": 3500.0, "name": "Overdraw"},
            {"account_id": self.journal_b.default_account_id.id, "credit": 3500.0, "name": "Overdraw"},
        ], "Overdraw movement")
        data = self._dashboard()
        rows_by_id = {r["id"]: r for r in data["rows"] if r.get("id")}
        self.assertAlmostEqual(
            rows_by_id[self.branch_b.id]["current_cash_balance"], -1000.0, places=2
        )

    def test_branch_filter_updates_table_rows_and_total(self):
        """Filtering by branch updates rows and total to that branch only."""
        data_a = self._dashboard(branch_id=self.branch_a.id)
        self.assertEqual(len(data_a["rows"]), 1)
        self.assertEqual(data_a["rows"][0]["id"], self.branch_a.id)
        self.assertAlmostEqual(
            data_a["rows"][0]["current_cash_balance"], 4000.0, places=2
        )
        self.assertAlmostEqual(data_a["current_cash_balance"], 4000.0, places=2)

    def test_manager_sees_only_assigned_branch_in_summary_rows(self):
        """A manager user only sees their assigned branch in the summary rows."""
        manager = self.env["res.users"].create({
            "name": "VAT Cash Row Manager",
            "login": "vat_cash_row_manager",
            "groups_id": [
                Command.set([
                    self.env.ref("base.group_user").id,
                    self.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_user"
                    ).id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(self.branch_b.ids)],
            "company_ids": [Command.set(self.company.ids)],
        })
        data = self.env(user=manager)["kio.cash.deposit"].get_vat_payment_dashboard_data()
        branch_rows = [r for r in data["rows"] if not r.get("is_unallocated")]
        self.assertEqual(len(branch_rows), 1)
        self.assertEqual(branch_rows[0]["id"], self.branch_b.id)
        self.assertAlmostEqual(
            branch_rows[0]["current_cash_balance"], 2500.0, places=2
        )
        self.assertAlmostEqual(data["current_cash_balance"], 2500.0, places=2)
