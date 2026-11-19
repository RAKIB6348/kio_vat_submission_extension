/** @odoo-module */

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class VatPaymentPage extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            activeTab: "branch",
            branches: [],
            filters: {
                branchId: "all",
                dateFrom: "",
                dateTo: "",
                search: "",
            },
        });

        onWillStart(async () => {
            await this.loadBranchOptions();
        });
    }

    async loadBranchOptions() {
        try {
            const data = await this.orm.call(
                "kio.cash.deposit",
                "get_cash_deposit_branch_options",
                []
            );
            const branches = Array.isArray(data?.branches) ? data.branches : [];
            this.state.branches = branches.map((branch) => ({
                id: String(branch.id),
                name: branch.name || `Branch ${branch.id}`,
            }));
        } catch (error) {
            this.state.branches = [];
        }
    }

    get kpiCards() {
        return [
            {
                key: "payable",
                label: "Total VAT Payable",
                icon: "fa fa-file-text-o",
                accent: "payable",
                value: "BDT 0.00",
            },
            {
                key: "paid",
                label: "Total Paid VAT",
                icon: "fa fa-check-circle",
                accent: "paid",
                value: "BDT 0.00",
            },
            {
                key: "outstanding",
                label: "Outstanding VAT",
                icon: "fa fa-briefcase",
                accent: "outstanding",
                value: "BDT 0.00",
            },
        ];
    }

    get branchSummaryRows() {
        return [];
    }

    get paymentHistoryRows() {
        return [];
    }

    setActiveTab(tab) {
        this.state.activeTab = tab;
    }

    isActiveTab(tab) {
        return this.state.activeTab === tab;
    }

    onFilterChange(field, ev) {
        this.state.filters[field] = ev.target.value;
    }

    resetFilters() {
        this.state.filters.branchId = "all";
        this.state.filters.dateFrom = "";
        this.state.filters.dateTo = "";
        this.state.filters.search = "";
    }

    async openNewPayment() {
        const data = await this.orm.searchRead(
            "ir.model.data",
            [
                ["module", "=", "account"],
                ["name", "=", "view_account_payment_register_form"],
            ],
            ["res_id"],
            { limit: 1 }
        );
        const viewId = data && data.length ? data[0].res_id : false;

        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Register Payment",
            res_model: "account.payment.register",
            view_mode: "form",
            views: [[viewId || false, "form"]],
            target: "new",
            context: {
                kio_vat_payment_dashboard: true,
            },
        });
    }
}

VatPaymentPage.template = "kio_vat_submission_extension.vat_payment_page";

registry.category("actions").add(
    "kio_vat_submission_extension.vat_payment_page",
    VatPaymentPage
);
