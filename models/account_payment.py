# -*- coding: utf-8 -*-
# KIO VAT Submission Extension — Phases 1 and 3
#
# VAT payment identification, server-side guards and posting protection on the
# standard ``account.payment`` model. Payment *creation* lives in the dedicated
# VAT workflow in ``account_payment_register.py``; this file never creates a
# standalone journal entry.

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression


class AccountPayment(models.Model):
    _inherit = "account.payment"

    is_kio_vat_payment = fields.Boolean(
        string="KIO VAT Payment",
        default=False,
        copy=False,
        help="Technical marker for payments belonging to the KIO VAT payment workflow.",
    )
    kio_vat_branch_id = fields.Many2one(
        "kio.cash.deposit.branch",
        string="Branch",
        copy=False,
        check_company=True,
        ondelete="restrict",
        help="Cash Deposit branch this VAT payment is attributed to.",
    )
    # Deterministic key computed from the branch, amount, date and the freshly
    # recomputed outstanding VAT. It makes a repeated Create Payment request
    # idempotent even when the client does not supply a key.
    kio_vat_idempotency_key = fields.Char(copy=False, index=True)

    _sql_constraints = [
        (
            "kio_vat_idempotency_key_uniq",
            "unique(kio_vat_idempotency_key)",
            "This VAT payment has already been created.",
        ),
    ]

    # Accounting fields that must stay frozen once a VAT payment is posted.
    _KIO_VAT_POSTED_LOCKED_FIELDS = frozenset({
        "is_kio_vat_payment", "kio_vat_branch_id", "kio_vat_idempotency_key",
        "amount", "currency_id", "date", "journal_id", "payment_method_line_id",
        "destination_account_id", "company_id", "partner_id", "partner_type",
        "payment_type", "move_id",
    })

    # ------------------------------------------------------------------
    # VAT payment access helpers
    # ------------------------------------------------------------------
    @api.model
    def _kio_vat_payable_account(self, company=None, required=False):
        """Configured VAT Payable account (200904) for ``company``."""
        company = company or self.env.company
        account = self.env["kio.cash.deposit"]._get_vat_payable_account(company)
        if required and (not account or account.deprecated
                         or account.company_id != company):
            raise UserError(_(
                "The VAT Payable account (200904) is not properly configured "
                "for this company. No VAT payment was created."
            ))
        return account

    @api.model
    def _kio_vat_authorized_branches(self, company=None):
        """Branches the current user may use for VAT payments in ``company``.

        Delegates to the same authorization service used by the VAT dashboard
        and the Register Payment wizard. That service also enforces Cash Deposit
        group membership, so plain users can never reach this point.
        """
        company = company or self.env.company
        return self.env["kio.cash.deposit"]._get_authorized_vat_branches().filtered(
            lambda branch: branch.company_id == company
        )

    @api.constrains("is_kio_vat_payment", "kio_vat_branch_id", "company_id")
    def _check_kio_vat_payment_marking(self):
        """Only authorized users may mark a payment as a KIO VAT payment.

        Runs on create/write of the marker (and of its branch/company), which
        prevents an ordinary customer/vendor payment from being relabelled by
        a user without Cash Deposit access, or with a branch outside their
        mandate / company.
        """
        for payment in self.filtered("is_kio_vat_payment"):
            company = payment.company_id or self.env.company
            # Calling this first is deliberate: it raises AccessError for a
            # user without Cash Deposit access even when no branch is given.
            authorized = self._kio_vat_authorized_branches(company=company)
            branch = payment.kio_vat_branch_id
            if not branch:
                continue
            # Read the company with sudo: an unauthorized branch is invisible
            # to a manager because of the Cash Deposit branch record rules, and
            # this guard must still report a deterministic validation error.
            if branch.sudo().company_id != company:
                raise ValidationError(_(
                    "The branch of a VAT payment must belong to the payment company."
                ))
            if branch not in authorized:
                raise ValidationError(_(
                    "You are not authorized to use this branch for VAT payments."
                ))

    @api.depends(
        "journal_id", "partner_id", "partner_type", "is_internal_transfer",
        "destination_journal_id", "is_kio_vat_payment", "company_id",
    )
    def _compute_destination_account_id(self):
        """Debit the configured VAT Payable account for VAT payments.

        Standard Odoo uses a receivable/payable account here; the field domain
        is UI-only, so an explicit programmatic value is safe for a payment
        move (``move_type = entry``). Normal payments keep standard behaviour.
        """
        super()._compute_destination_account_id()
        for payment in self.filtered("is_kio_vat_payment"):
            account = self._kio_vat_payable_account(
                payment.company_id or self.env.company
            )
            if account:
                payment.destination_account_id = account

    def write(self, vals):
        # Odoo synchronizes a posted payment from its move with sudo(); that
        # internal write must not be confused with a user modification.
        if not self.env.su and not self.env.context.get("kio_vat_payment_system_update"):
            posted_vat = self.filtered(
                lambda p: p.is_kio_vat_payment and p.state == "posted"
            )
            if posted_vat and self._KIO_VAT_POSTED_LOCKED_FIELDS.intersection(vals):
                raise UserError(_(
                    "Posted VAT payments cannot be modified. A safe reversal "
                    "workflow is not available yet."
                ))
        return super().write(vals)

    def unlink(self):
        if any(p.is_kio_vat_payment and p.state == "posted" for p in self):
            raise UserError(_(
                "Posted VAT payments cannot be deleted. A safe reversal "
                "workflow is not available yet."
            ))
        return super().unlink()

    def action_cancel(self):
        if any(p.is_kio_vat_payment and p.state == "posted" for p in self):
            raise UserError(_(
                "Posted VAT payments cannot be cancelled. A safe reversal "
                "workflow is not available yet."
            ))
        return super().action_cancel()

    def action_draft(self):
        if any(p.is_kio_vat_payment and p.state == "posted" for p in self):
            raise UserError(_(
                "Posted VAT payments cannot be reset to draft. A safe reversal "
                "workflow is not available yet."
            ))
        return super().action_draft()

    # ------------------------------------------------------------------
    # Payment History (server-side scoping)
    # ------------------------------------------------------------------
    @api.model
    def _kio_vat_payment_history_domain(self):
        """Extra domain applied only to the VAT Payment History action.

        The browser cannot be trusted to keep the action domain intact, so the
        branch/company restrictions are re-applied server-side whenever the
        explicit history context flag is present.
        """
        self.env["kio.cash.deposit"]._check_cash_deposit_access()
        domain = [
            ("is_kio_vat_payment", "=", True),
            ("company_id", "in", self.env.companies.ids),
        ]
        # Admins see all VAT payments of their allowed companies (the core
        # account.payment company rule applies as well). Managers are limited
        # to the branches assigned to them.
        if not self.env.user.has_group("kio_cash_deposit.group_kio_cash_deposit_admin"):
            allowed_branches = self._kio_vat_authorized_branches(
                company=self.env.company
            )
            domain.append(("kio_vat_branch_id", "in", allowed_branches.ids))
        return domain

    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        # Covers search(), search_read(), search_count() and read_group(),
        # i.e. every list/search/get entry point used by the standard views.
        if self.env.context.get("kio_vat_payment_history"):
            domain = expression.AND([domain, self._kio_vat_payment_history_domain()])
        return super()._search(
            domain,
            offset=offset,
            limit=limit,
            order=order,
            access_rights_uid=access_rights_uid,
        )
