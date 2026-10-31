const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function ui() {
  const app = { innerHTML: "", querySelectorAll: () => [] };
  const queries = new Map();
  const all = new Map();
  const context = vm.createContext({
    URLSearchParams, setTimeout, clearTimeout,
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    document: { querySelector: (selector) => selector === "#app" ? app : queries.get(selector) || null, querySelectorAll: (selector) => all.get(selector) || [] },
    window: { location: { search: "" }, addEventListener: () => {}, confirm: () => true, history: { pushState: () => {}, replaceState: () => {} } },
  });
  const run = (code) => vm.runInContext(code, context);
  run(fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8").replace(/\nbootstrap\(\);\s*$/, "\n"));
  run(`
    state.user = { id: 1, name: "Alex", email: "alex@test", is_admin: true, theme: "light" };
    state.trackerId = 1;
    state.trackers = [{ id: 1, name: "Home", members: [{ user_id: 1, name: "Alex", role: "owner" }], default_currency: "CAD" }];
    state.categories = [{ id: 1, name: "Food", color: "#ff0" }];
    realRenderApp = renderApp; renderApp = () => {};
    refresh = async () => {}; refreshOverview = async () => {};
  `);
  return { run, context, app, queries, all };
}

test("Search combines description/category/payer with existing filters", () => {
  const view = ui();
  view.run(`state.expenses = [
    { id: 1, description: "Coffee shop", category: "Food", category_id: 1, paid_by: "Alex", paid_by_id: 1, is_shared: true },
    { id: 2, description: "Train", category: "Travel", category_id: 2, paid_by: "Sam", paid_by_id: 2, is_shared: false }
  ]; state.expenseTableFilters.monthly.search = "COFFEE";`);
  assert.equal(view.run('filteredAndSortedExpenses(state.expenses, "monthly")[0].id'), 1);
  view.run('state.expenseTableFilters.monthly.search = "sam"');
  assert.equal(view.run('filteredAndSortedExpenses(state.expenses, "monthly")[0].id'), 2);
  view.run('state.expenseTableFilters.monthly.type = "shared"');
  assert.equal(view.run('filteredAndSortedExpenses(state.expenses, "monthly").length'), 0);
});

test("Overview subtotal follows the listed expenses, including combined filters and no matches", () => {
  const view = ui();
  view.run(`state.expenses = [
    { id: 1, date: "2026-10-01", description: "Coffee", category: "Food", category_id: 1, paid_by: "Alex", paid_by_id: 1, is_shared: true, amount: "12.34" },
    { id: 2, date: "2026-10-02", description: "Dinner", category: "Food", category_id: 1, paid_by: "Alex", paid_by_id: 1, is_shared: false, amount: 20.01 },
    { id: 3, date: "2026-10-03", description: "Coffee", category: "Food", category_id: 1, paid_by: "Sam", paid_by_id: 2, is_shared: true, amount: 4.5 }
  ];`);
  const subtotal = () => view.run('renderExpenseTable(state.expenses)').match(/class="expense-subtotal"[\s\S]*?<strong class="amount">([^<]+)<\/strong>/)[1];
  assert.equal(subtotal(), view.run('currency(36.85)'));
  view.run('state.expenseTableFilters.overview.search = "coffee"');
  assert.equal(subtotal(), view.run('currency(16.84)'));
  view.run('state.expenseTableFilters.overview.paidBy = "1"; state.expenseTableFilters.overview.category = "1"; state.expenseTableFilters.overview.type = "shared"');
  assert.equal(subtotal(), view.run('currency(12.34)'));
  view.run('state.expenseTableFilters.overview.type = "individual"');
  assert.equal(subtotal(), view.run('currency(0)'));
});

test("Bank import includes selected transactions hidden by search", async () => {
  const view = ui();
  const calls = [];
  view.context.api = async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return { imported: 2, skipped: [] }; };
  view.run(`state.bankSearch = "Coffee"; state.bankTransactions = [
    { id: 1, description: "Coffee", default_paid_by_id: 1, _reviewSelected: true, _reviewCategoryId: 1 },
    { id: 2, description: "Dinner", default_paid_by_id: 1, _reviewSelected: true, _reviewCategoryId: 1, _reviewIsShared: false }
  ];`);
  assert.equal(view.run('visibleBankTransactions().length'), 1);
  await view.run('importBankTransactions({ preventDefault() {} })');
  assert.equal(calls[0].body.transactions.length, 2);
  assert.equal(calls[0].body.transactions[1].is_shared, false);
  assert.match(view.run('state.notices[0].message'), /Imported 2/);
});

test("Mutation prevents duplicate requests and reports failures", async () => {
  const view = ui();
  let release;
  let calls = 0;
  view.context.operation = () => { calls++; return new Promise((resolve) => { release = resolve; }); };
  const pending = view.run('mutate(operation)');
  assert.equal(await view.run('mutate(operation)'), false);
  assert.equal(calls, 1);
  release({}); await pending;
  assert.equal(await view.run('mutate(async () => { throw new Error("Offline"); })'), false);
  assert.equal(view.run('state.error'), "Offline");
  assert.equal(view.run('state.mutating'), false);
});

test("Delete offers Undo against the original tracker after navigation", async () => {
  const view = ui();
  const calls = [];
  view.context.api = async (url) => { calls.push(url); return { deleted: 1, undo_token: "undo-token" }; };
  await view.run('deleteExpenses([42])');
  view.run('state.trackerId = 2; state.trackers.push({id: 2})');
  await view.run('state.notices[0].action()');
  assert.equal(calls[1], "/api/trackers/1/expenses/undo/undo-token");
});

test("Inline autosave serializes edits and does not overwrite a newer change", async () => {
  const view = ui();
  const requests = [];
  const resolvers = [];
  view.context.api = (url, options) => { requests.push(JSON.parse(options.body)); return new Promise((resolve) => resolvers.push(resolve)); };
  view.run('state.expenses = [{id: 9, description: "latest"}]; expenseSaves.set("1:9", {payload: {description: "first"}, revision: 1, saving: false})');
  const saving = view.run('persistExpenseEdit(1, 9)');
  view.run('expenseSaves.get("1:9").payload = {description: "latest"}; expenseSaves.get("1:9").revision = 2');
  resolvers[0]({id: 9, description: "first"});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.equal(view.run('state.expenses[0].description'), "latest");
  resolvers[1]({id: 9, description: "latest"});
  await saving;
  assert.equal(view.run('expenseSaves.size'), 0);
});

test("Failed inline save restores the saved expense and removes the pending edit", async () => {
  const view = ui();
  view.context.api = async () => { throw new Error("Offline"); };
  view.run(`state.expenses = [{id: 9, description: "Changed", amount: 42}];
    expenseSaves.set("1:9", {payload: {description: "Changed", amount: 42}, saved: {id: 9, description: "Saved", amount: 10}, revision: 1, saving: false});`);
  await view.run('persistExpenseEdit(1, 9)');
  assert.equal(view.run('expenseSaves.size'), 0);
  assert.equal(view.run('state.expenses[0].description'), "Saved");
  assert.equal(view.run('state.expenses[0].amount'), 10);
  assert.match(view.run('state.error'), /Offline.*last saved value has been restored/);
});

test("A failed newer autosave restores the most recently successful save", async () => {
  const view = ui();
  let release;
  let calls = 0;
  view.context.api = () => ++calls === 1
    ? new Promise((resolve) => { release = resolve; })
    : Promise.reject(new Error("Offline"));
  view.run(`state.expenses = [{id: 9, description: "First"}];
    expenseSaves.set("1:9", {payload: {description: "First"}, saved: {id: 9, description: "Original"}, revision: 1, saving: false});`);
  const saving = view.run('persistExpenseEdit(1, 9)');
  view.run('expenseSaves.get("1:9").payload = {description: "Second"}; expenseSaves.get("1:9").revision = 2; state.expenses[0].description = "Second"');
  release({id: 9, description: "First"});
  await saving;
  assert.equal(calls, 2);
  assert.equal(view.run('state.expenses[0].description'), "First");
  assert.equal(view.run('expenseSaves.size'), 0);
});

test("Leaving bank review clears unimported values without a confirmation", async () => {
  const view = ui();
  view.context.window.confirm = () => { throw new Error("Unexpected confirmation"); };
  view.run(`state.tab = "bank"; state.bankTransactions = [{id: 1, description: "Saved", _reviewDescription: "Edited", _reviewSelected: true, _reviewCategoryId: 1}];
    window.location.search = "?tracker=1&tab=overview"; renderApp = realRenderApp;`);
  await view.run('handleNavigationChange()');
  assert.equal(view.run('state.tab'), "overview");
  assert.equal(view.run('state.bankTransactions[0].description'), "Saved");
  assert.equal(view.run('state.bankTransactions[0]._reviewDescription'), undefined);
  assert.equal(view.run('state.bankTransactions[0]._reviewSelected'), undefined);
  view.run('window.location.search = "?tracker=1&tab=bank"');
  await view.run('handleNavigationChange()');
  assert.equal(view.run('state.bankTransactions[0]._reviewCategoryId'), undefined);
});

test("Navigation still waits for an active submission", () => {
  const view = ui();
  view.run('activeSubmission = {form: {id: "expense-form"}}');
  assert.equal(view.run('allowNavigation()'), false);
  view.run('activeSubmission = null');
  assert.equal(view.run('allowNavigation()'), true);
});

test("Bank health distinguishes a failed bank update from a successful app sync", () => {
  const view = ui();
  view.run('bankHealth.set("1:3", {last_successful_update: "2026-10-22T14:00:00Z", last_failed_update: "2026-10-31T09:00:00Z"})');
  assert.match(view.run('renderBankHealth({id: 3, provider: "plaid"})'), /latest update failed/);
  view.run('bankHealth.set("1:3", {error_code: "ITEM_LOGIN_REQUIRED"})');
  assert.match(view.run('renderBankHealth({id: 3, provider: "plaid"})'), /Reconnect/);
  view.run('bankHealth.set("1:3", {error: true})');
  assert.match(view.run('renderBankHealth({id: 3, provider: "plaid"})'), /Retry status/);
});

test("Every screen renders with the new controls and notices", () => {
  const view = ui();
  for (const tab of ["overview", "expenses", "bank", "settings", "user-settings", "admin"]) {
    view.run(`state.tab = ${JSON.stringify(tab)}; realRenderApp()`);
    assert.match(view.app.innerHTML, /id="logout-button"/);
  }
  view.run('state.periodType = "year"; state.tab = "overview"; realRenderApp()');
  assert.match(view.app.innerHTML, /id="overview-expenses"/);
  assert.doesNotMatch(view.app.innerHTML, /data-drilldown/);
});

test("Category totals stay compact and percentages use every category", () => {
  const view = ui();
  view.run('categoryData = { by_category: Array.from({length: 10}, (_, index) => ({name: `Category ${index}`, total: 10})), by_person_category: [{category: "Category 0", person: "Alex", total: 10}] }');
  let html = view.run('renderCategoryBreakdownTable(categoryData)');
  assert.equal((html.match(/class="category-total-item"/g) || []).length, 6);
  assert.match(html, /10\.0%/);
  assert.match(html, /Show all 10 categories/);
  assert.doesNotMatch(html, /<details class="category-payers" open/);
  view.run('state.categoryBreakdownExpanded = true');
  html = view.run('renderCategoryBreakdownTable(categoryData)');
  assert.equal((html.match(/class="category-total-item"/g) || []).length, 10);
  assert.match(html, /Show fewer/);
});

test("Monthly settlements follow member breakdown and totals are not links", () => {
  const view = ui();
  view.run('state.periodType = "month"');
  const html = view.run('renderOverview()');
  assert.ok(html.indexOf("Member breakdown") < html.indexOf("Who owes who"));
  assert.ok(html.indexOf("Who owes who") < html.indexOf("Category chart"));
  assert.doesNotMatch(html, /data-drilldown|<button[^>]*overview-total-metric/);
});

test("Spending tabs render only their selected view and preserve category preferences", () => {
  const view = ui();
  view.run('state.categoryChartMember = "1"; state.categoryChartSort = "alpha"');
  let html = view.run('renderSpendingBreakdown({})');
  assert.match(html, /id="category-chart-member"/);
  assert.doesNotMatch(html, /id="category-breakdown-sort"/);
  view.run('selectAnalysisView("totals")');
  html = view.run('renderSpendingBreakdown({})');
  assert.match(html, /id="category-breakdown-sort"/);
  assert.doesNotMatch(html, /id="category-chart-member"/);
  view.run('selectAnalysisView("payer")');
  html = view.run('renderSpendingBreakdown({})');
  assert.match(html, /aria-labelledby="analysis-tab-payer"/);
  assert.doesNotMatch(html, /id="category-breakdown-sort"|id="category-chart-member"/);
  view.run('selectAnalysisView("category")');
  assert.equal(view.run('state.categoryChartMember'), "1");
  assert.equal(view.run('state.categoryChartSort'), "alpha");
});

test("Spending tabs support arrow keys, Home, and End", () => {
  const view = ui();
  const buttons = ["category", "payer", "totals"].map((id) => ({ dataset: { analysisView: id }, handlers: {}, addEventListener(event, handler) { this.handlers[event] = handler; } }));
  view.all.set("[data-analysis-view]", buttons);
  const focused = [];
  buttons.forEach((button) => view.queries.set(`#analysis-tab-${button.dataset.analysisView}`, { focus: () => focused.push(button.dataset.analysisView) }));
  view.run('bindUxEvents()');
  buttons[0].handlers.keydown({key: "ArrowLeft", preventDefault() {}});
  assert.equal(view.run('state.analysisView'), "totals");
  buttons[2].handlers.keydown({key: "Home", preventDefault() {}});
  assert.equal(view.run('state.analysisView'), "category");
  buttons[0].handlers.keydown({key: "End", preventDefault() {}});
  assert.deepEqual(focused, ["totals", "category", "totals"]);
});
