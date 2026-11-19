# -*- coding: utf-8 -*-
"""Phase 2 — automatic branch cash journal and memo in the VAT register wizard.

Tests use in-memory wizard records (``new``) and only call the standard
compute/onchange methods; no payment or journal entry is ever created.
"""

from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestVatPaymentJournalSelection(TransactionCase):
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

        cls.journal_a = cls._make_cash_journal("VAT JS Test Cash A", "VJSA")
        cls.journal_b = cls._make_cash_journal("VAT JS Test Cash B", "VJSB")
        cls.bank_journal = cls.env["account.journal"].create({
            "name": "VAT JS Test Bank",
            "code": "VJSBNK",
            "type": "bank",
            "company_id": cls.company.id,
            "default_account_id": cls._make_account("VAT JS Test Bank Account").id,
        })

        cls.branch_a = cls._make_branch("VAT JS Branch A", cls.journal_a)
        cls.branch_b = cls._make_branch("VAT JS Branch B", cls.journal_b)
        cls.branch_without_journal = cls._make_branch("VAT JS No Journal", False)

    @classmethod
    def _make_account(cls, name):
        return cls.env["account.account"].create({
            "name": name,
            "code": cls.env["account.account"]._search_new_account_code(
                cls.company, 6, "97"
            ),
            "account_type": "asset_cash",
            "company_id": cls.company.id,
        })

    @classmethod
    def _make_cash_journal(cls, name, code):
        return cls.env["account.journal"].create({
            "name": name,
            "code": code,
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": cls._make_account(name + " Account").id,
        })

    @classmethod
    def _make_branch(cls, name, journal):
        return cls.env["kio.cash.deposit.branch"].create({
            "name": name,
            "company_id": cls.company.id,
            "branch_cash_account_id": journal.id if journal else False,
        })

    def _wizard(self, **vals):
        model = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True
        )
        defaults = model.default_get([
            "company_id", "payment_date", "payment_type", "partner_type",
            "currency_id", "source_currency_id",
        ])
        defaults.update(vals)
        return model.new(defaults)

    def _apply_computes(self, wizard):
        wizard._compute_from_lines()
        wizard._compute_available_journal_ids()
        wizard._compute_journal_id()
        wizard._compute_payment_method_line_id()
        wizard._compute_communication()
        return wizard

    # ------------------------------------------------------------------
    # Journal / method / memo selection
    # ------------------------------------------------------------------
    def test_branch_selects_configured_cash_journal_and_memo(self):
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        self.assertEqual(wizard.kio_vat_branch_id, self.branch_a)
        self.assertEqual(wizard.journal_id._origin, self.journal_a)
        self.assertEqual(wizard.available_journal_ids._origin, self.journal_a)
        self.assertEqual(wizard.payment_method_line_id.code, "manual")
        self.assertEqual(wizard.payment_method_line_id.payment_type, "outbound")
        self.assertEqual(wizard.communication, "VAT Payment - VAT JS Branch A")

    def test_changing_branch_updates_journal_and_memo(self):
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        self.assertEqual(wizard.journal_id._origin, self.journal_a)

        wizard.kio_vat_branch_id = self.branch_b
        result = wizard._onchange_kio_vat_branch_id()
        self._apply_computes(wizard)
        self.assertFalse(result)
        self.assertEqual(wizard.journal_id._origin, self.journal_b)
        self.assertEqual(wizard.available_journal_ids._origin, self.journal_b)
        self.assertEqual(wizard.communication, "VAT Payment - VAT JS Branch B")

    def test_clearing_branch_resets_journal_method_memo_and_amount(self):
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        wizard.amount = 500.0
        wizard.kio_vat_branch_id = False
        wizard._onchange_kio_vat_branch_id()
        self._apply_computes(wizard)
        self.assertFalse(wizard.journal_id)
        self.assertFalse(wizard.payment_method_line_id)
        self.assertFalse(wizard.available_journal_ids)
        self.assertFalse(wizard.communication)
        self.assertEqual(wizard.amount, 0.0)
        self.assertFalse(wizard.kio_vat_journal_warning)

    def test_outstanding_amount_logic_is_still_applied(self):
        wizard = self._wizard(kio_vat_branch_id=self.branch_a.id)
        with patch.object(
            type(self.env["account.payment.register"]),
            "_get_vat_branch_outstanding_amount",
            autospec=True,
            return_value=987.0,
        ) as mocked:
            wizard._onchange_kio_vat_branch_id()
            self.assertEqual(wizard.amount, 987.0)
            mocked.assert_called_once()

    # ------------------------------------------------------------------
    # Initialization with a preselected branch
    # ------------------------------------------------------------------
    def test_dashboard_preselection_initializes_all_fields(self):
        model = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True,
            default_kio_vat_branch_id=self.branch_a.id,
        )
        defaults = model.default_get(list(model._fields))
        self.assertEqual(defaults["kio_vat_branch_id"], self.branch_a.id)
        self.assertEqual(defaults["journal_id"], self.journal_a.id)
        self.assertEqual(defaults["payment_type"], "outbound")
        self.assertEqual(
            defaults["payment_method_line_id"],
            self.journal_a.outbound_payment_method_line_ids.filtered(
                lambda line: line.code == "manual"
            )[:1].id,
        )
        self.assertEqual(defaults["communication"], "VAT Payment - VAT JS Branch A")
        self.assertFalse(defaults["kio_vat_journal_warning"])
        # The computed values agree with the defaults on the actual wizard.
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        self.assertEqual(wizard.journal_id._origin.id, defaults["journal_id"])
        self.assertEqual(wizard.communication, defaults["communication"])

    # ------------------------------------------------------------------
    # Missing / unauthorized journal configuration
    # ------------------------------------------------------------------
    def test_missing_cash_journal_gives_warning_and_no_fallback(self):
        wizard = self._wizard(kio_vat_branch_id=self.branch_without_journal.id)
        result = wizard._onchange_kio_vat_branch_id()
        self._apply_computes(wizard)
        self.assertIn("warning", result)
        self.assertIn("No valid Cash Journal", str(result["warning"]["message"]))
        self.assertIn("No valid Cash Journal", wizard.kio_vat_journal_warning)
        self.assertFalse(wizard.journal_id)
        self.assertFalse(wizard.payment_method_line_id)
        self.assertFalse(wizard.available_journal_ids)
        # Never fall back to a bank or to another branch's cash journal.
        self.assertNotIn(self.bank_journal, wizard.available_journal_ids._origin)
        self.assertNotIn(self.journal_a, wizard.available_journal_ids._origin)

        defaults = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True,
            default_kio_vat_branch_id=self.branch_without_journal.id,
        ).default_get(list(self.env["account.payment.register"]._fields))
        self.assertIn("No valid Cash Journal", defaults["kio_vat_journal_warning"])
        self.assertFalse(defaults["journal_id"])

    def test_only_authorized_branch_journal_is_selectable(self):
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        self.assertEqual(wizard.available_journal_ids._origin, self.journal_a)
        self.assertNotIn(self.journal_b, wizard.available_journal_ids._origin)
        self.assertNotIn(self.bank_journal, wizard.available_journal_ids._origin)

    # ------------------------------------------------------------------
    # UI and untouched behaviour
    # ------------------------------------------------------------------
    def test_recipient_bank_hidden_for_vat_cash_payments(self):
        view = self.env.ref(
            "kio_vat_submission_extension.view_account_payment_register_form_kio_vat"
        )
        self.assertIn("kio_vat_payment_dashboard", view.arch_db)
        self.assertIn("partner_bank_id", view.arch_db)
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        # Standard Odoo already hides it for cash journals.
        wizard._compute_show_require_partner_bank()
        self.assertFalse(wizard.show_partner_bank_account)

    def test_normal_payment_wizard_is_unaffected(self):
        # Feed the standard compute path a normal (non-dashboard) batch. The
        # only stubbed piece is _get_batches so no invoice/payment is created.
        line = self.env["account.move.line"].search([
            ("company_id", "=", self.company.id),
        ], limit=1)
        batch = {
            "lines": line,
            "moves": line.move_id,
            "payment_values": {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "currency_id": self.company.currency_id.id,
                "partner_bank_id": False,
                "partner_id": False,
            },
        }
        model = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=[]
        )
        with patch.object(
            type(model), "_get_batches", autospec=True, return_value=[batch]
        ):
            wizard = model.new({})
            wizard._compute_available_journal_ids()
            wizard._compute_journal_id()
            wizard._compute_communication()
            self.assertIn(self.bank_journal, wizard.available_journal_ids._origin)
            self.assertIn(self.journal_a, wizard.available_journal_ids._origin)
            self.assertFalse(wizard.kio_vat_journal_warning)
            self.assertFalse(wizard.kio_vat_branch_id)

    def test_old_create_payment_blocker_is_removed(self):
        wizard = self._apply_computes(self._wizard(kio_vat_branch_id=self.branch_a.id))
        wizard.amount = 0.0
        with self.assertRaises(UserError) as ctx:
            wizard.action_create_payments()
        self.assertNotIn("not available yet", str(ctx.exception))
