# -*- coding: utf-8 -*-
# KIO VAT Submission Extension
#
# Extends the Cash Deposit dashboard (kio_cash_deposit) with two extra KPI
# cards rendered immediately before the existing "Current Cash Balance" card:
#
#   1. Total Sales Amount  — sales excluding VAT (net of refunds)
#   2. Total VAT           — VAT collected from those same sales
#
# The KPIs are computed from actual POS sales transactions that are
# associated with the cash balance — i.e. closed-session POS orders paying
# into the same cash journals that back the "Current Cash Balance" card
# (see _get_cash_journal_balance in kio_cash_deposit):
#
#   * eligible = pos.order with a closed session, state in
#     ('paid', 'done', 'invoiced'), same company, and date_order up to the
#     end of the current date in the company timezone. Nothing earlier is
#     cut off — all history through today is included.
#   * for each order, only the cash-paid portion is counted: the payments
#     whose payment method points to one of the coverage cash journals.
#   * mixed payments contribute pro-rata:
#       cash_portion = cash_paid / order.amount_total
#       sales       += cash_portion * (order.amount_total - order.amount_tax)
#       vat         += cash_portion *  order.amount_tax
#     amount_total / amount_tax are Odoo's own totals, so discounted lines,
#     multiple VAT rates, tax-exempt products and refunds are handled
#     transparently. Refund orders (negative total/payments) subtract.
#   * bank, card, mobile banking (e.g. Bkash/Nagad), pay-later and other
#     non-cash payments are excluded — they are never part of the cash
#     balance population.
#   * opening balances, cash in/out, transfers, deposits and expenses are
#     not POS order payments, so they cannot contribute either.
#   * POS orders are counted once through their own payments. Linked
#     invoices (order.account_move) are never read for this calculation, so
#     an invoiced POS order cannot be double counted. Current Cash Balance
#     is deliberately left independent and is not assumed to equal
#     Sales + VAT.
#
# Branch filter semantics mirror the existing dashboard behaviour:
#   * admin with a branch selection  -> only those branches' cash journals,
#   * admin without a selection      -> all company cash journals,
#   * manager without branches       -> zeros (same as Current Cash Balance).
# Company scoping always applies. Amounts are net (refunds subtract) and in
# company currency.

import pytz
from datetime import datetime, time

from odoo import api, fields, models


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
    def _get_kpi_tz_name(self):
        # The company record carries no own timezone in v17, so fall back to
        # the user's timezone (Asia/Dhaka in this installation), then UTC.
        return (
            self.env.company.partner_id.tz
            or self.env.user.tz
            or "UTC"
        )

    @api.model
    def _get_kpi_end_of_today(self):
        tz = pytz.timezone(self._get_kpi_tz_name())
        now_utc = fields.Datetime.now()
        now_local = pytz.utc.localize(now_utc).astimezone(tz)
        end_local = tz.localize(datetime.combine(now_local.date(), time.max))
        return end_local.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _get_kpi_cash_journals(self, selected_branches=None, is_admin=False):
        """Return the coverage cash journals for the KPI calculation."""
        company = self.env.company
        selected_branches = selected_branches if selected_branches is not None else (
            self.env["kio.cash.deposit.branch"]
        )
        if selected_branches:
            journals = selected_branches.mapped("branch_cash_account_id")
            return journals.filtered(
                lambda journal: journal.type == "cash"
                and journal.company_id == company
            )
        if not is_admin:
            # Mirrors _get_cash_journal_balance: no branch, no figures.
            return self.env["account.journal"]
        return self.env["account.journal"].sudo().search([
            ("company_id", "=", company.id),
            ("type", "=", "cash"),
        ])

    @api.model
    def _get_eligible_pos_orders(self, company, end_of_today):
        return self.env["pos.order"].sudo().search([
            ("company_id", "=", company.id),
            ("state", "in", ("paid", "done", "invoiced")),
            ("session_id.state", "=", "closed"),
            ("date_order", "<=", end_of_today),
        ])

    @api.model
    def _sum_cash_payments_by_order(self, orders, cash_journals):
        """Map order_id -> cash-paid amount for the given cash journals."""
        if not orders:
            return {}
        grouped = self.env["pos.payment"].sudo().read_group(
            [
                ("pos_order_id", "in", orders.ids),
                ("payment_method_id.journal_id", "in", cash_journals.ids),
            ],
            ["amount:sum"],
            ["pos_order_id"],
        )
        cash_paid = {}
        for row in grouped:
            order_key = row.get("pos_order_id")
            order_id = (
                order_key[0]
                if isinstance(order_key, (list, tuple))
                else order_key
            )
            if order_id:
                cash_paid[order_id] = row.get("amount", 0.0) or 0.0
        return cash_paid

    @api.model
    def _get_sales_and_vat_kpis(self, selected_branches=None, is_admin=False):
        """Return the (total_sales, total_vat) dashboard KPI pair.

        total_sales is the tax-excluded sales amount and total_vat is the
        VAT collected on the same sales, both net of refunds, in company
        currency, and restricted to the cash-paid portion of closed-session
        POS orders through today.
        """
        company = self.env.company
        cash_journals = self._get_kpi_cash_journals(
            selected_branches=selected_branches,
            is_admin=is_admin,
        )
        if not cash_journals:
            return 0.0, 0.0
        orders = self._get_eligible_pos_orders(
            company, self._get_kpi_end_of_today()
        )
        cash_paid_by_order = self._sum_cash_payments_by_order(
            orders, cash_journals
        )

        total_sales = 0.0
        total_vat = 0.0
        for order in orders:
            cash_paid = cash_paid_by_order.get(order.id, 0.0)
            if not cash_paid:
                # No cash-paid portion in this order -> contributes nothing.
                continue
            order_total = order.amount_total or 0.0
            if order_total == 0.0:
                # Zero-total order (e.g. fully settled with change only) —
                # there is no meaningful portion to allocate.
                continue
            portion = cash_paid / order_total
            order_tax = order.amount_tax or 0.0
            total_sales += portion * (order_total - order_tax)
            total_vat += portion * order_tax
        return total_sales, total_vat
