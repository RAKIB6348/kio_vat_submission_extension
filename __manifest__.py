# -*- coding: utf-8 -*-
{
    "name": "KIO VAT Submission Extension",
    "version": "17.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Total Sales Amount and Total VAT KPI cards for the Cash Deposit dashboard",
    "description": """KIO VAT Submission Extension
===========================

Extends the KIO Cash Deposit dashboard (kio_cash_deposit) with two KPI
cards rendered immediately before the existing "Current Cash Balance" card:

* Total Sales Amount (excluding VAT), computed from POS sales.
* Total VAT, collected from those sales.

Values are computed from actual POS sales transactions associated with the
cash balance (closed-session POS orders, restricted to the cash-paid portion
and net of refunds). Invoiced POS orders are never double counted. The same
company, branch filter and access rights as the dashboard apply, and the
existing card design is reused.
""",
    "author": "Kendroo Limited",
    "website": "kendroo.io",
    "depends": [
        "kio_cash_deposit",
    ],
    "data": [],
    "assets": {
        "web.assets_backend": [
            "kio_vat_submission_extension/static/src/js/cash_deposit_dashboard_extension.js",
            "kio_vat_submission_extension/static/src/xml/cash_deposit_dashboard_extension.xml",
            "kio_vat_submission_extension/static/src/scss/cash_deposit_dashboard_extension.scss",
        ],
        "web.qunit_suite_tests": [
            "kio_vat_submission_extension/static/tests/cash_deposit_kpi_tests.js",
        ],
    },
    "installable": True,
    "application": False,
    "license": "OPL-1",
}
