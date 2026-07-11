const CURRENCY_FALLBACKS = ["USD", "CAD", "EUR", "GBP", "MXN", "BRL", "ARS", "CLP", "AUD", "JPY"];

const state = {
  token: localStorage.getItem("buddy_token"),
  user: null,
  users: [],
  currencies: CURRENCY_FALLBACKS,
  trackers: [],
  trackerId: Number(localStorage.getItem("buddy_tracker_id")) || null,
  tab: localStorage.getItem("buddy_tab") || "overview",
  periodType: localStorage.getItem("buddy_period_type") || "month",
  period: localStorage.getItem("buddy_period") || new Date().toISOString().slice(0, 7),
  categories: [],
  expenses: [],
  overview: null,
  periodOptions: { months: [], years: [] },
  csvConfigs: [],
  error: "",
};

const app = document.querySelector("#app");

function applyTheme() {
  document.body.dataset.theme = state.user?.theme || localStorage.getItem("buddy_theme") || "light";
}

function currency(value, code = currentTracker()?.default_currency || state.user?.default_currency || "USD") {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(Number(value || 0));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function currentTracker() {
  return state.trackers.find((tracker) => tracker.id === state.trackerId) || state.trackers[0] || null;
}

function currentMember() {
  const tracker = currentTracker();
  return tracker?.members.find((member) => member.user_id === state.user?.id) || null;
}

function canManageTracker() {
  return Boolean(state.user?.is_admin || currentMember()?.role === "owner");
}

function currencyOptions(selected) {
  return state.currencies
    .map((code) => `<option value="${code}" ${code === selected ? "selected" : ""}>${code}</option>`)
    .join("");
}

async function api(path, options = {}) {
  const headers = { "content-type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(data?.detail || "Request failed");
  return data;
}

async function bootstrap() {
  applyTheme();
  if (!state.token) {
    renderAuth();
    return;
  }
  try {
    state.user = await api("/api/me");
    localStorage.setItem("buddy_theme", state.user.theme);
    applyTheme();
    await loadBase();
    await loadTrackerData();
    renderApp();
  } catch (error) {
    localStorage.removeItem("buddy_token");
    state.token = null;
    state.error = error.message;
    renderAuth();
  }
}

async function loadBase() {
  const [users, trackers, currencies] = await Promise.all([api("/api/users"), api("/api/trackers"), api("/api/currencies")]);
  state.users = users;
  state.trackers = trackers;
  state.currencies = currencies;
  if (!state.trackerId && trackers.length) state.trackerId = trackers[0].id;
  if (state.trackerId && !trackers.some((tracker) => tracker.id === state.trackerId)) state.trackerId = trackers[0]?.id || null;
  if (state.trackerId) localStorage.setItem("buddy_tracker_id", String(state.trackerId));
}

async function loadTrackerData() {
  const tracker = currentTracker();
  if (!tracker) return;
  const overviewParams = new URLSearchParams({ period_type: state.periodType, period: state.period });
  const expenseParams = state.periodType === "year" ? new URLSearchParams({ year: state.period }) : new URLSearchParams({ month: state.period });
  const [categories, expenses, overview, periodOptions, csvConfigs] = await Promise.all([
    api(`/api/trackers/${tracker.id}/categories`),
    api(`/api/trackers/${tracker.id}/expenses?${expenseParams}`),
    api(`/api/trackers/${tracker.id}/overview?${overviewParams}`),
    api(`/api/trackers/${tracker.id}/period-options`),
    api(`/api/trackers/${tracker.id}/csv-configs`),
  ]);
  state.categories = categories;
  state.expenses = expenses;
  state.overview = overview;
  state.periodOptions = periodOptions;
  state.csvConfigs = csvConfigs;
}

function renderAuth() {
  app.innerHTML = `
    <main class="auth-page">
      <section class="auth-hero">
        <div class="brand"><span class="mark">B</span><span>Buddy</span></div>
        <h1>Budgets, shared costs, and payback math in one self-hosted place.</h1>
      </section>
      <section class="auth-panel">
        <div class="stack">
          <h2>Sign in</h2>
          ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
          <form id="login-form" class="stack">
            <label>Email<input name="email" type="email" required value="admin@buddy.local" /></label>
            <label>Password<input name="password" type="password" required value="change-me-now" /></label>
            <button class="button primary" type="submit">Sign in</button>
          </form>
        </div>
      </section>
    </main>
  `;
  document.querySelector("#login-form").addEventListener("submit", submitLogin);
}

async function submitLogin(event) {
  event.preventDefault();
  state.error = "";
  try {
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())) });
    state.token = result.token;
    state.user = result.user;
    localStorage.setItem("buddy_token", state.token);
    localStorage.setItem("buddy_theme", state.user.theme);
    applyTheme();
    await loadBase();
    await loadTrackerData();
    renderApp();
  } catch (error) {
    state.error = error.message;
    renderAuth();
  }
}

function renderApp() {
  const tracker = currentTracker();
  app.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand"><span class="mark">B</span><span>Buddy</span></div>
        <div class="user-block">
          <div class="user-name">${escapeHtml(state.user.name)}</div>
          <div class="user-email">${escapeHtml(state.user.email)}</div>
          <div class="tiny">${state.user.is_admin ? "Admin" : "Member"} · ${escapeHtml(state.user.default_currency)}</div>
        </div>
        <div class="tracker-list">
          ${state.trackers
            .map(
              (item) => `
                <button class="tracker-button ${item.id === state.trackerId ? "active" : ""}" data-tracker="${item.id}">
                  <strong>${escapeHtml(item.name)}</strong><br />
                  <span class="tiny">${item.members.length} members · ${escapeHtml(item.default_currency)}</span>
                </button>
              `,
            )
            .join("")}
        </div>
        <div class="tracker-list">
          <button class="tracker-button ${state.tab === "user-settings" ? "active" : ""}" data-tab="user-settings">User settings</button>
          ${state.user.is_admin ? `<button class="tracker-button ${state.tab === "admin" ? "active" : ""}" data-tab="admin">Admin</button>` : ""}
        </div>
        <button class="button ghost" id="logout-button">Sign out</button>
      </aside>
      <main class="main">
        <div class="topbar">
          <div>
            <h1>${state.tab === "admin" ? "Admin" : state.tab === "user-settings" ? "User Settings" : tracker ? escapeHtml(tracker.name) : "Buddy"}</h1>
            <p class="muted">${tracker && !["admin", "user-settings"].includes(state.tab) ? `${tracker.members.length} members` : "Self-hosted budgeting and expense tracking."}</p>
          </div>
        </div>
        ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
        ${renderContent()}
      </main>
    </div>
  `;
  bindAppEvents();
}

function renderContent() {
  if (state.tab === "admin") return state.user.is_admin ? renderAdmin() : `<div class="empty">Admin access required.</div>`;
  if (state.tab === "user-settings") return renderUserSettings();
  if (!currentTracker()) return state.user.is_admin ? renderAdmin() : `<div class="empty">No trackers yet.</div>`;
  const tabs = ["overview", "expenses", "settings"];
  if (!tabs.includes(state.tab)) state.tab = "overview";
  return `
    <div class="tabs">
      ${tabs.map((tab) => `<button class="tab ${state.tab === tab ? "active" : ""}" data-tab="${tab}">${label(tab)}</button>`).join("")}
    </div>
    ${state.tab === "overview" ? renderOverview() : ""}
    ${state.tab === "expenses" ? renderExpenses() : ""}
    ${state.tab === "settings" ? renderTrackerSettings() : ""}
  `;
}

function label(value) {
  return value.split("-").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}

function periodChoices() {
  const values = state.periodType === "year" ? state.periodOptions.years.map(String) : state.periodOptions.months;
  const selected = values.includes(String(state.period)) ? String(state.period) : String(state.period);
  const options = values.length ? values : [selected];
  return options.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}

function renderOverview() {
  const data = state.overview?.summary || {};
  return `
    <section class="stack">
      <div class="toolbar">
        <label>Period type
          <select id="period-type">
            <option value="month" ${state.periodType === "month" ? "selected" : ""}>Month</option>
            <option value="year" ${state.periodType === "year" ? "selected" : ""}>Year</option>
          </select>
        </label>
        <label>Period<select id="period-select">${periodChoices()}</select></label>
      </div>
      <div class="grid three">
        <div class="card metric"><span class="muted">${state.periodType === "year" ? "Year total" : "Month total"}</span><span class="metric-value">${currency(data.total)}</span></div>
        <div class="card metric"><span class="muted">Categories</span><span class="metric-value">${data.by_category?.length || 0}</span></div>
        <div class="card metric"><span class="muted">Payers</span><span class="metric-value">${data.by_person?.length || 0}</span></div>
      </div>
      ${state.periodType === "year" ? renderTable("Total by month", ["Month", "Total"], state.overview?.monthly_totals?.map((row) => [row.month, currency(row.total)]) || []) : ""}
      <div class="grid two">
        ${renderTable("Total by category", ["Category", "Total"], data.by_category?.map((row) => [row.name, currency(row.total)]) || [])}
        ${renderTable("Total paid by person", ["Person", "Shared", "Individual", "Total"], data.by_person?.map((row) => [row.name, currency(row.shared), currency(row.individual), currency(row.total)]) || [])}
      </div>
      ${renderTable("Paid by person for each category", ["Person", "Category", "Total"], data.by_person_category?.map((row) => [row.person, row.category, currency(row.total)]) || [])}
      ${state.periodType === "month" ? `<div class="panel stack"><h2>Expenses this month</h2>${renderExpenseTable(state.overview?.expenses || [])}</div>` : ""}
    </section>
  `;
}

function renderExpenses() {
  const tracker = currentTracker();
  return `
    <section class="grid two">
      <div class="panel stack">
        <h2>Add expense</h2>
        <form id="expense-form" class="stack">
          <div class="form-row">
            <label>Date<input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}" /></label>
            <label>Amount<input name="amount" type="number" step="0.01" min="0" required /></label>
            <label>Currency<select name="currency" required>${currencyOptions(tracker.default_currency)}</select></label>
          </div>
          <div class="form-row">
            <label>Category<select name="category_id" required>${state.categories.map((category) => `<option value="${category.id}">${escapeHtml(category.name)}</option>`).join("")}</select></label>
            <label>Paid by<select name="paid_by_id" required>${tracker.members.map((member) => `<option value="${member.user_id}">${escapeHtml(member.name)}</option>`).join("")}</select></label>
          </div>
          <label>Description<textarea name="description"></textarea></label>
          <label class="check-row"><input name="is_shared" type="checkbox" checked /> Shared expense</label>
          <button class="button primary" type="submit" ${state.categories.length ? "" : "disabled"}>Add expense</button>
        </form>
      </div>
      <div class="panel stack">
        <h2>Import CSV</h2>
        <form id="csv-import-form" class="stack">
          <label>Schema<select name="config_id" required>${state.csvConfigs.map((config) => `<option value="${config.id}">${escapeHtml(config.name)}</option>`).join("")}</select></label>
          <label>CSV file<input name="csv_file" type="file" accept=".csv,text/csv" required /></label>
          <label>Fallback category<select name="fallback_category_id" required>${state.categories.map((category) => `<option value="${category.id}">${escapeHtml(category.name)}</option>`).join("")}</select></label>
          <label>Fallback paid by<select name="fallback_paid_by_id" required>${tracker.members.map((member) => `<option value="${member.user_id}">${escapeHtml(member.name)}</option>`).join("")}</select></label>
          <label class="check-row"><input name="is_shared" type="checkbox" checked /> Imported expenses are shared</label>
          <button class="button" type="submit" ${state.csvConfigs.length && state.categories.length ? "" : "disabled"}>Import</button>
        </form>
      </div>
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Expenses</h2>
        ${renderExpenseTable(state.expenses)}
      </div>
    </section>
  `;
}

function renderExpenseTable(expenses) {
  if (!expenses.length) return `<div class="empty">No expenses for this selection.</div>`;
  return `
    <table>
      <thead><tr><th>Date</th><th>Category</th><th>Paid by</th><th>Description</th><th>Type</th><th>Amount</th></tr></thead>
      <tbody>
        ${expenses
          .map(
            (expense) => `
            <tr>
              <td>${escapeHtml(expense.date)}</td>
              <td><span class="swatch" style="background:${escapeHtml(expense.category_color)}"></span>${escapeHtml(expense.category)}</td>
              <td>${escapeHtml(expense.paid_by)}</td>
              <td>${escapeHtml(expense.description)}</td>
              <td><span class="pill">${expense.is_shared ? "Shared" : "Individual"}</span></td>
              <td class="amount">${currency(expense.amount, expense.currency)}</td>
            </tr>
          `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function renderTrackerSettings() {
  const tracker = currentTracker();
  return `
    <section class="grid two">
      <div class="panel stack">
        <h2>Categories</h2>
        <form id="category-form" class="stack">
          <label>Name<input name="name" required /></label>
          <label>Color<input name="color" type="color" value="#4677ff" /></label>
          <button class="button" type="submit">Add category</button>
        </form>
        <div class="stack">
          ${state.categories
            .map(
              (category) => `
              <div class="row between">
                <span><span class="swatch" style="background:${escapeHtml(category.color)}"></span>${escapeHtml(category.name)}</span>
                ${canManageTracker() ? `<button class="button small" data-delete-category="${category.id}">Delete</button>` : ""}
              </div>
            `,
            )
            .join("") || '<span class="muted">No categories yet.</span>'}
        </div>
      </div>
      <div class="panel stack">
        <h2>Members and shares</h2>
        ${
          canManageTracker()
            ? `<form id="members-form" class="stack">
                ${state.users
                  .map((user) => {
                    const member = tracker.members.find((item) => item.user_id === user.id);
                    return `
                      <div class="row between">
                        <label class="check-row"><input type="checkbox" name="member_${user.id}" ${member ? "checked" : ""} /> ${escapeHtml(user.name)}</label>
                        <label style="max-width: 120px">Share %<input type="number" step="0.01" min="0" max="100" name="share_${user.id}" value="${member?.share_percent ?? 0}" /></label>
                        <label style="max-width: 120px">Role<select name="role_${user.id}"><option value="member" ${member?.role !== "owner" ? "selected" : ""}>Member</option><option value="owner" ${member?.role === "owner" ? "selected" : ""}>Owner</option></select></label>
                      </div>
                    `;
                  })
                  .join("")}
                <button class="button primary" type="submit">Save members</button>
              </form>`
            : `<div class="empty">Only tracker owners can manage members.</div>`
        }
      </div>
      ${
        state.user.is_admin
          ? `<div class="panel stack" style="grid-column: 1 / -1">
              <h2>CSV import schemas</h2>
              <form id="csv-config-form" class="grid two">
                <label>Name<input name="name" required placeholder="Scotiabank credit" /></label>
                <label>Currency<select name="currency">${currencyOptions(tracker.default_currency)}</select></label>
                <label>Date column<input name="date" placeholder="Date" required /></label>
                <label>Amount column<input name="amount" placeholder="Amount" required /></label>
                <label>Description column<input name="description" placeholder="Description" /></label>
                <label>Category column<input name="category" placeholder="Category" /></label>
                <label>Paid by column<input name="paid_by" placeholder="Paid by" /></label>
                <label class="check-row"><input name="invert_amount" type="checkbox" /> Invert amount sign</label>
                <button class="button primary" type="submit">Save CSV schema</button>
              </form>
              ${renderTable(
                "Saved schemas",
                ["Name", "Currency", "Invert", "Mapped fields", ""],
                state.csvConfigs.map((config) => [
                  config.name,
                  config.currency,
                  config.invert_amount ? "Yes" : "No",
                  escapeHtml(Object.entries(config.field_map).map(([key, value]) => `${key}: ${value}`).join(", ")),
                  `<button class="button small" data-delete-csv-config="${config.id}">Delete</button>`,
                ]),
                true,
              )}
            </div>`
          : ""
      }
    </section>
  `;
}

function renderUserSettings() {
  return `
    <section class="panel stack">
      <h2>User settings</h2>
      <form id="profile-form" class="grid two">
        <label>Display name<input name="name" value="${escapeHtml(state.user.name)}" /></label>
        <label>Default currency<select name="default_currency">${currencyOptions(state.user.default_currency)}</select></label>
        <label>Theme<select name="theme"><option value="light" ${state.user.theme === "light" ? "selected" : ""}>Light</option><option value="dark" ${state.user.theme === "dark" ? "selected" : ""}>Dark</option></select></label>
        <label>Current password<input name="current_password" type="password" /></label>
        <label>New password<input name="new_password" type="password" minlength="8" /></label>
        <div></div>
        <button class="button primary" type="submit">Save settings</button>
      </form>
    </section>
  `;
}

function renderAdmin() {
  return `
    <section class="grid two">
      ${renderCreateTracker()}
      <div class="panel stack">
        <h2>Create user</h2>
        <form id="admin-user-form" class="stack">
          <label>Name<input name="name" required /></label>
          <label>Email<input name="email" type="email" required /></label>
          <label>Password<input name="password" type="password" minlength="8" required /></label>
          <label>Default currency<select name="default_currency">${currencyOptions(state.user.default_currency)}</select></label>
          <label class="check-row"><input name="is_admin" type="checkbox" /> Admin user</label>
          <button class="button primary" type="submit">Create user</button>
        </form>
      </div>
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Users</h2>
        ${renderTable(
          "Active accounts",
          ["Name", "Email", "Currency", "Role", ""],
          state.users.map((user) => [
            escapeHtml(user.name),
            escapeHtml(user.email),
            escapeHtml(user.default_currency),
            user.is_admin ? "Admin" : "Member",
            user.id === state.user.id ? "" : `<button class="button small" data-delete-user="${user.id}">Delete</button>`,
          ]),
          true,
        )}
      </div>
    </section>
  `;
}

function renderCreateTracker() {
  return `
    <div class="panel stack">
      <h2>Create tracker</h2>
      <form id="tracker-form" class="stack">
        <label>Name<input name="name" required /></label>
        <label>Currency<select name="default_currency">${currencyOptions(state.user?.default_currency || "USD")}</select></label>
        <div class="stack">
          ${state.users
            .map((user) => `<label class="check-row"><input type="checkbox" name="member_ids" value="${user.id}" ${user.id === state.user?.id ? "checked" : ""} /> ${escapeHtml(user.name)}</label>`)
            .join("")}
        </div>
        <button class="button primary" type="submit">Create tracker</button>
      </form>
    </div>
  `;
}

function renderTable(title, headers, rows, raw = false) {
  return `
    <div class="panel stack">
      <h2>${escapeHtml(title)}</h2>
      ${
        rows.length
          ? `<table>
              <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
              <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${raw ? cell : escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
            </table>`
          : `<div class="empty">No data for this selection.</div>`
      }
    </div>
  `;
}

function bindAppEvents() {
  document.querySelector("#logout-button")?.addEventListener("click", logout);
  document.querySelectorAll("[data-tracker]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.trackerId = Number(button.dataset.tracker);
      state.tab = "overview";
      localStorage.setItem("buddy_tracker_id", String(state.trackerId));
      localStorage.setItem("buddy_tab", state.tab);
      await refresh();
    });
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tab = button.dataset.tab;
      localStorage.setItem("buddy_tab", state.tab);
      renderApp();
    });
  });
  document.querySelector("#period-type")?.addEventListener("change", async (event) => {
    state.periodType = event.target.value;
    state.period = state.periodType === "year" ? String(new Date().getFullYear()) : new Date().toISOString().slice(0, 7);
    localStorage.setItem("buddy_period_type", state.periodType);
    localStorage.setItem("buddy_period", state.period);
    await refresh();
  });
  document.querySelector("#period-select")?.addEventListener("change", async (event) => {
    state.period = event.target.value;
    localStorage.setItem("buddy_period", state.period);
    await refresh();
  });
  bindForms();
}

function bindForms() {
  document.querySelector("#tracker-form")?.addEventListener("submit", submitTracker);
  document.querySelector("#admin-user-form")?.addEventListener("submit", submitAdminUser);
  document.querySelector("#category-form")?.addEventListener("submit", submitCategory);
  document.querySelector("#expense-form")?.addEventListener("submit", submitExpense);
  document.querySelector("#csv-import-form")?.addEventListener("submit", submitCsvImport);
  document.querySelector("#csv-config-form")?.addEventListener("submit", submitCsvConfig);
  document.querySelector("#profile-form")?.addEventListener("submit", submitProfile);
  document.querySelector("#members-form")?.addEventListener("submit", submitMembers);
  document.querySelectorAll("[data-delete-user]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/admin/users/${button.dataset.deleteUser}`, { method: "DELETE" }))));
  document.querySelectorAll("[data-delete-category]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/trackers/${currentTracker().id}/categories/${button.dataset.deleteCategory}`, { method: "DELETE" }))));
  document.querySelectorAll("[data-delete-csv-config]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/trackers/${currentTracker().id}/csv-configs/${button.dataset.deleteCsvConfig}`, { method: "DELETE" }))));
}

async function refresh() {
  state.error = "";
  try {
    await loadBase();
    await loadTrackerData();
  } catch (error) {
    state.error = error.message;
  }
  renderApp();
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "DELETE" });
  } catch (_) {}
  localStorage.removeItem("buddy_token");
  localStorage.removeItem("buddy_tracker_id");
  state.token = null;
  state.user = null;
  renderAuth();
}

async function submitTracker(event) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  await mutate(() =>
    api("/api/trackers", {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        default_currency: formData.get("default_currency"),
        member_ids: formData.getAll("member_ids").map(Number),
      }),
    }),
  );
}

async function submitAdminUser(event) {
  event.preventDefault();
  const formData = new FormData(event.currentTarget);
  await mutate(() =>
    api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        email: formData.get("email"),
        password: formData.get("password"),
        default_currency: formData.get("default_currency"),
        is_admin: formData.get("is_admin") === "on",
      }),
    }),
  );
}

async function submitCategory(event) {
  event.preventDefault();
  const tracker = currentTracker();
  await mutate(() => api(`/api/trackers/${tracker.id}/categories`, { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())) }));
}

async function submitExpense(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/expenses`, {
      method: "POST",
      body: JSON.stringify({
        date: formData.get("date"),
        category_id: Number(formData.get("category_id")),
        amount: formData.get("amount"),
        currency: formData.get("currency"),
        paid_by_id: Number(formData.get("paid_by_id")),
        description: formData.get("description"),
        is_shared: formData.get("is_shared") === "on",
      }),
    }),
  );
}

async function submitCsvConfig(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/csv-configs`, {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        currency: formData.get("currency"),
        invert_amount: formData.get("invert_amount") === "on",
        field_map: {
          date: formData.get("date"),
          amount: formData.get("amount"),
          description: formData.get("description"),
          category: formData.get("category"),
          paid_by: formData.get("paid_by"),
        },
      }),
    }),
  );
}

async function submitCsvImport(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  const file = formData.get("csv_file");
  const csvText = await file.text();
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/csv-imports`, {
      method: "POST",
      body: JSON.stringify({
        config_id: Number(formData.get("config_id")),
        csv_text: csvText,
        fallback_category_id: Number(formData.get("fallback_category_id")),
        fallback_paid_by_id: Number(formData.get("fallback_paid_by_id")),
        is_shared: formData.get("is_shared") === "on",
      }),
    }),
  );
}

async function submitProfile(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  if (!data.new_password) {
    delete data.new_password;
    delete data.current_password;
  }
  await mutate(async () => {
    state.user = await api("/api/me/preferences", { method: "PUT", body: JSON.stringify(data) });
    localStorage.setItem("buddy_theme", state.user.theme);
    applyTheme();
  });
}

async function submitMembers(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  const members = state.users
    .filter((user) => formData.get(`member_${user.id}`) === "on")
    .map((user) => ({
      user_id: user.id,
      role: formData.get(`role_${user.id}`) || "member",
      share_percent: Number(formData.get(`share_${user.id}`) || 0),
    }));
  const total = members.reduce((sum, member) => sum + member.share_percent, 0);
  if (total > 100) {
    state.error = "Member share percentages cannot exceed 100%.";
    renderApp();
    return;
  }
  await mutate(() => api(`/api/trackers/${tracker.id}/members`, { method: "PUT", body: JSON.stringify({ members }) }));
}

async function mutate(operation) {
  state.error = "";
  try {
    const result = await operation();
    if (result?.imported !== undefined) state.error = `Imported ${result.imported} expenses. Skipped ${result.skipped.length}.`;
    await refresh();
  } catch (error) {
    state.error = error.message;
    renderApp();
  }
}

bootstrap();
