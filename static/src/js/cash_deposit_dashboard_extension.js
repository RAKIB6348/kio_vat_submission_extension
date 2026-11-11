/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";

// The dashboard component class is not exported by kio_cash_deposit, so it
// is retrieved from the actions registry where it is registered.
const CashDepositDashboard = registry.category("actions").get(
    "kio_cash_deposit.cash_deposit_dashboard"
);

if (CashDepositDashboard) {
    patch(CashDepositDashboard.prototype, {
        setup() {
            super.setup(...arguments);
            this.state.totalSales = 0;
            this.state.totalVat = 0;
        },

        // The base loadData() consumes only the pre-existing keys, so it is
        // re-implemented here to also pick up the two new KPI keys returned
        // by get_cash_deposit_dashboard_data(). Everything else mirrors the
        // base implementation exactly (same RPC arguments, same state keys).
        async loadData() {
            this.state.loading = true;
            try {
                const data = await this.orm.call(
                    "kio.cash.deposit",
                    "get_cash_deposit_dashboard_data",
                    [
                        this.state.searchQuery,
                        this.offset,
                        this.state.pageSize,
                        this.orderExpression,
                        this.state.selectedBranchIds,
                    ]
                );
                this.state.currentBalance = Number(data.current_balance || 0);
                this.state.totalSales = Number(data.total_sales || 0);
                this.state.totalVat = Number(data.total_vat || 0);
                this.state.currencySymbol = data.currency_symbol || "৳";
                this.state.rows = Array.isArray(data.rows) ? data.rows : [];
                this.state.totalCount = Number(data.total_count || 0);
            } finally {
                this.state.loading = false;
            }
        },
    });
}
