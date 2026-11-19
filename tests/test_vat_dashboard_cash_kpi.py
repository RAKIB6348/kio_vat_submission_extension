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
    # Frontend card definition (source-level regression guard)
    # ------------------------------------------------------------------
    def test_kpi_card_is_declared_in_frontend_assets(self):
        root = Path(__file__).resolve().parents[1] / "static" / "src"
        js = (root / "js" / "vat_payment_page.js").read_text()
        scss = (root / "scss" / "vat_payment_page.scss").read_text()
        self.assertIn('label: "Current Cash Balance"', js)
        self.assertIn('icon: "fa fa-wallet"', js)
        self.assertIn("data.current_cash_balance", js)
        self.assertIn("repeat(4, minmax(0, 1fr))", scss)
        self.assertIn("kio-vat-payment__kpi-card--cash", scss)
        self.assertIn("border-top: 3px solid var(--kio-vat-blue)", scss)
