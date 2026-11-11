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

* Total Sales Amount — posted sales excluding VAT (net of refunds)
* Total VAT — VAT collected from those sales

Sales figures are derived from posted Odoo records (invoices/refunds and
POS session journal entries), so invoiced POS orders and their invoices are
never double counted. Cards honour the dashboard's company, branch filter
and user access rights, and reuse the existing card design.
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
