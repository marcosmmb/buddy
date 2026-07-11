const CURRENCY_FALLBACKS = ["USD", "CAD", "EUR", "GBP", "MXN", "BRL", "ARS", "CLP", "AUD", "JPY"];

const state = {
  token: localStorage.getItem("buddy_token"),
  user: null,
  users: [],
  currencies: CURRENCY_FALLBACKS,
  trackers: [],
  trackerId: Number(localStorage.getItem("buddy_tracker_id")) || null,
  sidebarCollapsed: localStorage.getItem("buddy_sidebar_collapsed") === "true",
  tab: localStorage.getItem("buddy_tab") || "overview",
  periodType: localStorage.getItem("buddy_period_type") || "month",
  period: localStorage.getItem("buddy_period") || new Date().toISOString().slice(0, 7),
  expenseMonth: localStorage.getItem("buddy_expense_month") || new Date().toISOString().slice(0, 7),
  categoryChartMember: localStorage.getItem("buddy_category_chart_member") || "all",
  categories: [],
  expenses: [],
  overview: null,
  periodOptions: { months: [], years: [] },
  csvConfigs: [],
  monthlyShares: { month: "", shares: [] },
  csvModal: null,
  error: "",
};

const app = document.querySelector("#app");
const autosaveTimers = new Map();

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

function renderError() {
  if (!state.error) return "";
  return `
    <div class="error">
      <span>${escapeHtml(state.error)}</span>
      <button class="icon-button" id="close-error" type="button" aria-label="Close error">X</button>
    </div>
  `;
}

function monthChoices(selected = state.expenseMonth) {
  const options = state.periodOptions.months.length ? state.periodOptions.months : [selected];
  return options.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}

function monthTotal() {
  return state.expenses.reduce((total, expense) => total + Number(expense.amount || 0), 0);
}

function monthSharedTotal() {
  return state.expenses.reduce((total, expense) => total + (expense.is_shared ? Number(expense.amount || 0) : 0), 0);
}

function expenseDuplicateMap(expenses) {
  const counts = new Map();
  for (const expense of expenses) {
    const key = [
      expense.date,
      expense.category_id,
      expense.paid_by_id,
      Number(expense.amount).toFixed(2),
      expense.description.trim().toLowerCase(),
      expense.is_shared ? "shared" : "individual",
    ].join("|");
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const result = new Map();
  for (const expense of expenses) {
    const key = [
      expense.date,
      expense.category_id,
      expense.paid_by_id,
      Number(expense.amount).toFixed(2),
      expense.description.trim().toLowerCase(),
      expense.is_shared ? "shared" : "individual",
    ].join("|");
    result.set(expense.id, counts.get(key) > 1);
  }
  return result;
}

function duplicateExpenses(expenses) {
  const duplicates = expenseDuplicateMap(expenses);
  return expenses.filter((expense) => duplicates.get(expense.id));
}

function paidTotalsByMember(expenses = state.expenses) {
  const rows = new Map();
  for (const expense of expenses) {
    const row = rows.get(expense.paid_by_id) || {
      user_id: expense.paid_by_id,
      name: expense.paid_by,
      paid_shared: 0,
      paid_individual: 0,
      paid_total: 0,
    };
    const amount = Number(expense.amount || 0);
    if (expense.is_shared) row.paid_shared += amount;
    else row.paid_individual += amount;
    row.paid_total += amount;
    rows.set(expense.paid_by_id, row);
  }
  return rows;
}

function memberResponsibilityTotals(expenses = state.expenses) {
  const rows = new Map();
  const shares = new Map();
  const monthlyShares = state.monthlyShares.shares?.length
    ? state.monthlyShares.shares
    : (currentTracker()?.members || []).map((member) => ({
        user_id: member.user_id,
        name: member.name,
        share_percent: member.share_percent,
      }));
  for (const share of monthlyShares) {
    rows.set(share.user_id, {
      user_id: share.user_id,
      name: share.name,
      responsibility_shared: 0,
      responsibility_individual: 0,
      responsibility_total: 0,
    });
    shares.set(share.user_id, Number(share.share_percent || 0) / 100);
  }
  for (const expense of expenses) {
    const amount = Number(expense.amount || 0);
    if (expense.is_shared) {
      for (const [userId, shareRatio] of shares.entries()) {
        const row = rows.get(userId);
        if (!row) continue;
        const allocated = amount * shareRatio;
        row.responsibility_shared += allocated;
        row.responsibility_total += allocated;
      }
    } else {
      const row = rows.get(expense.paid_by_id) || {
        user_id: expense.paid_by_id,
        name: expense.paid_by,
        responsibility_shared: 0,
        responsibility_individual: 0,
        responsibility_total: 0,
      };
      row.responsibility_individual += amount;
      row.responsibility_total += amount;
      rows.set(expense.paid_by_id, row);
    }
  }
  return rows;
}

function memberBreakdownFromExpenses(expenses = state.expenses) {
  const responsibilityRows = memberResponsibilityTotals(expenses);
  const paidRows = paidTotalsByMember(expenses);
  const ids = new Set([...responsibilityRows.keys(), ...paidRows.keys()]);
  return [...ids]
    .map((userId) => {
      const responsibility = responsibilityRows.get(userId) || {};
      const paid = paidRows.get(userId) || {};
      return {
        user_id: userId,
        name: responsibility.name || paid.name || "Unknown",
        responsibility_shared: responsibility.responsibility_shared || 0,
        responsibility_individual: responsibility.responsibility_individual || 0,
        responsibility_total: responsibility.responsibility_total || 0,
        paid_shared: paid.paid_shared || 0,
        paid_individual: paid.paid_individual || 0,
        paid_total: paid.paid_total || 0,
      };
    })
    .filter((row) => row.responsibility_total || row.paid_total)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function renderMemberBreakdown(rows, emptyText = "No expenses for this selection.") {
  const tracker = currentTracker();
  return renderTable(
    "Member breakdown",
    ["Member", "Total expenses", "Shared expenses", "Individual expenses", "Paid total", "Paid shared", "Paid individual"],
    (rows || []).map((row) => [
      escapeHtml(row.name),
      currency(row.responsibility_total, tracker.default_currency),
      currency(row.responsibility_shared, tracker.default_currency),
      currency(row.responsibility_individual, tracker.default_currency),
      currency(row.paid_total, tracker.default_currency),
      currency(row.paid_shared, tracker.default_currency),
      currency(row.paid_individual, tracker.default_currency),
    ]),
    true,
    emptyText,
  );
}

function categoryRowsForSelectedMember(summary) {
  if (state.categoryChartMember === "all") return summary.by_category || [];
  const selectedId = Number(state.categoryChartMember);
  const selectedMember = currentTracker()?.members.find((member) => member.user_id === selectedId);
  if (!selectedMember) return summary.by_category || [];
  const totals = new Map();
  for (const row of summary.by_person_category || []) {
    if (row.person === selectedMember.name) totals.set(row.category, (totals.get(row.category) || 0) + Number(row.total || 0));
  }
  return [...totals.entries()].map(([name, total]) => ({ name, total })).sort((a, b) => a.name.localeCompare(b.name));
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
  const expenseParams = new URLSearchParams({ month: state.expenseMonth });
  const shareParams = new URLSearchParams({ month: state.expenseMonth });
  const [categories, expenses, overview, periodOptions, csvConfigs, monthlyShares] = await Promise.all([
    api(`/api/trackers/${tracker.id}/categories`),
    api(`/api/trackers/${tracker.id}/expenses?${expenseParams}`),
    api(`/api/trackers/${tracker.id}/overview?${overviewParams}`),
    api(`/api/trackers/${tracker.id}/period-options`),
    api(`/api/trackers/${tracker.id}/csv-configs`),
    api(`/api/trackers/${tracker.id}/monthly-shares?${shareParams}`),
  ]);
  state.categories = categories;
  state.expenses = expenses;
  state.overview = overview;
  state.periodOptions = periodOptions;
  state.csvConfigs = csvConfigs;
  state.monthlyShares = monthlyShares;
}

function renderAuth() {
  app.innerHTML = `
    <main class="auth-page">
      <section class="auth-hero">
        <div class="auth-brand-cluster">
          <div class="brand auth-brand"><span class="mark auth-mark">B</span><span>Buddy</span></div>
          <img class="auth-mascot" src="/static/buddy-mascot-bee.png" alt="Buddy bee mascot" />
        </div>
        <h1>Budgets, shared costs, and payback math in one self-hosted place.</h1>
      </section>
      <section class="auth-panel">
        <div class="stack">
          <h2>Sign in</h2>
          ${renderError()}
          <form id="login-form" class="stack">
            <label>Email<input name="email" type="email" required value="admin@buddy.local" /></label>
            <label>Password
              <span class="password-field">
                <input id="login-password" name="password" type="password" required value="change-me-now" />
                <button class="password-toggle" id="toggle-login-password" type="button" aria-label="Show password">Show</button>
              </span>
            </label>
            <button class="button primary" type="submit">Sign in</button>
          </form>
        </div>
      </section>
    </main>
  `;
  document.querySelector("#login-form").addEventListener("submit", submitLogin);
  document.querySelector("#toggle-login-password")?.addEventListener("click", () => {
    const input = document.querySelector("#login-password");
    const button = document.querySelector("#toggle-login-password");
    const isHidden = input.type === "password";
    input.type = isHidden ? "text" : "password";
    button.textContent = isHidden ? "Hide" : "Show";
    button.setAttribute("aria-label", isHidden ? "Hide password" : "Show password");
  });
  document.querySelector("#close-error")?.addEventListener("click", () => {
    state.error = "";
    renderAuth();
  });
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
    <div class="shell ${state.sidebarCollapsed ? "sidebar-collapsed" : ""}">
      <aside class="sidebar">
        <div class="brand-row">
          <div class="brand"><span class="mark">B</span><span class="sidebar-text">Buddy</span></div>
          <button class="icon-button sidebar-toggle" id="sidebar-toggle" type="button" aria-label="Toggle menu">${state.sidebarCollapsed ? ">" : "<"}</button>
        </div>
        <div class="user-block">
          <div class="sidebar-text">
            <div class="user-name">${escapeHtml(state.user.name)}</div>
            <div class="user-email">${escapeHtml(state.user.email)}</div>
            <div class="tiny">${state.user.is_admin ? "Admin" : "Member"} · ${escapeHtml(state.user.default_currency)}</div>
          </div>
        </div>
        <div class="nav-label sidebar-text">Trackers</div>
        <div class="tracker-list">
          ${state.trackers
            .map(
              (item) => `
                <button class="tracker-button ${item.id === state.trackerId ? "active" : ""}" data-tracker="${item.id}">
                  <strong>${escapeHtml(item.name)}</strong><br />
                  <span class="tiny sidebar-text">${item.members.length} members · ${escapeHtml(item.default_currency)}</span>
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
        ${renderError()}
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
  if (value === "expenses") return "Monthly Expenses";
  return value.split("-").map((part) => part[0].toUpperCase() + part.slice(1)).join(" ");
}

function periodChoices() {
  const values = state.periodType === "year" ? state.periodOptions.years.map(String) : state.periodOptions.months;
  const selected = values.includes(String(state.period)) ? String(state.period) : String(state.period);
  const options = values.length ? values : [selected];
  return options.map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value}</option>`).join("");
}

function barWidth(value, max) {
  if (!max) return 0;
  return Math.max(4, Math.round((Math.abs(Number(value || 0)) / max) * 100));
}

function renderBarChart(title, rows, labelKey = "name", valueKey = "total") {
  if (!rows?.length) return `<div class="panel stack"><h2>${escapeHtml(title)}</h2><div class="empty">No chart data for this selection.</div></div>`;
  return `<div class="panel stack"><h2>${escapeHtml(title)}</h2>${renderBarChartRows(rows, labelKey, valueKey)}</div>`;
}

function renderBarChartRows(rows, labelKey = "name", valueKey = "total") {
  if (!rows?.length) return `<div class="empty">No chart data for this selection.</div>`;
  const max = Math.max(...rows.map((row) => Math.abs(Number(row[valueKey] || 0))));
  return `
    <div class="bar-chart">
      ${rows
        .map(
          (row) => `
          <div class="bar-row">
            <div class="bar-label">${escapeHtml(row[labelKey])}</div>
            <div class="bar-track"><div class="bar-fill" style="width: ${barWidth(row[valueKey], max)}%"></div></div>
            <div class="bar-value">${currency(row[valueKey])}</div>
          </div>
        `,
        )
        .join("")}
    </div>
  `;
}

function renderMonthlySettlements() {
  const tracker = currentTracker();
  const balance = state.overview?.balance;
  const settlements = balance?.settlements || [];
  const rows = settlements.map((row) => [
    escapeHtml(row.from),
    escapeHtml(row.to),
    currency(row.amount, tracker.default_currency),
  ]);
  return `
    <div class="panel stack">
      <div>
        <h2>Who owes who</h2>
        <p class="muted">Based on shared expenses and the share split for ${escapeHtml(state.overview?.period || state.period)}.</p>
      </div>
      ${
        rows.length
          ? `<div class="table-scroll"><table>
              <thead><tr><th>From</th><th>To</th><th>Amount</th></tr></thead>
              <tbody>${rows.map((row) => `<tr><td>${row[0]}</td><td>${row[1]}</td><td class="amount">${row[2]}</td></tr>`).join("")}</tbody>
            </table></div>`
          : `<div class="empty">All settled for this month.</div>`
      }
    </div>
  `;
}

function renderCategoryBreakdownTable(data) {
  const byPerson = new Map();
  for (const row of data.by_person_category || []) {
    if (!byPerson.has(row.category)) byPerson.set(row.category, []);
    byPerson.get(row.category).push(`${escapeHtml(row.person)}: ${currency(row.total)}`);
  }
  return renderTable(
    "Total by category",
    ["Category", "Total", "Paid by person"],
    (data.by_category || []).map((row) => [
      escapeHtml(row.name),
      currency(row.total),
      byPerson.get(row.name)?.join("<br />") || "",
    ]),
    true,
  );
}

function renderDuplicateExpenseSection(expenses) {
  const rows = duplicateExpenses(expenses || []);
  if (!rows.length) return "";
  return `
    <div class="panel stack">
      <div>
        <h2>Possible duplicates</h2>
        <p class="muted">These entries match another expense by date, category, payer, amount, description, and type.</p>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Date</th><th>Category</th><th>Paid by</th><th>Description</th><th>Type</th><th>Amount</th></tr></thead>
          <tbody>
            ${rows
              .map(
                (expense) => `
                <tr class="duplicate-row">
                  <td>${escapeHtml(expense.date)}</td>
                  <td><span class="swatch" style="background:${escapeHtml(expense.category_color)}"></span>${escapeHtml(expense.category)}</td>
                  <td>${escapeHtml(expense.paid_by)}</td>
                  <td>${escapeHtml(expense.description)}<span class="duplicate-pill">Possible duplicate</span></td>
                  <td><span class="pill">${expense.is_shared ? "Shared" : "Individual"}</span></td>
                  <td class="amount">${currency(expense.amount, expense.currency)}</td>
                </tr>
              `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

function renderOverview() {
  const data = state.overview?.summary || {};
  const payerRows = data.by_person?.map((row) => ({ name: row.name, total: row.total })) || [];
  const categoryRows = categoryRowsForSelectedMember(data);
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
      ${renderDuplicateExpenseSection(state.overview?.expenses || [])}
      <div class="grid three">
        <div class="card metric"><span class="muted">${state.periodType === "year" ? "Year total" : "Month total"}</span><span class="metric-value">${currency(data.total)}</span></div>
        <div class="card metric"><span class="muted">Categories</span><span class="metric-value">${data.by_category?.length || 0}</span></div>
        <div class="card metric"><span class="muted">Payers</span><span class="metric-value">${data.by_person?.length || 0}</span></div>
      </div>
      <div class="grid two">
        <div class="panel stack">
          <div class="row between">
            <h2>Category chart</h2>
            <label class="compact-label">Member
              <select id="category-chart-member">
                <option value="all" ${state.categoryChartMember === "all" ? "selected" : ""}>All</option>
                ${(currentTracker()?.members || []).map((member) => `<option value="${member.user_id}" ${String(member.user_id) === state.categoryChartMember ? "selected" : ""}>${escapeHtml(member.name)}</option>`).join("")}
              </select>
            </label>
          </div>
          ${renderBarChartRows(categoryRows)}
        </div>
        ${renderBarChart("Payer chart", payerRows)}
      </div>
      ${state.periodType === "year" ? renderBarChart("Monthly chart", state.overview?.monthly_totals || [], "month", "total") : ""}
      ${state.periodType === "year" ? renderTable("Total by month", ["Month", "Total"], state.overview?.monthly_totals?.map((row) => [row.month, currency(row.total)]) || []) : ""}
      ${state.periodType === "month" ? renderMonthlySettlements() : ""}
      ${renderMemberBreakdown(state.overview?.member_breakdown || [])}
      ${renderCategoryBreakdownTable(data)}
      ${state.periodType === "month" ? `<div class="panel stack"><h2>Expenses this month</h2>${renderExpenseTable(state.overview?.expenses || [])}</div>` : ""}
    </section>
  `;
}

function renderExpenses() {
  const tracker = currentTracker();
  return `
    <section class="stack">
      <div class="toolbar">
        <label>Expense month<select id="expense-month-select">${monthChoices()}</select></label>
        <div class="card metric compact-metric"><span class="muted">Month total</span><span class="metric-value" id="expense-month-total">${currency(monthTotal(), tracker.default_currency)}</span></div>
        <div class="card metric compact-metric"><span class="muted">Month shared total</span><span class="metric-value" id="expense-month-shared-total">${currency(monthSharedTotal(), tracker.default_currency)}</span></div>
      </div>
      ${renderDuplicateExpenseSection(state.expenses)}
      <div class="panel stack">
        <div>
          <h2>Share split for ${escapeHtml(state.expenseMonth)}</h2>
          <p class="muted">This split is used to allocate shared expenses in this month. It starts from the tracker defaults until you save a custom split.</p>
        </div>
        ${
          canManageTracker()
            ? `<form id="monthly-shares-form" class="stack">
                ${state.monthlyShares.shares
                  .map(
                    (share) => `
                    <div class="row between">
                      <div>
                        <strong>${escapeHtml(share.name)}</strong>
                        <div class="muted">Default ${Number(share.default_share_percent).toFixed(2)}%${share.has_override ? " · Custom for this month" : ""}</div>
                      </div>
                      <label style="max-width: 170px">Month share %<input type="number" step="0.01" min="0" max="100" name="monthly_share_${share.user_id}" value="${share.share_percent ?? share.default_share_percent}" /></label>
                    </div>
                  `,
                  )
                  .join("")}
                <button class="button primary" type="submit">Save monthly shares</button>
              </form>`
            : `<div class="empty">Only tracker owners can manage monthly shares.</div>`
        }
      </div>
      <div id="member-month-breakdown">
        ${renderMemberBreakdown(memberBreakdownFromExpenses(state.expenses), "No expenses for this month.")}
      </div>
      <div class="grid two">
      <div class="panel stack">
        <h2>Add expense</h2>
        <form id="expense-form" class="stack">
          <div class="form-row">
            <label>Date<input name="date" type="date" required value="${new Date().toISOString().slice(0, 10)}" /></label>
            <label>Amount<input name="amount" type="number" step="0.01" min="0" required /></label>
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
        <p class="muted">Preview CSV rows before adding them. Rows are unselected by default.</p>
        <button class="button" id="open-csv-import" ${state.csvConfigs.length && state.categories.length ? "" : "disabled"}>Open CSV import</button>
      </div>
      <div class="panel stack" style="grid-column: 1 / -1">
        <div class="row between">
          <h2>Expenses</h2>
          <button class="button small" id="bulk-delete-expenses">Delete selected</button>
        </div>
        ${renderExpenseTable(state.expenses, true)}
      </div>
      </div>
    </section>
    ${renderCsvModal()}
  `;
}

function renderExpenseTable(expenses, editable = false) {
  if (!expenses.length) return `<div class="empty">No expenses for this selection.</div>`;
  const duplicates = expenseDuplicateMap(expenses);
  return `
    <div class="table-scroll">
      <table>
        <thead><tr>${editable ? "<th></th>" : ""}<th>Date</th><th>Category</th><th>Paid by</th><th>Description</th><th>Type</th><th>Amount</th>${editable ? "<th></th>" : ""}</tr></thead>
        <tbody>
          ${expenses
            .map(
              (expense) => `
              <tr data-expense-row="${expense.id}" class="${duplicates.get(expense.id) ? "duplicate-row" : ""}">
                ${editable ? `<td><input class="compact-check" type="checkbox" data-expense-select="${expense.id}" /></td>` : ""}
                <td>${editable ? `<input class="table-input" name="date" type="date" value="${escapeHtml(expense.date)}" />` : escapeHtml(expense.date)}</td>
                <td>${
                  editable
                    ? `<select class="table-input" name="category_id">${state.categories.map((category) => `<option value="${category.id}" ${category.id === expense.category_id ? "selected" : ""}>${escapeHtml(category.name)}</option>`).join("")}</select>`
                    : `<span class="swatch" style="background:${escapeHtml(expense.category_color)}"></span>${escapeHtml(expense.category)}`
                }</td>
                <td>${
                  editable
                    ? `<select class="table-input" name="paid_by_id">${currentTracker().members.map((member) => `<option value="${member.user_id}" ${member.user_id === expense.paid_by_id ? "selected" : ""}>${escapeHtml(member.name)}</option>`).join("")}</select>`
                    : escapeHtml(expense.paid_by)
                }</td>
                <td>
                  ${editable ? `<input class="table-input" name="description" value="${escapeHtml(expense.description)}" />` : escapeHtml(expense.description)}
                  ${duplicates.get(expense.id) ? `<span class="duplicate-pill">Possible duplicate</span>` : ""}
                </td>
                <td>${
                  editable
                    ? `<label class="check-row table-check"><input name="is_shared" type="checkbox" ${expense.is_shared ? "checked" : ""} /> Shared</label>`
                    : `<span class="pill">${expense.is_shared ? "Shared" : "Individual"}</span>`
                }</td>
                <td class="amount">${
                  editable
                    ? `<input class="table-input amount-input" name="amount" type="number" step="0.01" value="${expense.amount}" />`
                    : currency(expense.amount, expense.currency)
                }</td>
                ${
                  editable
                    ? `<td><div class="row"><span class="autosave-note" data-autosave-status="${expense.id}">Autosaves</span><button class="button small" data-delete-expense="${expense.id}">Delete</button></div></td>`
                    : ""
                }
              </tr>
            `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCsvModal() {
  if (!state.csvModal?.open) return "";
  const tracker = currentTracker();
  const rows = state.csvModal.preview?.rows || [];
  const skipped = state.csvModal.preview?.skipped || [];
  return `
    <div class="modal-backdrop">
      <div class="modal panel stack">
        <div class="row between">
          <h2>Import CSV</h2>
          <button class="button small" id="close-csv-import">Close</button>
        </div>
        ${
          rows.length
            ? `
              <div class="stack">
                <div class="row between">
                  <p class="muted">Review parsed expenses and select the rows to import.</p>
                  <div class="row">
                    <strong id="preview-selected-total">${currency(0)}</strong>
                    <button class="button small" id="toggle-preview-selection">Select all</button>
                  </div>
                </div>
                ${
                  skipped.length
                    ? `<div class="error">${skipped.length} rows could not be parsed.</div>`
                    : ""
                }
                <div class="table-scroll">
                  <table>
                    <thead><tr><th></th><th>Date</th><th>Category</th><th>Paid by</th><th>Description</th><th>Type</th><th>Amount</th></tr></thead>
                    <tbody>
                      ${rows
                        .map(
                          (row, index) => `
                          <tr>
                            <td><input class="compact-check" type="checkbox" data-preview-row="${index}" /></td>
                            <td>${escapeHtml(row.date)}</td>
                            <td>${escapeHtml(row.category)}</td>
                            <td>${escapeHtml(row.paid_by)}</td>
                            <td>${escapeHtml(row.description)}</td>
                            <td>
                              <select class="table-input preview-shared-select" data-preview-shared="${index}">
                                <option value="false" ${row.is_shared ? "" : "selected"}>Individual</option>
                                <option value="true" ${row.is_shared ? "selected" : ""}>Shared</option>
                              </select>
                            </td>
                            <td class="amount">${currency(row.amount, row.currency)}</td>
                          </tr>
                        `,
                        )
                        .join("")}
                    </tbody>
                  </table>
                </div>
                <button class="button primary" id="confirm-csv-import">Import selected</button>
              </div>
            `
            : `
              <form id="csv-import-form" class="stack">
                <label>Schema<select name="config_id" required>${state.csvConfigs.map((config) => `<option value="${config.id}">${escapeHtml(config.name)}</option>`).join("")}</select></label>
                <label>CSV file<input name="csv_file" type="file" accept=".csv,text/csv" required /></label>
                <div class="form-row">
                  <label>Fallback category<select name="fallback_category_id" required>${state.categories.map((category) => `<option value="${category.id}">${escapeHtml(category.name)}</option>`).join("")}</select></label>
                  <label>Fallback paid by<select name="fallback_paid_by_id" required>${tracker.members.map((member) => `<option value="${member.user_id}">${escapeHtml(member.name)}</option>`).join("")}</select></label>
                </div>
                <button class="button primary" type="submit">Preview CSV</button>
              </form>
            `
        }
      </div>
    </div>
  `;
}

function renderTrackerSettings() {
  const tracker = currentTracker();
  return `
    <section class="grid two">
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Tracker settings</h2>
        ${
          canManageTracker()
            ? `<form id="tracker-settings-form" class="grid two">
                <label>Name<input name="name" required value="${escapeHtml(tracker.name)}" /></label>
                <label>Currency<select name="default_currency">${currencyOptions(tracker.default_currency)}</select></label>
                <div class="row">
                  <button class="button primary" type="submit">Save tracker</button>
                  <button class="button danger" id="delete-tracker" type="button">Delete tracker</button>
                </div>
              </form>`
            : `<div class="empty">Only tracker owners can update tracker settings.</div>`
        }
      </div>
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Default members and shares</h2>
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
      <div class="panel stack" style="grid-column: 1 / -1">
        <h2>Categories</h2>
        <form id="category-form" class="stack">
          <label>Name<input name="name" required /></label>
          <label>Color<input name="color" type="color" value="#f1b84b" /></label>
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
      ${
        state.user.is_admin
          ? `<div class="panel stack" style="grid-column: 1 / -1">
              <h2>CSV import schemas</h2>
              <form id="csv-config-form" class="grid two">
                <label>Name<input name="name" required placeholder="Scotiabank credit" /></label>
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
                ["Name", "Invert", "Mapped fields", ""],
                state.csvConfigs.map((config) => [
                  config.name,
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

function renderTable(title, headers, rows, raw = false, emptyText = "No data for this selection.") {
  return `
    <div class="panel stack">
      <h2>${escapeHtml(title)}</h2>
      ${
        rows.length
          ? `<div class="table-scroll"><table>
                <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
                <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${raw ? cell : escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
              </table></div>`
          : `<div class="empty">${escapeHtml(emptyText)}</div>`
      }
    </div>
  `;
}

function bindAppEvents() {
  document.querySelector("#close-error")?.addEventListener("click", () => {
    state.error = "";
    renderApp();
  });
  document.querySelector("#sidebar-toggle")?.addEventListener("click", () => {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    localStorage.setItem("buddy_sidebar_collapsed", String(state.sidebarCollapsed));
    renderApp();
  });
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
  document.querySelector("#expense-month-select")?.addEventListener("change", async (event) => {
    state.expenseMonth = event.target.value;
    localStorage.setItem("buddy_expense_month", state.expenseMonth);
    await refresh();
  });
  document.querySelector("#category-chart-member")?.addEventListener("change", (event) => {
    state.categoryChartMember = event.target.value;
    localStorage.setItem("buddy_category_chart_member", state.categoryChartMember);
    renderApp();
  });
  bindForms();
}

function bindForms() {
  document.querySelector("#tracker-form")?.addEventListener("submit", submitTracker);
  document.querySelector("#tracker-settings-form")?.addEventListener("submit", submitTrackerSettings);
  document.querySelector("#delete-tracker")?.addEventListener("click", deleteCurrentTracker);
  document.querySelector("#admin-user-form")?.addEventListener("submit", submitAdminUser);
  document.querySelector("#category-form")?.addEventListener("submit", submitCategory);
  document.querySelector("#expense-form")?.addEventListener("submit", submitExpense);
  document.querySelector("#csv-import-form")?.addEventListener("submit", submitCsvPreview);
  document.querySelector("#csv-config-form")?.addEventListener("submit", submitCsvConfig);
  document.querySelector("#profile-form")?.addEventListener("submit", submitProfile);
  document.querySelector("#members-form")?.addEventListener("submit", submitMembers);
  document.querySelector("#monthly-shares-form")?.addEventListener("submit", submitMonthlyShares);
  document.querySelector("#open-csv-import")?.addEventListener("click", () => {
    state.csvModal = { open: true, preview: null };
    renderApp();
  });
  document.querySelector("#close-csv-import")?.addEventListener("click", () => {
    state.csvModal = null;
    renderApp();
  });
  document.querySelector("#toggle-preview-selection")?.addEventListener("click", togglePreviewSelection);
  document.querySelectorAll("[data-preview-row]").forEach((input) => input.addEventListener("change", updatePreviewSelectionUi));
  document.querySelectorAll("[data-preview-shared]").forEach((input) => input.addEventListener("change", updatePreviewSharedValue));
  document.querySelector("#confirm-csv-import")?.addEventListener("click", confirmCsvImport);
  document.querySelector("#bulk-delete-expenses")?.addEventListener("click", bulkDeleteExpenses);
  document.querySelectorAll("[data-delete-user]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/admin/users/${button.dataset.deleteUser}`, { method: "DELETE" }))));
  document.querySelectorAll("[data-delete-category]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/trackers/${currentTracker().id}/categories/${button.dataset.deleteCategory}`, { method: "DELETE" }))));
  document.querySelectorAll("[data-delete-csv-config]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/trackers/${currentTracker().id}/csv-configs/${button.dataset.deleteCsvConfig}`, { method: "DELETE" }))));
  document.querySelectorAll("[data-expense-row]").forEach((row) => {
    row.querySelectorAll("input, select").forEach((field) => {
      if (field.matches("[data-expense-select]")) return;
      field.addEventListener("change", () => scheduleExpenseAutosave(Number(row.dataset.expenseRow)));
    });
  });
  document.querySelectorAll("[data-delete-expense]").forEach((button) => button.addEventListener("click", () => mutate(() => api(`/api/trackers/${currentTracker().id}/expenses/${button.dataset.deleteExpense}`, { method: "DELETE" }))));
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

async function submitTrackerSettings(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  await mutate(() =>
    api(`/api/trackers/${tracker.id}`, {
      method: "PUT",
      body: JSON.stringify({
        name: formData.get("name"),
        default_currency: formData.get("default_currency"),
      }),
    }),
  );
}

async function deleteCurrentTracker() {
  const tracker = currentTracker();
  if (!tracker) return;
  if (!window.confirm(`Delete tracker "${tracker.name}" and all of its expenses? This cannot be undone.`)) return;
  state.error = "";
  try {
    await api(`/api/trackers/${tracker.id}`, { method: "DELETE" });
    state.trackerId = null;
    state.tab = "overview";
    localStorage.removeItem("buddy_tracker_id");
    localStorage.setItem("buddy_tab", state.tab);
    await refresh();
  } catch (error) {
    state.error = error.message;
    renderApp();
  }
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
        paid_by_id: Number(formData.get("paid_by_id")),
        description: formData.get("description"),
        is_shared: formData.get("is_shared") === "on",
      }),
    }),
  );
}

function expensePayloadFromRow(expenseId) {
  const row = document.querySelector(`[data-expense-row="${expenseId}"]`);
  return {
    date: row.querySelector('[name="date"]').value,
    category_id: Number(row.querySelector('[name="category_id"]').value),
    amount: row.querySelector('[name="amount"]').value,
    paid_by_id: Number(row.querySelector('[name="paid_by_id"]').value),
    description: row.querySelector('[name="description"]').value,
    is_shared: row.querySelector('[name="is_shared"]').checked,
  };
}

async function saveExpense(expenseId) {
  const tracker = currentTracker();
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/expenses/${expenseId}`, {
      method: "PUT",
      body: JSON.stringify(expensePayloadFromRow(expenseId)),
    }),
  );
}

function setAutosaveStatus(expenseId, text, tone = "") {
  const status = document.querySelector(`[data-autosave-status="${expenseId}"]`);
  if (!status) return;
  status.textContent = text;
  status.className = `autosave-note ${tone}`.trim();
}

function scheduleExpenseAutosave(expenseId) {
  clearTimeout(autosaveTimers.get(expenseId));
  setAutosaveStatus(expenseId, "Saving...");
  autosaveTimers.set(
    expenseId,
    setTimeout(async () => {
      const tracker = currentTracker();
      try {
        const updated = await api(`/api/trackers/${tracker.id}/expenses/${expenseId}`, {
          method: "PUT",
          body: JSON.stringify(expensePayloadFromRow(expenseId)),
        });
        state.expenses = state.expenses.map((expense) => (expense.id === expenseId ? updated : expense));
        const total = document.querySelector("#expense-month-total");
        if (total) total.textContent = currency(monthTotal(), tracker.default_currency);
        const sharedTotal = document.querySelector("#expense-month-shared-total");
        if (sharedTotal) sharedTotal.textContent = currency(monthSharedTotal(), tracker.default_currency);
        const memberBreakdown = document.querySelector("#member-month-breakdown");
        if (memberBreakdown) memberBreakdown.innerHTML = renderMemberBreakdown(memberBreakdownFromExpenses(state.expenses), "No expenses for this month.");
        setAutosaveStatus(expenseId, "Saved", "positive");
      } catch (error) {
        state.error = error.message;
        setAutosaveStatus(expenseId, "Not saved", "negative");
        renderApp();
      }
    }, 650),
  );
}

async function bulkDeleteExpenses() {
  const tracker = currentTracker();
  const expenseIds = [...document.querySelectorAll("[data-expense-select]:checked")].map((input) => Number(input.dataset.expenseSelect));
  if (!expenseIds.length) {
    state.error = "Select at least one expense to delete.";
    renderApp();
    return;
  }
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/expenses/bulk-delete`, {
      method: "POST",
      body: JSON.stringify({ expense_ids: expenseIds }),
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

async function submitCsvPreview(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  const file = formData.get("csv_file");
  const csvText = await file.text();
  try {
    const preview = await api(`/api/trackers/${tracker.id}/csv-imports/preview`, {
      method: "POST",
      body: JSON.stringify({
        config_id: Number(formData.get("config_id")),
        csv_text: csvText,
        fallback_category_id: Number(formData.get("fallback_category_id")),
        fallback_paid_by_id: Number(formData.get("fallback_paid_by_id")),
      }),
    });
    state.csvModal = { open: true, preview };
    renderApp();
    updatePreviewSelectionUi();
  } catch (error) {
    state.error = error.message;
    renderApp();
  }
}

function selectedPreviewRows() {
  const rows = state.csvModal?.preview?.rows || [];
  return [...document.querySelectorAll("[data-preview-row]:checked")].map((input) => rows[Number(input.dataset.previewRow)]);
}

function updatePreviewSelectionUi() {
  const checkboxes = [...document.querySelectorAll("[data-preview-row]")];
  const selected = selectedPreviewRows();
  const total = selected.reduce((sum, row) => sum + Number(row?.amount || 0), 0);
  const totalNode = document.querySelector("#preview-selected-total");
  if (totalNode) totalNode.textContent = `Selected total ${currency(total)}`;
  const toggle = document.querySelector("#toggle-preview-selection");
  if (toggle) toggle.textContent = checkboxes.length && checkboxes.every((input) => input.checked) ? "Unselect all" : "Select all";
}

function togglePreviewSelection() {
  const checkboxes = [...document.querySelectorAll("[data-preview-row]")];
  const shouldSelect = !checkboxes.length || !checkboxes.every((input) => input.checked);
  checkboxes.forEach((input) => {
    input.checked = shouldSelect;
  });
  updatePreviewSelectionUi();
}

function updatePreviewSharedValue(event) {
  const index = Number(event.target.dataset.previewShared);
  const row = state.csvModal?.preview?.rows?.[index];
  if (row) row.is_shared = event.target.value === "true";
}

async function confirmCsvImport() {
  const tracker = currentTracker();
  const selected = selectedPreviewRows();
  if (!selected.length) {
    state.error = "Select at least one preview row to import.";
    renderApp();
    return;
  }
  state.csvModal = null;
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/csv-imports`, {
      method: "POST",
      body: JSON.stringify({
        expenses: selected.map((row) => ({
          date: row.date,
          category_id: row.category_id,
          amount: row.amount,
          paid_by_id: row.paid_by_id,
          description: row.description,
          is_shared: row.is_shared,
        })),
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

async function submitMonthlyShares(event) {
  event.preventDefault();
  const tracker = currentTracker();
  const formData = new FormData(event.currentTarget);
  const shares = state.monthlyShares.shares.map((share) => ({
    user_id: share.user_id,
    share_percent: Number(formData.get(`monthly_share_${share.user_id}`) || 0),
  }));
  const total = shares.reduce((sum, share) => sum + share.share_percent, 0);
  if (total > 100) {
    state.error = "Member share percentages cannot exceed 100%.";
    renderApp();
    return;
  }
  await mutate(() =>
    api(`/api/trackers/${tracker.id}/monthly-shares`, {
      method: "PUT",
      body: JSON.stringify({ month: state.expenseMonth, shares }),
    }),
  );
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
