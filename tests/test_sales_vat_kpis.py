# -*- coding: utf-8 -*-

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCashDepositSalesKpis(TransactionCase):
    """Total Sales Amount / Total VAT KPI cards for the Cash Deposit dashboard."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env.user.write({
            "groups_id": [
                Command.link(
                    cls.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_admin"
                    ).id
                ),
            ],
        })
        cls.income_account = cls._create_account(
            "VAT KPI Test Sales Account",
            "income",
        )
        cls.vat_account = cls._create_account(
            "VAT KPI Test VAT Output",
            "liability_current",
        )
        cls.receivable_account = cls._create_account(
            "VAT KPI Test Receivable",
            "asset_receivable",
        )
        cls.cash_account = cls._create_account(
            "VAT KPI Test Cash",
            "asset_cash",
        )
        cls.other_cash_account = cls._create_account(
            "VAT KPI Test Other Cash",
            "asset_cash",
        )
        cls.cash_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test Cash",
            "code": "VKPC",
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": cls.cash_account.id,
        })
        cls.other_cash_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test Other Cash",
            "code": "VKOPC",
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": cls.other_cash_account.id,
        })
        cls.pos_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test POS",
            "code": "VKPOS",
            "type": "general",
            "company_id": cls.company.id,
        })
        cls.sale_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test Sale",
            "code": "VKPS",
            "type": "sale",
            "company_id": cls.company.id,
        })
        cls.sales_tax = cls.env["account.tax"].create({
            "name": "VAT KPI Test VAT 10%",
            "amount_type": "percent",
            "amount": 10.0,
            "type_tax_use": "sale",
            "company_id": cls.company.id,
            "invoice_repartition_line_ids": [
                (0, 0, {"factor_percent": 100.0, "repartition_type": "base"}),
                (0, 0, {
                    "factor_percent": 100.0,
                    "repartition_type": "tax",
                    "account_id": cls.vat_account.id,
                }),
            ],
            "refund_repartition_line_ids": [
                (0, 0, {"factor_percent": 100.0, "repartition_type": "base"}),
                (0, 0, {
                    "factor_percent": 100.0,
                    "repartition_type": "tax",
                    "account_id": cls.vat_account.id,
                }),
            ],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "VAT KPI Test Customer",
        })
        cls.branch = cls.env["kio.cash.deposit.branch"].create({
            "name": "VAT KPI Test Branch",
            "company_id": cls.company.id,
            "branch_cash_account_id": cls.cash_journal.id,
        })
        cls.other_branch = cls.env["kio.cash.deposit.branch"].create({
            "name": "VAT KPI Test Other Branch",
            "company_id": cls.company.id,
            "branch_cash_account_id": cls.other_cash_journal.id,
        })
        cls.cash_payment_method = cls.env["pos.payment.method"].create({
            "name": "VAT KPI Test POS Cash",
            "journal_id": cls.cash_journal.id,
            "company_id": cls.company.id,
        })
        cls.other_cash_payment_method = cls.env["pos.payment.method"].create({
            "name": "VAT KPI Test Other POS Cash",
            "journal_id": cls.other_cash_journal.id,
            "company_id": cls.company.id,
        })
        cls.pos_config = cls.env["pos.config"].create({
            "name": "VAT KPI Test POS",
            "company_id": cls.company.id,
            "journal_id": cls.pos_journal.id,
            "invoice_journal_id": cls.sale_journal.id,
            "payment_method_ids": [
                Command.set(cls.cash_payment_method.ids),
            ],
        })
        cls.other_pos_config = cls.env["pos.config"].create({
            "name": "VAT KPI Test Other POS",
            "company_id": cls.company.id,
            "journal_id": cls.pos_journal.id,
            "invoice_journal_id": cls.sale_journal.id,
            "payment_method_ids": [
                Command.set(cls.other_cash_payment_method.ids),
            ],
        })

    @classmethod
    def _create_account(cls, name, account_type):
        return cls.env["account.account"].create({
            "name": name,
            "code": cls.env["account.account"]._search_new_account_code(
                cls.company,
                6,
                "99",
            ),
            "account_type": account_type,
            "company_id": cls.company.id,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dashboard_data(self, **kwargs):
        return self.env["kio.cash.deposit"].get_cash_deposit_dashboard_data(**kwargs)

    @staticmethod
    def _kpi_deltas(before, after):
        return (
            after["total_sales"] - before["total_sales"],
            after["total_vat"] - before["total_vat"],
        )

    def _create_invoice(self, amount, move_type="out_invoice", partner=None):
        invoice = self.env["account.move"].create({
            "move_type": move_type,
            "partner_id": (partner or self.partner).id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [
                Command.create({
                    "name": "VAT KPI Test Line",
                    "display_type": "product",
                    "account_id": self.income_account.id,
                    "quantity": 1.0,
                    "price_unit": amount,
                    "tax_ids": [Command.set(self.sales_tax.ids)],
                }),
            ],
        })
        invoice.action_post()
        return invoice

    def _create_pos_session(self, config):
        return self.env["pos.session"].create({
            "config_id": config.id,
            "user_id": self.env.user.id,
        })

    def _create_pos_session_move(self, sales, vat, journal=None):
        """Simulate the aggregate journal entry posted when a POS session is
        closed (move_type='entry' on the POS journal).

        Like the real POS closing, the move is created first and the lines are
        added with ``skip_invoice_sync`` so the tax lines are kept — the
        invoice-sync machinery would otherwise drop manually created tax
        lines.
        """
        tax_repartition_line = self.sales_tax.invoice_repartition_line_ids.filtered(
            lambda line: line.repartition_type == "tax"
        )
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": (journal or self.pos_journal).id,
            "date": fields.Date.today(),
            "ref": "VAT KPI Test POS Session",
        })
        move_lines = self.env["account.move.line"].with_context(
            skip_invoice_sync=True,
            check_move_validity=False,
        )
        move_lines.create([
            {
                "name": "VAT KPI Test POS Sale",
                "account_id": self.income_account.id,
                "move_id": move.id,
                "debit": 0.0,
                "credit": sales,
                "tax_ids": [(6, 0, self.sales_tax.ids)],
            },
            {
                "name": "VAT KPI Test POS VAT",
                "account_id": self.vat_account.id,
                "move_id": move.id,
                "debit": 0.0,
                "credit": vat,
                "tax_repartition_line_id": tax_repartition_line.id,
                "display_type": "tax",
            },
            {
                "name": "VAT KPI Test POS Receivable",
                "account_id": self.receivable_account.id,
                "move_id": move.id,
                "debit": sales + vat,
                "credit": 0.0,
            },
        ])
        move.action_post()
        return move

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_dashboard_data_includes_kpi_keys(self):
        data = self._dashboard_data()
        self.assertIn("total_sales", data)
        self.assertIn("total_vat", data)
        self.assertIsInstance(data["total_sales"], (int, float))
        self.assertIsInstance(data["total_vat"], (int, float))

    def test_current_balance_key_is_unchanged(self):
        data = self._dashboard_data()
        self.assertIn("current_balance", data)

    def test_posted_invoice_is_counted_with_vat(self):
        before = self._dashboard_data()
        self._create_invoice(1000.0)
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 1000.0, places=2)
        self.assertAlmostEqual(delta_vat, 100.0, places=2)

    def test_posted_refund_reduces_kpis(self):
        self._create_invoice(1000.0)
        before = self._dashboard_data()
        self._create_invoice(250.0, move_type="out_refund")
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, -250.0, places=2)
        self.assertAlmostEqual(delta_vat, -25.0, places=2)

    def test_unposted_invoice_is_not_counted(self):
        before = self._dashboard_data()
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [
                Command.create({
                    "name": "VAT KPI Test Draft Line",
                    "display_type": "product",
                    "account_id": self.income_account.id,
                    "quantity": 1.0,
                    "price_unit": 500.0,
                    "tax_ids": [Command.set(self.sales_tax.ids)],
                }),
            ],
        })
        self.assertEqual(invoice.state, "draft")
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_pos_session_move_is_counted(self):
        session = self._create_pos_session(self.pos_config)
        before = self._dashboard_data()
        move = self._create_pos_session_move(sales=900.0, vat=90.0)
        session.move_id = move.id
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 900.0, places=2)
        self.assertAlmostEqual(delta_vat, 90.0, places=2)

    def test_invoiced_pos_order_is_counted_exactly_once(self):
        before = self._dashboard_data()
        session = self._create_pos_session(self.pos_config)
        # Session entry holds the non-invoiced POS orders (500 + 50 VAT).
        session_move = self._create_pos_session_move(sales=500.0, vat=50.0)
        session.move_id = session_move.id
        # The invoiced POS order is counted through its invoice only.
        invoice = self._create_invoice(1000.0)
        order = self.env["pos.order"].create({
            "session_id": session.id,
            "amount_tax": 100.0,
            "amount_total": 1100.0,
            "amount_paid": 1100.0,
            "amount_return": 0.0,
            "state": "invoiced",
        })
        order.account_move = invoice.id

        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        # 500 (session entry) + 1000 (invoice) — the invoiced POS order must
        # not be counted through the session entry a second time.
        self.assertAlmostEqual(delta_sales, 1500.0, places=2)
        self.assertAlmostEqual(delta_vat, 150.0, places=2)

    def test_branch_filter_scopes_pos_and_pos_invoiced_records(self):
        session = self._create_pos_session(self.pos_config)
        move = self._create_pos_session_move(sales=400.0, vat=40.0)
        session.move_id = move.id

        all_data = self._dashboard_data()
        branch_data = self._dashboard_data(branch_ids=[self.branch.id])
        other_branch_data = self._dashboard_data(branch_ids=[self.other_branch.id])

        # Branch-attributed POS move is included for its own branch.
        self.assertAlmostEqual(branch_data["total_sales"], 400.0, places=2)
        self.assertAlmostEqual(branch_data["total_vat"], 40.0, places=2)
        # The other branch (different cash journal) sees nothing of it.
        self.assertAlmostEqual(other_branch_data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_vat"], 0.0, places=2)
        # Company-wide admin view includes it.
        self.assertGreaterEqual(all_data["total_sales"], 400.0)

    def test_regular_invoice_not_counted_with_active_branch_filter(self):
        self._create_invoice(888.0)
        branch_data = self._dashboard_data(branch_ids=[self.branch.id])
        all_data = self._dashboard_data()
        # Branch-filtered: regular invoice (no branch) is excluded.
        self.assertAlmostEqual(branch_data["total_sales"], 0.0, places=2)
        # Company-wide: it is included.
        self.assertGreaterEqual(all_data["total_sales"], 888.0)

    def test_pos_invoiced_order_is_counted_with_branch_filter(self):
        session = self._create_pos_session(self.pos_config)
        invoice = self._create_invoice(700.0)
        order = self.env["pos.order"].create({
            "session_id": session.id,
            "amount_tax": 70.0,
            "amount_total": 770.0,
            "amount_paid": 770.0,
            "amount_return": 0.0,
            "state": "invoiced",
        })
        order.account_move = invoice.id

        branch_data = self._dashboard_data(branch_ids=[self.branch.id])
        other_branch_data = self._dashboard_data(branch_ids=[self.other_branch.id])

        # The POS-invoiced invoice is attributed to the session's branch.
        self.assertAlmostEqual(branch_data["total_sales"], 700.0, places=2)
        self.assertAlmostEqual(branch_data["total_vat"], 70.0, places=2)
        # ... and not to the other branch.
        self.assertAlmostEqual(other_branch_data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_vat"], 0.0, places=2)

    def test_manager_with_branch_sees_branch_scoped_kpis(self):
        manager = self.env["res.users"].create({
            "name": "VAT KPI Manager",
            "login": "vat_kpi_manager",
            "groups_id": [
                Command.set([
                    self.env.ref("base.group_user").id,
                    self.env.ref("kio_cash_deposit.group_kio_cash_deposit_user").id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(self.branch.ids)],
            "company_ids": [Command.set(self.company.ids)],
        })
        session = self._create_pos_session(self.pos_config)
        move = self._create_pos_session_move(sales=250.0, vat=25.0)
        session.move_id = move.id

        manager_data = self._dashboard_data_for_user(manager)
        self.assertAlmostEqual(manager_data["total_sales"], 250.0, places=2)
        self.assertAlmostEqual(manager_data["total_vat"], 25.0, places=2)

    def test_plain_user_is_denied(self):
        plain_user = self.env["res.users"].create({
            "name": "VAT KPI Plain User",
            "login": "vat_kpi_plain_user",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self._dashboard_data_for_user(plain_user)

    def _dashboard_data_for_user(self, user):
        return self.env(user=user)["kio.cash.deposit"].get_cash_deposit_dashboard_data()
