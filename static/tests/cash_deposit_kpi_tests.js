/** @odoo-module */

import {
    getFixture,
    mount,
    nextTick,
} from "@web/../tests/helpers/utils";
import { makeTestEnv } from "@web/../tests/helpers/mock_env";
import { registry } from "@web/core/registry";

// Ensure the dashboard module (and our extension) are evaluated so the
// patched component class is registered.
import "@kio_cash_deposit/js/cash_deposit_action";
import "@kio_vat_submission_extension/js/cash_deposit_dashboard_extension";

const serviceRegistry = registry.category("services");

const DASHBOARD_DATA = {
    current_balance: 574000,
    total_sales: 94968.16,
    total_vat: 9496.85,
    currency_symbol: "৳",
    rows: [],
    total_count: 0,
};

const BRANCH_OPTIONS = {
    can_filter: true,
    default_branch_ids: [1],
    branches: [
        { id: 1, name: "Main Branch", branch_cash_account_id: 10 },
    ],
};

function makeOrmService() {
    return {
        dependencies: [],
        start() {
            return {
                async call(model, method) {
                    if (model === "kio.cash.deposit") {
                        if (method === "get_cash_deposit_branch_options") {
                            return BRANCH_OPTIONS;
                        }
                        if (method === "get_cash_deposit_dashboard_data") {
                            return DASHBOARD_DATA;
                        }
                    }
                    throw new Error(`Unexpected orm.call: ${model}/${method}`);
                },
            };
        },
    };
}

let target;
let env;

QUnit.module("Cash Deposit Dashboard - KPI cards", {
    async beforeEach() {
        target = getFixture();
        serviceRegistry.add("orm", makeOrmService(), { force: true });
        serviceRegistry.add(
            "action",
            { dependencies: [], start: () => ({ doAction: () => {} }) },
            { force: true }
        );
        env = await makeTestEnv();
    },
});

QUnit.test("renders Total Sales Amount, Total VAT and Current Cash Balance cards in order", async (assert) => {
    const CashDepositDashboard = registry
        .category("actions")
        .get("kio_cash_deposit.cash_deposit_dashboard");

    await mount(CashDepositDashboard, target, { env });
    await nextTick();

    const cards = target.querySelectorAll(
        ".kio-cash-deposit__balance-card"
    );
    assert.strictEqual(cards.length, 3, "renders exactly three KPI cards");

    const labels = [...cards].map(
        (card) => card.querySelector(".kio-cash-deposit__balance-body span")?.textContent
    );
    assert.deepEqual(
        labels,
        ["Total Sales Amount", "Total VAT", "Current Cash Balance"],
        "KPI cards are rendered in the expected order"
    );

    const values = [...cards].map(
        (card) => card.querySelector(".kio-cash-deposit__balance-body strong")?.textContent
    );
    const format = (amount) =>
        `৳ ${new Intl.NumberFormat("en-IN", {
            maximumFractionDigits: 0,
            minimumFractionDigits: 0,
        }).format(amount)}`;
    assert.strictEqual(values[0], format(DASHBOARD_DATA.total_sales));
    assert.strictEqual(values[1], format(DASHBOARD_DATA.total_vat));
    assert.strictEqual(values[2], format(DASHBOARD_DATA.current_balance));

    const ariaLabels = [...cards].map((card) => card.getAttribute("aria-label"));
    assert.deepEqual(
        ariaLabels,
        ["Total Sales Amount", "Total VAT", "Current Cash Balance"],
        "aria labels match the rendered cards"
    );
});

QUnit.test("renders KPI values from the backend payload, falling back to zero", async (assert) => {
    const CashDepositDashboard = registry
        .category("actions")
        .get("kio_cash_deposit.cash_deposit_dashboard");

    serviceRegistry.add(
        "orm",
        {
            dependencies: [],
            start() {
                return {
                    async call(model, method) {
                        if (method === "get_cash_deposit_branch_options") {
                            return BRANCH_OPTIONS;
                        }
                        if (method === "get_cash_deposit_dashboard_data") {
                            // Missing total_sales/total_vat must default to 0.
                            return {
                                current_balance: 100,
                                currency_symbol: "৳",
                                rows: [],
                                total_count: 0,
                            };
                        }
                        throw new Error(`Unexpected orm.call: ${model}/${method}`);
                    },
                };
            },
        },
        { force: true }
    );
    env = await makeTestEnv();

    await mount(CashDepositDashboard, target, { env });
    await nextTick();

    const values = [...target.querySelectorAll(
        ".kio-cash-deposit__balance-card .kio-cash-deposit__balance-body strong"
    )].map((el) => el.textContent);
    assert.strictEqual(values[0], "৳ 0");
    assert.strictEqual(values[1], "৳ 0");
    assert.strictEqual(values[2], "৳ 100");
});
