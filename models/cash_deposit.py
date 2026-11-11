# -*- coding: utf-8 -*-
# KIO VAT Submission Extension
#
# Extends the Cash Deposit dashboard (kio_cash_deposit) with two extra KPI
# cards rendered immediately before the existing "Current Cash Balance" card:
#
#   1. Total Sales Amount  — sales excluding VAT (net of refunds)
#   2. Total VAT           — VAT collected from those sales
#
# Sales figures are computed from *posted* accounting records only:
#
#   * Posted customer invoices / refunds (move_type out_invoice / out_refund).
#     This covers regular invoicing and invoiced POS orders (their revenue
#     lives on the out_invoice/out_refund, never on the POS session entry).
#   * Posted POS session journal entries (account.move linked to a
#     pos.session through move_id). Those entries only contain NON-invoiced
#     orders, because invoiced orders are skipped when the session creates
#     its accounting entry.
#
# Because of that split, an invoiced POS order is counted exactly once (in its
# invoice) and a non-invoiced POS order is counted exactly once (in its
# session entry) — POS orders and their invoices/journal entries are never
# double counted.
#
# Branch filter semantics mirror the existing dashboard behaviour:
#   * admin with a branch selection  -> only the selected branches
#     (POS session entries + POS-invoiced invoices of those sessions; non-POS
#     invoices cannot be attributed to a branch and are excluded),
#   * admin without a selection      -> company-wide,
#   * manager without branches       -> zeros (same as Current Cash Balance).
# Company scoping always applies. Amounts are net (refunds subtract) and in
# company currency.

from odoo import api, models


class KioCashDeposit(models.Model):
    _inherit = "kio.cash.deposit"

    @api.model
    def get_cash_deposit_dashboard_data(self, search_query=False, offset=0,
                                        limit=5,
                                        order="date desc, transaction_id asc, id desc",
                                        branch_ids=False):
        # Access rights were already checked by the base implementation
        # (_check_cash_deposit_access) before any data is returned.
        data = super().get_cash_deposit_dashboard_data(
            search_query=search_query,
            offset=offset,
            limit=limit,
            order=order,
            branch_ids=branch_ids,
        )
        selected_branches = self._get_effective_dashboard_branches(branch_ids)
        total_sales, total_vat = self._get_sales_and_vat_kpis(
            selected_branches=selected_branches,
            is_admin=self._is_cash_deposit_admin(),
        )
        data["total_sales"] = total_sales
        data["total_vat"] = total_vat
        return data

    @api.model
    def _get_sales_and_vat_kpis(self, selected_branches=None, is_admin=False):
        """Return the (total_sales, total_vat) dashboard KPI pair.

        total_sales is the untaxed sales amount (VAT excluded) and total_vat
        is the VAT collected on sales, both net of refunds, in company
        currency, computed from posted accounting records only.
        """
        company = self.env.company
        selected_branches = selected_branches if selected_branches is not None else (
            self.env["kio.cash.deposit.branch"]
        )
        if selected_branches:
            branch_journals = selected_branches.mapped("branch_cash_account_id")
            return self._compute_sales_and_vat(company, branch_journals=branch_journals)
        if not is_admin:
            # Mirrors _get_cash_journal_balance: no branch, no figures.
            return 0.0, 0.0
        return self._compute_sales_and_vat(company)

    @api.model
    def _compute_sales_and_vat(self, company, branch_journals=None):
        move_ids = self._get_sales_kpi_move_ids(company, branch_journals=branch_journals)
        if not move_ids:
            return 0.0, 0.0
        line_model = self.env["account.move.line"].sudo()
        base_domain = [
            ("move_id", "in", move_ids),
            ("parent_state", "=", "posted"),
            ("display_type", "not in", ("line_section", "line_note")),
        ]
        sales = self._sum_signed_move_line_balance(
            base_domain
            + [
                ("tax_repartition_line_id", "=", False),
                ("account_id.account_type", "in", ("income", "income_other")),
            ]
        )
        vat = self._sum_signed_move_line_balance(
            base_domain + [("tax_repartition_line_id", "!=", False)]
        )
        return sales, vat

    @api.model
    def _sum_signed_move_line_balance(self, domain):
        # Invoice/POS revenue and VAT lines carry negative (credit) balances,
        # refunds carry positive (debit) balances, so negating the raw sum
        # yields the net signed amount.
        result = self.env["account.move.line"].sudo().read_group(
            domain, ["balance:sum"], []
        )
        balance = result[0].get("balance", 0.0) if result else 0.0
        return -(balance or 0.0)

    @api.model
    def _get_sales_kpi_move_ids(self, company, branch_journals=None):
        """Posted moves that carry sales revenue/VAT for the KPIs."""
        session_domain = [("company_id", "=", company.id)]
        if branch_journals:
            session_domain.append(("cash_journal_id", "in", branch_journals.ids))
        sessions = self.env["pos.session"].sudo().search(session_domain)
        # Session closing entries carry revenue/VAT of NON-invoiced orders.
        session_moves = sessions.filtered("move_id").mapped("move_id").filtered(
            lambda move: move.state == "posted"
        )

        invoice_domain = [
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("move_type", "in", ("out_invoice", "out_refund")),
        ]
        if branch_journals:
            # Branch filter: only POS-invoiced invoices/refunds that belong to
            # the selected branches are attributable to those branches.
            pos_move_ids = [
                order.account_move.id
                for order in sessions.mapped("order_ids")
                if order.account_move
            ]
            invoice_domain.append(("id", "in", pos_move_ids or [0]))
        invoices = self.env["account.move"].sudo().search(invoice_domain)
        # Session entries exclude invoiced orders, so an invoiced POS order is
        # counted once through its invoice and a non-invoiced POS order once
        # through its session entry — never double counted.
        return list((session_moves | invoices).ids)
