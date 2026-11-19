# -*- coding: utf-8 -*-
# KIO VAT Submission Extension — Phase 1
#
# VAT payment identification on the standard ``account.payment`` model.
# This module only *identifies* VAT payments and guards that identification;
# it never creates or posts payments and never touches accounting entries.

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
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

    # ------------------------------------------------------------------
    # VAT payment access helpers
    # ------------------------------------------------------------------
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
