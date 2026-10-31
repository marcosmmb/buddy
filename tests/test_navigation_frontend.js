const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("Sidebar toggle preserves named navigation and updates its expanded state", () => {
  const app = { innerHTML: "", querySelectorAll: () => [] };
  const listeners = {};
  const stored = {};
  const context = vm.createContext({
    URLSearchParams,
    localStorage: { getItem: () => null, setItem: (key, value) => { stored[key] = value; } },
    document: {
      querySelector: (selector) => selector === "#app" ? app
        : selector === "#sidebar-toggle" ? { addEventListener: (event, fn) => { listeners[event] = fn; } } : null,
      querySelectorAll: () => [],
    },
    window: { location: { search: "" }, addEventListener: () => {} },
  });
  const source = fs.readFileSync(path.join(__dirname, "../frontend/static/app.js"), "utf8");
  vm.runInContext(source.replace(/\nbootstrap\(\);\s*$/, "\n"), context);
  vm.runInContext(`
    state.user = { name: "Alex", email: "alex@example.test", is_admin: true, default_currency: "CAD" };
    state.trackers = [{ id: 1, name: 'Home & "Travel"', members: [], default_currency: "CAD" }];
    state.trackerId = 1;
    renderContent = () => "";
    renderApp();
  `, context);
  assert.match(app.innerHTML, /aria-label="Collapse menu"[^>]*aria-expanded="true"/);
  listeners.click();
  assert.equal(stored.buddy_sidebar_collapsed, "true");
  assert.match(app.innerHTML, /aria-label="Expand menu"[^>]*aria-expanded="false"/);
  assert.match(app.innerHTML, /aria-controls="sidebar-navigation"/);
  assert.match(app.innerHTML, /data-tracker="1" aria-label="Home &amp; &quot;Travel&quot;" title="Home &amp; &quot;Travel&quot;"/);
  for (const label of ["User settings", "Admin", "Sign out"]) {
    assert.ok(app.innerHTML.includes(`aria-label="${label}" title="${label}"><svg`));
  }
  listeners.click();
  assert.equal(stored.buddy_sidebar_collapsed, "false");
  assert.match(app.innerHTML, /aria-label="Collapse menu"[^>]*aria-expanded="true"/);
});
