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

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


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

    # -------------------------------------------------------------------------
    # VAT Payment Dashboard (accounting VAT liability / settlement, branch-wise)
    # -------------------------------------------------------------------------

    @api.model
    def _get_vat_payable_account(self, company=None):
        """Return the company's VAT Payable account (code 200904) if configured."""
        company = company or self.env.company
        Account = self.env["account.account"].sudo()
        domain = [
            ("company_id", "=", company.id),
            ("code", "=like", "200904%"),
        ]
        accounts = Account.search(domain, order="code, id")
        if not accounts:
            return Account.browse()
        # Prefer the exact code when present, otherwise keep deterministic order.
        exact = accounts.filtered(lambda a: (a.code or "").strip() == "200904")
        return exact[:1] if exact else accounts[:1]

    @api.model
    def _check_vat_payment_access(self):
        if not (
            self.env.user.has_group("base.group_system")
            or self.env.user.has_group(
                "kio_branch_inventory.group_branch_inventory_user"
            )
        ):
            raise AccessError(_("You do not have access to VAT Payment."))

    @api.model
    def _get_vat_payment_branch_options(self):
        self._check_vat_payment_access()
        branches = self._get_authorized_vat_branches()
        is_admin = self.env.user.has_group("base.group_system")
        return {
            "can_filter": is_admin,
            "default_branch_ids": self._get_head_office_branch().ids if is_admin else branches.ids,
            "branches": [
                {"id": branch.id, "name": branch.display_name,
                 "branch_cash_account_id": branch.branch_cash_account_id.id}
                for branch in branches
            ],
        }

    @api.model
    def _get_authorized_vat_branches(self, branch_id=False):
        """Authorized cash-deposit branches for the current user/company.

        Mirrors cash-deposit dashboard access semantics:
        - admin: all active branches in current company (optionally filtered),
        - manager: assigned active branches in current company only.

        The VAT Payment dashboard and wizard gate on _check_vat_payment_access
        (administrators and Branch Inventory Users). The Payment History search
        is also used by Cash Deposit Managers, so this service accepts either
        VAT Payment access or Cash Deposit access when deriving the authorized
        branch universe.
        """
        # VAT Payment (administrator / Branch Inventory User) or Cash Deposit
        # (manager) access is required to derive the authorized branch universe.
        try:
            self._check_vat_payment_access()
        except AccessError:
            self._check_cash_deposit_access()
        company = self.env.company
        Branch = self.env["kio.cash.deposit.branch"].sudo()
        company_domain = [("active", "=", True), ("company_id", "=", company.id)]
        if (self.env.user.has_group("base.group_system")
                or self._is_cash_deposit_admin()):
            branches = Branch.search(company_domain, order="sequence, name, id")
        else:
            branches = self._get_user_cash_deposit_branches().sudo().filtered(
                lambda b: b.active and b.company_id == company
            ).sorted(key=lambda b: (b.sequence or 0, b.name or "", b.id))

        if branch_id:
            try:
                branch_id = int(branch_id)
            except (TypeError, ValueError):
                branch_id = False
            if branch_id:
                branches = branches.filtered(lambda b: b.id == branch_id)
        return branches

    @api.model
    def _get_pos_config_branch_map(self, company, branches):
        """Map pos.config -> attributable kio.cash.deposit.branch.

        Attribution uses existing payment-method/journal relations:
        a config is attributed to a branch when the branch cash journal is one
        of the config's payment method journals and that resolves to exactly one
        authorized branch. Ambiguous or missing mappings return no branch.
        """
        branches = branches.sudo()
        branch_by_cash_journal = {}
        for branch in branches:
            journal = branch.branch_cash_account_id
            if journal:
                # Keep a deterministic single mapping per cash journal.
                branch_by_cash_journal.setdefault(journal.id, branch)
        configs = self.env["pos.config"].sudo().search([
            ("company_id", "=", company.id),
        ])
        mapping = {}
        for config in configs:
            journal_ids = set(config.payment_method_ids.sudo().mapped("journal_id").ids)
            matched_branches = {
                branch_by_cash_journal[jid].id
                for jid in journal_ids
                if jid in branch_by_cash_journal
            }
            if len(matched_branches) == 1:
                mapping[config.id] = next(iter(matched_branches))
        return mapping

    @api.model
    def _kio_vat_settlement_payment(self, move):
        """Return the posted VAT payment linked to ``move``, if any."""
        if not move:
            return self.env["account.payment"]
        move_sudo = move.sudo()
        for field_name in ("payment_id", "payment_ids"):
            if field_name in move_sudo._fields:
                payments = move_sudo[field_name]
                for payment in payments.sudo():
                    if (payment._name == "account.payment"
                            and "is_kio_vat_payment" in payment._fields
                            and payment.is_kio_vat_payment
                            and payment.state == "posted"):
                        return payment
        return self.env["account.payment"]

    @api.model
    def _is_vat_settlement_move(self, move):
        """Only posted, explicitly marked VAT settlement payments qualify.

        Ordinary refunds/debits of the VAT account never carry the marker, so
        they are never counted as payments.
        """
        return bool(self._kio_vat_settlement_payment(move))

    @api.model
    def _resolve_vat_move_branch_id(self, move, branch_by_config_id):
        """Resolve a VAT-related move to a branch id or False (unallocated)."""
        if not move:
            return False
        move_sudo = move.sudo()
        # A posted VAT settlement is attributed to the branch recorded on the
        # payment itself; POS attribution is only a fallback for tax moves.
        payment = self._kio_vat_settlement_payment(move)
        if payment and payment.kio_vat_branch_id:
            return payment.kio_vat_branch_id.id
        config_ids = set()

        # POS closing entries: pos.session.move_id -> config_id
        session = self.env["pos.session"].sudo().search([
            ("move_id", "=", move_sudo.id),
            ("company_id", "=", move_sudo.company_id.id),
        ], limit=1)
        if session and session.config_id:
            config_ids.add(session.config_id.id)

        # Invoiced POS orders linked to the move.
        if "pos_order_ids" in move_sudo._fields:
            config_ids.update(move_sudo.pos_order_ids.sudo().mapped("config_id").ids)

        if "account_move" in self.env["pos.order"]._fields:
            orders = self.env["pos.order"].sudo().search([
                ("company_id", "=", move_sudo.company_id.id),
                ("account_move", "=", move_sudo.id),
            ])
            config_ids.update(orders.mapped("config_id").ids)

        branch_ids = {
            branch_by_config_id[cid]
            for cid in config_ids
            if cid in branch_by_config_id
        }
        if len(branch_ids) == 1:
            return next(iter(branch_ids))
        return False

    @api.model
    def _get_vat_branch_summary_data(
        self, branch_id=False, date_from=False, date_to=False, search=False
    ):
        self._check_vat_payment_access()
        company = self.env.company
        currency = company.currency_id
        vat_account = self._get_vat_payable_account(company)
        authorized_branches = self._get_authorized_vat_branches()
        selected_branches = self._get_authorized_vat_branches(branch_id=branch_id)
        is_single_branch = bool(branch_id)
        branch_by_config_id = self._get_pos_config_branch_map(company, authorized_branches)

        branch_map = {
            b.id: {
                "id": b.id,
                "name": b.display_name,
                "payable": 0.0,
                "paid": 0.0,
            }
            for b in authorized_branches
        }
        search_term = (search or "").strip().lower()
        date_to_value = date_to or False
        # Date From is accepted; cumulative totals up to Date To are used so
        # opening liabilities/settlements are included and outstanding remains
        # accurate. (Pre-date_from amounts are therefore never excluded.)

        if vat_account:
            line_domain = [
                ("account_id", "=", vat_account.id),
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("display_type", "not in", ("line_section", "line_note")),
            ]
            if date_to_value:
                line_domain.append(("date", "<=", date_to_value))

            lines = self.env["account.move.line"].sudo().search(line_domain)
            for line in lines:
                move = line.move_id
                is_settlement = self._is_vat_settlement_move(move)
                recognized_amount = (line.credit or 0.0) - (line.debit or 0.0)
                settled_amount = (line.debit or 0.0) - (line.credit or 0.0)

                resolved_branch_id = self._resolve_vat_move_branch_id(
                    move, branch_by_config_id
                )
                bucket = branch_map.get(resolved_branch_id)
                if bucket is None:
                    # VAT that cannot be attributed to a configured,
                    # authorized branch is not displayed and not included in
                    # the KPI/total figures. The accounting records themselves
                    # are left untouched.
                    continue
                if is_settlement:
                    bucket["paid"] += settled_amount
                else:
                    bucket["payable"] += recognized_amount

        # Only actual authorized branches are displayed: all of them for the
        # "All Branches" selection, or just the selected branch.
        display_branches = (
            selected_branches if is_single_branch else authorized_branches
        )

        rows = []
        total_payable = 0.0
        total_paid = 0.0
        total_outstanding = 0.0

        def _row_status(outstanding_amount, payable_amount, paid_amount):
            precision = currency.rounding or 0.01
            if abs(outstanding_amount) < precision and abs(payable_amount) < precision and abs(paid_amount) < precision:
                return "paid", _("Settled")
            if abs(outstanding_amount) < precision:
                return "paid", _("Settled")
            if abs(paid_amount) >= precision and abs(outstanding_amount) >= precision:
                if payable_amount == 0 or (paid_amount * payable_amount) > 0:
                    return "partial", _("Partial")
            if payable_amount <= 0 and abs(paid_amount) < precision:
                return "paid", _("Settled")
            if abs(paid_amount) < precision and abs(outstanding_amount) >= precision:
                return "unpaid", _("Unpaid")
            return "unpaid", _("Unpaid")

        def _append_row(
            name, values, row_id=None, current_cash_balance=None
        ):
            nonlocal total_payable, total_paid, total_outstanding
            payable = values.get("payable", 0.0)
            paid = values.get("paid", 0.0)
            outstanding = payable - paid
            total_payable += payable
            total_paid += paid
            total_outstanding += outstanding
            status_key, status_label = _row_status(outstanding, payable, paid)
            rows.append({
                "id": row_id,
                "branch": name,
                "total_payable": payable,
                "paid_vat": paid,
                "outstanding_vat": outstanding,
                "current_cash_balance": current_cash_balance,
                "status_key": status_key,
                "status": status_label,
            })

        for branch in display_branches:
            values = branch_map.get(branch.id, {"payable": 0.0, "paid": 0.0})
            name = values.get("name") or branch.display_name
            if search_term and search_term not in (name or "").lower():
                continue
            journal = branch.branch_cash_account_id
            if journal and journal.type == "cash" and journal.company_id == company:
                branch_cash = self._get_cash_journal_balance(journal, company=company)
            else:
                branch_cash = 0.0
            _append_row(
                name,
                values,
                row_id=branch.id,
                current_cash_balance=branch_cash,
            )

        # GL reconciliation reference (posted VAT payable account balance, liability-positive)
        gl_outstanding = 0.0
        if vat_account:
            gl_domain = [
                ("account_id", "=", vat_account.id),
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("display_type", "not in", ("line_section", "line_note")),
            ]
            if date_to_value:
                gl_domain.append(("date", "<=", date_to_value))
            rg = self.env["account.move.line"].sudo().read_group(
                gl_domain, ["debit:sum", "credit:sum"], [],
            )
            if rg:
                gl_outstanding = (rg[0].get("credit") or 0.0) - (rg[0].get("debit") or 0.0)

        total_cash = self._get_vat_current_cash_balance(
            branch_id=branch_id if is_single_branch else False
        )

        return {
            "currency_symbol": currency.symbol or "",
            "currency_id": currency.id,
            "total_payable": total_payable,
            "total_paid": total_paid,
            "outstanding": total_outstanding,
            "current_cash_balance": total_cash,
            "gl_outstanding": gl_outstanding,
            "rows": rows,
            "is_single_branch": is_single_branch,
            "has_vat_account": bool(vat_account),
        }

    @api.model
    def get_vat_payment_dashboard_data(
        self, branch_id=False, date_from=False, date_to=False, search=False
    ):
        self._check_vat_payment_access()
        branch_options = self._get_vat_payment_branch_options()
        # Normalize branch filter against authorized options for managers/admins.
        allowed_ids = {b["id"] for b in branch_options.get("branches", [])}
        selected_branch_id = False
        if branch_id and str(branch_id) != "all":
            try:
                candidate = int(branch_id)
            except (TypeError, ValueError):
                candidate = False
            if candidate and candidate in allowed_ids:
                selected_branch_id = candidate

        summary = self._get_vat_branch_summary_data(
            branch_id=selected_branch_id,
            date_from=date_from or False,
            date_to=date_to or False,
            search=search or False,
        )
        summary.update({
            "branch_options": branch_options,
            "selected_branch_id": selected_branch_id,
            # Reuses the Cash Deposit Dashboard's GL-based balance calculation.
            # Deliberately independent of the VAT date filters; the balance is
            # read as posted so a VAT settlement is reflected exactly once.
            "current_cash_balance": self._get_vat_current_cash_balance(
                selected_branch_id
            ),
        })
        return summary

    @api.model
    def _get_vat_current_cash_balance(self, branch_id=False):
        """Actual cash balance of the authorized branch(es).

        No VAT date/search filter is applied here. Bank journals are never
        included: only the branches' configured cash journals contribute. A
        posted VAT payment already credits its branch cash account, so the
        GL-derived balance needs no extra manual deduction.
        """
        company = self.env.company
        if branch_id:
            branches = self._get_authorized_vat_branches(branch_id=branch_id)
        else:
            branches = self._get_authorized_vat_branches()
        journals = branches.mapped("branch_cash_account_id").filtered(
            lambda journal: journal.type == "cash" and journal.company_id == company
        )
        if not journals:
            # Mirrors the Cash Deposit Dashboard: a manager without branches
            # sees no figures rather than the whole company's cash.
            return 0.0
        return self._get_cash_journal_balance(journals, company=company)

    @api.model
    def _can_open_vat_journal_entry(self):
        """Only administrators may open Journal Entries from the VAT dashboard."""
        return bool(
            self.env.su
            or self.env.user.has_group("base.group_system")
        )

    @api.model
    def action_open_vat_journal_entry(self, move_id):
        """Return the Journal Entry form action for authorized administrators."""
        self._check_vat_payment_access()
        if not self._can_open_vat_journal_entry():
            raise AccessError(_(
                "You are not authorized to open Journal Entries from the VAT Payment dashboard."
            ))
        try:
            move_id = int(move_id)
        except (TypeError, ValueError):
            move_id = False
        if not move_id:
            return False
        payment = self.env["account.payment"].sudo().search([
            ("is_kio_vat_payment", "=", True),
            ("move_id", "=", move_id),
            ("company_id", "in", self.env.companies.ids),
        ], limit=1)
        if not payment:
            raise AccessError(_(
                "The requested Journal Entry is not an accessible VAT payment entry."
            ))
        view = self.env.ref("account.view_move_form", raise_if_not_found=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("Journal Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "views": [[view.id if view else False, "form"]],
            "res_id": move_id,
            "target": "current",
        }

    @api.model
    def get_vat_payment_history_data(
        self, branch_id=False, date_from=False, date_to=False, search=False, limit=100
    ):
        """Rows for the VAT Payment page's Payment History tab.

        Uses the same Phase 1 marker and branch/company authorization as the
        Payment History menu, so ordinary customer/vendor payments are never
        exposed. sudo() is used only for the read; the domain is built from the
        caller's authorized branches, so access is still enforced server-side.
        """
        self._check_vat_payment_access()
        can_open_journal_entry = self._can_open_vat_journal_entry()
        company = self.env.company
        authorized = self._get_authorized_vat_branches()
        if branch_id and str(branch_id) != "all":
            try:
                branch_id = int(branch_id)
            except (TypeError, ValueError):
                branch_id = False
            if branch_id:
                authorized = authorized.filtered(lambda b: b.id == branch_id)

        domain = [
            ("is_kio_vat_payment", "=", True),
            ("company_id", "=", company.id),
            ("kio_vat_branch_id", "in", authorized.ids),
        ]
        if date_from:
            domain.append(("date", ">=", date_from))
        if date_to:
            domain.append(("date", "<=", date_to))
        if search:
            term = str(search).strip()
            if term:
                domain += ["|", ("name", "ilike", term), ("ref", "ilike", term)]

        try:
            limit = max(1, min(int(limit or 100), 200))
        except (TypeError, ValueError):
            limit = 100
        payments = self.env["account.payment"].sudo().search(
            domain, order="date desc, id desc", limit=limit
        )
        symbol = company.currency_id.symbol or "BDT"
        status_labels = {
            "draft": "Draft",
            "posted": "Posted",
            "cancel": "Cancelled",
        }
        rows = []
        for payment in payments:
            amount = f"{symbol} {(payment.amount or 0.0):,.2f}"
            # account.payment delegates move_id/name to its existing
            # account.move; no new journal entry is created here.
            move = payment.move_id
            rows.append({
                "reference": payment.name or "",
                "date": fields.Date.to_string(payment.date) if payment.date else "",
                "branch": payment.kio_vat_branch_id.name or "",
                "amount": amount,
                "journal": payment.journal_id.code or payment.journal_id.name or "",
                "move_id": (move.id or False) if can_open_journal_entry else False,
                "move_name": move.name or "",
                # Kept in the payload; no challan field is configured yet.
                "challan": "",
                "status": status_labels.get(payment.state, payment.state or ""),
            })
        return {
            "rows": rows,
            "total_count": len(rows),
            "can_open_journal_entry": can_open_journal_entry,
        }
