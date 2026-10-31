const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function renderLogin(version, pending = false) {
  const source = fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8");
  const app = { innerHTML: "" };
  const context = vm.createContext({
    URLSearchParams,
    localStorage: { getItem: () => null },
    document: {
      querySelector: (selector) => selector === "#app" ? app
        : selector === 'meta[name="app-version"]' && version !== undefined ? { content: version } : null,
      querySelectorAll: () => [],
    },
    window: { location: { search: "" }, addEventListener: () => {} },
  });
  vm.runInContext(source.replace(/\nbootstrap\(\);\s*$/, "\n"), context);
  if (pending) vm.runInContext('state.pendingLogin = { challenge_token: "test" }', context);
  vm.runInContext("renderAuth()", context);
  return app.innerHTML;
}

test("Login never displays missing or unexpanded version metadata", () => {
  for (const version of [undefined, "", "  ", "__APP_VERSION__"]) {
    for (const pending of [false, true]) {
      const html = renderLogin(version, pending);
      assert.doesNotMatch(html, /__APP_VERSION__|class="auth-version"/);
      assert.match(html, pending ? /id="login-2fa-form"/ : /id="login-form"/);
    }
  }
});

test("Login displays the server version as escaped text", () => {
  for (const pending of [false, true]) {
    assert.match(renderLogin("0.9.2", pending), /Version 0\.9\.2/);
    const html = renderLogin('<img src=x onerror="alert(1)">', pending);
    assert.match(html, /Version &lt;img/);
    assert.doesNotMatch(html, /<img src=x/);
  }
});
