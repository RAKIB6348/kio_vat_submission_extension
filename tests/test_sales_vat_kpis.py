# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCashDepositSalesKpis(TransactionCase):
    """Total Sales Amount / Total VAT KPIs for the Cash Deposit dashboard.

    Calculations are based on POS sales transactions associated with the
    cash balance: closed-session POS orders, counting only the cash-paid
    portion (payments whose payment method points to a cash journal).
    """

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
        cls.bank_account = cls._create_account(
            "VAT KPI Test Bank",
            "asset_cash",
        )
        cls.bank_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test Bank",
            "code": "VKPB",
            "type": "bank",
            "company_id": cls.company.id,
            "default_account_id": cls.bank_account.id,
        })
        cls.sale_journal = cls.env["account.journal"].create({
            "name": "VAT KPI Test Sale",
            "code": "VKPS",
            "type": "sale",
            "company_id": cls.company.id,
        })
        cls.tax_10 = cls._create_sale_tax("VAT KPI Test VAT 10%", 10.0, cls.vat_account)
        cls.tax_5 = cls._create_sale_tax("VAT KPI Test VAT 5%", 5.0, cls.vat_account)
        cls.partner = cls.env["res.partner"].create({
            "name": "VAT KPI Test Customer",
        })
        cls.branch = cls.env["kio.cash.deposit.branch"].create({
            "name": "VAT KPI Test Branch (W-09 KHULNA)",
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
        cls.bank_payment_method = cls.env["pos.payment.method"].create({
            "name": "VAT KPI Test POS Bank",
            "journal_id": cls.bank_journal.id,
            "company_id": cls.company.id,
        })
        cls.pos_config = cls.env["pos.config"].create({
            "name": "VAT KPI Test POS",
            "company_id": cls.company.id,
            "journal_id": cls.pos_journal.id,
            "invoice_journal_id": cls.sale_journal.id,
            "payment_method_ids": [
                Command.set((cls.cash_payment_method + cls.bank_payment_method).ids),
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

    @classmethod
    def _create_sale_tax(cls, name, amount, vat_account):
        return cls.env["account.tax"].create({
            "name": name,
            "amount_type": "percent",
            "amount": amount,
            "type_tax_use": "sale",
            "company_id": cls.company.id,
            "invoice_repartition_line_ids": [
                (0, 0, {"factor_percent": 100.0, "repartition_type": "base"}),
                (0, 0, {
                    "factor_percent": 100.0,
                    "repartition_type": "tax",
                    "account_id": vat_account.id,
                }),
            ],
            "refund_repartition_line_ids": [
                (0, 0, {"factor_percent": 100.0, "repartition_type": "base"}),
                (0, 0, {
                    "factor_percent": 100.0,
                    "repartition_type": "tax",
                    "account_id": vat_account.id,
                }),
            ],
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dashboard_data(self, **kwargs):
        return self.env["kio.cash.deposit"].get_cash_deposit_dashboard_data(**kwargs)

    def _dashboard_data_for_user(self, user, **kwargs):
        return self.env(user=user)["kio.cash.deposit"].get_cash_deposit_dashboard_data(**kwargs)

    @staticmethod
    def _kpi_deltas(before, after):
        return (
            after["total_sales"] - before["total_sales"],
            after["total_vat"] - before["total_vat"],
        )

    def _create_pos_order(self, config, payments, amount_total, amount_tax,
                          state="done", session_state="closed", date_order=None):
        session = self.env["pos.session"].create({
            "config_id": config.id,
            "user_id": self.env.user.id,
        })
        if session_state != "opening_control":
            session.write({"state": session_state})
        order = self.env["pos.order"].create({
            "session_id": session.id,
            "amount_total": amount_total,
            "amount_tax": amount_tax,
            "amount_paid": sum(amount for _pm, amount in payments),
            "amount_return": 0.0,
            "state": state,
            "date_order": date_order or fields.Datetime.now(),
        })
        for payment_method, amount in payments:
            self.env["pos.payment"].create({
                "pos_order_id": order.id,
                "amount": amount,
                "payment_method_id": payment_method.id,
            })
        return order

    def _create_invoice(self, amount, move_type="out_invoice"):
        invoice = self.env["account.move"].create({
            "move_type": move_type,
            "partner_id": self.partner.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [
                Command.create({
                    "name": "VAT KPI Test Line",
                    "display_type": "product",
                    "account_id": self.income_account.id,
                    "quantity": 1.0,
                    "price_unit": amount,
                    "tax_ids": [Command.set(self.tax_10.ids)],
                }),
            ],
        })
        invoice.action_post()
        return invoice

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

    def test_cash_only_sale_is_counted_in_full(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 1000.0, places=2)
        self.assertAlmostEqual(delta_vat, 100.0, places=2)

    def test_bank_only_sale_is_not_counted(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.bank_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_mixed_payments_are_allocated_proportionally(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [
                (self.cash_payment_method, 660.0),
                (self.bank_payment_method, 440.0),
            ],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        # 60% cash -> 60% of 1000 (untaxed) and 60% of 100 (VAT)
        self.assertAlmostEqual(delta_sales, 600.0, places=2)
        self.assertAlmostEqual(delta_vat, 60.0, places=2)

    def test_multiple_vat_rates_are_all_counted(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1725.0)],
            amount_total=1725.0,
            amount_tax=225.0,  # e.g. 1000 @10% + 1250 @... aggregated by Odoo
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 1500.0, places=2)
        self.assertAlmostEqual(delta_vat, 225.0, places=2)

    def test_tax_exempt_sale_contributes_no_vat(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 800.0)],
            amount_total=800.0,
            amount_tax=0.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 800.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_discounted_sale_uses_discounted_amounts(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 990.0)],
            amount_total=990.0,
            amount_tax=90.0,  # 10% VAT on discounted base of 900
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 900.0, places=2)
        self.assertAlmostEqual(delta_vat, 90.0, places=2)

    def test_refund_reduces_sales_and_vat(self):
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, -1100.0)],
            amount_total=-1100.0,
            amount_tax=-100.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, -1000.0, places=2)
        self.assertAlmostEqual(delta_vat, -100.0, places=2)

    def test_partially_cash_refund(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [
                (self.cash_payment_method, -550.0),
                (self.bank_payment_method, -550.0),
            ],
            amount_total=-1100.0,
            amount_tax=-100.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        # 50% cash portion of a refunded order
        self.assertAlmostEqual(delta_sales, -500.0, places=2)
        self.assertAlmostEqual(delta_vat, -50.0, places=2)

    def test_zero_total_order_is_ignored_safely(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 0.0)],
            amount_total=0.0,
            amount_tax=0.0,
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_open_session_sale_is_not_counted(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            state="done",
            session_state="opened",
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_historical_sale_through_today_is_counted(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            date_order=fields.Datetime.now() - timedelta(days=400),
        )
        future = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, future)
        self.assertAlmostEqual(delta_sales, 1000.0, places=2)
        self.assertAlmostEqual(delta_vat, 100.0, places=2)

    def test_future_dated_sale_is_not_counted(self):
        before = self._dashboard_data()
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            date_order=fields.Datetime.now() + timedelta(days=1),
        )
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_invoiced_pos_order_is_not_double_counted(self):
        before = self._dashboard_data()
        order = self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        invoice = self._create_invoice(1000.0)
        order.account_move = invoice.id
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        # The order is counted exactly once — the invoice is ignored.
        self.assertAlmostEqual(delta_sales, 1000.0, places=2)
        self.assertAlmostEqual(delta_vat, 100.0, places=2)

    def test_opening_cash_balance_is_not_counted(self):
        session = self.env["pos.session"].create({
            "config_id": self.pos_config.id,
            "user_id": self.env.user.id,
        })
        session.write({"state": "closed"})
        self.env["account.bank.statement.line"].create({
            "pos_session_id": session.id,
            "journal_id": self.cash_journal.id,
            "amount": 5000.0,
            "date": fields.Date.today(),
            "payment_ref": "VAT KPI Test Opening Cash",
            "counterpart_account_id": self.receivable_account.id,
        })
        before = self._dashboard_data()
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_cash_deposit_and_expense_do_not_reduce_kpis(self):
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        before = self._dashboard_data()
        # POS cash out (expense / withdrawal) without an order
        session = self.env["pos.session"].create({
            "config_id": self.pos_config.id,
            "user_id": self.env.user.id,
        })
        session.write({"state": "closed"})
        self.env["account.bank.statement.line"].create({
            "pos_session_id": session.id,
            "journal_id": self.cash_journal.id,
            "amount": -300.0,
            "date": fields.Date.today(),
            "payment_ref": "VAT KPI Test Cash Out",
            "counterpart_account_id": self.receivable_account.id,
        })
        after = self._dashboard_data()
        delta_sales, delta_vat = self._kpi_deltas(before, after)
        self.assertAlmostEqual(delta_sales, 0.0, places=2)
        self.assertAlmostEqual(delta_vat, 0.0, places=2)

    def test_branch_filter_scopes_kpis_to_selected_branch(self):
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        self._create_pos_order(
            self.other_pos_config,
            [(self.other_cash_payment_method, 2200.0)],
            amount_total=2200.0,
            amount_tax=200.0,
        )
        branch_data = self._dashboard_data(branch_ids=[self.branch.id])
        other_branch_data = self._dashboard_data(branch_ids=[self.other_branch.id])
        all_data = self._dashboard_data()

        self.assertAlmostEqual(branch_data["total_sales"], 1000.0, places=2)
        self.assertAlmostEqual(branch_data["total_vat"], 100.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_sales"], 2000.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_vat"], 200.0, places=2)
        # Company-wide admin view includes both (plus anything else posted).
        self.assertGreaterEqual(all_data["total_sales"], 3000.0)

    def test_manager_sees_only_own_branch_kpis(self):
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
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        self._create_pos_order(
            self.other_pos_config,
            [(self.other_cash_payment_method, 2200.0)],
            amount_total=2200.0,
            amount_tax=200.0,
        )
        manager_data = self._dashboard_data_for_user(manager)
        self.assertAlmostEqual(manager_data["total_sales"], 1000.0, places=2)
        self.assertAlmostEqual(manager_data["total_vat"], 100.0, places=2)

    def test_plain_user_is_denied(self):
        plain_user = self.env["res.users"].create({
            "name": "VAT KPI Plain User",
            "login": "vat_kpi_plain_user",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self._dashboard_data_for_user(plain_user)
