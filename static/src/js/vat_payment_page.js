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
            currencySymbol: "BDT",
            totals: {
                payable: 0,
                paid: 0,
                outstanding: 0,
            },
            branchRows: [],
            filters: {
                branchId: "all",
                dateFrom: "",
                dateTo: "",
                search: "",
            },
            loading: false,
        });

        onWillStart(async () => {
            await this.loadDashboardData();
        });
    }

    formatAmount(amount) {
        const value = Number(amount || 0);
        const formatted = new Intl.NumberFormat("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(value);
        const symbol = this.state.currencySymbol || "BDT";
        return `${symbol} ${formatted}`;
    }

    async loadDashboardData() {
        this.state.loading = true;
        try {
            const params = {
                branch_id:
                    this.state.filters.branchId === "all"
                        ? false
                        : Number(this.state.filters.branchId),
                date_from: this.state.filters.dateFrom || false,
                date_to: this.state.filters.dateTo || false,
                search: this.state.filters.search || "",
            };
            const data = await this.orm.call(
                "kio.cash.deposit",
                "get_vat_payment_dashboard_data",
                [],
                params
            );

            const options = data.branch_options || {};
            const branches = Array.isArray(options.branches) ? options.branches : [];
            this.state.branches = branches.map((branch) => ({
                id: String(branch.id),
                name: branch.name || `Branch ${branch.id}`,
            }));
            if (data.selected_branch_id) {
                this.state.filters.branchId = String(data.selected_branch_id);
            } else if (!this.state.branches.some((b) => b.id === this.state.filters.branchId)) {
                this.state.filters.branchId = "all";
            }

            this.state.currencySymbol = data.currency_symbol || this.state.currencySymbol || "BDT";
            this.state.totals = {
                payable: Number(data.total_payable || 0),
                paid: Number(data.total_paid || 0),
                outstanding: Number(data.outstanding || 0),
            };
            const rows = Array.isArray(data.rows) ? data.rows : [];
            this.state.branchRows = rows.map((row, index) => ({
                key: row.id !== false && row.id !== null ? `branch_${row.id}` : `unallocated_${index}`,
                branch: row.branch || "",
                total_payable: this.formatAmount(row.total_payable),
                paid_vat: this.formatAmount(row.paid_vat),
                outstanding_vat: this.formatAmount(row.outstanding_vat),
                status_key: row.status_key || "unpaid",
                status: row.status || "Unpaid",
            }));
        } catch (error) {
            this.state.branches = [];
            this.state.branchRows = [];
            this.state.totals = { payable: 0, paid: 0, outstanding: 0 };
        } finally {
            this.state.loading = false;
        }
    }

    get kpiCards() {
        return [
            {
                key: "payable",
                label: "Total VAT Payable",
                icon: "fa fa-file-text-o",
                accent: "payable",
                value: this.formatAmount(this.state.totals.payable),
            },
            {
                key: "paid",
                label: "Total Paid VAT",
                icon: "fa fa-check-circle",
                accent: "paid",
                value: this.formatAmount(this.state.totals.paid),
            },
            {
                key: "outstanding",
                label: "Outstanding VAT",
                icon: "fa fa-briefcase",
                accent: "outstanding",
                value: this.formatAmount(this.state.totals.outstanding),
            },
        ];
    }

    get branchSummaryRows() {
        return this.state.branchRows;
    }

    get paymentHistoryRows() {
        return [];
    }

    get summaryTotals() {
        return {
            payable: this.formatAmount(this.state.totals.payable),
            paid: this.formatAmount(this.state.totals.paid),
            outstanding: this.formatAmount(this.state.totals.outstanding),
        };
    }

    setActiveTab(tab) {
        this.state.activeTab = tab;
    }

    isActiveTab(tab) {
        return this.state.activeTab === tab;
    }

    async onFilterChange(field, ev) {
        this.state.filters[field] = ev.target.value;
        await this.loadDashboardData();
    }

    async resetFilters() {
        this.state.filters.branchId = "all";
        this.state.filters.dateFrom = "";
        this.state.filters.dateTo = "";
        this.state.filters.search = "";
        await this.loadDashboardData();
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

        const context = {
            kio_vat_payment_dashboard: true,
        };
        if (this.state.filters.branchId && this.state.filters.branchId !== "all") {
            const branchId = Number(this.state.filters.branchId);
            if (branchId) {
                context.default_kio_vat_branch_id = branchId;
            }
        }

        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Register Payment",
            res_model: "account.payment.register",
            view_mode: "form",
            views: [[viewId || false, "form"]],
            target: "new",
            context,
        });
    }
}

VatPaymentPage.template = "kio_vat_submission_extension.vat_payment_page";

registry.category("actions").add(
    "kio_vat_submission_extension.vat_payment_page",
    VatPaymentPage
);
