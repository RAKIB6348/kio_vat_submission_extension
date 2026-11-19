# KIO VAT Submission Extension

Extends the **KIO Cash Deposit** application (`kio_cash_deposit`) in Odoo 17
without modifying the base module.

## Features

### 1. Cash Deposit dashboard KPI cards
Adds two KPI cards immediately before the existing **Current Cash Balance**
card on the Cash Deposit dashboard:

| Order | Card                 | Meaning                                                                 |
| ----- | -------------------- | ----------------------------------------------------------------------- |
| 1     | Total Sales Amount   | Sales excluding VAT attributable to remaining cash (FIFO-based)         |
| 2     | Total VAT            | VAT attributable to remaining cash (FIFO-based)                         |
| 3     | Current Cash Balance | Existing base card, unchanged                                           |

**KPI computation (backend):**

- Source POS orders: company-scoped, `state in (paid, done, invoiced)`,
  `session_id.state = closed`, and `date_order <= end of today` (company/user
  timezone fallback).
- Cash coverage follows the dashboard branch/admin scope (selected branches'
  cash journals, or all company cash journals for admins with no branch
  selection; no figures when a non-admin has no effective branch, mirroring the
  base dashboard behavior).
- For each eligible order, only the **cash-paid portion** is counted from POS
  payments whose payment method journal is in the coverage cash journals
  (mixed payments allocated pro-rata: `cash_paid / amount_total`). Odoo order
  totals are used for sales/VAT basis (`amount_total`, `amount_tax`).
- A **FIFO cash allocation** model is applied on coverage cash accounts:
  positive POS cash receipts create sales layers (with their own sales ex-VAT
  and VAT), opening/non-POS debit inflows create non-sales layers, and cash
  outflows (including negative POS cash payments/refunds and non-POS credits
  such as deposits/transfers/expenses, excluding identified POS statement moves
  to avoid double counting) consume the oldest available inflows first. Same-day
  opening cash is consumed before same-day sales, and inflows are available
  before same-day withdrawals.
- Attributable **Total Sales Amount** and **Total VAT** are calculated from the
  remaining portion of each POS sales layer (preserving each order's own tax
  data). POS payment data is used for sales layers; linked invoices are not
  read for KPI computation.

### 2. VAT Payment menu and dashboard page
Adds a **VAT Payment** menu under the Cash Deposit app (after **Cash Deposit**
dashboard, before **Deposits**) opening a client-action dashboard page:

- Header: **VAT Payment** title and **+ New Payment** button.
- KPI cards (UI placeholders, BDT 0.00): **Total VAT Payable**, **Total Paid
  VAT**, **Outstanding VAT**.
- Filter toolbar (frontend UI state): Branch (defaults to *All Branches*, loaded
  read-only from existing `kio.cash.deposit.get_cash_deposit_branch_options`
  when available), date range, search, and reset filters.
- Tabs (frontend switching):
  - **Branch-wise Summary** table (`Branch`, `Total VAT Payable`, `Paid VAT`,
    `Outstanding VAT`, `Status`) with total row and status badge styling
    (Paid/Partial/Unpaid); no fabricated records.
  - **Payment History** table (`Payment Reference`, `Payment Date`, `Branch`,
    `Payment Amount`, `Payment Journal`, `Challan Number`, `Status`) with empty
    state when no records are available.

**New Payment (current phase):**
- The **+ New Payment** button opens the standard Odoo Register Payment form
  (`account.view_account_payment_register_form`, `account.payment.register`,
  form, `target="new"`), resolved by XML ID.
- A module-scoped initialization path (`kio_vat_payment_dashboard` context)
  allows the standard wizard to open from this dashboard without
  invoice/journal-item context and without fake accounting records.
- Payment confirmation is blocked in this dashboard flow until the VAT payment
  accounting workflow is implemented (no payments are created/posted). Standard
  normal invoice/journal-item register-payment behavior remains unchanged.

### 3. Payment History menu
Adds a **Payment History** menu (after **VAT Payment**, before **Deposits**)
using a standard `ir.actions.act_window` on `account.payment`
(`view_mode="tree,form"`), reusing existing default Odoo views:

- Search: `account.view_account_payment_search`
- Tree: `account.view_account_payment_tree`
- Form: `account.view_account_payment_form`

The action domain is `[('is_kio_vat_payment', '=', True)]`, so only payments that
are explicitly marked as KIO VAT payments are listed; ordinary customer/vendor
payments are never shown. Standard search, filters, grouping, sorting,
pagination, and accounting access/record rules are preserved.

**Phase 1 — VAT payment identification:**

- `account.payment` gains two non-copied fields: `is_kio_vat_payment` (boolean,
  default `False`) and `kio_vat_branch_id` (branch of the same
  `kio.cash.deposit.branch` model used by the Register Payment wizard).
- Server-side checks prevent a user without Cash Deposit access from marking a
  payment as a VAT payment, and reject branches outside the user's mandate or
  outside the payment's company.
- The history action sets a `kio_vat_payment_history` context flag. On the
  server this adds the company restriction and, for managers, the assigned
  branch restriction to every search/search_read/read_group call, so the view
  cannot be widened from the client.
- The standard `account.view_account_payment_tree` is inherited only to add a
  **Branch** column that is visible in the history context; ordinary customer
  and vendor payment lists are unchanged.

## Menu structure (Cash Deposit app)
Under `kio_cash_deposit.menu_kio_cash_deposit_root` (base menus unchanged):

1. Cash Deposit (base dashboard)
2. VAT Payment (this module)
3. Payment History (this module)
4. Deposits (base)
5. Reporting (base)
6. Configuration (base)

## Implementation notes
- **Scope:** implemented entirely in `kio_vat_submission_extension`; does not
  modify `kio_cash_deposit` or standard Odoo accounting modules.
- **Backend:** `models/cash_deposit.py` extends dashboard RPC/KPI logic;
  `models/account_payment_register.py` provides the scoped dashboard-only
  `account.payment.register` initialization/blocking behavior;
  `models/account_payment.py` adds the VAT identification fields, their access
  guards, and the server-side Payment History scoping.
- **Frontend:** dashboard KPI patch and VAT Payment page assets in
  `static/src/{js,xml,scss}`; VAT Payment page styling is scoped to avoid
  impacting existing dashboard layout.
- **Access:** menus follow Cash Deposit app user access (`group_kio_cash_deposit_user`)
  where applicable; standard `account.payment` security and branch/record rules
  are not bypassed.

## Tests
- Backend: `tests/test_sales_vat_kpis.py` (`post_install`) — KPI/FIFO behavior,
  POS cash eligibility and allocation, refunds/withdrawals/opening balances,
  branch scoping, and access rights.
- Backend: `tests/test_account_payment_vat_identification.py` (`post_install`) —
  field definitions/defaults, marking restrictions, Payment History scoping,
  Branch column and the retained Create Payment blocker.
- Frontend: `static/tests/cash_deposit_kpi_tests.js` (QUnit) — dashboard KPI
  card order, rendering, and fallback behavior.

## Installation
Install or upgrade `kio_vat_submission_extension` as a standard Odoo 17 module.
It depends on `kio_cash_deposit`.
