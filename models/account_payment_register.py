# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
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
                "can_edit_wizard": True,
                "can_group_payments": False,
                "group_payment": False,
                "communication": False,
                "line_ids": [(6, 0, [])],
            })
            return res
        return super().default_get(fields_list)

    @api.onchange("kio_vat_branch_id", "currency_id", "payment_date")
    def _onchange_kio_vat_branch_id(self):
        for wizard in self:
            if not wizard._is_vat_payment_dashboard_mode():
                continue
            branch = wizard.kio_vat_branch_id
            if not branch:
                wizard.amount = 0.0
                continue
            authorized = wizard._get_vat_payment_authorized_branches(
                company=wizard.company_id or self.env.company
            )
            if branch not in authorized:
                wizard.kio_vat_branch_id = False
                wizard.amount = 0.0
                return {
                    "warning": {
                        "title": _("Unauthorized Branch"),
                        "message": _("You are not authorized to select this branch."),
                    }
                }
            wizard.amount = self._get_vat_branch_outstanding_amount(
                branch,
                wizard.company_id or self.env.company,
                wizard.currency_id or (wizard.company_id or self.env.company).currency_id,
                wizard.payment_date,
            )

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

    @api.depends('can_edit_wizard')
    def _compute_communication(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.communication = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_communication()

    @api.depends('can_edit_wizard')
    def _compute_group_payment(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            wizard.group_payment = False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_group_payment()

    @api.depends('payment_type', 'company_id', 'can_edit_wizard')
    def _compute_available_journal_ids(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            company = wizard.company_id or self.env.company
            domain = [
                *self.env["account.journal"]._check_company_domain(company),
                ("type", "in", ("bank", "cash")),
            ]
            if wizard.payment_type == "inbound":
                domain.append(("inbound_payment_method_line_ids", "!=", False))
            else:
                domain.append(("outbound_payment_method_line_ids", "!=", False))
            journals = self.env["account.journal"].search(domain)
            if not journals:
                journals = self.env["account.journal"].search([
                    *self.env["account.journal"]._check_company_domain(company),
                    ("type", "in", ("bank", "cash")),
                ])
            wizard.available_journal_ids = [(6, 0, journals.ids)]
        super(AccountPaymentRegister, self - vat_dashboard)._compute_available_journal_ids()

    @api.depends('available_journal_ids')
    def _compute_journal_id(self):
        vat_dashboard = self.filtered(lambda w: w._is_vat_payment_dashboard_mode())
        for wizard in vat_dashboard:
            available = wizard.available_journal_ids
            wizard.journal_id = available[:1].id if available else False
        super(AccountPaymentRegister, self - vat_dashboard)._compute_journal_id()

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

    def action_create_payments(self):
        if self.env.context.get("kio_vat_payment_dashboard"):
            raise UserError(_(
                "VAT payment confirmation is not available yet. "
                "The VAT payment accounting workflow will be implemented in a future phase."
            ))
        return super().action_create_payments()
