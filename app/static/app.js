"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const money = (v) => (v == null ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let token = null;
try { token = localStorage.getItem("pharmaopt.token"); } catch (_) { /* storage unavailable */ }
let me = null;
let currentSupplier = null;

function setToken(value) {
  token = value;
  try { value ? localStorage.setItem("pharmaopt.token", value) : localStorage.removeItem("pharmaopt.token"); } catch (_) { /* ignore */ }
}

async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (res.status === 401 && token) { logout(); throw new Error("Session expired"); }
  if (res.status === 204) return null;
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = data?.detail;
    throw new Error(Array.isArray(detail) ? detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join(", ") : detail || res.statusText);
  }
  return data;
}

function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) out[k] = v;
  return out;
}
const numOrNull = (v) => (v === "" || v == null ? null : Number(v));

function table(columns, rows, rowClass = () => "") {
  if (!rows.length) return '<p class="muted">Nothing yet.</p>';
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r) => `<tr class="${rowClass(r)}">${columns.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : esc(c.value(r))}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- Auth ---------- */
$$("[data-auth]").forEach((btn) => btn.addEventListener("click", () => {
  $$("[data-auth]").forEach((b) => b.classList.toggle("active", b === btn));
  $("#login-form").classList.toggle("hidden", btn.dataset.auth !== "login");
  $("#register-form").classList.toggle("hidden", btn.dataset.auth !== "register");
  $("#auth-error").textContent = "";
}));

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { setToken((await api("/api/auth/login", { method: "POST", body: formData(e.target) })).access_token); start(); }
  catch (err) { $("#auth-error").textContent = err.message; }
});

$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { setToken((await api("/api/auth/register", { method: "POST", body: formData(e.target) })).access_token); start(); }
  catch (err) { $("#auth-error").textContent = err.message; }
});

function logout() {
  setToken(null);
  me = null;
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
}
$("#logout").addEventListener("click", logout);

async function start() {
  if (!token) { logout(); return; }
  try { me = await api("/api/auth/me"); } catch (_) { logout(); return; }
  $("#tenant-name").textContent = `${me.tenant_name} · ${me.email}`;
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  showTab("optimize");
}

/* ---------- Tabs ---------- */
const loaders = {
  optimize: loadOptimizeSuppliers,
  products: loadProducts,
  suppliers: loadSuppliers,
  compare: loadComparison,
  history: loadHistory,
  team: loadTeam,
};
function showTab(name) {
  $$("#main-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${name}`));
  loaders[name]?.();
}
$$("#main-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- Optimize ---------- */
async function loadOptimizeSuppliers() {
  const suppliers = await api("/api/suppliers");
  $("#optimize-suppliers").innerHTML = suppliers.filter((s) => s.active)
    .map((s) => `<label class="inline"><input type="checkbox" value="${s.id}" checked> ${esc(s.name)}</label>`).join("") ||
    '<span class="muted">No supplier yet.</span>';
}

$("#optimize-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = formData(e.target);
  const supplier_ids = $$("#optimize-suppliers input:checked").map((i) => Number(i.value));
  const target = $("#optimize-result");
  target.innerHTML = '<p class="muted">Optimizing…</p>';
  try {
    const result = await api("/api/optimize", {
      method: "POST",
      body: { budget: Number(f.budget), name: f.name, save: !!f.save, supplier_ids },
    });
    target.innerHTML = renderResult(result);
  } catch (err) {
    target.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
});

function renderResult(r) {
  const good = r.status === "optimal" || r.status === "feasible";
  let html = `<div class="banner ${good ? "ok" : "warn"}">${esc(r.message)}${r.plan_id ? ` – saved as order #${r.plan_id} (<a href="/api/orders/${r.plan_id}/export.csv" data-export="${r.plan_id}">export CSV</a>)` : ""}</div>`;
  if (r.lines.length) {
    html += `<div class="kpis">
      <div class="kpi"><div class="label">Expected profit</div><div class="value">${money(r.total_profit)}</div></div>
      <div class="kpi"><div class="label">Total cost</div><div class="value">${money(r.total_cost)}</div></div>
      <div class="kpi"><div class="label">Expected revenue</div><div class="value">${money(r.total_revenue)}</div></div>
      <div class="kpi"><div class="label">Budget left</div><div class="value">${money(r.budget_left)}</div></div>
      <div class="kpi"><div class="label">Markup (profit / cost)</div><div class="value">${r.total_cost ? ((r.total_profit / r.total_cost) * 100).toFixed(1) : "0"}%</div></div>
    </div>`;
    for (const s of r.suppliers) {
      const lines = r.lines.filter((l) => l.supplier_id === s.supplier_id);
      html += `<div class="card"><h3>${esc(s.supplier_name)}</h3>
        <p class="muted">${s.line_count} line(s) · subtotal ${money(s.subtotal)}${s.shipping_fee ? ` · shipping ${money(s.shipping_fee)}` : ""}</p>
        ${table([
          { label: "Code", value: (l) => l.product_code },
          { label: "Product", value: (l) => l.product_name },
          { label: "Qty", num: true, value: (l) => l.quantity },
          { label: "Unit price", num: true, value: (l) => money(l.unit_price) },
          { label: "Selling price", num: true, value: (l) => money(l.selling_price) },
          { label: "Cost", num: true, value: (l) => money(l.cost) },
          { label: "Profit", num: true, value: (l) => money(l.profit) },
        ], lines)}</div>`;
    }
  }
  if (r.unmet.length) {
    html += `<div class="card"><h3>Demand not covered</h3>${table([
      { label: "Code", value: (u) => u.product_code },
      { label: "Product", value: (u) => u.product_name },
      { label: "Missing qty", num: true, value: (u) => u.missing_quantity },
    ], r.unmet)}</div>`;
  }
  if (r.no_offer.length) html += `<p class="muted">No supplier offer for: ${r.no_offer.map(esc).join(", ")}</p>`;
  return html;
}

document.addEventListener("click", async (e) => {
  const link = e.target.closest("[data-export]");
  if (!link) return;
  e.preventDefault();
  const res = await fetch(link.getAttribute("href"), { headers: { Authorization: `Bearer ${token}` } });
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `order-${link.dataset.export}.csv` });
  a.click();
  URL.revokeObjectURL(url);
});

/* ---------- Products ---------- */
async function loadProducts() {
  const q = encodeURIComponent($("#product-search").value);
  const products = await api(`/api/products?q=${q}`);
  $("#product-table").innerHTML = table([
    { label: "Code", value: (p) => p.code },
    { label: "Name", value: (p) => p.name },
    { label: "Selling price", num: true, value: (p) => money(p.selling_price) },
    { label: "Max qty", num: true, value: (p) => p.max_quantity },
    { label: "Min qty", num: true, value: (p) => p.min_quantity },
    { label: "", html: (p) => `<button class="link" data-edit-product='${esc(JSON.stringify(p))}'>edit</button> <button class="link danger" data-del-product="${esc(p.code)}">delete</button>` },
  ], products);
}
$("#product-search").addEventListener("input", () => loadProducts());

$("#product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = formData(e.target);
  try {
    await api("/api/products", { method: "PUT", body: {
      code: f.code, name: f.name, selling_price: numOrNull(f.selling_price),
      max_quantity: Number(f.max_quantity || 0), min_quantity: Number(f.min_quantity || 0),
    } });
    e.target.reset();
    $("#product-msg").textContent = "Saved.";
    loadProducts();
  } catch (err) { $("#product-msg").textContent = err.message; }
});

$("#product-import").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/products/import", { method: "POST", form: new FormData(e.target) });
    $("#product-msg").textContent = `Imported: ${r.created} created, ${r.updated} updated${r.errors.length ? ` · ${r.errors.length} error(s): ${r.errors.slice(0, 5).join("; ")}` : ""}`;
    e.target.reset();
    loadProducts();
  } catch (err) { $("#product-msg").textContent = err.message; }
});

document.addEventListener("click", async (e) => {
  const edit = e.target.closest("[data-edit-product]");
  if (edit) {
    const p = JSON.parse(edit.dataset.editProduct);
    const form = $("#product-form");
    for (const k of ["code", "name", "selling_price", "max_quantity", "min_quantity"]) form.elements[k].value = p[k] ?? "";
    form.scrollIntoView({ behavior: "smooth" });
  }
  const del = e.target.closest("[data-del-product]");
  if (del && confirm(`Delete product ${del.dataset.delProduct} and its catalog entries?`)) {
    await api(`/api/products/${encodeURIComponent(del.dataset.delProduct)}`, { method: "DELETE" });
    loadProducts();
  }
});

/* ---------- Suppliers & catalogs ---------- */
async function loadSuppliers() {
  const suppliers = await api("/api/suppliers");
  $("#supplier-table").innerHTML = table([
    { label: "Name", value: (s) => s.name },
    { label: "Items", num: true, value: (s) => s.item_count },
    { label: "Min order value", num: true, value: (s) => money(s.min_order_value) },
    { label: "Shipping fee", num: true, value: (s) => money(s.shipping_fee) },
    { label: "Active", html: (s) => `<input type="checkbox" data-toggle-supplier='${esc(JSON.stringify(s))}' ${s.active ? "checked" : ""}>` },
    { label: "", html: (s) => `<button class="link" data-catalog="${s.id}" data-name="${esc(s.name)}">catalog</button> <button class="link danger" data-del-supplier="${s.id}">delete</button>` },
  ], suppliers);
}

$("#supplier-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = formData(e.target);
  try {
    await api("/api/suppliers", { method: "POST", body: { name: f.name, min_order_value: Number(f.min_order_value || 0), shipping_fee: Number(f.shipping_fee || 0) } });
    e.target.reset();
    loadSuppliers();
  } catch (err) { alert(err.message); }
});

async function loadCatalog() {
  if (!currentSupplier) return;
  const items = await api(`/api/suppliers/${currentSupplier.id}/catalog`);
  $("#catalog-table").innerHTML = table([
    { label: "Code", value: (i) => i.code },
    { label: "Name", value: (i) => i.name },
    { label: "Unit price", num: true, value: (i) => money(i.unit_price) },
    { label: "MOQ", num: true, value: (i) => i.moq },
    { label: "Pack", num: true, value: (i) => i.pack_size },
    { label: "Stock", num: true, value: (i) => i.stock ?? "∞" },
    { label: "", html: (i) => `<button class="link danger" data-del-item="${i.id}">delete</button>` },
  ], items);
}

$("#catalog-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = formData(e.target);
  try {
    await api(`/api/suppliers/${currentSupplier.id}/catalog`, { method: "PUT", body: {
      code: f.code, name: f.name, unit_price: Number(f.unit_price), moq: Number(f.moq || 1),
      pack_size: Number(f.pack_size || 1), stock: numOrNull(f.stock),
    } });
    e.target.reset();
    $("#catalog-msg").textContent = "Saved.";
    loadCatalog(); loadSuppliers();
  } catch (err) { $("#catalog-msg").textContent = err.message; }
});

$("#catalog-import").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const replace = fd.get("replace") ? "true" : "false";
  fd.delete("replace");
  try {
    const r = await api(`/api/suppliers/${currentSupplier.id}/catalog/import?replace=${replace}`, { method: "POST", form: fd });
    $("#catalog-msg").textContent = `Imported: ${r.created} created, ${r.updated} updated${r.errors.length ? ` · ${r.errors.length} error(s): ${r.errors.slice(0, 5).join("; ")}` : ""}`;
    e.target.reset();
    loadCatalog(); loadSuppliers();
  } catch (err) { $("#catalog-msg").textContent = err.message; }
});

document.addEventListener("click", async (e) => {
  const cat = e.target.closest("[data-catalog]");
  if (cat) {
    currentSupplier = { id: Number(cat.dataset.catalog), name: cat.dataset.name };
    $("#catalog-supplier").textContent = currentSupplier.name;
    $("#catalog-card").classList.remove("hidden");
    $("#catalog-msg").textContent = "";
    loadCatalog();
  }
  const delS = e.target.closest("[data-del-supplier]");
  if (delS && confirm("Delete this supplier and its catalog?")) {
    await api(`/api/suppliers/${delS.dataset.delSupplier}`, { method: "DELETE" });
    if (currentSupplier?.id === Number(delS.dataset.delSupplier)) { currentSupplier = null; $("#catalog-card").classList.add("hidden"); }
    loadSuppliers();
  }
  const delI = e.target.closest("[data-del-item]");
  if (delI) {
    await api(`/api/suppliers/${currentSupplier.id}/catalog/${delI.dataset.delItem}`, { method: "DELETE" });
    loadCatalog(); loadSuppliers();
  }
});

document.addEventListener("change", async (e) => {
  const toggle = e.target.closest("[data-toggle-supplier]");
  if (!toggle) return;
  const s = JSON.parse(toggle.dataset.toggleSupplier);
  await api(`/api/suppliers/${s.id}`, { method: "PUT", body: { name: s.name, min_order_value: s.min_order_value, shipping_fee: s.shipping_fee, active: toggle.checked } });
  loadSuppliers();
});

/* ---------- Comparison ---------- */
async function loadComparison() {
  const q = encodeURIComponent($("#compare-search").value);
  const products = await api(`/api/products/comparison?q=${q}`);
  const rows = products.flatMap((p) => (p.offers.length ? p.offers : [null]).map((o, idx) => ({ p, o, idx })));
  $("#compare-table").innerHTML = table([
    { label: "Code", value: (r) => (r.idx ? "" : r.p.code) },
    { label: "Product", value: (r) => (r.idx ? "" : r.p.name) },
    { label: "Selling price", num: true, value: (r) => (r.idx ? "" : money(r.p.selling_price)) },
    { label: "Supplier", value: (r) => r.o?.supplier_name ?? "no offer" },
    { label: "Unit price", num: true, value: (r) => (r.o ? money(r.o.unit_price) : "") },
    { label: "Unit margin", num: true, value: (r) => (r.o ? money(r.o.margin) : "") },
    { label: "MOQ", num: true, value: (r) => r.o?.moq ?? "" },
    { label: "Pack", num: true, value: (r) => r.o?.pack_size ?? "" },
    { label: "Stock", num: true, value: (r) => (r.o ? r.o.stock ?? "∞" : "") },
  ], rows, (r) => (r.o && r.idx === 0 && r.p.offers.length > 1 ? "best" : ""));
}
$("#compare-search").addEventListener("input", () => loadComparison());

/* ---------- History ---------- */
async function loadHistory() {
  $("#history-detail").innerHTML = "";
  const plans = await api("/api/orders");
  $("#history-table").innerHTML = table([
    { label: "#", value: (p) => p.id },
    { label: "Name", value: (p) => p.name },
    { label: "Date", value: (p) => new Date(p.created_at).toLocaleString() },
    { label: "Budget", num: true, value: (p) => money(p.budget) },
    { label: "Cost", num: true, value: (p) => money(p.total_cost) },
    { label: "Profit", num: true, value: (p) => money(p.total_profit) },
    { label: "", html: (p) => `<button class="link" data-view-plan="${p.id}">view</button> <a href="/api/orders/${p.id}/export.csv" data-export="${p.id}">CSV</a> <button class="link danger" data-del-plan="${p.id}">delete</button>` },
  ], plans);
}

document.addEventListener("click", async (e) => {
  const view = e.target.closest("[data-view-plan]");
  if (view) $("#history-detail").innerHTML = renderResult(await api(`/api/orders/${view.dataset.viewPlan}`));
  const del = e.target.closest("[data-del-plan]");
  if (del && confirm("Delete this saved order?")) {
    await api(`/api/orders/${del.dataset.delPlan}`, { method: "DELETE" });
    loadHistory();
  }
});

/* ---------- Team ---------- */
async function loadTeam() {
  const users = await api("/api/users");
  $("#user-form").classList.toggle("hidden", me?.role !== "admin");
  $("#user-table").innerHTML = table([
    { label: "Email", value: (u) => u.email },
    { label: "Name", value: (u) => u.full_name },
    { label: "Role", value: (u) => u.role },
  ], users);
}

$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/users", { method: "POST", body: formData(e.target) });
    e.target.reset();
    $("#user-msg").textContent = "User added.";
    loadTeam();
  } catch (err) { $("#user-msg").textContent = err.message; }
});

start();
