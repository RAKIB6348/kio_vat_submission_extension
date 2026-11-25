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
                cash: 0,
            },
            branchRows: [],
            paymentHistoryRows: [],
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
        let value = Number(amount || 0);
        if (Math.abs(value) < 0.00001) {
            value = 0;
        }
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
                // Actual GL balance; independent of the VAT date filters.
                cash: Number(data.current_cash_balance || 0),
            };
            const rows = Array.isArray(data.rows) ? data.rows : [];
            this.state.branchRows = rows.map((row) => {
                const hasCash =
                    row.current_cash_balance !== null &&
                    row.current_cash_balance !== undefined;
                return {
                    key: `branch_${row.id}`,
                    branch: row.branch || "",
                    total_payable: this.formatAmount(row.total_payable),
                    paid_vat: this.formatAmount(row.paid_vat),
                    outstanding_vat: this.formatAmount(row.outstanding_vat),
                    current_cash_balance: hasCash
                        ? this.formatAmount(row.current_cash_balance)
                        : "—",
                    status_key: row.status_key || "unpaid",
                    status: row.status || "Unpaid",
                };
            });

            const history = await this.orm.call(
                "kio.cash.deposit",
                "get_vat_payment_history_data",
                [],
                params
            );
            const canOpenJournalEntry = Boolean(history && history.can_open_journal_entry);
            const historyRows = Array.isArray(history && history.rows) ? history.rows : [];
            this.state.paymentHistoryRows = historyRows.map((row, index) => ({
                key: `payment_${index}`,
                reference: row.reference || "",
                date: row.date || "",
                branch: row.branch || "",
                amount: row.amount || "",
                journal: row.journal || "",
                moveId: canOpenJournalEntry ? (row.move_id || false) : false,
                moveName: row.move_name || "",
                canOpenJournalEntry,
                challan: row.challan || "",
                status: row.status || "",
            }));
        } catch (error) {
            this.state.branches = [];
            this.state.branchRows = [];
            this.state.paymentHistoryRows = [];
            this.state.totals = { payable: 0, paid: 0, outstanding: 0, cash: 0 };
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
            {
                key: "cash",
                label: "Current Cash Balance",
                icon: "fa fa-money",
                accent: "cash",
                value: this.formatAmount(this.state.totals.cash),
            },
        ];
    }

    get branchSummaryRows() {
        return this.state.branchRows;
    }

    get paymentHistoryRows() {
        return this.state.paymentHistoryRows;
    }

    get summaryTotals() {
        return {
            payable: this.formatAmount(this.state.totals.payable),
            paid: this.formatAmount(this.state.totals.paid),
            outstanding: this.formatAmount(this.state.totals.outstanding),
            cash: this.formatAmount(this.state.totals.cash),
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

    async openJournalEntry(moveId) {
        if (!moveId) {
            return;
        }
        const action = await this.orm.call(
            "kio.cash.deposit",
            "action_open_vat_journal_entry",
            [moveId]
        );
        if (action) {
            await this.action.doAction(action);
        }
    }

    generateIdempotencyKey() {
        // Scoped to one wizard opening: a double click on Create Payment
        // reuses the same key, which the server maps to the already created
        // payment (no duplicate). Reopening the wizard later gets a fresh key,
        // so an intentional second payment of the same amount is not blocked.
        const cryptoObj = typeof window !== "undefined" ? window.crypto : undefined;
        if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
            return cryptoObj.randomUUID();
        }
        return `vat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
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
            kio_vat_payment_idempotency_key: this.generateIdempotencyKey(),
        };
        if (this.state.filters.branchId && this.state.filters.branchId !== "all") {
            const branchId = Number(this.state.filters.branchId);
            if (branchId) {
                context.default_kio_vat_branch_id = branchId;
            }
        }

        await this.action.doAction(
            {
                type: "ir.actions.act_window",
                name: "Register Payment",
                res_model: "account.payment.register",
                view_mode: "form",
                views: [[viewId || false, "form"]],
                target: "new",
                context,
            },
            {
                // doAction() resolves as soon as the dialog is mounted, so the
                // refresh is driven by this close callback instead. It runs
                // only when the wizard is actually closed (successful posting
                // or cancel), without a browser reload, and reloads the KPI
                // cards, Branch-wise Summary and Payment History while keeping
                // the selected branch, filters, search and active tab.
                // A validation error leaves the wizard open, so no refresh is
                // triggered in that case.
                onClose: async () => {
                    await this.loadDashboardData();
                },
            }
        );
    }
}

VatPaymentPage.template = "kio_vat_submission_extension.vat_payment_page";

registry.category("actions").add(
    "kio_vat_submission_extension.vat_payment_page",
    VatPaymentPage
);
