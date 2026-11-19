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
- Four equal-width KPI cards in one row: **Total VAT Payable**, **Total Paid
  VAT**, **Outstanding VAT**, **Current Cash Balance** (blue top border, money
  icon `fa fa-money` with blue icon color and light blue background, BDT). All
  values come from backend RPCs; no placeholders.
- Filter toolbar (frontend UI state): Branch (defaults to *All Branches*, loaded
  read-only from existing `kio.cash.deposit.get_cash_deposit_branch_options`
  when available), date range, search, and reset filters.
- Tabs (frontend switching):
  - **Branch-wise Summary** table (`Branch`, `Total VAT Payable`, `Paid VAT`,
    `Outstanding VAT`, `Current Cash Balance`, `Status`) with total row and status
    badge styling (Paid/Partial/Unpaid); each branch shows its actual GL cash
    balance, unallocated VAT shows a dash, and the total row matches the Current
    Cash Balance KPI while deduplicating shared cash journals.
  - **Payment History** table (`Payment Reference`, `Payment Date`, `Branch`,
    `Payment Amount`, `Payment Journal`, `Challan Number`, `Status`) with empty
    state when no records are available.

**New Payment:**
- The **+ New Payment** button opens the standard Odoo Register Payment form
  (`account.view_account_payment_register_form`, `account.payment.register`,
  form, `target="new"`), resolved by XML ID.
- A module-scoped initialization path (`kio_vat_payment_dashboard` context)
  allows the standard wizard to open from this dashboard without
  invoice/journal-item context and without fake accounting records.
- **Phase 3:** Create Payment now creates and posts one validated outbound VAT
  payment (full or partial) through a dedicated workflow. Standard normal
  invoice/journal-item register-payment behavior remains unchanged.

**Current Cash Balance KPI:**
- `get_vat_payment_dashboard_data()` also returns `current_cash_balance`, reusing
  the Cash Deposit Dashboard's GL calculation
  (`kio.cash.deposit._get_cash_journal_balance`) on the authorized branches'
  configured cash journals. *All Branches* sums all authorized branches;
  selecting a branch restricts the value to that branch. Managers see only
  their assigned branches and company scoping always applies.
- The value is independent of VAT date/search filters and reflects posted GL
  movements only. A posted VAT settlement already credits the branch cash
  account, so no manual deduction is made; bank journals never contribute.
- The card refreshes on branch/filter changes, on Reset Filters, and after the
  Register Payment dialog closes following a successful posting.

**Phase 2 — automatic branch cash journal and memo:**
- Selecting a branch reuses `kio.cash.deposit.branch.branch_cash_account_id`
  (the same branch-to-journal mapping used by the Cash Deposit dashboard) to
  set the wizard's `journal_id`. Available journals are restricted to that
  branch's configured cash journal(s) in the wizard's company/currency; bank
  journals and other branches' cash journals are never offered and there is no
  fallback when the mapping is missing.
- The configured **Manual** outbound payment method line is selected when
  available; a clear warning (inline alert plus onchange warning) is shown when
  the branch has no valid cash journal. Clearing the branch clears the journal,
  method, memo and amount. Dashboard branch preselection initializes all of
  these fields.
- The memo (`communication`) is set to `VAT Payment - <Branch Name>` and follows
  branch changes. The Outstanding VAT amount calculation is unchanged.
- The **Recipient Bank Account** field is hidden in the VAT dashboard flow;
  normal customer/vendor payment forms keep Odoo's standard behaviour.

**Phase 3 — VAT settlement posting:**
- Create Payment in the VAT context validates (on the server, immediately before
  posting) access rights, branch authorization/company, journal/method
  configuration, amount > 0 and ≤ freshly recomputed outstanding VAT, available
  cash, VAT Payable account configuration, currency consistency and accounting
  lock dates.
- One outbound `account.payment` is created and posted with `is_kio_vat_payment`
  and `kio_vat_branch_id` set. Its single payment move debits 200904 and credits
  the branch cash account. A branch-level advisory lock plus a deterministic
  idempotency key serialize and de-duplicate submissions; any failure rolls
  back atomically. Posted VAT payments cannot be edited, cancelled or deleted.
- Posted settlements are attributed to their branch in the VAT summary, so
  Total Paid VAT increases, Outstanding VAT decreases and the GL reconciles.
  Current Cash Balance falls automatically through the posted credit line.

**Accounting configuration findings (read-only inspection, live DB):**
- All 16 active branches map to a `cash` journal (companies 1, no journal
  currency). Each journal's default liquidity account equals its Manual
  outbound method line's `payment_account_id`, both of type `asset_cash`
  (e.g. W-09 KHULNA → journal KLNA → account 1001017 "KHULNA Cash").
- For a manual outbound payment Odoo credits the method line's
  `payment_account_id`, i.e. the branch cash account directly (the company
  "Outstanding Payments" 100104 fallback is not used because the method lines
  define their own accounts). This matches the intended credit side.
- The **debit** side is `destination_account_id`, which for a supplier payment
  defaults to the partner's payable account (or the first `liability_payable`
  account, 200101, when no partner is set). That is **not** the configured VAT
  Payable account **200904** (`liability_non_current`, reconcile), and 200904
  is outside the standard `destination_account_id` domain.
- **Phase 3 resolution:** inspection confirmed that Odoo 17 has no Python/SQL
  constraint enforcing the UI domain on `destination_account_id`. The scoped
  VAT workflow passes 200904 explicitly as the destination account of a payment
  move (`move_type = entry`), producing Debit 200904 / Credit branch cash
  account. No account type, journal configuration or accounting setting was
  changed. See `docs/VAT_PAYMENT_PHASE_3_REPORT.md`.

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
- **Backend:** `models/cash_deposit.py` extends dashboard RPC/KPI logic and
  attributes posted settlements to their branch;
  `models/account_payment_register.py` provides the scoped dashboard-only
  `account.payment.register` initialization, journal/memo selection and the
  validated VAT payment creation workflow;
  `models/account_payment.py` adds the VAT identification fields, their access
  guards, the VAT Payable destination account, server-side Payment History
  scoping and posted-payment protection.
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
  field definitions/defaults, marking restrictions, Payment History scoping and
  Branch column.
- Backend: `tests/test_vat_payment_journal_selection.py` (`post_install`) —
  branch→journal/method/memo selection, branch change and clearing, dashboard
  preselection, missing-journal warning, journal restriction, bank-account
  hiding, and normal wizard isolation.
- Backend: `tests/test_kio_vat_payment_posting.py` (`post_install`) — full and
  partial postings, exact journal entry, validation failures, multi-branch,
  idempotency, rollback, posted-payment protection, history, VAT/GL/cash
  reconciliation and normal payment regression.
- Backend: `tests/test_vat_dashboard_cash_kpi.py` (`post_install`) — cash KPI
  value, branch scoping, date-filter independence, manager security, single
  deduction after posting, bank exclusion and frontend asset declarations.
- Frontend: `static/tests/cash_deposit_kpi_tests.js` (QUnit) — dashboard KPI
  card order, rendering, and fallback behavior.

## Installation
Install or upgrade `kio_vat_submission_extension` as a standard Odoo 17 module.
It depends on `kio_cash_deposit`.
