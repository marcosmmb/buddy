const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function bankUi() {
  const source = fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8");
  const calls = [];
  let linkOptions;
  const context = vm.createContext({
    URLSearchParams,
    localStorage: { getItem: () => null, setItem: () => {} },
    document: { querySelector: () => null },
    window: {
      location: { search: "" },
      addEventListener: () => {},
      Plaid: { create: (options) => { linkOptions = options; return { open: () => {} }; } },
    },
    fetch: async (url, options) => {
      calls.push({ url, method: options.method, body: options.body && JSON.parse(options.body) });
      return {
        ok: true, status: 201,
        text: async () => JSON.stringify(url.endsWith("/link-token")
          ? { link_token: "link-test", bank_link_token: "challenge-test" }
          : { status: "ok" }),
      };
    },
  });
  // Exercise bank behavior without bootstrapping unrelated screens or a browser DOM.
  vm.runInContext(source.replace(/\nbootstrap\(\);\s*$/, "\n"), context);
  vm.runInContext(`
    state.user = { two_factor_enabled: true };
    state.trackerId = 1;
    state.trackers = [{ id: 1 }];
    renderApp = () => {};
    refresh = async () => {};
  `, context);
  return { context, calls, run: (code) => vm.runInContext(code, context), linkOptions: () => linkOptions };
}

test("Reconnect prompts for 2FA for the selected connection", async () => {
  const ui = bankUi();
  await ui.run("connectBank(3)");
  assert.equal(ui.run("state.bankTwoFactor.open"), true);
  assert.equal(ui.run("state.bankTwoFactor.connectionId"), 3);
  assert.equal(ui.calls.length, 0);
  ui.run("state.user.two_factor_enabled = false; state.bankTwoFactor.open = false");
  await ui.run("connectBank(3)");
  assert.equal(ui.run("state.bankTwoFactor.open"), false);
  assert.match(ui.run("state.error"), /Enable 2FA/);
});

test("Update Link retries the existing connection without exchanging a new token", async () => {
  const ui = bankUi();
  await ui.run('openPlaidLink("123456", 3)');
  assert.equal(ui.calls[0].url, "/api/trackers/1/bank/connections/3/link-token");
  assert.deepEqual(ui.calls[0].body, { two_factor_code: "123456" });
  assert.equal(ui.linkOptions().token, "link-test");
  await ui.linkOptions().onSuccess("unused-public-token", {});
  assert.equal(ui.calls[1].url, "/api/trackers/1/bank/connections/3/sync?days=8&refresh_accounts=true");
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.run("state.syncingBankConnectionId"), null);
});

test("New bank linking still exchanges its public token and 2FA challenge", async () => {
  const ui = bankUi();
  await ui.run('openPlaidLink("123456")');
  await ui.linkOptions().onSuccess("public-new", { institution: { name: "Test Bank" } });
  assert.equal(ui.calls[0].url, "/api/trackers/1/bank/link-token");
  assert.equal(ui.calls[1].url, "/api/trackers/1/bank/exchange-token");
  assert.deepEqual(ui.calls[1].body, {
    public_token: "public-new", bank_link_token: "challenge-test", institution_name: "Test Bank",
  });
});

test("Every existing Plaid connection offers Reconnect even without a login-required error", () => {
  const ui = bankUi();
  ui.run(`state.bankConnections = [
    { id: 3, institution_name: "Test Bank", provider: "plaid", status: "active", accounts: [] },
    { id: 4, institution_name: "Other Bank", provider: "plaid", status: "error", accounts: [] },
    { id: 5, institution_name: "Demo", provider: "demo", status: "active", accounts: [] }
  ]`);
  const html = ui.run("renderBankImport()");
  assert.match(html, /data-reconnect-bank="3"/);
  assert.match(html, /data-reconnect-bank="4"/);
  assert.doesNotMatch(html, /data-reconnect-bank="5"/);
});

test("Failed sync refreshes the persisted reconnection status and keeps its message", async () => {
  const ui = bankUi();
  ui.context.fetch = async (url) => ({
    ok: !url.includes("/sync?"),
    status: url.includes("/sync?") ? 409 : 200,
    text: async () => JSON.stringify(url.includes("/sync?")
      ? { detail: "Reconnect this bank account to continue syncing transactions." }
      : [{ id: 3, status: "reauth_required" }]),
  });
  await ui.run("syncBankConnection(3)");
  assert.equal(ui.run("state.bankConnections[0].status"), "reauth_required");
  assert.match(ui.run("state.error"), /Reconnect/);
  assert.equal(ui.run("state.syncingBankConnectionId"), null);
});

test("Empty sync explains that Plaid may not have fetched recent bank activity", async () => {
  const ui = bankUi();
  ui.run('state.bankConnections = [{ id: 3, institution_name: "Test Bank", accounts: [] }]');
  await ui.run("syncBankConnection(3)");
  assert.match(ui.run("state.bankSyncResult.message"), /Test Bank: 0 added, 0 updated, 0 removed/);
  assert.match(ui.run("state.bankSyncResult.message"), /Plaid may not have fetched recent bank activity yet/);
  assert.match(ui.run("renderBankImport()"), /role="status"/);
  ui.run("state.trackerId = 2; state.trackers.push({ id: 2 })");
  assert.doesNotMatch(ui.run("renderBankImport()"), /role="status"/);
});

test("Sync reports provider changes separately from reviewable transactions", async () => {
  const ui = bankUi();
  ui.context.fetch = async () => ({ ok: true, status: 201, text: async () => JSON.stringify({ added: 3, modified: 2, removed: 1 }) });
  ui.run(`refresh = async () => {
    state.bankTransactions = [{ connection_id: 3 }, { connection_id: 4 }];
  }`);
  await ui.run("syncBankConnection(3)");
  assert.match(ui.run("state.bankSyncResult.message"), /3 added, 2 updated, 1 removed. 1 available to review/);
  assert.doesNotMatch(ui.run("state.bankSyncResult.message"), /No new transaction updates/);
});

test("Sync distinguishes data still being prepared from no new updates", async () => {
  const ui = bankUi();
  ui.context.fetch = async () => ({ ok: true, status: 201, text: async () => JSON.stringify({
    added: 0, modified: 0, removed: 0, transactions_update_status: "NOT_READY",
  }) });
  await ui.run("syncBankConnection(3)");
  assert.match(ui.run("state.bankSyncResult.message"), /still preparing transaction data/);
  assert.doesNotMatch(ui.run("state.bankSyncResult.message"), /No new transaction updates/);
  ui.context.fetch = async () => ({ ok: false, status: 502, text: async () => JSON.stringify({ detail: "Bank unavailable" }) });
  await ui.run("syncBankConnection(3)");
  assert.equal(ui.run("state.bankSyncResult"), null);
  assert.equal(ui.run("state.error"), "Bank unavailable");
});

test("Leaving update Link preserves the connection and does not start a sync", async () => {
  const ui = bankUi();
  ui.run('state.bankConnections = [{ id: 3, status: "reauth_required" }]');
  await ui.run('openPlaidLink("123456", 3)');
  ui.linkOptions().onExit(null);
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.run("state.bankConnections[0].status"), "reauth_required");
  ui.linkOptions().onExit({ display_message: "Please try again later." });
  assert.equal(ui.run("state.error"), "Please try again later.");
});

test("Cancelling removal sends no request and keeps the bank connection", async () => {
  const ui = bankUi();
  ui.run('state.bankConnections = [{ id: 3, institution_name: "Test Bank" }]');
  let confirmation;
  ui.context.window.confirm = (message) => { confirmation = message; return false; };
  await ui.run("removeBankConnection(3)");
  assert.match(confirmation, /Test Bank/);
  assert.match(confirmation, /Expenses already imported will be kept/);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.run("state.bankConnections.length"), 1);
  assert.equal(ui.run("state.removingBankConnectionId"), null);
});

test("Confirmed removal deletes only the selected bank connection and refreshes", async () => {
  const ui = bankUi();
  ui.run(`
    state.bankConnections = [{ id: 3, institution_name: "Test Bank" }, { id: 4, institution_name: "Other Bank" }];
    globalThis.refreshCalls = 0;
    refresh = async () => { refreshCalls += 1; };
  `);
  ui.context.window.confirm = () => true;
  await ui.run("removeBankConnection(3)");
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].method, "DELETE");
  assert.equal(ui.calls[0].url, "/api/trackers/1/bank/connections/3");
  assert.equal(ui.run("refreshCalls"), 1);
  assert.equal(ui.run("state.removingBankConnectionId"), null);
});

test("Failed removal shows an error and leaves the connection available", async () => {
  const ui = bankUi();
  ui.run('state.bankConnections = [{ id: 3, institution_name: "Test Bank" }]');
  ui.context.window.confirm = () => true;
  ui.context.fetch = async () => ({ ok: false, status: 500, text: async () => JSON.stringify({ detail: "Removal failed" }) });
  await ui.run("removeBankConnection(3)");
  assert.equal(ui.run("state.error"), "Removal failed");
  assert.equal(ui.run("state.bankConnections[0].id"), 3);
  assert.equal(ui.run("state.removingBankConnectionId"), null);
});

test("Removal cannot start during a sync or another removal", async () => {
  const ui = bankUi();
  ui.run('state.bankConnections = [{ id: 3, institution_name: "Test Bank" }]');
  ui.context.window.confirm = () => { assert.fail("Busy bank actions must not prompt for removal"); };
  ui.run("state.syncingBankConnectionId = 3");
  await ui.run("removeBankConnection(3)");
  ui.run("state.syncingBankConnectionId = null; state.removingBankConnectionId = 4");
  await ui.run("removeBankConnection(3)");
  assert.equal(ui.calls.length, 0);
});
