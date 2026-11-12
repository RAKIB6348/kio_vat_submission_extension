# KIO Cash Deposit Dashboard — KPI Analysis Report (W-09 KHULNA)

**তারিখ:** ১১ নভেম্বর ২০২৬ · **পরিবেশ:** Odoo 17 / `westeen-server` (production) · **ধরন:** সম্পূর্ণ READ-ONLY অ্যানালাইসিস (code, DB বা module পরিবর্তন করা হয়নি)
**লক্ষ্য ব্রাঞ্চ:** W-09 KHULNA (`kio.cash.deposit.branch` id = 9, cash journal = 19 "KHULNA Cash", GL account = 146 "KHULNA Cash", type = `asset_cash`)
**ড্যাশবোর্ড দেখছেন:** user `#17` — "Manager of W-09, KHULNA" (Cash Deposit Manager গ্রুপ; এই ইউজারের একমাত্র branch id = 9)

**প্রদর্শিত মান (যা screen-এ দেখা যাচ্ছে) ↔ প্রকৃত গণনা:**

| KPI | স্ক্রিন-এ দেখানো | প্রকৃত (backend থেকে সরাসরি যাচাই) | Head |
|---|---|---|---|
| Total Sales Amount | BDT 48,100 | **48,099.77** BDT | `kio_vat_submission_extension` |
| Total VAT | BDT 4,810 | **4,809.99** BDT | `kio_vat_submission_extension` |
| Current Cash Balance | BDT 26,910 | **26,909.76** BDT | `kio_cash_deposit` |

সারাংশ: **কোনো হিসাবের ভুল নেই।** তিনটি মানই সঠিক, এবং Sales + VAT (52,909.76) থেকে ঠিক 26,000.00 BDT বাদ গেলে Current Cash Balance (26,909.76) হয় — আর এই 26,000 হলো দুটি অনুমোদিত Cash Deposit (1,000 + 25,000, দুটোই BNK1 "Bank" journal 6-এ)। বিস্তারিত নিচে দেওয়া হলো।

---

## 1. KPI গণনার সূত্র ও দায়িত্বশীল method/file/line

### 1.1 Total Sales Amount (Without VAT) — `kio_vat_submission_extension/models/cash_deposit.py`

ব্রাঞ্চের জন্য একমাত্র coverage journal = নির্বাচিত branch তার `branch_cash_account_id` (এখানে journal 19)।

```
total_sales = Σ over eligible POS orders o:
                 cash_paid(o) = Σ pos.payment p, (p.journal = 19 via payment_method)
                 portion     = cash_paid / o.amount_total      # signed (refund হলে দুটোই ঋণাত্মক)
                 sales      += portion × (o.amount_total − o.amount_tax)
```
`_get_sales_and_vat_kpis()` → `models/cash_deposit.py`, lines 152–190; cash coverage journal বের হয় `_get_kpi_cash_journals()` (lines 97–115); eligible orders `_get_eligible_pos_orders()` (lines 118–124): `state in ('paid','done','invoiced')`, `session_id.state = 'closed'`, `company_id = <company>`, `date_order <= end of today (company tz)`।
RPC wrapper `get_cash_deposit_dashboard_data()` (lines 56–76) — `super()` কল দিয়ে base dashboard data নেওয়া হয় এবং `total_sales`, `total_vat` যোগ করা হয়; `current_balance` ধরা ছোয়া হয় নি।

### 1.2 Total VAT — একই ফাইল, একই method

```
total_vat = Σ ( portion × o.amount_tax )
```
VAT সরাসরি `pos.order.amount_tax` (Odoo-এর নিজের হিসাব করা tax total) থেকে নেওয়া হয় — rate ভেদে শেয়ারিং হয় না, product/discount/tax-exempt সবই ইতিমধ্যে ওই সংখ্যায় embedded। (`models/cash_deposit.py`, lines 187–189)

### 1.3 Current Cash Balance — `kio_cash_deposit/models/cash_deposit.py`

`_get_cash_journal_balance(balance_journals)` → lines 573–586:

```
current_balance = Σ(account_move_line.balance)
                WHERE account_id = cash journal এর default_account_id  (এখানে 146)
                  AND company_id = <company>
                  AND display_type NOT IN ('line_section','line_note')
                  AND parent_state NOT IN ('cancel')
```
অর্থাৎ journal 19 "KHULNA Cash"-এর GL account 146-এ সব (posted/draft) move line-এর `balance` যোগফল। এই সমষ্টি memo-ভিত্তিক; সরাসরি GL ledger-এর সাথে মিলে।

### 1.4 Dashboard RPC ও access rules (উভয় মডিউল)

- Base: `kio_cash_deposit/models/cash_deposit.py::get_cash_deposit_dashboard_data()`, lines 516–560 — `_check_cash_deposit_access()` (line 517; group `kio_cash_deposit.group_kio_cash_deposit_user`, lines 164–170)।
- Branch selection: `_get_effective_dashboard_branches()` (lines 563–571) — Manager হলে নিজের assigned branch (এখানে uid 17 → branch 9), Admin হলে parameter দেওয়া branches, কিছু না দিলে company-ব্যাপী সব cash journal।
- Non-admin, no branch ⇒ balance journals খালি ⇒ `0.0`।
- Front-end: দুই কার্ড `static/src/xml/cash_deposit_dashboard_extension.xml` (t-inherit) দিয়ে inserted; `static/src/js/cash_deposit_dashboard_extension.js` (v+state.patch) row values বসায়; `cash_deposit_action.js::formatCurrency()` — `en-IN` grouping, **0 দশমিক** (তাই 48,099.77 → "BDT 48,100")।
- **গুরুত্বপূর্ণ:** Cash Balance ও KPI কখনোই সরাসরি সমীকরণে বাদ দেওয়া/ম্যাচ করানো হয় না — তিনটেই স্বাধীন হিসেব।

---

## 2. POS order-wise Total Sales (48,099.77) ও VAT (4,809.99) breakdown

**শর্ত:** `company_id = 1`, order state ∈ (paid, done, invoiced), `pos_session.state = 'closed'`, journal 19 দিয়ে paid (Cash journal 19), `date_order <= ২০২৬-১১-১১ (Asia/Dhaka, end of day)`। মোট **22 টি** order, সবগুলোর cash portion = 1.000 (সবই full-cash; এই ব্রাঞ্চে mixed/bank payment এখনো হয়নি)।

| # | Order | তারিখ | State | Session (closed) | Total | VAT | Sales (ex-VAT) | Cash paid | Portion | Sales contribution | VAT contribution |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | W-09 KHULNA/0009 (#9) | 2026-09-24 | done | POS/00045 | 2,640.00 | 240.00 | 2,400.00 | 2,640.00 | 1.0 | **2,400.00** | **240.00** |
| 2 | W-09 KHULNA/0011 (#21) | 2026-09-29 | invoiced (refund) | POS/00063 | −846.00 | −76.90 | −769.10 | −846.00 | 1.0 | **−769.10** | **−76.90** |
| 3 | W-09 KHULNA/0012 (#35) | 2026-10-06 | invoiced | POS/00066 | 3,594.00 | 326.72 | 3,267.28 | 3,594.00 | 1.0 | **3,267.28** | **326.72** |
| 4 | W-09 KHULNA/0013 (#36) | 2026-10-07 | done | POS/00072 | 897.00 | 81.55 | 815.45 | 897.00 | 1.0 | **815.45** | **81.55** |
| 5 | W-09 KHULNA/0014 (#37) | 2026-10-07 | done | POS/00072 | 149.25 | 13.56 | 135.69 | 149.25 | 1.0 | **135.69** | **13.56** |
| 6 | W-09 KHULNA/0015 (#39) | 2026-10-07 | done | POS/00072 | 2,995.00 | 272.27 | 2,722.73 | 2,995.00 | 1.0 | **2,722.73** | **272.27** |
| 7 | W-09 KHULNA/0017 (#41) | 2026-10-07 | done | POS/00072 | 2,595.00 | 235.91 | 2,359.09 | 2,595.00 | 1.0 | **2,359.09** | **235.91** |
| 8 | W-09 KHULNA/0019 (#45) | 2026-10-07 | done | POS/00072 | 2,023.00 | 183.91 | 1,839.09 | 2,023.00 | 1.0 | **1,839.09** | **183.91** |
| 9 | W-09 KHULNA/0020 (#46) | 2026-10-07 | done | POS/00072 | 4,035.00 | 366.82 | 3,668.18 | 4,035.00 | 1.0 | **3,668.18** | **366.82** |
| 10 | W-09 KHULNA/0021 (#47) | 2026-10-07 | done | POS/00072 | 4,035.00 | 366.82 | 3,668.18 | 4,035.00 | 1.0 | **3,668.18** | **366.82** |
| 11 | W-09 KHULNA/0022 (#48) | 2026-10-07 | done | POS/00072 | 6,885.00 | 625.92 | 6,259.08 | 6,885.00 | 1.0 | **6,259.08** | **625.92** |
| 12 | W-09 KHULNA/0024 (#50) | 2026-10-07 | invoiced | POS/00073 | 3,373.50 | 306.68 | 3,066.82 | 3,373.50 | 1.0 | **3,066.82** | **306.68** |
| 13 | W-09 KHULNA/0026 (#53) | 2026-10-08 | done | POS/00078 | 4,067.00 | 369.73 | 3,697.27 | 4,067.00 | 1.0 | **3,697.27** | **369.73** |
| 14 | W-09 KHULNA/0027 (#54) | 2026-10-12 | done | POS/00080 | 1,038.00 | 94.36 | 943.64 | 1,038.00 | 1.0 | **943.64** | **94.36** |
| 15 | W-09 KHULNA/0028 (#55) | 2026-10-12 | done | POS/00080 | 3,064.50 | 278.59 | 2,785.91 | 3,064.50 | 1.0 | **2,785.91** | **278.59** |
| 16 | W-09 KHULNA/0030 (#57) | 2026-10-12 | done | POS/00082 | 282.00 | 25.64 | 256.36 | 282.00 | 1.0 | **256.36** | **25.64** |
| 17 | W-09 KHULNA/0031 (#58) | 2026-10-12 | done | POS/00082 | 4,962.51 | 451.14 | 4,511.37 | 4,962.51 | 1.0 | **4,511.37** | **451.14** |
| 18 | W-09 KHULNA/0032 (#77) | 2026-11-07 | done | POS/00092 | 1,320.00 | 120.00 | 1,200.00 | 1,320.00 | 1.0 | **1,200.00** | **120.00** |
| 19 | W-09 KHULNA/0033 (#78) | 2026-11-07 | done | POS/00092 | 1,200.00 | 109.09 | 1,090.91 | 1,200.00 | 1.0 | **1,090.91** | **109.09** |
| 20 | W-09 KHULNA/0034 (#79) | 2026-11-07 | done | POS/00092 | 1,000.00 | 90.91 | 909.09 | 1,000.00 | 1.0 | **909.09** | **90.91** |
| 21 | W-09 KHULNA/0035 (#80) | 2026-11-08 | done | POS/00093 | 1,000.00 | 90.91 | 909.09 | 1,000.00 | 1.0 | **909.09** | **90.91** |
| 22 | W-09 KHULNA/0036 (#81) | 2026-11-08 | done | POS/00094 | 2,600.00 | 236.36 | 2,363.64 | 2,600.00 | 1.0 | **2,363.64** | **236.36** |
| **মোট (Net)** | | | | | **52,909.76** | **4,809.99** | **48,099.77** | **52,909.76** | | **48,099.77** | **4,809.99** |

Sales এর যোগফল = **48,099.77** (backend থেকে `get_cash_deposit_dashboard_data()` চালিয়ে সরাসরি যাচাই — `total_sales = 48099.770000000004`, floating point-এর স্বাভাবিক artifact); VAT = **4,809.99**.
Sales + VAT = 52,909.76 ⇔ সব POS cash-receipt orders-এর মোট = 52,909.76 (Journal 19-এর সব POS lines এর net = ঠিক 52,909.76 ✓)।

**Refund note (#2):** order 21 (−846.00) একটি refund (state `invoiced`, linked `out_refund` move #154) — sales ও VAT থেকে যথাক্রমে −769.10 এবং −76.90 কমেছে। এটি double counting নয়; এটাই refund-দের ঠিকভাবে net করা।
**Invoice note:** তিন order (#21→move 154, #35→move 200, #50→move 224) invoice-সহ invoiced; তারা কেবল নিজেদের payment-এ একবার গোনা হয়েছে। invoice কোথাও KPI-তে যোগ/বিয়োগ হয়নি ⇒ double counting নেই।

---

## 3. Current Cash Balance (26,909.76) — GL-ভিত্তিক লাইন-ভিত্তিক ব্রেকডাউন

Account: **146 "KHULNA Cash" (`asset_cash`)**, journal 19 "KHULNA Cash"। `display_type ∉ {section, note}`, `parent_state ≠ 'cancel'`। মোট ১৫ লাইন:

| Line id | তারিখ | Journal | Ref / Name | Debit | Credit | Balance |
|---|---|---|---|---|---|---|
| 263 | 2026-09-24 | 3 Misc. Operations (move 101) | (খালি) POS/00072 টাইপ রেফারেন্স নেই | **+5,000.00** | | +5,000.00 |
| 266 | 2026-09-24 | 6 Bank (BNK1 / move 102, TXN-2026-012) | TXN-2026-012 | | **−1,000.00** | 4,000.00 |
| 286 | 2026-09-24 | 19 KHULNA Cash (POS/00045) | POS/00045 | +2,640.00 | | 6,640.00 |
| 402 | 2026-09-29 | 27 DBBL Bank (BNK2 / move 150, TXN-2026-028) | TXN-2026-028 | | **−5,000.00** | 1,640.00 |
| 466 | 2026-10-03 | 19 KHULNA Cash (POS/00063) | POS/00063 | | −846.00 | 794.00 |
| 574 | 2026-10-07 | 19 KHULNA Cash (POS/00066) | POS/00066 | +3,594.00 | | 4,388.00 |
| 634 | 2026-10-07 | 19 KHULNA Cash (POS/00072) | POS/00072 | +23,614.25 | | 28,002.25 |
| 661 | 2026-10-07 | 19 KHULNA Cash (POS/00073) | POS/00073 | +3,373.50 | | 31,375.75 |
| 675 | 2026-10-08 | 19 KHULNA Cash (POS/00078) | POS/00078 | +4,067.00 | | 35,442.75 |
| 833 | 2026-10-12 | 19 KHULNA Cash (POS/00080) | POS/00080 | +4,102.50 | | 39,545.25 |
| 858 | 2026-10-12 | 19 KHULNA Cash (POS/00082) | POS/00082 | +5,244.51 | | 44,789.76 |
| 878 | 2026-10-12 | 6 Bank (BNK1 / move 319, TXN-2026-035) | TXN-2026-035 | | **−25,000.00** | 19,789.76 |
| 1031 | 2026-11-07 | 19 KHULNA Cash (POS/00092) | POS/00092 | +3,520.00 | | 23,309.76 |
| 1040 | 2026-11-08 | 19 KHULNA Cash (POS/00093) | POS/00093 | +1,000.00 | | 24,309.76 |
| 1210 | 2026-11-11 | 19 KHULNA Cash (POS/00094) | POS/00094 | +2,600.00 | | **26,909.76** ✅ |

সূত্র অনুযায়ী যোগফল: `+5,000.00 − 1,000.00 + 2,640.00 − 5,000.00 − 846.00 + 3,594.00 + 23,614.25 + 3,373.50 + 4,067.00 + 4,102.50 + 5,244.51 − 25,000.00 + 3,520.00 + 1,000.00 + 2,600.00 = 26,909.76` ✓ (screen-এ 26,910)

**POS lines ↔ orders মিলকরণ (সব exact):**
- POS/00045 = 2,640.00 = order #9
- POS/00063 = −846.00 = order #21 (refund)
- POS/00066 = 3,594.00 = order #35
- POS/00072 = 23,614.25 = #36+#37+#39+#41+#45+#46+#47+#48 = 897.00+149.25+2,995.00+2,595.00+2,023.00+4,035.00+4,035.00+6,885.00 = 23,614.25 ✓
- POS/00073 = 3,373.50 = order #50
- POS/00078 = 4,067.00 = order #53
- POS/00080 = 4,102.50 = #54+#55 = 1,038.00+3,064.50 = 4,102.50 ✓
- POS/00082 = 5,244.51 = #57+#58 = 282.00+4,962.51 = 5,244.51 ✓
- POS/00092 = 3,520.00 = #77+#78+#79 = 1,320.00+1,200.00+1,000.00 = 3,520.00 ✓
- POS/00093 = 1,000.00 = order #80
- POS/00094 = 2,600.00 = order #81
POS lines এর নেট যোগফল = **52,909.76** = Sales+VAT ✅ সময় ঘনিষ্ঠভাবে মিলছে।

---

## 4. Sales (48,100) + VAT (4,810) = 52,910 — কিন্তু Balance কেন 26,910?

**পার্থক্য = 52,909.76 − 26,909.76 = 26,000.00 BDT।** এই 26,000 দুটি সম্পূর্ণ যাচাইকৃত Cash Deposit-এর যোগফল (opening 5,000 ও DBBL deposit 5,000 পরস্পর offset করে):

| উপাদান | প্রমাণ | লাইন | পরিমাণ |
|---|---|---|---|
| **BNK1 deposit (TXN-2026-012)** | `kio.cash.deposit` id=3, date 2026-09-24, `W-09 KHULNA` → journal 6 "Bank" (BNK1), state `done`, journal entry #102 (BNK1/2026/00001) | 266 (account 146 credit 1,000.00) | **−1,000.00** |
| **DBBL deposit (TXN-2026-028)** | `kio.cash.deposit` id=7, date 2026-09-29, → journal 27 "DBBL Bank" (BNK2), state `done`, entry #150 (BNK2/2026/00001) | 402 | −5,000.00 |
| **BNK1 deposit (TXN-2026-035)** | `kio.cash.deposit` id=10, date 2026-10-12, → journal 6 "Bank" (BNK1), state `done`, entry #319 (BNK1/2026/00003) | 878 | **−25,000.00** |
| **Opening (MISC)** | move 101 (2026-09-24): debit KHULNA Cash 5,000, credit "Bank" account 136, ref খালি (ব্যাংকে প্রাথমিক/হস্তান্তর হিসেবে ধরা যায়) | 263 | +5,000.00 |

যাচাই (সঠিক arithmetic):
```
  POS cash receipts        = 52,909.76   (journal 19 lines, যোগফল)
+ Opening                  =  5,000.00   (line 263)
− Deposit TXN-2026-012     =  1,000.00   (line 266)
− Deposit TXN-2026-028     =  5,000.00   (line 402)
− Deposit TXN-2026-035     = 25,000.00   (line 878)
= Current Cash Balance     = 26,909.76   ✓
```
Sales + VAT থেকে সরাসরি পার্থক্য = 52,909.76 − 26,909.76 = **26,000.00** = 1,000 (266) + 25,000 (878); কারণ opening 5,000 (263) ও DBBL deposit 5,000 (402) পরস্পর বাদ যায় (offset)।
→ **উত্তর: পার্থক্যের কার্যকারণ হলো দুটি অনুমোদিত cash deposit (1,000 এবং 25,000)।** Opening 5,000 ও DBBL deposit 5,000 বাদ দিলেও net 26,000 অপরিবর্তিত (৫,০০০ + ৫,০০০ বাদ দিয়ে শেষ পর্যন্ত ওই open টাইমের same-day দুটো ঘটনা coincidentally পরস্পর offset করেছে)।
→ ❌ **146 account-এ expenses/transfers/manual entries পৃথক কিছু নেই।** শুধু একটি misc opening entry, POS lines এবং তিনটি deposit আছে। অন্য কোনো `out_invoice` 146-এ পোস্ট হয়নি। Move 101 (opening)-এর counterpart হলো "Bank" (account 136, BNK1 `asset_current`) — অর্থাৎ ব্যাংকের ক্যাশে transfer হলেও মনে রাখা যেতে পারে।

---

## 5. Checklist — যাচাইবদ্ধ ফলাফল

| # | যাচাই | ফলাফল | প্রমাণ |
|---|---|---|---|
| 1 | শুধু closed POS session-এর orders | ✅ সঠিক | সব 22 order-এর session = closed (45,63,66,72,73,78,80,82,92,93,94); open-session order নেই |
| 2 | Branch filtering | ✅ সঠিক | user 17 (Manager W-09) জন্য effective branch = [W-09 KHULNA], coverage journal = [19]; RPC সরাসরি `get_cash_deposit_dashboard_data()` চালিয়ে যাচাই — user 17 context-এ sales 48,099.77, VAT 4,809.99, balance 26,909.76 |
| 3 | Cash-only payment | ✅ সঠিক | 22 order — সব `journal 19 (cash)`; card/mobile/other-type journal coverage-তে নেই |
| 4 | Card/Bank/Bkash/Nagad বাদ | ✅ | Bkash(32)/Nagad(34) = `type=bank`; KPIs-তে যোগ হয় না। (bnk ডাটাতে Khulna-এর bank-paid order নেই) |
| 5 | Refund বাদ সঠিক | ✅ | order 21 (−846.00) — sales −769.10, VAT −76.90; journal 19-এ credit line (466, POS/00063) −846.00 — সব তিনটি consistent |
| 6 | VAT = আসল POS tax | ✅ | `pos.order.amount_tax` (Odoo-computed) directly; এখানে একাধিক product+discount দিয়ে computed tax; zero-tax product থাকলে এমনিতেই 0 হতো |
| 7 | POS order ও invoice double count নয় | ✅ | order 21 (refund, →move #154), 35 (→200), 50 (→224) — সবসময় order এর payment-এ একবার; invoice KPI-তে ব্যবহৃত হয় না (mobile order এবং refund — read unrelated) |
| 8 | Historical through today | ✅ | সবচেয়ে পুরনো 2026-09-24 → সবচেয়ে নতুন 2026-11-08; window end = Asia/Dhaka 2026-11-11 23:59:59 — কিছুই বাদ যায় নি; কোনো order এর তারিখ আজকের পরে নয় |
| 9 | Opening balance flows | ✓ noted | Move 263 (KHULNA Cash +5,000 vs Bank 136) — KPI-তে যোগ হয় না (order payment না); GL-এ +5,000 যোগ হয় (লাইন 263) — তাই deposit 1,000+25,000 বাদ +5,000 opening আসলেই 26,910 |
| 10 | Amount rounding | ⚠️ প্রদর্শনমাত্র | মূল float আছে; screen 0 দশমিক রাউন্ডে দেখায় — 48,099.77 → "48,100", 4,809.99 → "4,810", 26,909.76 → "26,910" |

---

## 6. Bugs / inconsistencies / risks

| # | ধরন | বিবরণ | তাৎপর্য |
|---|---|---|---|
| 1 | Display rounding | `formatCurrency` 0 decimals — 48,100 shows but actual 48,099.77 (and 4,809.99 → 4,810) | শুধু প্রেজেন্টেশন; backend value ঠিক |
| 2 | Data quality | Move 101 (opening, 5,000) এর `ref`/`name` খালি — কে/কখন/কেন হলো পরিষ্কার নয় | Audit করলে বুঝতে হবে; KPI এতে স্পর্শ করে না |
| 3 | Mixed orders এখনো নেই | W-09-এ এখন শুধু নগদে বিক্রি; শর্ত থাকলে 100% portion চলে; এই উদাহরণে প্রদর্শন ঠিক | 100%-guest allocation ঠিক কাজ করছে ✓ |
| 4 | Balance vs KPI semantics | GL balance = POS + opening + deposits; KPI = শুধু POS-ভিত্তিক cash portion। তাই `Sales+VAT ≠ Balance` স্বাভাবিক — "Current Cash Balance = Sales + VAT" assumption করা উচিত নয় | 26,000 deposit আসলেই checklist item #7 এর উত্তর |
| 5 | FD calculation point | KPI/GL রেফারেন্স `branch_cash_account_id` সব cash-টাইপের journal ধরতে পারে; শুধু এই ডাটাতে branch যে journal ব্যবহার করে থাকে | Hybrid set (যেমন অন্য cash journal) admin-view-এ খালি হলে KPI 0 দেখাতো — by design (admin-এ branch filter ছাড়া সব company cash journals ধরা হয়) |
| 6 | POS system sales moved | `parent_state != 'cancel'` — draft move থাকলে balance-এ ধরা পড়ত; এখানে posted-only reconcile অনুযায়ী কোনো draft entry নেই | Theoretical note only |
| 7 | Refund out_refund duplication check | order 21 invoiced refund order — POS payment-এ একবার (−846 deduct); live `out_refund` move #154 KPI-তে পঠিত হয় না ⇒ দ্বিগুণ বিয়োগ নেই | সঠিক |
| 8 | Verification completeness | Balance (26,909.76) POS GL lines-এর যোগফলের সাথে ঠিকভাবে মেলে: POS receipts 52,909.76 + opening 5,000 − deposit 1,000 − deposit 5,000 − deposit 25,000 = 26,909.76; তিন retail deposits (1,000/5,000/25,000) `kio.cash.deposit`-এ state=`done`, journal entries 102/150/319 | Exact reconciliation ✓ |

**কোন প্রকৃত গণনাগত ভুল নেই** — user 17 context-এ RPC তিনটি মান exact (48,099.77 / 4,809.99 / 26,909.76) দেয়; SQL দিয়ে একই সংখ্যা পাই।

---

## 7. প্রস্তাবিত সংশোধন (প্রয়োগ করিনি — শুধু সুপারিশ)

1. **প্রদর্শন রাউন্ডিং**: `৳ 48,099.77` দেখানোর option রাখা যেতে পারে (2 decimals) — আপনার ব্যবসায়িক সং‌স্কতি অনুযায়ী, না করলেও সমস্যা নেই।
2. **Line 101 (opening 5,000)** — ref/name যোগ করে auditability বাড়ানো উচিত।
3. **KPIs-এর explanatory tooltip**: Sales/VAT কোডের হিসাব কোথা থেকে (closed-session POS cash) হচ্ছে, UI-তে ছোট hint text দিলে ব্যবহারকারীরা ভুল assumption ("Balance = Sales + VAT") থেকে বাঁচবেন।
4. **Balance-এ `parent_state != cancel`** (draft সহ) — চাইলে `posted`-only করে নিলে POS statement-এর সাথে আরও exact match হবে (এখন draft-কিছু নেই, তাই কোনো পরিবর্তন আসেনি)।
5. **Multi-currency POS orders থাকলে** amount_total company currency-তে convert করে না (এটা একটা real-world limitation, এই ডাটাতে নেই — সবাই BDT)।
6. **Timestamp window**: eligibility `date_order <= end of today (tz)` — দিনের পরে নতুন orders POS-এ যোগ হলে dashboard স্বয়ংক্রিয়ভাবে fresh পাবে (backend per-request) ✓।
7. যদি "All Branches" নির্বাচন করা হয় (admin), KPI সবার POS sales ধরে — এটাই সঠিক। যদি branch filter ছাড়া admin কোনো কারণে branch না সিলেক্ট করে, সব cash journals ধরা হয় ✓ intended design।

---

## 8. Appendix — যাচাইয়ের জন্য ব্যবহৃত মূল query/RPC

```
# KPI দুটো (RPC, user 17 context, read-only):
env(user=17)['kio.cash.deposit'].get_cash_deposit_dashboard_data()
# → {'total_sales': 48099.770000000004, 'total_vat': 4809.990000000001,
#    'current_balance': 26909.76, 'currency_symbol': 'BDT'}

# KPI বিস্তারিত (order-wise, SQL):
SELECT o.id, o.name, o.state, s.state AS sess, o.amount_total, o.amount_tax,
       p.cash_paid, o.amount_total - o.amount_tax AS sales_ex_vat,
       (o.amount_total - o.amount_tax) * (p.cash_paid / o.amount_total) AS sales,
       o.amount_tax * (p.cash_paid / o.amount_total) AS vat
FROM pos_order o JOIN pos_session s ON s.id = o.session_id
JOIN (SELECT pos_order_id, SUM(amount) cash_paid FROM pos_payment
      JOIN pos_payment_method pm ON pm.id = payment_method_id
      WHERE pm.journal_id = 19 GROUP BY pos_order_id) p ON p.pos_order_id = o.id
WHERE o.company_id = 1 AND o.state IN ('paid','done','invoiced')
  AND s.state = 'closed';
# → 22 রেকর্ড; sales = 48,099.77; vat = 4,809.99

# Balance ব্রেকডাউন (GL, account 146):
SELECT l.id, l.date, j.name AS journal, m.ref, l.name, l.debit, l.credit
FROM account_move_line l JOIN account_move m ON m.id = l.move_id
JOIN account_journal j ON j.id = m.journal_id
WHERE l.account_id = 146;
# → 15 লাইন; Σ(balance) = 26,909.76

# Deposits ↔ GL:
SELECT id, date, transaction_id, depositor_branch, amount, state, journal_entry_id
FROM kio_cash_deposit WHERE transaction_id IN ('TXN-2026-012','TXN-2026-028','TXN-2026-035');
# → 3 records; all state='done'; total 31,000 with BNK1 26,000 outside/balance side
```

---

## 9. সর্বশেষ সিদ্ধান্ত

- **Total Sales Amount = 48,099.77 BDT** (দেখানো হয় 48,100) ✅ সঠিক
- **Total VAT = 4,809.99 BDT** (দেখানো হয় 4,810) ✅ সঠিক
- **Current Cash Balance = 26,909.76 BDT** (দেখানো হয় 26,910) ✅ সঠিক
- **পার্থক্য = 52,909.76 − 26,909.76 = 26,000.00** — এর পুরোটা দুটি অনুমোদিত Cash Deposit (1,000 TXN-2026-012, 09-24 → BNK1 Bank; 25,000 TXN-2026-035, 10-12 → BNK1 Bank)। 5,000 opening (09-24) ও 5,000 DBBL deposit (09-29) পরস্পর বাদ যাওয়ায় net পার্থক্য 26,000 এ দেখায়।
- কোনো গণনাগত বাগ/ভুল নেই; শুধুমাত্র প্রদর্শন রাউন্ডিং (0 decimals) ও একটি empty-ref misc opening entry বাদে কোনো অসামঞ্জস্য নেই।
- কোনো মডিউল বা রেকর্ড/এসকিউএল পরিবর্তন করা হয়নি; সম্পূর্ণ read-only analysis।
