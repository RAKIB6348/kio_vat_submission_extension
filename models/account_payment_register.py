# -*- coding: utf-8 -*-
# KIO VAT payment register wizard — Phases 1, 2 and 3.

import hashlib

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    kio_vat_branch_id = fields.Many2one(
        "kio.cash.deposit.branch",
        string="Branch",
        check_company=True,
    )
    available_kio_vat_branch_ids = fields.Many2many(
        "kio.cash.deposit.branch",
        compute="_compute_available_kio_vat_branch_ids",
        compute_sudo=True,
    )
    # Inline feedback for a branch without a usable cash journal. A real
    # onchange warning is also returned, but this stays visible after load.
    kio_vat_journal_warning = fields.Char(string="VAT Journal Warning", copy=False)

    def _is_vat_payment_dashboard_mode(self):
        self.ensure_one()
        return bool(
            self.env.context.get("kio_vat_payment_dashboard")
            and not self.line_ids
        )

    @api.model
    def _get_vat_payment_authorized_branches(self, company=None):
        company = company or self.env.company
        # Reuse the same branch authorization and VAT calculation service as the
        # VAT Payment Dashboard (kio.cash.deposit).
        return self.env["kio.cash.deposit"]._get_authorized_vat_branches().filtered(
            lambda b: b.company_id == company
        )

    @api.model
    def _get_vat_branch_cash_journals(self, branch, company=None, currency=None):
        """Cash journals configured on ``branch`` and valid for this payment.

        Reuses the existing branch-to-journal relationship
        ``kio.cash.deposit.branch.branch_cash_account_id`` (the same mapping the
        Cash Deposit dashboard uses). No name matching or hardcoded journal IDs
        are involved. An empty recordset is returned when the mapping is
        missing, inactive, of the wrong type, from another company or in a
        different currency - never a bank journal or an unrelated cash journal.
        """
        Journal = self.env["account.journal"]
        company = company or self.env.company
        if not branch or not branch.exists() or not branch.active:
            return Journal
        if branch.company_id != company:
            return Journal
        journal = branch.sudo().branch_cash_account_id
        if (not journal or not journal.active or journal.type != "cash"
                or journal.company_id != company):
            return Journal
        if currency and journal.currency_id and journal.currency_id != currency:
            return Journal
        return journal

    @api.model
    def _get_vat_manual_outbound_method(self, journal):
        """Return the journal's configured Manual outbound method line, if any."""
        if not journal:
            return self.env["account.payment.method.line"]
        return journal.sudo().outbound_payment_method_line_ids.filtered(
            lambda line: line.code == "manual" and line.payment_type == "outbound"
        )[:1]

    @api.depends("company_id")
    def _compute_available_kio_vat_branch_ids(self):
        for wizard in self:
            if wizard.env.context.get("kio_vat_payment_dashboard") and not wizard.line_ids:
                wizard.available_kio_vat_branch_ids = wizard._get_vat_payment_authorized_branches(
                    company=wizard.company_id or self.env.company
                )
            else:
                wizard.available_kio_vat_branch_ids = [(5, 0, 0)]

    @api.model
    def _get_vat_branch_outstanding_amount(self, branch, company, currency, date=None):
        if not branch:
            return 0.0
        company = company or self.env.company
        currency = currency or company.currency_id
        summary = self.env["kio.cash.deposit"]._get_vat_branch_summary_data(
            branch_id=branch.id
        )
        outstanding = summary.get("outstanding", 0.0) or 0.0
        # Outstanding should not be negative for payment amount purposes.
        if company.currency_id.compare_amounts(outstanding, 0.0) <= 0:
            return 0.0
        amount = company.currency_id._convert(
            outstanding,
            currency,
            company,
            date or fields.Date.context_today(self),
        )
        return amount

    @api.model
    def default_get(self, fields_list):
        if self.env.context.get("kio_vat_payment_dashboard"):
            # Allow opening the standard Register Payment form from the VAT
            # Payment dashboard without invoice/journal-item context. This path
            # is isolated by an explicit context key and does not use fake
            # accounting records.
            res = models.TransientModel.default_get(self, fields_list)
            company = self.env.company
            currency = company.currency_id
            branch = False
            branch_id = self.env.context.get("default_kio_vat_branch_id") or self.env.context.get(
                "kio_vat_branch_id"
            )
            if branch_id:
                try:
                    branch_id = int(branch_id)
                except (TypeError, ValueError):
                    branch_id = False
            authorized = self._get_vat_payment_authorized_branches(company=company)
            if branch_id:
                branch = authorized.filtered(lambda b: b.id == branch_id)[:1]
            amount = self._get_vat_branch_outstanding_amount(
                branch, company, currency, res.get("payment_date")
            )
            # Preselected branch: initialize the journal, method and memo in the
            # same way the branch onchange does for an interactive selection.
            journals = self._get_vat_branch_cash_journals(
                branch, company=company, currency=currency
            )
            method = self._get_vat_manual_outbound_method(journals[:1])
            warning = False
            if branch and not journals:
                warning = _(
                    "No valid Cash Journal is configured for branch %(branch)s. "
                    "Set its 'Branch Cash Account' before creating a VAT payment.",
                    branch=branch.display_name,
                )
            res.update({
                "payment_date": res.get("payment_date") or fields.Date.context_today(self),
                "company_id": company.id,
                "partner_id": False,
                "partner_type": "supplier",
                "payment_type": "outbound",
                "source_currency_id": currency.id,
                "currency_id": currency.id,
                "source_amount": 0.0,
                "source_amount_currency": 0.0,
                "amount": amount,
                "kio_vat_branch_id": branch.id if branch else False,
                "journal_id": journals[:1].id if journals else False,
                "payment_method_line_id": method.id if method else False,
                "communication": f"VAT Payment - {branch.name}" if branch else False,
                "kio_vat_journal_warning": warning,
                "can_edit_wizard": True,
                "can_group_payments": False,
                "group_payment": False,
                "line_ids": [(6, 0, [])],
            })
            return res
        return super().default_get(fields_list)

    @api.onchange("kio_vat_branch_id", "currency_id", "payment_date")
    def _onchange_kio_vat_branch_id(self):
        warning = False
        for wizard in self:
            if not wizard._is_vat_payment_dashboard_mode():
                continue
            branch = wizard.kio_vat_branch_id
            company = wizard.company_id or self.env.company
            currency = wizard.currency_id or company.currency_id
            if not branch:
                wizard.amount = 0.0
                wizard.kio_vat_journal_warning = False
                continue
            authorized = wizard._get_vat_payment_authorized_branches(company=company)
            if branch not in authorized:
                wizard.kio_vat_branch_id = False
                wizard.amount = 0.0
                wizard.kio_vat_journal_warning = False
                warning = {
                    "title": _("Unauthorized Branch"),
                    "message": _("You are not authorized to select this branch."),
                }
                continue
            journals = wizard._get_vat_branch_cash_journals(
                branch, company=company, currency=currency
            )
            if not journals:
                wizard.journal_id = False
                wizard.payment_method_line_id = False
                wizard.kio_vat_journal_warning = _(
                    "No valid Cash Journal is configured for branch %(branch)s. "
                    "Set its 'Branch Cash Account' before creating a VAT payment.",
                    branch=branch.display_name,
                )
                warning = {
                    "title": _("No Cash Journal Configured"),
                    "message": wizard.kio_vat_journal_warning,
                }
            else:
                wizard.journal_id = journals[:1]
                method = wizard._get_vat_manual_outbound_method(journals[:1])
                if method:
                    wizard.payment_method_line_id = method
                wizard.kio_vat_journal_warning = False
            # Outstanding VAT amount logic is deliberately unchanged.
            wizard.amount = self._get_vat_branch_outstanding_amount(
                branch, company, currency, wizard.payment_date,
            )
        if warning:
            return {"warning": warning}

    @api.depends('line_ids')
    def _compute_from_lines(self):
        vat_dashboard = self.filtered(
            lambda w: w.env.context.get("kio_vat_payment_dashboard") and not w.line_ids
        )
        for wizard in vat_dashboard:
            company = wizard.company_id or self.env.company
            currency = company.currency_id
            wizard.company_id = company.id
            wizard.partner_id = False
            wizard.partner_type = "supplier"
            wizard.payment_type = wizard.payment_type or "outbound"
            wizard.source_currency_id = currency.id
            wizard.source_amount = 0.0
            wizard.source_amount_currency = 0.0
            wizard.can_edit_wizard = True
            wizard.can_group_payments = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_from_lines()

    @api.depends('can_edit_wizard', 'kio_vat_branch_id')
    def _compute_communication(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            branch = wizard.kio_vat_branch_id
            wizard.communication = f"VAT Payment - {branch.name}" if branch else False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_communication()

    @api.depends('can_edit_wizard')
    def _compute_group_payment(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.group_payment = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_group_payment()

    @api.depends('payment_type', 'company_id', 'can_edit_wizard', 'kio_vat_branch_id')
    def _compute_available_journal_ids(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            company = wizard.company_id or self.env.company
            authorized = wizard._get_vat_payment_authorized_branches(company=company)
            branch = wizard.kio_vat_branch_id if wizard.kio_vat_branch_id in authorized else False
            # Restricted to the branch's configured cash journal(s). No fallback
            # to a bank journal or to an unrelated cash journal is allowed.
            journals = wizard._get_vat_branch_cash_journals(
                branch,
                company=company,
                currency=wizard.currency_id or company.currency_id,
            )
            wizard.available_journal_ids = [Command.set(journals.ids)]
        super(AccountPaymentRegister, self - vat_dashboard)._compute_available_journal_ids()

    @api.depends('available_journal_ids')
    def _compute_journal_id(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            available = wizard.available_journal_ids
            # Keep a user-made choice when it is still among the authorized
            # journals (relevant if a branch ever exposes several cash journals).
            if wizard.journal_id and wizard.journal_id in available:
                continue
            wizard.journal_id = available[:1].id if available else False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_journal_id()

    @api.depends('payment_type', 'journal_id')
    def _compute_payment_method_line_id(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            manual = wizard._get_vat_manual_outbound_method(wizard.journal_id)
            if manual:
                wizard.payment_method_line_id = manual
            else:
                # Keep standard Odoo behaviour when Manual is not configured.
                super(AccountPaymentRegister, wizard)._compute_payment_method_line_id()
        super(AccountPaymentRegister, self - vat_dashboard)._compute_payment_method_line_id()

    @api.depends('can_edit_wizard', 'journal_id')
    def _compute_available_partner_bank_ids(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.available_partner_bank_ids = [(6, 0, [])]
        super(AccountPaymentRegister, self - vat_dashboard)._compute_available_partner_bank_ids()

    @api.depends('journal_id', 'available_partner_bank_ids')
    def _compute_partner_bank_id(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.partner_bank_id = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_partner_bank_id()

    @api.depends('payment_method_line_id', 'line_ids', 'group_payment')
    def _compute_trust_values(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.total_payments_amount = 1
            wizard.untrusted_payments_count = 0
            wizard.untrusted_bank_ids = [(6, 0, [])]
        super(AccountPaymentRegister, self - vat_dashboard)._compute_trust_values()

    @api.depends('can_edit_wizard', 'source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id', 'payment_date')
    def _compute_amount(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            # Amount in VAT dashboard mode is driven by selected branch
            # outstanding VAT (and user-editable for future partial payments).
            # Do not reset it during unrelated recomputations.
            wizard.amount = wizard.amount
        super(AccountPaymentRegister, self - vat_dashboard)._compute_amount()

    @api.depends('can_edit_wizard', 'payment_date', 'currency_id', 'amount')
    def _compute_early_payment_discount_mode(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.early_payment_discount_mode = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_early_payment_discount_mode()

    @api.depends('can_edit_wizard', 'amount')
    def _compute_payment_difference(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.payment_difference = 0.0
        super(AccountPaymentRegister, self - vat_dashboard)._compute_payment_difference()

    # ------------------------------------------------------------------
    # Phase 3 — validated VAT payment creation and posting
    # ------------------------------------------------------------------
    def _kio_vat_payment_permission(self):
        self.env["kio.cash.deposit"]._check_cash_deposit_access()
        if not self.env["account.payment"].check_access_rights(
            "create", raise_exception=False
        ):
            raise UserError(_(
                "You do not have permission to create VAT payments. "
                "Ask your Accounting administrator for payment rights."
            ))

    def _validate_kio_vat_payment(self):
        """Recompute every server-side constraint immediately before posting."""
        self.ensure_one()
        self._kio_vat_payment_permission()
        # ``_origin`` makes this work for both saved wizards and in-memory
        # (NewId) wizards used by onchange/tests.
        company = (self.company_id or self.env.company)._origin
        branch = self.kio_vat_branch_id._origin
        journal = self.journal_id._origin
        method = self.payment_method_line_id._origin
        currency = (self.currency_id or company.currency_id)._origin

        if not branch:
            raise UserError(_("Select a branch before creating a VAT payment."))
        if branch not in self._get_vat_payment_authorized_branches(company=company):
            raise UserError(_("You are not authorized to pay VAT for this branch."))
        if branch.sudo().company_id != company:
            raise UserError(_("The selected branch does not belong to the payment company."))
        if (not journal or not journal.active or journal.type != "cash"
                or journal.company_id != company):
            raise UserError(_("Select a valid cash journal for the selected branch."))
        if branch.sudo().branch_cash_account_id != journal:
            raise UserError(_(
                "The selected journal is not the configured cash journal of this branch."
            ))

        if (not method or method.journal_id != journal
                or method.payment_type != "outbound" or method.code != "manual"):
            raise UserError(_(
                "Select a valid Manual outbound payment method for this journal."
            ))
        if self.payment_type != "outbound":
            raise UserError(_("A VAT payment must be an outbound payment."))
        expected_currency = journal.currency_id or company.currency_id
        if not currency or currency != expected_currency or currency != company.currency_id:
            raise UserError(_("The journal, payment and company currencies must be consistent."))

        # A direct cash settlement is only safe when the payment credits the
        # branch cash account itself. A separate clearing account would need an
        # additional bank-statement reconciliation workflow.
        outstanding_account = (
            method.payment_account_id
            or company.account_journal_payment_credit_account_id
        )
        if not outstanding_account:
            raise UserError(_(
                "No Outstanding Payments Account is configured for this "
                "payment method or company."
            ))
        if outstanding_account != journal.default_account_id:
            raise UserError(_(
                "This journal uses a separate Outstanding Payments Account. "
                "Direct VAT cash settlement is not available for this configuration."
            ))

        amount = company.currency_id.round(self.amount or 0.0)
        if company.currency_id.compare_amounts(amount, 0.0) <= 0:
            raise UserError(_("The VAT payment amount must be greater than zero."))

        outstanding = self._get_vat_branch_outstanding_amount(
            branch, company, currency, self.payment_date
        )
        if company.currency_id.compare_amounts(amount, outstanding) > 0:
            raise UserError(_(
                "The payment amount exceeds the outstanding VAT of this branch."
            ))
        cash = self.env["kio.cash.deposit"]._get_cash_journal_balance(
            journal, company=company
        )
        if company.currency_id.compare_amounts(amount, cash) > 0:
            raise UserError(_(
                "The branch cash account does not have enough balance for this payment."
            ))

        vat_account = self.env["account.payment"]._kio_vat_payable_account(
            company, required=True
        )
        date = self.payment_date or fields.Date.context_today(self)
        locks = company._get_violated_lock_dates(date, False)
        if locks:
            raise UserError(_(
                "The payment date is inside an accounting lock date (%(date)s, %(kind)s).",
                date=locks[0][0], kind=locks[0][1],
            ))

        # The key deliberately excludes the (recomputed) outstanding amount so
        # that two identical requests within the same wizard state are treated
        # as one submission. A client may supply its own key for intentional
        # repeat payments of the same amount on the same day.
        key_source = "|".join([
            str(company.id), str(branch.id), str(journal.id),
            f"{amount:.4f}", fields.Date.to_string(date),
            self.env.context.get("kio_vat_payment_idempotency_key", ""),
        ])
        return {
            "company": company,
            "branch": branch,
            "journal": journal,
            "method": method,
            "currency": currency,
            "vat_account": vat_account,
            "amount": amount,
            "date": date,
            "outstanding": outstanding,
            "key": hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _kio_vat_success_action(payment):
        """Show the success notification and close the wizard in place.

        The ``next`` action must be an ``act_window_close`` instead of an
        ``act_window``: opening the payment form would navigate the user away
        from the VAT Payment Dashboard. The dashboard passes an ``onClose``
        callback to ``doAction()`` and reloads its data when this dialog
        closes, keeping the selected branch, filters, search and active tab.
        """
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("VAT Payment Posted"),
                "message": _("VAT payment %s has been posted successfully.", payment.name),
                "type": "success",
                # Exposed for clients/tests that need the settled payment
                # without navigating to it.
                "payment_id": payment.id,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _create_kio_vat_payment(self):
        self.ensure_one()
        # Validate once for a clear error, then serialize per branch and
        # validate again against freshly recomputed VAT/cash availability.
        self._validate_kio_vat_payment()
        branch = self.kio_vat_branch_id._origin
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", (0x4B4956, branch.id)
        )
        # Validation always re-reads outstanding VAT and cash from the database,
        # so the second pass sees the state committed by the competing request.
        data = self._validate_kio_vat_payment()
        company = data["company"]
        Payment = self.env["account.payment"]

        existing = Payment.search([
            ("kio_vat_idempotency_key", "=", data["key"]),
            ("company_id", "=", company.id),
        ], limit=1)
        if existing:
            return self._kio_vat_success_action(existing)

        payment = Payment.create({
            "payment_type": "outbound",
            "partner_type": "supplier",
            "partner_id": False,
            "amount": data["amount"],
            "currency_id": data["currency"].id,
            "date": data["date"],
            "journal_id": data["journal"].id,
            "payment_method_line_id": data["method"].id,
            "company_id": company.id,
            "ref": self.communication or f"VAT Payment - {data['branch'].name}",
            "is_kio_vat_payment": True,
            "kio_vat_branch_id": data["branch"].id,
            "kio_vat_idempotency_key": data["key"],
            "destination_account_id": data["vat_account"].id,
        })
        payment.action_post()

        # Verify the single, balanced VAT settlement entry before committing.
        move = payment.move_id
        debit_lines = move.line_ids.filtered(
            lambda line: line.account_id == data["vat_account"]
        )
        credit_lines = move.line_ids.filtered(
            lambda line: line.account_id == data["journal"].default_account_id
        )
        if (move.state != "posted" or len(debit_lines) != 1 or len(credit_lines) != 1
                or len(move.line_ids) != 2
                or company.currency_id.compare_amounts(
                    debit_lines[:1].balance, data["amount"]
                ) != 0
                or company.currency_id.compare_amounts(
                    credit_lines[:1].balance, -data["amount"]
                ) != 0):
            raise UserError(_(
                "The VAT payment did not produce the expected balanced "
                "settlement entry. Nothing was recorded."
            ))
        return self._kio_vat_success_action(payment)

    def action_create_payments(self):
        if self.env.context.get("kio_vat_payment_dashboard"):
            return self._create_kio_vat_payment()
        return super().action_create_payments()
