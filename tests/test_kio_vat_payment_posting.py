# -*- coding: utf-8 -*-
"""Phase 3 — VAT payment creation, posting, balances and safety.

All records are created in the test transaction of a non-production database.
Nothing here touches the production database or existing accounting entries.
"""

from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestKioVatPaymentPosting(TransactionCase):
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
        cls._base_payable = cls.env[
            "kio.cash.deposit"
        ].get_vat_payment_dashboard_data()["total_payable"]
        cls.general_journal = cls.env["account.journal"].create({
            "name": "VAT Posting Test Misc",
            "code": "VPTM",
            "type": "general",
            "company_id": cls.company.id,
        })
        cls.journal_a, cls.cash_account_a, cls.branch_a = cls._make_branch_data(
            "VAT Posting Branch A", "VPBA"
        )
        cls.journal_b, cls.cash_account_b, cls.branch_b = cls._make_branch_data(
            "VAT Posting Branch B", "VPPB"
        )

    @classmethod
    def _make_account(cls, name):
        return cls.env["account.account"].create({
            "name": name,
            "code": cls.env["account.account"]._search_new_account_code(
                cls.company, 6, "96"
            ),
            "account_type": "asset_cash",
            "company_id": cls.company.id,
        })

    @classmethod
    def _make_branch_data(cls, name, code):
        account = cls._make_account(name + " Cash")
        journal = cls.env["account.journal"].create({
            "name": name + " Cash",
            "code": code,
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": account.id,
        })
        # Direct cash settlement requires the manual method to credit the
        # branch cash account itself.
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

    def _fund_vat(self, branch, amount):
        """Post a VAT credit and map it to ``branch`` via the existing POS mapping.

        ``_get_pos_config_branch_map`` attributes a move to a branch when a
        pos.session links it to a config whose payment method uses the branch
        cash journal. This keeps the test on the real calculation service.
        """
        move = self._post_entry([
            {
                "account_id": self.payable_account.id,
                "debit": amount,
                "name": "VAT payable opening",
            },
            {
                "account_id": self.vat_account.id,
                "credit": amount,
                "name": "VAT payable opening",
            },
        ], "VAT test liability " + branch.name)
        method = self.env["pos.payment.method"].create({
            "name": "VAT Posting " + branch.name,
            "journal_id": branch.branch_cash_account_id.id,
            "company_id": self.company.id,
        })
        config = self.env["pos.config"].create({
            "name": "VAT Posting Config " + branch.name,
            "company_id": self.company.id,
            "payment_method_ids": [Command.set(method.ids)],
        })
        self.env["pos.session"].create({
            "config_id": config.id,
            "user_id": self.env.user.id,
            "move_id": move.id,
            "state": "closed",
        })

    def _fund_cash(self, journal, amount):
        self._post_entry([
            {
                "account_id": journal.default_account_id.id,
                "debit": amount,
                "name": "Cash opening",
            },
            {
                "account_id": self.payable_account.id,
                "credit": amount,
                "name": "Cash opening",
            },
        ], "Cash test funding " + journal.code)

    def _wizard(self, branch, amount, payment_date=None):
        model = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True
        )
        vals = {
            "company_id": self.company.id,
            "payment_type": "outbound",
            "kio_vat_branch_id": branch.id,
            "amount": amount,
        }
        if payment_date:
            vals["payment_date"] = payment_date
        wizard = model.new(vals)
        wizard._compute_from_lines()
        wizard._compute_available_journal_ids()
        wizard._compute_journal_id()
        wizard._compute_payment_method_line_id()
        wizard._compute_communication()
        return wizard

    def _cash_balance(self, journal):
        return self.env["kio.cash.deposit"]._get_cash_journal_balance(
            journal, company=self.company
        )

    def _vat_totals(self):
        data = self.env["kio.cash.deposit"].get_vat_payment_dashboard_data()
        return data

    # ------------------------------------------------------------------
    # Core posting
    # ------------------------------------------------------------------
    def test_full_vat_payment_creates_posted_payment_and_correct_entry(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        before_cash = self._cash_balance(self.journal_a)
        wizard = self._wizard(self.branch_a, 1000.0)

        action = wizard.action_create_payments()
        # The success notification must close the wizard in place: opening the
        # payment form (act_window) would navigate away from the dashboard.
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(
            action["params"]["next"], {"type": "ir.actions.act_window_close"}
        )
        self.assertNotIn("res_model", action["params"]["next"])
        payment = self.env["account.payment"].browse(
            action["params"]["payment_id"]
        )
        self.assertEqual(payment.state, "posted")
        self.assertTrue(payment.is_kio_vat_payment)
        self.assertEqual(payment.kio_vat_branch_id, self.branch_a)
        self.assertEqual(payment.journal_id, self.journal_a)
        self.assertEqual(payment.payment_method_line_id.code, "manual")
        self.assertEqual(payment.amount, 1000.0)
        self.assertEqual(payment.ref, "VAT Payment - VAT Posting Branch A")
        self.assertTrue(payment.kio_vat_idempotency_key)

        move = payment.move_id
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.payment_id, payment)
        self.assertEqual(len(move.line_ids), 2)
        debit = move.line_ids.filtered(lambda l: l.account_id == self.vat_account)
        credit = move.line_ids.filtered(
            lambda l: l.account_id == self.cash_account_a
        )
        self.assertEqual(len(debit), 1)
        self.assertEqual(len(credit), 1)
        self.assertEqual(debit.balance, 1000.0)
        self.assertEqual(credit.balance, -1000.0)
        self.assertEqual(sum(move.line_ids.mapped("balance")), 0.0)
        self.assertAlmostEqual(
            self._cash_balance(self.journal_a), before_cash - 1000.0, places=2
        )

    def test_vat_journal_item_labels(self):
        """Both settlement lines carry the dynamic VAT label, before/after posting."""
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        payment_date = fields.Date.from_string("2026-11-19")
        expected = "VAT Payment - VAT Posting Branch A - 19/11/2026"

        captured = {}
        original_action_post = type(self.env["account.payment"]).action_post

        def _capture_before_post(records):
            captured["labels"] = list(records.move_id.line_ids.mapped("name"))
            return original_action_post(records)

        with patch.object(
            type(self.env["account.payment"]), "action_post", _capture_before_post
        ):
            action = self._wizard(
                self.branch_a, 250.0, payment_date=payment_date
            ).action_create_payments()

        # Labels are already in place on the move lines before posting.
        self.assertEqual(captured["labels"], [expected, expected])
        payment = self.env["account.payment"].browse(
            action["params"]["payment_id"]
        )
        self.assertEqual(payment.date, payment_date)
        self.assertEqual(len(payment.move_id.line_ids), 2)
        self.assertEqual(
            payment.move_id.line_ids.mapped("name"), [expected, expected]
        )
        # Posting must not alter the label.
        self.assertEqual(payment.state, "posted")
        self.assertEqual(payment.move_id.state, "posted")
        self.assertEqual(
            set(payment.move_id.line_ids.mapped("name")), {expected}
        )

    def test_partial_vat_payment(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        before = self._vat_totals()
        wizard = self._wizard(self.branch_a, 400.0)
        action = wizard.action_create_payments()
        payment = self.env["account.payment"].browse(
            action["params"]["payment_id"]
        )
        self.assertEqual(payment.amount, 400.0)
        self.assertEqual(payment.move_id.line_ids.filtered(
            lambda l: l.account_id == self.vat_account
        ).balance, 400.0)
        data = self._vat_totals()
        self.assertAlmostEqual(data["total_paid"], before["total_paid"] + 400.0, places=2)
        self.assertAlmostEqual(
            data["outstanding"], before["outstanding"] - 400.0, places=2
        )

    # ------------------------------------------------------------------
    # Validation failures
    # ------------------------------------------------------------------
    def _assert_validation_error(self, wizard, message):
        with self.assertRaises(UserError) as ctx:
            wizard.action_create_payments()
        self.assertIn(message, str(ctx.exception))
        self.assertFalse(self.env["account.payment"].search([
            ("is_kio_vat_payment", "=", True),
            ("kio_vat_branch_id", "=", wizard.kio_vat_branch_id.id),
        ]))

    def test_zero_amount_rejected(self):
        self._fund_cash(self.journal_a, 5000.0)
        self._assert_validation_error(
            self._wizard(self.branch_a, 0.0), "greater than zero"
        )

    def test_amount_exceeding_outstanding_rejected(self):
        self._fund_vat(self.branch_a, 100.0)
        self._fund_cash(self.journal_a, 5000.0)
        self._assert_validation_error(
            self._wizard(self.branch_a, 200.0), "exceeds the outstanding VAT"
        )

    def test_insufficient_cash_rejected(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 100.0)
        self._assert_validation_error(
            self._wizard(self.branch_a, 500.0), "does not have enough balance"
        )

    def test_invalid_journal_rejected(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        wizard = self._wizard(self.branch_a, 100.0)
        wizard.journal_id = self.journal_b
        self._assert_validation_error(wizard, "configured cash journal")

    def test_unauthorized_branch_rejected(self):
        self._fund_vat(self.branch_b, 1000.0)
        self._fund_cash(self.journal_b, 5000.0)
        manager = self.env["res.users"].create({
            "name": "VAT Posting Manager",
            "login": "vat_posting_manager",
            "groups_id": [
                Command.set([
                    self.env.ref("base.group_user").id,
                    self.env.ref("account.group_account_invoice").id,
                    self.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_user"
                    ).id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(self.branch_a.ids)],
            "company_ids": [Command.set(self.company.ids)],
        })
        wizard = self._wizard(self.branch_b, 100.0).with_user(manager)
        with self.assertRaises(Exception):
            wizard.action_create_payments()

    # ------------------------------------------------------------------
    # Multi-branch, idempotency, transaction safety
    # ------------------------------------------------------------------
    def test_multiple_branch_payments(self):
        for branch, journal, amount in (
            (self.branch_a, self.journal_a, 300.0),
            (self.branch_b, self.journal_b, 700.0),
        ):
            self._fund_vat(branch, amount)
            self._fund_cash(journal, 5000.0)
            wizard = self._wizard(branch, amount)
            action = wizard.action_create_payments()
            payment = self.env["account.payment"].browse(
                action["params"]["payment_id"]
            )
            self.assertEqual(payment.kio_vat_branch_id, branch)
            self.assertEqual(payment.journal_id, journal)
            self.assertEqual(payment.amount, amount)

    def test_duplicate_submission_is_idempotent(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        wizard = self._wizard(self.branch_a, 500.0)
        first = wizard.action_create_payments()
        first_id = first["params"]["payment_id"]
        # A second click with the same state must reuse the same payment.
        second = self._wizard(self.branch_a, 500.0).action_create_payments()
        self.assertEqual(second["params"]["payment_id"], first_id)
        self.assertEqual(self.env["account.payment"].search_count([
            ("is_kio_vat_payment", "=", True),
            ("kio_vat_branch_id", "=", self.branch_a.id),
        ]), 1)

    def test_posting_failure_rolls_back_payment(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        wizard = self._wizard(self.branch_a, 100.0)
        with patch.object(
            type(self.env["account.payment"]),
            "action_post",
            side_effect=UserError("simulated posting failure"),
        ):
            with self.assertRaises(UserError):
                with self.env.cr.savepoint():
                    wizard.action_create_payments()
        self.assertFalse(self.env["account.payment"].search([
            ("is_kio_vat_payment", "=", True),
            ("kio_vat_branch_id", "=", self.branch_a.id),
        ]))

    def test_posted_vat_payment_is_protected(self):
        self._fund_vat(self.branch_a, 100.0)
        self._fund_cash(self.journal_a, 5000.0)
        action = self._wizard(self.branch_a, 100.0).action_create_payments()
        payment = self.env["account.payment"].browse(
            action["params"]["payment_id"]
        )
        # Tests run as SUPERUSER; use a normal admin to exercise user guards.
        payment = payment.with_user(self.env.ref("base.user_admin"))
        with self.assertRaises(UserError):
            payment.write({"amount": 50.0})
        with self.assertRaises(UserError):
            payment.action_draft()
        with self.assertRaises(UserError):
            payment.action_cancel()
        with self.assertRaises(UserError):
            payment.unlink()

    # ------------------------------------------------------------------
    # History, dashboard reconciliation, normal regression
    # ------------------------------------------------------------------
    def test_payment_history_visibility_and_branch_security(self):
        self._fund_vat(self.branch_a, 100.0)
        self._fund_cash(self.journal_a, 5000.0)
        action = self._wizard(self.branch_a, 100.0).action_create_payments()
        payment = self.env["account.payment"].browse(
            action["params"]["payment_id"]
        )
        found = self.env["account.payment"].with_context(
            kio_vat_payment_history=True
        ).search([("id", "=", payment.id)])
        self.assertEqual(found, payment)
        self.assertTrue(payment.move_id)  # standard form can open the entry

    def test_vat_dashboard_reconciles_after_posting(self):
        self._fund_vat(self.branch_a, 1000.0)
        self._fund_cash(self.journal_a, 5000.0)
        before = self._vat_totals()
        self.assertAlmostEqual(
            before["total_payable"], self._base_payable + 1000.0, places=2
        )
        self._wizard(self.branch_a, 250.0).action_create_payments()
        after = self._vat_totals()
        self.assertAlmostEqual(after["total_payable"], before["total_payable"], places=2)
        self.assertAlmostEqual(after["total_paid"], before["total_paid"] + 250.0, places=2)
        self.assertAlmostEqual(after["outstanding"], before["outstanding"] - 250.0, places=2)
        self.assertAlmostEqual(
            after["gl_outstanding"], before["gl_outstanding"] - 250.0, places=2
        )
        row = next(r for r in after["rows"] if r["id"] == self.branch_a.id)
        self.assertAlmostEqual(row["paid_vat"], 250.0, places=2)
        self.assertAlmostEqual(row["outstanding_vat"], 750.0, places=2)

    def test_normal_payment_regression(self):
        """Ordinary vendor payments keep the standard payable destination."""
        payment = self.env["account.payment"].create({
            "payment_type": "outbound",
            "partner_type": "supplier",
            "amount": 10.0,
            "journal_id": self.journal_a.id,
            "company_id": self.company.id,
            "date": fields.Date.today(),
        })
        self.assertFalse(payment.is_kio_vat_payment)
        self.assertNotEqual(payment.destination_account_id, self.vat_account)
        self.assertEqual(payment.destination_account_id.account_type,
                         "liability_payable")
        payment.action_post()
        # Standard vendor payments keep the standard journal item labels.
        for line in payment.move_id.line_ids:
            self.assertTrue(line.name.startswith("Vendor Payment "))
            self.assertNotIn("VAT Payment -", line.name)
