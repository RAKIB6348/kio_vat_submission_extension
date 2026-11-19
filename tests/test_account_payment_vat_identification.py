# -*- coding: utf-8 -*-
"""Phase 1 — VAT payment identification and Payment History foundation.

These tests only create *draft* account.payment records in the test
transaction; they never post payments and never touch accounting entries.
"""

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestAccountPaymentVatIdentification(TransactionCase):
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

        cls.cash_account = cls.env["account.account"].create({
            "name": "VAT Identification Test Cash",
            "code": cls.env["account.account"]._search_new_account_code(
                cls.company, 6, "98"
            ),
            "account_type": "asset_cash",
            "company_id": cls.company.id,
        })
        cls.journal = cls.env["account.journal"].create({
            "name": "VAT Identification Test Cash",
            "code": "VICJ",
            "type": "cash",
            "company_id": cls.company.id,
            "default_account_id": cls.cash_account.id,
        })
        cls.branch_a = cls.env["kio.cash.deposit.branch"].create({
            "name": "VAT Identification Branch A",
            "company_id": cls.company.id,
            "branch_cash_account_id": cls.journal.id,
        })
        cls.branch_b = cls.env["kio.cash.deposit.branch"].create({
            "name": "VAT Identification Branch B",
            "company_id": cls.company.id,
        })

        cls.manager = cls.env["res.users"].create({
            "name": "VAT Identification Manager",
            "login": "vat_identification_manager",
            "groups_id": [
                Command.set([
                    cls.env.ref("base.group_user").id,
                    cls.env.ref("account.group_account_invoice").id,
                    cls.env.ref(
                        "kio_cash_deposit.group_kio_cash_deposit_user"
                    ).id,
                ]),
            ],
            "kio_cash_deposit_branch_ids": [Command.set(cls.branch_a.ids)],
            "company_ids": [Command.set(cls.company.ids)],
        })
        cls.plain_accountant = cls.env["res.users"].create({
            "name": "VAT Identification Plain Accountant",
            "login": "vat_identification_plain",
            "groups_id": [
                Command.set([
                    cls.env.ref("base.group_user").id,
                    cls.env.ref("account.group_account_invoice").id,
                ]),
            ],
            "company_ids": [Command.set(cls.company.ids)],
        })

    def _create_payment(self, **extra):
        vals = {
            "payment_type": "outbound",
            "amount": 100.0,
            "partner_type": "supplier",
            "journal_id": self.journal.id,
            "company_id": self.company.id,
            "date": fields.Date.today(),
        }
        vals.update(extra)
        return self.env["account.payment"].create(vals)

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    def test_fields_exist_with_expected_definition(self):
        payment_fields = self.env["account.payment"]._fields
        self.assertIn("is_kio_vat_payment", payment_fields)
        self.assertIn("kio_vat_branch_id", payment_fields)
        marker = payment_fields["is_kio_vat_payment"]
        branch = payment_fields["kio_vat_branch_id"]
        self.assertFalse(marker.copy)
        self.assertFalse(branch.copy)
        self.assertEqual(branch.comodel_name, "kio.cash.deposit.branch")
        defaults = self.env["account.payment"].default_get(["is_kio_vat_payment"])
        self.assertFalse(defaults["is_kio_vat_payment"])

    def test_normal_payment_is_not_a_vat_payment(self):
        payment = self._create_payment()
        self.assertFalse(payment.is_kio_vat_payment)
        self.assertFalse(payment.kio_vat_branch_id)
        self.assertEqual(payment.state, "draft")

    # ------------------------------------------------------------------
    # Marking restrictions
    # ------------------------------------------------------------------
    def test_plain_user_cannot_mark_payment_as_vat(self):
        with self.assertRaises(AccessError):
            self._create_payment().with_user(self.plain_accountant).write({
                "is_kio_vat_payment": True,
            })
        with self.assertRaises(AccessError):
            self.env["account.payment"].with_user(self.plain_accountant).create({
                "payment_type": "outbound",
                "amount": 50.0,
                "partner_type": "supplier",
                "journal_id": self.journal.id,
                "company_id": self.company.id,
                "is_kio_vat_payment": True,
            })

    def test_manager_cannot_use_unassigned_branch(self):
        with self.assertRaises(ValidationError):
            self.env["account.payment"].with_user(self.manager).create({
                "payment_type": "outbound",
                "amount": 50.0,
                "partner_type": "supplier",
                "journal_id": self.journal.id,
                "company_id": self.company.id,
                "is_kio_vat_payment": True,
                "kio_vat_branch_id": self.branch_b.id,
            })

    # ------------------------------------------------------------------
    # Payment History
    # ------------------------------------------------------------------
    def test_history_only_returns_authorized_vat_payments(self):
        vat_a = self._create_payment(
            is_kio_vat_payment=True, kio_vat_branch_id=self.branch_a.id
        )
        vat_b = self._create_payment(
            is_kio_vat_payment=True, kio_vat_branch_id=self.branch_b.id
        )
        normal = self._create_payment()
        ids = (vat_a + vat_b + normal).ids

        # Without the history context, nothing is restricted.
        all_found = self.env["account.payment"].search([("id", "in", ids)])
        self.assertEqual(set(all_found.ids), set(ids))

        # Admin sees both VAT payments but never the ordinary payment.
        admin_found = self.env["account.payment"].with_context(
            kio_vat_payment_history=True
        ).search([("id", "in", ids)])
        self.assertEqual(set(admin_found.ids), {vat_a.id, vat_b.id})

        # Manager sees only the payment of their assigned branch.
        manager_found = self.env["account.payment"].with_user(self.manager).with_context(
            kio_vat_payment_history=True
        ).search([("id", "in", ids)])
        self.assertEqual(set(manager_found.ids), {vat_a.id})

    def test_history_denies_user_without_cash_deposit_access(self):
        with self.assertRaises(AccessError):
            self.env["account.payment"].with_user(self.plain_accountant).with_context(
                kio_vat_payment_history=True
            ).search([])

    def test_branch_column_is_declared_on_standard_tree_view(self):
        view = self.env.ref(
            "kio_vat_submission_extension.view_account_payment_tree_kio_vat_history"
        )
        self.assertEqual(view.inherit_id, self.env.ref("account.view_account_payment_tree"))
        self.assertIn("kio_vat_branch_id", view.arch_db)
        self.assertIn("kio_vat_payment_history", view.arch_db)

    # ------------------------------------------------------------------
    # Phase 3: the old "not available yet" blocker is gone
    # ------------------------------------------------------------------
    def test_vat_payment_workflow_is_enabled(self):
        model = self.env["account.payment.register"].with_context(
            kio_vat_payment_dashboard=True
        )
        wizard = model.new({
            "company_id": self.company.id,
            "payment_type": "outbound",
            "kio_vat_branch_id": self.branch_a.id,
            "amount": 0.0,
        })
        wizard._compute_available_journal_ids()
        wizard._compute_journal_id()
        wizard._compute_payment_method_line_id()
        with self.assertRaises(UserError) as ctx:
            wizard.action_create_payments()
        self.assertNotIn("not available yet", str(ctx.exception))
