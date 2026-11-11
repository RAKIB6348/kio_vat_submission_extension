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
  adds `total_sales` / `total_vat` to the response. Aggregates are computed
  from posted accounting records only:

  * posted `out_invoice` / `out_refund` (covers regular invoices and invoiced
    POS orders), and
  * posted POS session journal entries (`move_id` on `pos.session`, which
    contain **non-invoiced** orders only).

  Invoiced POS orders therefore contribute through their invoice and through
  nothing else, while non-invoiced POS orders contribute through their
  session entry — nothing is double counted. Revenue/VAT lines are identified
  by `account_type in ('income', 'income_other')` with no
  `tax_repartition_line_id` (sales) resp. by a set `tax_repartition_line_id`
  (VAT). Refund move lines carry the mirror sign, so totals are net.

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

* Backend: `tests/test_sales_vat_kpis.py` (post_install) — KPI calculation,
  VAT split, refunds, draft invoices excluded, POS session entries,
  invoiced POS orders counted exactly once, branch filter scoping, manager
  scoping and access rights.
* Frontend: `static/tests/cash_deposit_kpi_tests.js` (QUnit) — card order,
  rendering of the three cards and zero-fallback of the KPI values.

## Installation

Install/update `kio_vat_submission_extension` like any other module. It
depends on `kio_cash_deposit`.
