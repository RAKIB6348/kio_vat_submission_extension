# KIO VAT Submission Extension

Extends the **KIO Cash Deposit** dashboard (`kio_cash_deposit`) with two
additional KPI cards rendered immediately before the existing
**Current Cash Balance** card:

| Order | Card                | Meaning                                        |
| ----- | ------------------- | ---------------------------------------------- |
| 1     | Total Sales Amount  | Posted sales excluding VAT (net of refunds)    |
| 2     | Total VAT           | VAT collected from those sales (net of refunds)|
| 3     | Current Cash Balance| Existing card, calculation unchanged           |

## Implementation

The extension is developed against `kio_cash_deposit` **without modifying it**:

* **Backend** (`models/cash_deposit.py`) — `_inherit = "kio.cash.deposit"` and
  an override of `get_cash_deposit_dashboard_data()` that calls the base
  implementation (leaving `current_balance` and everything else untouched) and
  adds `total_sales` / `total_vat` to the response. The KPIs are computed
  from actual POS sales transactions that are associated with the cash
  balance:

  * eligible orders are POS orders in a **closed session**, with state in
    `paid` / `done` / `invoiced`, in the current company and with
    `date_order` up to the end of the current date in the company
    timezone (all history through today is included),
  * for each order only the **cash-paid portion** is counted: payments
    whose payment method points to the same cash journals that back the
    Current Cash Balance card (the selected branch's cash journal, or all
    company cash journals for admins with no branch selection),
  * mixed payments are allocated pro-rata (`cash_paid / amount_total`),
  * `Total Sales Amount` = `Σ portion × (amount_total − amount_tax)` and
    `Total VAT` = `Σ portion × amount_tax` — Odoo's own totals, so
    discounts, multiple VAT rates, tax-exempt products and refunds (net
    negative) are all handled. Bank, card, mobile banking, pay-later and
    other non-cash payments are excluded. Opening balances, cash in/out,
    transfers, deposits and expenses never contribute (they are not POS
    order payments). Linked invoices are never read, so an invoiced POS
    order cannot be double counted. Cash Balance and the KPIs are computed
    independently.

* **Company / branch / access rights** — the RPC keeps the base access
  check; company scoping always applies; amount computations use `sudo()`
  only for the aggregated read, mirroring the base module's balance
  calculation. A branch filter (the existing dashboard filter) restricts the
  POS part to the selected branches' POS sessions and to the POS-invoiced
  invoices of those sessions; non-POS invoices cannot be attributed to a
  branch and are excluded while a branch filter is active.

* **Frontend** (`static/src/...`) — a small `patch()` on the registered
  dashboard component consumes the two new keys, an XML `t-inherit` extension
  inserts the two cards before the balance card, and SCSS keeps the three
  cards on one horizontal row on desktop and responsive on smaller screens.
  Card design, colors, typography and spacing are reused from the existing
  dashboard card classes.

## Tests

* Backend: `tests/test_sales_vat_kpis.py` (post_install) — KPI calculation
  for cash-paid POS sales: cash-only orders, mixed cash/card allocation,
  multiple VAT rates and tax-exempt sales, discounts, refunds (full and
  partial), zero-total orders, open sessions excluded, historical orders
  through today, opening cash balances/deposits/expenses ignored, branch
  filter scoping, manager scoping, invoiced POS orders counted once, and
  access rights.
* Frontend: `static/tests/cash_deposit_kpi_tests.js` (QUnit) — card order,
  rendering of the three cards and zero-fallback of the KPI values.

## Installation

Install/update `kio_vat_submission_extension` like any other module. It
depends on `kio_cash_deposit`.
