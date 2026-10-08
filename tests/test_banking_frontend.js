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
      calls.push({ url, body: options.body && JSON.parse(options.body) });
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
  assert.equal(ui.calls[1].url, "/api/trackers/1/bank/connections/3/sync?days=8");
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
