# Phase 3 — VAT Payment Creation and Accounting Journal Entry Integration

Target module: `kio_vat_submission_extension` (Odoo 17)
Date: 2026-11-19

## 1. Accounting architecture verification (mandatory pre-check)

### Verdict: SAFE — `account.payment` can support the liability account via a scoped extension.

Evidence from Odoo 17 core (`addons/account/models/account_payment.py`):

| Check | Finding |
|---|---|
| Constraints on `account.payment` | Only `_check_payment_method_line_id` (lines 688-697) and SQL `CHECK(amount >= 0)` (162-168). No account-type constraint on `destination_account_id`. |
| `destination_account_id` | Stored editable computed M2O (104-110); domain `[('account_type','in',('asset_receivable','liability_payable'))]` is **UI-only**; programmatic `create()` values are respected (Odoo's own tests do this for internal transfers). |
| Payment move lines | `_prepare_move_line_default_vals` (287-360) uses `destination_account_id` verbatim as counterpart line account; no account-type validation. |
| `_seek_for_lines` (178-206) | A single non-trade counterpart line is promoted from "writeoff" to "counterpart" by the fallback at 200-204, so settlement synchronization succeeds. |
| `account.move.line` checks | `_check_payable_receivable` only applies to sale/purchase documents; a payment move is `move_type='entry'`. No partner requirement for `liability_non_current`. |
| Account/journal config | 200904 is company 1, active, no currency, no `allowed_journal_ids`; branch cash journals have no `account_control_ids` (verified in DB). Posting cannot be rejected on those grounds. |

**No account type was changed.** 200904 remains `liability_non_current` with `reconcile=True`.

### Existing branch → journal mapping (reused, not reinvented)

- `kio.cash.deposit.branch.branch_cash_account_id` (M2O → `account.journal`, domain `type = cash`, `check_company=True`) in `kio_cash_deposit/models/cash_deposit_branch.py:14-19`.
- The same field drives the POS-config branch attribution in `kio_vat_submission_extension/models/cash_deposit.py::_get_pos_config_branch_map` and the Phase 2 wizard journal selection.
- Live data: all 16 active branches map to a `cash` journal, company 1, no journal currency. Each journal's default liquidity account equals its Manual outbound method line's `payment_account_id` (e.g. W-09 KHULNA → KLNA → 1001017 "KHULNA Cash", both `asset_cash`).

## 2. Modified files

| File | Change |
|---|---|
| `models/account_payment_register.py` | Replaced the confirmation blocker with `_validate_kio_vat_payment()`, `_create_kio_vat_payment()` and `_kio_vat_success_action()`. VAT-context `action_create_payments()` now routes through the dedicated validated workflow; non-VAT context still calls `super()`. |
| `models/account_payment.py` | Added `kio_vat_idempotency_key` (+ unique SQL constraint), VAT-aware `_compute_destination_account_id` (200904), and protection of posted VAT payments against `write`/`unlink`/`action_cancel`/`action_draft`. Phase 1 fields, marker constraint and history scoping unchanged. |
| `models/cash_deposit.py` | Added `_kio_vat_settlement_payment()`; `_is_vat_settlement_move()` now requires a posted, explicitly marked VAT payment; `_resolve_vat_move_branch_id()` attributes payment moves through `payment.kio_vat_branch_id` (POS mapping remains the fallback). |
| `tests/test_kio_vat_payment_posting.py` | **New** — 14 `post_install` tests covering the full Phase 3 checklist. |
| `tests/test_account_payment_vat_identification.py`, `tests/test_vat_payment_journal_selection.py` | Replaced the two "blocker still active" assertions with checks that the old blocker is gone. |
| `tests/__init__.py`, `README.md` | Test registration and documentation. |

No changes to `kio_cash_deposit`, Odoo core, accounting configuration, existing payments or accounting entries. No new payment model, no standalone journal entry, no new wizard.

## 3. Payment creation workflow (technical)

`action_create_payments()` → `_create_kio_vat_payment()`:

1. Validate once for a clear error, then acquire a branch-scoped PostgreSQL advisory transaction lock (`pg_advisory_xact_lock(0x4B4956, branch.id)`) and validate again against freshly recomputed data.
2. Server-side validation (never trusting wizard display values):
   - Cash Deposit access + `account.payment` create rights;
   - branch authorized, active and in the payment company;
   - journal is active, `type=cash`, same company and exactly `branch.branch_cash_account_id`;
   - payment method is Manual, outbound and belongs to that journal;
   - expected currency = `journal.currency_id or company.currency_id` = wizard currency = company currency;
   - manual method's `payment_account_id` must be the journal's default (cash) account — otherwise a separate clearing workflow would be required and a clear configuration error is raised;
   - amount > 0 and ≤ outstanding VAT recomputed via `_get_vat_branch_outstanding_amount()`;
   - amount ≤ branch cash balance from `_get_cash_journal_balance()`;
   - VAT Payable account 200904 present and valid for the company;
   - payment date does not violate company lock dates (`_get_violated_lock_dates`);
3. Idempotency key = SHA-256 of `(company, branch, journal, amount, date, optional client key)`. Existing payment with that key is returned instead of creating a duplicate.
4. `account.payment.create()` with one outbound payment: marker, branch, journal, method, amount, currency, date, memo (`VAT Payment - <Branch>`) and explicit `destination_account_id = 200904`. No partner is set (none is configured for the VAT authority). Both generated journal items are labelled `VAT Payment - <Branch Name> - <DD/MM/YYYY>` through the standard liquidity/counterpart display-name hooks (before posting; standard payments are unaffected).
5. `payment.action_post()` posts the single linked `account.move`.
6. Verify: move posted, exactly two lines, debit 200904 and credit branch cash account, each equal to the amount, total balance zero. Any failure raises → Odoo rolls back the whole request, leaving no partial payment.
7. Return a success notification whose `next` action is `ir.actions.act_window_close`: the Register Payment dialog closes and the user stays on the VAT Payment Dashboard. The dashboard's dialog `onClose` callback reloads KPIs, Branch-wise Summary and Payment History while preserving branch/date/search filters and the active tab.

Full and partial payments use the same path; only the amount differs.

## 4. Actual journal entry from the test database

Non-production clone `kio_vat_phase1_test`, branch W-09 KHULNA, partial payment of 100.00 BDT (request rolled back after printing):

```
PAYMENT PKLNA/2026/00001 | state=posted | amount=100.0
        ref="VAT Payment - W-09 KHULNA" | journal=KLNA | method=manual
MOVE_LINE 1001017 (KHULNA Cash)  debit=0.00    credit=100.00  balance=-100.00
MOVE_LINE 200904  (VAT Payable)  debit=100.00  credit=0.00    balance= 100.00
```

Exactly two balanced lines, linked through `account.payment.move_id` (`move.payment_id` = payment). No duplicate standalone move was created.

## 5. Before / after verification (same test database)

| Metric | Before | After (100.00 payment) |
|---|---:|---:|
| Branch 9 Total Paid VAT | 0.00 | 100.00 |
| Branch 9 Outstanding VAT | 5,862.71 | 5,762.71 |
| Branch 9 Current Cash Balance | 38,489.76 | 38,389.76 |
| Company Total VAT Payable | 10,785.93 | 10,785.93 (unchanged) |
| Company Total Paid VAT | 0.00 | 100.00 |
| Company Outstanding VAT | 10,785.93 | 10,685.93 |
| VAT Payable GL outstanding | 10,785.93 | 10,685.93 |

Total Payable is unchanged; Paid VAT and GL/outstanding reconcile exactly; cash falls by the payment amount through the posted credit on the branch cash account. No extra manual cash deduction was needed because `kio_cash_deposit._get_cash_journal_balance()` already sums non-cancelled lines on the journal's default account.

## 6. Test results

- `kio_vat_phase1_test` (production-data clone), Phase 3 class: **14 / 14 passed**.
- `kio_vat_phase1_clean` (clean database), full module suite: **53 / 53 passed** (14 new + 39 existing), module upgrade successful (`Modules loaded`, no errors).
- Covered: full/partial payments, zero and over-payment rejection, insufficient cash, wrong journal, unauthorized branch, multiple branches, duplicate submission (idempotent), posting failure rollback via savepoint, posted-payment immutability, Payment History visibility, exact journal entry, VAT/GL/cash reconciliation, and normal vendor-payment regression (destination remains a standard `liability_payable` account).

## 7. Remaining limitations

- **No VAT authority partner / challan number exists** anywhere in the configuration. Payments are therefore created with `partner_id = False`, and no challan field is populated. If the business requires a named authority or challan reference, that configuration/workflow must be supplied first (approval required before adding such fields).
- **Idempotency key** is deterministic from branch/company/journal/amount/date. Two intentional repeat payments of the *same* amount for the *same* branch on the *same* day are treated as duplicates unless the client passes a distinct `kio_vat_payment_idempotency_key` context value.
- **Cash Deposit Managers** do not have standard `account.payment` create rights; the workflow checks and reports this clearly rather than bypassing ACLs via sudo. The VAT menu remains visible to them, so they need Accounting payment rights to confirm a VAT payment.
- **Lock dates:** the workflow rejects dates inside a lock date. Odoo itself would otherwise silently shift a posted move date forward; no such silent shift is used here.
- **No reversal workflow.** Posted VAT payments are intentionally immutable (no edit/cancel/reset/delete). A future phase must define a safe reversal before those operations are allowed.
- The existing cash-deposit sales/VAT KPI cards still treat the payment credit as a non-sales cash outflow (existing FIFO logic). This was not changed, as requested.
- The frontend VAT Payment page still shows a hard-coded empty `paymentHistoryRows` table; the authoritative list is the standard Payment History menu, which now contains posted VAT payments. Wiring the client-side table to a real RPC is a separate frontend task.
- The pre-existing `test_sales_vat_kpis` setup fails on a copy of the production database because `pos_exchange_record` rows reference `pos_order` during raw-SQL cleanup; it passes on a clean database and is unrelated to this phase.
