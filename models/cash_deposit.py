# -*- coding: utf-8 -*-
# KIO VAT Submission Extension
#
# Extends the Cash Deposit dashboard (kio_cash_deposit) with two extra KPI
# cards rendered immediately before the existing "Current Cash Balance" card:
#
#   1. Total Sales Amount  — sales excluding VAT attributable to remaining cash
#   2. Total VAT           — VAT attributable to remaining cash
#
# The KPIs use a documented FIFO cash allocation policy:
#
#   * POS sales receipts (cash-paid portion of closed-session POS orders) are
#     cash inflows, ordered chronologically by date_order.
#   * Non-sales cash outflows (approved cash deposits, cash expenses,
#     transfers, refunds paid in cash) are cash outflows, ordered chronologically
#     by move date.
#   * Opening balances are non-sales cash inflows, ordered chronologically.
#   * FIFO rule: withdrawals deduct from the oldest cash inflows first.
#     Opening balance is consumed before sales receipts on the same date.
#   * Withdrawals consume the oldest available inflows. The sales and VAT
#     attached to each POS cash receipt are reduced by that receipt's own
#     remaining-cash fraction; POS tax data is never replaced by a fixed rate.
#   * Negative POS payments are cash withdrawals and consume sales layers once.
#
# Opening balances and non-sales cash movements are never treated as sales.
# POS orders are counted once through their own payments; linked invoices are
# never read, so no double counting occurs.
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
    def _get_kpi_coverage_account_ids(self, cash_journals):
        """Return default account IDs for the coverage cash journals."""
        company = self.env.company
        return cash_journals.mapped("default_account_id").filtered(
            lambda a: a.company_id == company
        ).ids

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
    def _get_kpi_inflows_outflows(self, company, account_ids, cash_journals,
                                   end_of_today):
        """Classify movements on coverage cash accounts into inflows/outflows.

        Returns:
            sales_inflows: list of dicts with date, amount, sales_ex_vat, vat
                (positive cash-paid POS receipts only)
            opening_inflows: list of dicts with date, amount (non-sales)
            outflows: list of dicts with date, amount (cash refunds plus
                posted cash withdrawals, deposits, expenses and transfers)
            POS accounting moves are excluded from opening/outflow lines,
            because POS payments and refunds are represented by pos.payment.
        """
        orders = self._get_eligible_pos_orders(company, end_of_today)
        cash_paid_by_order = self._sum_cash_payments_by_order(orders, cash_journals)

        sales_inflows = []
        refund_outflows = []

        for order in orders:
            cash_paid = cash_paid_by_order.get(order.id, 0.0)
            if not cash_paid:
                continue
            order_total = order.amount_total or 0.0
            if order_total == 0.0:
                continue
            portion = cash_paid / order_total
            order_tax = order.amount_tax or 0.0
            sales_ex_vat = portion * (order_total - order_tax)
            vat = portion * order_tax

            if cash_paid > 0 and order_total > 0:
                sales_inflows.append({
                    "date": order.date_order.date(),
                    "amount": cash_paid,
                    "sales_ex_vat": sales_ex_vat,
                    "vat": vat,
                    "id": order.id,
                })
            elif cash_paid < 0:
                # A POS refund is a cash withdrawal from the oldest remaining
                # sales layers. Do not also subtract its negative order totals:
                # the refund's cash amount removes the corresponding layer,
                # including that layer's actual POS tax data.
                refund_outflows.append({
                    "date": order.date_order.date(),
                    "amount": abs(cash_paid),
                    "id": order.id,
                })

        # Identify POS sales receipt move IDs via statement lines linked to
        # POS sessions. These moves are debits to coverage cash accounts from
        # cash journals and represent sales receipts, not opening balances.
        statement_lines = self.env["account.bank.statement.line"].sudo().search([
            ("pos_session_id", "!=", False),
            ("company_id", "=", company.id),
        ])
        pos_receipt_move_ids = statement_lines.mapped("move_id").ids

        # Opening balances: debits to coverage accounts that are NOT POS
        # sales receipt moves. These are non-sales cash inflows.
        opening_lines = self.env["account.move.line"].sudo().search([
            ("account_id", "in", account_ids),
            ("company_id", "=", company.id),
            ("display_type", "not in", ("line_section", "line_note")),
            ("parent_state", "!=", "cancel"),
            ("debit", ">", 0),
            ("date", "<=", end_of_today.date()),
            ("move_id", "not in", pos_receipt_move_ids),
        ], order="date, id")

        opening_inflows = [
            {"date": line.date, "amount": line.debit, "id": line.id}
            for line in opening_lines
        ]

        # Outflows: all non-POS credits to coverage cash accounts. Restricting
        # by journal would miss expenses recorded in a cash journal and cash
        # transfers; POS moves are excluded by move ID to avoid counting them
        # twice alongside pos.payment records.
        outflow_lines = self.env["account.move.line"].sudo().search([
            ("account_id", "in", account_ids),
            ("company_id", "=", company.id),
            ("display_type", "not in", ("line_section", "line_note")),
            ("parent_state", "!=", "cancel"),
            ("credit", ">", 0),
            ("date", "<=", end_of_today.date()),
            ("move_id", "not in", pos_receipt_move_ids),
        ], order="date, id")

        outflows = refund_outflows + [
            {"date": line.date, "amount": line.credit, "id": line.id}
            for line in outflow_lines
        ]

        return {
            "sales_inflows": sales_inflows,
            "opening_inflows": opening_inflows,
            "outflows": outflows,
        }

    @api.model
    def _apply_fifo_allocation(self, sales_inflows, opening_inflows, outflows):
        """Apply FIFO cash allocation.

        Withdrawals deduct from the oldest inflows first. Opening balance has
        priority over sales receipts on the same date.

        Returns:
            remaining_sales_cash: cash remaining from POS receipts
            remaining sales cash, attributable sales excluding VAT, and VAT
        """
        # Build a chronological event queue. Opening balances and POS sales
        # are available before same-day withdrawals; same-day opening cash is
        # consumed before that day's sales.
        events = []
        for inf in opening_inflows:
            events.append({
                "date": inf["date"],
                "amount": inf["amount"],
                "priority": 0,  # opening balance consumed first on same date
                "type": "opening",
                "id": inf.get("id", 0),
            })
        for inf in sales_inflows:
            events.append({
                "date": inf["date"],
                "amount": inf["amount"],
                "priority": 1,  # sales receipts
                "type": "sales",
                "sales_ex_vat": inf["sales_ex_vat"],
                "vat": inf["vat"],
                "id": inf.get("id", 0),
            })
        for outflow in outflows:
            events.append({
                "date": outflow["date"],
                "amount": outflow["amount"],
                "priority": 2,
                "type": "outflow",
                "id": outflow.get("id", 0),
            })

        events.sort(key=lambda event: (
            event["date"], event["priority"], event["id"]
        ))

        inflows = []
        for event in events:
            if event["type"] in ("opening", "sales"):
                event["remaining"] = event["amount"]
                inflows.append(event)
                continue

            withdrawal = event["amount"]
            for inflow in inflows:
                if withdrawal <= 0:
                    break
                if inflow["remaining"] > 0:
                    consumed = min(inflow["remaining"], withdrawal)
                    inflow["remaining"] -= consumed
                    withdrawal -= consumed

        remaining_sales_cash = 0.0
        total_sales = 0.0
        total_vat = 0.0
        for inflow in inflows:
            if inflow["type"] != "sales" or inflow["amount"] <= 0:
                continue
            remaining = max(inflow["remaining"], 0.0)
            ratio = remaining / inflow["amount"]
            remaining_sales_cash += remaining
            total_sales += inflow["sales_ex_vat"] * ratio
            total_vat += inflow["vat"] * ratio

        return remaining_sales_cash, total_sales, total_vat

    @api.model
    def _get_sales_and_vat_kpis(self, selected_branches=None, is_admin=False):
        """Return the (total_sales, total_vat) dashboard KPI pair.

        Uses FIFO cash allocation: positive POS receipts create sales layers,
        opening balances create non-sales layers, and POS refunds plus other
        cash credits consume the oldest available layer. Each layer retains
        its own Odoo-computed sales and VAT amounts in proportion to its
        remaining cash.

        Opening balances and non-sales cash movements are never treated as
        sales. POS orders are counted once through their own payments; linked
        invoices are never read, so no double counting occurs.
        """
        company = self.env.company
        cash_journals = self._get_kpi_cash_journals(
            selected_branches=selected_branches,
            is_admin=is_admin,
        )
        if not cash_journals:
            return 0.0, 0.0

        account_ids = self._get_kpi_coverage_account_ids(cash_journals)
        if not account_ids:
            return 0.0, 0.0

        end_of_today = self._get_kpi_end_of_today()

        flows = self._get_kpi_inflows_outflows(
            company, account_ids, cash_journals, end_of_today
        )

        _, total_sales, total_vat = self._apply_fifo_allocation(
            flows["sales_inflows"],
            flows["opening_inflows"],
            flows["outflows"],
        )

        return total_sales, total_vat
