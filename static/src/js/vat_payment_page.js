/** @odoo-module */

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

export class VatPaymentPage extends Component {}

VatPaymentPage.template = "kio_vat_submission_extension.vat_payment_page";

registry.category("actions").add(
    "kio_vat_submission_extension.vat_payment_page",
    VatPaymentPage
);
