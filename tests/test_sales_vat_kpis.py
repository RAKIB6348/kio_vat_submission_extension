# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCashDepositSalesKpis(TransactionCase):
    """Total Sales Amount / Total VAT KPIs for the Cash Deposit dashboard.

    Uses FIFO cash allocation: POS sales receipts are inflows, non-sales
    cash outflows (deposits, expenses, transfers) and opening balances are
    also modelled. Withdrawals deduct from the oldest inflows first. The
    remaining sales-derived cash proportionally determines attributable
    sales and VAT.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Clean any POS/demo data so tests start from a blank slate.
        # Delete all accounting entries touching cash accounts, then POS records.
        cash_account_ids = cls.env["account.account"].sudo().search([
            ("account_type", "=", "asset_cash"),
            ("company_id", "=", cls.env.company.id),
        ]).ids
        if cash_account_ids:
            cls.env.cr.execute(
                "DELETE FROM account_partial_reconcile "
                "WHERE debit_move_id IN (SELECT id FROM account_move_line "
                "WHERE account_id IN %s) "
                "OR credit_move_id IN (SELECT id FROM account_move_line "
                "WHERE account_id IN %s)",
                (tuple(cash_account_ids), tuple(cash_account_ids)),
            )
        cls.env.cr.execute("DELETE FROM account_bank_statement_line WHERE pos_session_id IS NOT NULL")
        cls.env.cr.execute("DELETE FROM pos_payment")
        cls.env.cr.execute("DELETE FROM pos_order")
        cls.env.cr.execute("DELETE FROM pos_session")
        cls.env.cr.execute(
            "DELETE FROM account_move_line WHERE account_id IN %s",
            (tuple(cash_account_ids),),
        )
        cls.env.cr.execute(
            "DELETE FROM account_move WHERE id IN "
            "(SELECT DISTINCT move_id FROM account_move_line "
            "WHERE account_id IN %s)",
            (tuple(cash_account_ids),),
        )

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

    def _post_non_pos_credit(self, journal, amount, date=None):
        """Post a non-POS credit to the coverage cash account (a withdrawal:
        deposit to bank, transfer out, expense paid)."""
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": date or fields.Date.today(),
            "ref": "VAT KPI Test Withdrawal",
            "line_ids": [
                Command.create({
                    "name": "Withdrawal",
                    "account_id": self.receivable_account.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                Command.create({
                    "name": "Withdrawal",
                    "account_id": self.cash_account.id,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        })
        move.action_post()
        return move

    def _post_non_pos_debit(self, journal, amount, date=None):
        """Post a non-POS debit to the coverage cash account (opening balance)."""
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": date or fields.Date.today(),
            "ref": "VAT KPI Test Opening",
            "line_ids": [
                Command.create({
                    "name": "Opening",
                    "account_id": self.cash_account.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                Command.create({
                    "name": "Opening",
                    "account_id": self.receivable_account.id,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        })
        move.action_post()
        return move

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

    def _expected_fifo(self, sales_receipts, withdrawals, opening=0.0):
        """Compute expected FIFO result: (sales, vat) after withdrawals.

        sales_receipts: list of (cash_amount, sales_ex_vat, vat)
        withdrawals: list of amounts
        opening: opening balance amount
        """
        inflows = []
        if opening > 0:
            inflows.append({"amount": opening, "type": "opening", "sales_ex_vat": 0.0, "vat": 0.0})
        for cash, sales, vat in sales_receipts:
            inflows.append({"amount": cash, "type": "sales", "sales_ex_vat": sales, "vat": vat})

        remaining = [inf["amount"] for inf in inflows]
        for wd in withdrawals:
            wd_remaining = wd
            for i in range(len(inflows)):
                if wd_remaining <= 0:
                    break
                if remaining[i] > 0:
                    deduct = min(remaining[i], wd_remaining)
                    remaining[i] -= deduct
                    wd_remaining -= deduct

        attributable_sales = 0.0
        attributable_vat = 0.0

        for inf, rem in zip(inflows, remaining):
            if inf["type"] == "sales" and rem > 0:
                ratio = rem / inf["amount"]
                attributable_sales += inf["sales_ex_vat"] * ratio
                attributable_vat += inf["vat"] * ratio

        return attributable_sales, attributable_vat

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

    def test_cash_only_sale_no_withdrawals(self):
        """Full cash sale with no withdrawals: sales and VAT fully attributable."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 1000.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 100.0, places=2)

    def test_bank_only_sale_is_not_counted(self):
        """Bank payment is not a cash receipt: contributes nothing."""
        self._create_pos_order(
            self.pos_config,
            [(self.bank_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)

    def test_mixed_payments_pro_rata(self):
        """Mixed cash/card: only cash portion counts."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 660.0), (self.bank_payment_method, 440.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        data = self._dashboard_data()
        # 60% cash -> 60% of 1000 (untaxed) and 60% of 100 (VAT)
        self.assertAlmostEqual(data["total_sales"], 600.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 60.0, places=2)

    def test_multiple_vat_rates(self):
        """Multiple VAT rates: total VAT from Odoo's computed amount_tax."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1725.0)],
            amount_total=1725.0,
            amount_tax=225.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 1500.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 225.0, places=2)

    def test_tax_exempt_sale(self):
        """Tax-exempt sale: sales count, VAT is zero."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 800.0)],
            amount_total=800.0,
            amount_tax=0.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 800.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)

    def test_discounted_sale(self):
        """Discounted sale uses Odoo's discounted amount_total/amount_tax."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 990.0)],
            amount_total=990.0,
            amount_tax=90.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 900.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 90.0, places=2)

    def test_refund_reduces_sales_and_vat(self):
        """Refund (negative order) reduces sales and VAT."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, -1100.0)],
            amount_total=-1100.0,
            amount_tax=-100.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)

    def test_fifo_withdrawal_reduces_sales_proportionally(self):
        """FIFO: a withdrawal reduces sales/VAT proportionally to remaining cash."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        self._post_non_pos_credit(self.bank_journal, 550.0)
        data = self._dashboard_data()
        # Withdraw 550 (50% of cash) -> 50% of sales/VAT remain
        self.assertAlmostEqual(data["total_sales"], 500.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 50.0, places=2)

    def test_fifo_opening_balance_consumed_first(self):
        """FIFO: opening balance is consumed before sales on same date."""
        self._post_non_pos_debit(self.cash_journal, 500.0)
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        self._post_non_pos_credit(self.bank_journal, 1000.0)
        data = self._dashboard_data()
        # Opening 500 consumed first, then 500 from sales
        # Remaining sales cash: 1100 - 500 = 600 out of 1100
        # Ratio: 600/1100 = 0.5454...
        expected_sales, expected_vat = self._expected_fifo(
            [(1100.0, 1000.0, 100.0)], [1000.0], opening=500.0
        )
        self.assertAlmostEqual(data["total_sales"], expected_sales, places=2)
        self.assertAlmostEqual(data["total_vat"], expected_vat, places=2)

    def test_fifo_multiple_withdrawals(self):
        """FIFO: multiple withdrawals consumed in chronological order."""
        day1 = fields.Datetime.now() - timedelta(days=2)
        day2 = fields.Datetime.now() - timedelta(days=1)
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            date_order=day1,
        )
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 2200.0)],
            amount_total=2200.0,
            amount_tax=200.0,
            date_order=day2,
        )
        self._post_non_pos_credit(self.bank_journal, 1500.0)
        data = self._dashboard_data()
        # Withdraw 1500: day1 sale 1100 fully consumed, then 400 from day2
        # Remaining day2: 2200-400 = 1800 out of 3300 total
        expected_sales, expected_vat = self._expected_fifo(
            [(1100.0, 1000.0, 100.0), (2200.0, 2000.0, 200.0)], [1500.0]
        )
        self.assertAlmostEqual(data["total_sales"], expected_sales, places=2)
        self.assertAlmostEqual(data["total_vat"], expected_vat, places=2)

    def test_fifo_preserves_each_order_tax_mix_and_reconciles_cash(self):
        """FIFO applies remaining cash to each POS order's actual tax data."""
        day1 = fields.Date.today() - timedelta(days=2)
        day2 = fields.Date.today() - timedelta(days=1)
        remaining_cash, sales, vat = self.env["kio.cash.deposit"]._apply_fifo_allocation(
            [
                {
                    "date": day1,
                    "amount": 1100.0,
                    "sales_ex_vat": 1000.0,
                    "vat": 100.0,
                    "id": 1,
                },
                {
                    "date": day2,
                    "amount": 1200.0,
                    "sales_ex_vat": 1000.0,
                    "vat": 200.0,
                    "id": 2,
                },
            ],
            [{"date": day1, "amount": 500.0, "id": 1}],
            [{"date": day2, "amount": 1400.0, "id": 3}],
        )

        # The withdrawal consumes opening cash first, then 900 from order 1.
        # Order 1 retains 200/1100 of its base and VAT; order 2 remains whole.
        self.assertAlmostEqual(remaining_cash, 1400.0, places=2)
        self.assertAlmostEqual(sales, 1000.0 + 1000.0 * 200.0 / 1100.0, places=2)
        self.assertAlmostEqual(vat, 200.0 + 100.0 * 200.0 / 1100.0, places=2)
        self.assertAlmostEqual(sales + vat, remaining_cash, places=2)

    def test_open_session_sale_is_not_counted(self):
        """Sale from an open session is not eligible."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            state="done",
            session_state="opened",
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)

    def test_historical_sale_through_today_is_counted(self):
        """Sales from any date up to today are included."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            date_order=fields.Datetime.now() - timedelta(days=400),
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 1000.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 100.0, places=2)

    def test_future_dated_sale_is_not_counted(self):
        """Sales dated after today are not counted."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
            date_order=fields.Datetime.now() + timedelta(days=1),
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)

    def test_branch_filter_scopes_kpis(self):
        """Branch filter limits KPIs to that branch's cash journal."""
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

        self.assertAlmostEqual(branch_data["total_sales"], 1000.0, places=2)
        self.assertAlmostEqual(branch_data["total_vat"], 100.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_sales"], 2000.0, places=2)
        self.assertAlmostEqual(other_branch_data["total_vat"], 200.0, places=2)

    def test_withdrawal_on_main_branch_reduces_main_branch_kpis(self):
        """Withdrawal on main branch's cash account reduces main branch KPIs."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 1100.0)],
            amount_total=1100.0,
            amount_tax=100.0,
        )
        before = self._dashboard_data(branch_ids=[self.branch.id])
        self._post_non_pos_credit(self.bank_journal, 550.0)
        after = self._dashboard_data(branch_ids=[self.branch.id])
        self.assertAlmostEqual(after["total_sales"], before["total_sales"] - 500.0, places=2)
        self.assertAlmostEqual(after["total_vat"], before["total_vat"] - 50.0, places=2)

    def test_manager_sees_only_own_branch_kpis(self):
        """Manager with assigned branch sees only that branch's KPIs."""
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
        """Plain user without cash deposit groups gets AccessError."""
        plain_user = self.env["res.users"].create({
            "name": "VAT KPI Plain User",
            "login": "vat_kpi_plain_user",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(AccessError):
            self._dashboard_data_for_user(plain_user)

    def test_zero_total_order_is_ignored(self):
        """Zero-total order does not cause division by zero."""
        self._create_pos_order(
            self.pos_config,
            [(self.cash_payment_method, 0.0)],
            amount_total=0.0,
            amount_tax=0.0,
        )
        data = self._dashboard_data()
        self.assertAlmostEqual(data["total_sales"], 0.0, places=2)
        self.assertAlmostEqual(data["total_vat"], 0.0, places=2)
