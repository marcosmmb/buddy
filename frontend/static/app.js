const state = {
  token: localStorage.getItem("buddy_token"),
  user: null,
  users: [],
  trackers: [],
  trackerId: Number(localStorage.getItem("buddy_tracker_id")) || null,
  tab: localStorage.getItem("buddy_tab") || "overview",
  month: new Date().toISOString().slice(0, 7),
  year: new Date().getFullYear(),
  categories: [],
  expenses: [],
  overview: null,
  balance: null,
  ytd: null,
  error: "",
};

const app = document.querySelector("#app");

function currency(value, code = currentTracker()?.default_currency || state.user?.default_currency || "USD") {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: code,
  }).format(Number(value || 0));
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

async function api(path, options = {}) {
  const headers = {
    "content-type": "application/json",
    ...(options.headers || {}),
  };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || "Request failed");
  }
  return data;
}

async function bootstrap() {
  if (!state.token) {
    renderAuth();
    return;
  }
  try {
    state.user = await api("/api/me");
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
  const [users, trackers] = await Promise.all([api("/api/users"), api("/api/trackers")]);
  state.users = users;
  state.trackers = trackers;
  if (!state.trackerId && trackers.length) state.trackerId = trackers[0].id;
  if (state.trackerId && !trackers.some((tracker) => tracker.id === state.trackerId)) {
    state.trackerId = trackers[0]?.id || null;
  }
  if (state.trackerId) localStorage.setItem("buddy_tracker_id", String(state.trackerId));
}

async function loadTrackerData() {
  const tracker = currentTracker();
  if (!tracker) return;
  const params = new URLSearchParams({ month: state.month });
  const yearParams = new URLSearchParams({ year: String(state.year) });
  const [categories, expenses, overview, balance, ytd] = await Promise.all([
    api(`/api/trackers/${tracker.id}/categories`),
    api(`/api/trackers/${tracker.id}/expenses?${params}`),
    api(`/api/trackers/${tracker.id}/overview?${params}`),
    api(`/api/trackers/${tracker.id}/balance?${params}`),
    api(`/api/trackers/${tracker.id}/ytd?${yearParams}`),
  ]);
  state.categories = categories;
  state.expenses = expenses;
  state.overview = overview;
  state.balance = balance;
  state.ytd = ytd;
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
          <div class="panel">
            <h3>Create account</h3>
            <form id="register-form" class="stack" style="margin-top: 12px">
              <label>Name<input name="name" required /></label>
              <label>Email<input name="email" type="email" required /></label>
              <label>Password<input name="password" type="password" minlength="8" required /></label>
              <label>Currency<input name="default_currency" maxlength="3" value="USD" required /></label>
              <button class="button" type="submit">Create account</button>
            </form>
          </div>
        </div>
      </section>
    </main>
  `;

  document.querySelector("#login-form").addEventListener("submit", submitLogin);
  document.querySelector("#register-form").addEventListener("submit", submitRegister);
}

async function submitLogin(event) {
  event.preventDefault();
  await submitAuth("/api/auth/login", event.currentTarget);
}

async function submitRegister(event) {
  event.preventDefault();
  await submitAuth("/api/auth/register", event.currentTarget);
}

async function submitAuth(path, form) {
  state.error = "";
  try {
    const data = Object.fromEntries(new FormData(form).entries());
    const result = await api(path, { method: "POST", body: JSON.stringify(data) });
    state.token = result.token;
    state.user = result.user;
    localStorage.setItem("buddy_token", state.token);
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
        <button class="button ghost" id="logout-button">Sign out</button>
      </aside>
      <main class="main">
        <div class="topbar">
          <div>
            <h1>${tracker ? escapeHtml(tracker.name) : "Buddy"}</h1>
            <p class="muted">${tracker ? `${tracker.members.length} members` : "Create a tracker to begin."}</p>
          </div>
          <div class="actions">
            <label>Month<input id="month-input" type="month" value="${state.month}" /></label>
            <label>Year<input id="year-input" type="number" min="2000" max="2100" value="${state.year}" /></label>
          </div>
        </div>
        ${state.error ? `<div class="error">${escapeHtml(state.error)}</div>` : ""}
        ${renderTrackerShell()}
      </main>
    </div>
  `;

  bindAppEvents();
}

function renderTrackerShell() {
  if (!currentTracker()) {
    return state.user.is_admin ? renderCreateTracker() : `<div class="empty">No trackers yet.</div>`;
  }
  const tabs = ["overview", "expenses", "balance", "ytd", "settings"];
  return `
    <div class="tabs">
      ${tabs.map((tab) => `<button class="tab ${state.tab === tab ? "active" : ""}" data-tab="${tab}">${label(tab)}</button>`).join("")}
    </div>
    ${state.tab === "overview" ? renderOverview() : ""}
    ${state.tab === "expenses" ? renderExpenses() : ""}
    ${state.tab === "balance" ? renderBalance() : ""}
    ${state.tab === "ytd" ? renderYtd() : ""}
    ${state.tab === "settings" ? renderSettings() : ""}
  `;
}

function label(value) {
  return value === "ytd" ? "Year to Date" : value[0].toUpperCase() + value.slice(1);
}

function renderOverview(data = state.overview, title = "Overview") {
  const tracker = currentTracker();
  return `
    <section class="stack">
      <div class="grid three">
        <div class="card metric"><span class="muted">${title}</span><span class="metric-value">${currency(data?.total, tracker.default_currency)}</span></div>
        <div class="card metric"><span class="muted">Categories</span><span class="metric-value">${data?.by_category?.length || 0}</span></div>
        <div class="card metric"><span class="muted">Payers</span><span class="metric-value">${data?.by_person?.length || 0}</span></div>
      </div>
      <div class="grid two">
        ${renderTable("Total by category", ["Category", "Total"], data?.by_category?.map((row) => [row.name, currency(row.total)]) || [])}
        ${renderTable(
          "Total paid by person",
          ["Person", "Shared", "Individual", "Total"],
          data?.by_person?.map((row) => [row.name, currency(row.shared), currency(row.individual), currency(row.total)]) || [],
        )}
      </div>
      ${renderTable(
        "Paid by person for each category",
        ["Person", "Category", "Total"],
        data?.by_person_category?.map((row) => [row.person, row.category, currency(row.total)]) || [],
      )}
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
            <label>Currency<input name="currency" maxlength="3" required value="${escapeHtml(state.user.default_currency)}" /></label>
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
        <h2>Add category</h2>
        <form id="category-form" class="stack">
          <label>Name<input name="name" required /></label>
          <label>Color<input name="color" type="color" value="#4677ff" /></label>
          <button class="button" type="submit">Add category</button>
        </form>
        <div class="stack">
          ${state.categories.map((category) => `<span><span class="swatch" style="background:${escapeHtml(category.color)}"></span>${escapeHtml(category.name)}</span>`).join("") || '<span class="muted">No categories yet.</span>'}
        </div>
      </div>
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Expenses</h2>
        ${renderExpenseTable()}
      </div>
    </section>
  `;
}

function renderExpenseTable() {
  if (!state.expenses.length) return `<div class="empty">No expenses for this month.</div>`;
  return `
    <table>
      <thead><tr><th>Date</th><th>Category</th><th>Paid by</th><th>Description</th><th>Type</th><th>Amount</th></tr></thead>
      <tbody>
        ${state.expenses
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

function renderBalance(data = state.balance) {
  return `
    <section class="stack">
      <div class="grid two">
        <div class="card metric"><span class="muted">Shared total</span><span class="metric-value">${currency(data?.shared_total)}</span></div>
        <div class="card metric"><span class="muted">Settlements</span><span class="metric-value">${data?.settlements?.length || 0}</span></div>
      </div>
      ${renderTable(
        "Member balance",
        ["Person", "Share", "Paid shared", "Expected", "Individual", "Net"],
        data?.rows?.map((row) => [
          escapeHtml(row.name),
          `${row.share_percent.toFixed(2)}%`,
          currency(row.paid_shared),
          currency(row.expected_shared),
          currency(row.paid_individual),
          `<span class="${row.net >= 0 ? "positive" : "negative"}">${currency(row.net)}</span>`,
        ]) || [],
        true,
      )}
      ${renderTable(
        "Who owes whom",
        ["From", "To", "Amount"],
        data?.settlements?.map((row) => [row.from, row.to, currency(row.amount)]) || [],
      )}
    </section>
  `;
}

function renderYtd() {
  return `
    <section class="stack">
      ${renderOverview(state.ytd?.overview, `Year to date ${state.ytd?.year || state.year}`)}
      ${renderBalance(state.ytd?.balance)}
    </section>
  `;
}

function renderSettings() {
  const tracker = currentTracker();
  return `
    <section class="grid two">
      <div class="panel stack">
        <h2>Profile</h2>
        <form id="profile-form" class="stack">
          <label>Name<input name="name" value="${escapeHtml(state.user.name)}" /></label>
          <label>Default currency<input name="default_currency" maxlength="3" value="${escapeHtml(state.user.default_currency)}" /></label>
          <button class="button" type="submit">Save profile</button>
        </form>
      </div>
      ${state.user.is_admin ? renderCreateTracker() : ""}
      ${
        state.user.is_admin
          ? `<div class="panel stack" style="grid-column: 1 / -1">
              <h2>Members and shares</h2>
              <form id="members-form" class="stack">
                ${state.users
                  .map((user) => {
                    const member = tracker.members.find((item) => item.user_id === user.id);
                    return `
                      <div class="row between">
                        <label class="check-row"><input type="checkbox" name="member_${user.id}" ${member ? "checked" : ""} /> ${escapeHtml(user.name)}</label>
                        <label style="max-width: 150px">Share %<input type="number" step="0.01" min="0" name="share_${user.id}" value="${member?.share_percent ?? 0}" /></label>
                      </div>
                    `;
                  })
                  .join("")}
                <button class="button primary" type="submit">Save members</button>
              </form>
            </div>`
          : ""
      }
    </section>
  `;
}

function renderCreateTracker() {
  return `
    <div class="panel stack">
      <h2>Create tracker</h2>
      <form id="tracker-form" class="stack">
        <label>Name<input name="name" required /></label>
        <label>Currency<input name="default_currency" maxlength="3" value="${escapeHtml(state.user?.default_currency || "USD")}" required /></label>
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
              <tbody>${rows
                .map((row) => `<tr>${row.map((cell) => `<td>${raw ? cell : escapeHtml(cell)}</td>`).join("")}</tr>`)
                .join("")}</tbody>
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
      localStorage.setItem("buddy_tracker_id", String(state.trackerId));
      await refresh();
    });
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tab = button.dataset.tab;
      localStorage.setItem("buddy_tab", state.tab);
      renderApp();
      bindTabEvents();
    });
  });
  document.querySelector("#month-input")?.addEventListener("change", async (event) => {
    state.month = event.target.value;
    await refresh();
  });
  document.querySelector("#year-input")?.addEventListener("change", async (event) => {
    state.year = Number(event.target.value);
    await refresh();
  });
  bindTabEvents();
}

function bindTabEvents() {
  document.querySelector("#tracker-form")?.addEventListener("submit", submitTracker);
  document.querySelector("#category-form")?.addEventListener("submit", submitCategory);
  document.querySelector("#expense-form")?.addEventListener("submit", submitExpense);
  document.querySelector("#profile-form")?.addEventListener("submit", submitProfile);
  document.querySelector("#members-form")?.addEventListener("submit", submitMembers);
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
  } catch (_) {
    // Local logout should still succeed if the token is already invalid.
  }
  localStorage.removeItem("buddy_token");
  localStorage.removeItem("buddy_tracker_id");
  state.token = null;
  state.user = null;
  renderAuth();
}

async function submitTracker(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const memberIds = formData.getAll("member_ids").map(Number);
  await mutate(() =>
    api("/api/trackers", {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        default_currency: formData.get("default_currency"),
        member_ids: memberIds,
      }),
    }),
  );
}

async function submitCategory(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  await mutate(() => api(`/api/trackers/${tracker.id}/categories`, { method: "POST", body: JSON.stringify(data) }));
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

async function submitProfile(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  await mutate(async () => {
    state.user = await api("/api/me/preferences", { method: "PUT", body: JSON.stringify(data) });
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
      role: user.id === state.user.id ? "owner" : "member",
      share_percent: Number(formData.get(`share_${user.id}`) || 0),
    }));
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/members`, {
      method: "PUT",
      body: JSON.stringify({ members }),
    }),
  );
}

async function mutate(operation) {
  state.error = "";
  try {
    await operation();
    await refresh();
  } catch (error) {
    state.error = error.message;
    renderApp();
  }
}

bootstrap();
