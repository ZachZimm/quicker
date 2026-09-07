import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowRight,
  Check,
  CheckCheck,
  ChevronDown,
  FileText,
  FolderArchive,
  LayoutList,
  LogOut,
  Monitor,
  Plus,
  RefreshCw,
  RotateCcw,
  Settings as SettingsIcon,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api, post, setCsrf, upload, fieldsOnly } from "./api";
import type {
  Catalog,
  DocumentRecord,
  Fields,
  Route,
  Transaction,
} from "./api";
import "./style.css";
import { useDialogKeyboard } from "./accessibility";
import { useReviewTools } from "./webmcp";

function CategoryOptions({ catalog }: { catalog: Catalog }) {
  const preferred = new Set(Object.values(catalog.preferred_categories || {}));
  return (
    <>
      <optgroup label="Preferred categories">
        {catalog.categories
          .filter((c) => preferred.has(c.name))
          .map((c) => (
            <option key={c.name}>{c.name}</option>
          ))}
      </optgroup>
      <optgroup label="Other Quicken categories">
        {catalog.categories
          .filter((c) => !preferred.has(c.name))
          .map((c) => (
            <option key={c.name}>{c.name}</option>
          ))}
      </optgroup>
    </>
  );
}

const EMPTY: Catalog = {
  accounts: [],
  categories: [],
  tags: [],
  routes: [],
  payees: [],
  history: [],
};
const money = (n: number | null) =>
  n === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(n / 100);
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function App() {
  useDialogKeyboard();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [tab, setTab] = useState("review");
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [rows, setRows] = useState<Transaction[]>([]);
  const [catalog, setCatalog] = useState<Catalog>(EMPTY);
  const [devices, setDevices] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploader, setUploader] = useState(false);

  const reload = useCallback(async () => {
    const [d, r, c, device] = await Promise.all([
      api("/documents"),
      api("/transactions"),
      api("/catalog"),
      api("/devices"),
    ]);
    setDocs(d);
    setRows(r);
    setCatalog(c);
    setDevices(device);
  }, []);
  useEffect(() => {
    api("/session")
      .then((s) => {
        setCsrf(s.csrf);
        setSignedIn(true);
      })
      .catch(() => setSignedIn(false));
    const expired = () => {
      setSignedIn(false);
      setCsrf("");
    };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  useEffect(() => {
    if (!signedIn) return;
    reload().catch((e) => setError(errorText(e)));
    const timer = setInterval(
      () => reload().catch((e) => setError(errorText(e))),
      6000,
    );
    return () => clearInterval(timer);
  }, [signedIn, reload]);
  const run = async (action: () => Promise<unknown>, message = "") => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await reload();
      setNotice(message);
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  if (signedIn === null) return <div className="loading">Opening Quicker…</div>;
  if (!signedIn)
    return (
      <Login
        onLogin={(s) => {
          setCsrf(s);
          setSignedIn(true);
        }}
      />
    );
  const pending = rows.filter((r) => r.status === "review").length;
  const approved = rows.filter((r) => r.status === "approved").length;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">Q</span>quicker
          <span className="brand-dot">.</span>
        </div>
        <p className="section-label">WORKSPACE</p>
        <nav aria-label="Main navigation">
          {[
            ["review", "Review", LayoutList],
            ["documents", "Documents", FileText],
            ["companion", "Windows companion", Monitor],
            ["settings", "Settings", SettingsIcon],
          ].map(([id, label, Icon]: any) => (
            <button
              key={id}
              className={tab === id ? "nav active" : "nav"}
              onClick={() => setTab(id)}
            >
              <Icon size={19} />
              {label}
              {id === "review" && <span className="nav-count">{pending}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection">
            <span
              className={devices.some((d) => d.connected) ? "dot green" : "dot"}
            />
            {devices.some((d) => d.connected)
              ? "Windows connected"
              : "Windows offline"}
          </div>
          <button
            className="quiet"
            onClick={() =>
              run(async () => {
                await post("/logout");
                setSignedIn(false);
              })
            }
          >
            <LogOut size={16} /> Sign out
          </button>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            Rental bookkeeping <span className="slash">/</span>{" "}
            {tab === "companion"
              ? "Windows companion"
              : tab[0].toUpperCase() + tab.slice(1)}
          </span>
          <span className="account-badge">Shared workspace</span>
        </header>
        <div className="main-content">
          {error && (
            <div className="alert error" role="alert">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              {notice}
              <button
                aria-label="Dismiss message"
                onClick={() => setNotice("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <p className="eyebrow">
                {tab === "review"
                  ? "DOCUMENTS → TRANSACTIONS"
                  : "QUICKER WORKSPACE"}
              </p>
              <h1>
                {
                  {
                    review: "Review transactions",
                    documents: "Source documents",
                    companion: "Windows companion",
                    settings: "Workspace settings",
                  }[tab]
                }
              </h1>
            </div>
            <button className="primary" onClick={() => setUploader(true)}>
              <Upload size={18} /> Upload documents
            </button>
          </div>
          {tab === "review" && (
            <>
              <div className="metrics">
                <div>
                  <span>Needs review</span>
                  <strong>{pending}</strong>
                </div>
                <div>
                  <span>Approved</span>
                  <strong>{approved}</strong>
                </div>
                <div>
                  <span>Documents</span>
                  <strong>{docs.length}</strong>
                </div>
                <div className="metric-note">
                  <FolderArchive size={22} />
                  <span>
                    Original documents
                    <br />
                    kept in your archives
                  </span>
                </div>
              </div>
              {!catalog.accounts.length && (
                <div className="setup-note">
                  Import your Quicken QIF export to choose existing accounts,
                  categories, and tags.{" "}
                  <button className="link" onClick={() => setTab("settings")}>
                    Open settings <ArrowRight size={15} />
                  </button>
                </div>
              )}
              <Review
                error={error}
                rows={rows}
                docs={docs}
                catalog={catalog}
                run={run}
                busy={busy}
                onUpload={() => setUploader(true)}
              />
            </>
          )}
          {tab === "documents" && (
            <Documents
              error={error}
              docs={docs}
              rows={rows}
              run={run}
              onUpload={() => setUploader(true)}
            />
          )}
          {tab === "settings" && <Settings catalog={catalog} run={run} />}
          {tab === "companion" && (
            <Companion devices={devices} approved={approved} run={run} />
          )}
        </div>
      </main>
      {uploader && (
        <UploadDialog
          onClose={() => setUploader(false)}
          onDone={async () => {
            await reload();
            setUploader(false);
            setTab("documents");
            setNotice(
              "Originals stored. Extraction will begin when the worker is running.",
            );
          }}
        />
      )}
    </div>
  );
}

function Login({ onLogin }: { onLogin: (csrf: string) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="login-page">
      <div className="login-card">
        <div className="brand">
          <span className="brand-mark">Q</span>quicker.
        </div>
        <h1>
          Your bookkeeping,
          <br />
          ready for review.
        </h1>
        <p>Sign in to review documents and prepare transactions.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            const data = new FormData(e.currentTarget);
            try {
              const s = await post("/login", {
                username: data.get("username"),
                password: data.get("password"),
              });
              onLogin(s.csrf);
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Username
            <input name="username" autoComplete="username" required autoFocus />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          {error && (
            <p role="alert" className="field-error">
              {error}
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
            <ArrowRight size={17} />
          </button>
        </form>
        <p className="muted small">
          Use the shared account configured on your server.
        </p>
      </div>
    </div>
  );
}

type Run = (
  action: () => Promise<unknown>,
  message?: string,
) => Promise<boolean>;
function Review({
  error,
  rows,
  docs,
  catalog,
  run,
  busy,
  onUpload,
}: {
  error: string;
  rows: Transaction[];
  docs: DocumentRecord[];
  catalog: Catalog;
  run: Run;
  busy: boolean;
  onUpload: () => void;
}) {
  const [filter, setFilter] = useState("review");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [draft, setDraft] = useState<Transaction | null>(null);
  const [bulkProperty, setBulkProperty] = useState("");
  const [bulkAccount, setBulkAccount] = useState("");
  const [bulkDate, setBulkDate] = useState("");
  const [bulkCategory, setBulkCategory] = useState("");
  const [query, setQuery] = useState("");
  const [history, setHistory] = useState<any[] | null>(null);
  useReviewTools(rows, setFilter);
  const visible = rows.filter(
    (r) =>
      (filter === "all" || r.status === filter) &&
      (r.data.payee || "").toLowerCase().includes(query.toLowerCase()),
  );
  const chosen = visible.filter((r) => selected.has(r.id));
  const current = rows.find((r) => r.id === active);
  const doc = docs.find((d) => d.id === draft?.document_id);
  const properties = [...new Set(catalog.routes.map((r) => r.property))].sort();
  const act = async (
    action: string,
    items = chosen,
    fields?: (r: Transaction) => Fields,
  ) => {
    const ok = await run(
      () =>
        post("/review", {
          action,
          rows: items.map((r) => ({
            id: r.id,
            revision: r.revision,
            ...(fields ? { fields: fields(r) } : {}),
          })),
        }),
      `${items.length} transaction${items.length === 1 ? "" : "s"} ${action === "save" ? "saved" : action === "approve" ? "approved" : action === "remove" ? "removed" : "restored"}.`,
    );
    if (ok) {
      setSelected(new Set());
      if (items.length === 1 && items[0].id === active) {
        setDraft(null);
        setActive(null);
      }
    }
  };
  const open = (r: Transaction) => {
    setActive(r.id);
    setDraft(structuredClone(r));
    setHistory(null);
  };
  const update = (key: keyof Fields, value: any) =>
    setDraft((d) => {
      if (!d) return d;
      const data = { ...d.data, [key]: value };
      if (key === "property" && value !== d.data.property) {
        data.unit = "unresolved";
        data.unit_evidence = null;
      }
      if (key === "unit") data.unit_evidence = "Assigned during review.";
      if (["payee", "date", "amount_minor"].includes(key))
        data.duplicate_acknowledged = false;
      if ((key === "property" || key === "date") && !data.account_override)
        data.account =
          catalog.routes.find(
            (r) =>
              r.property === data.property &&
              r.year === Number(data.date?.slice(0, 4)),
          )?.account || null;
      return { ...d, data };
    });
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  return (
    <section className="review-section">
      <div className="table-top">
        <div className="tabs" role="tablist" aria-label="Transaction status">
          {[
            ["review", "Needs review"],
            ["approved", "Approved"],
            ["removed", "Removed"],
            ["all", "All"],
          ].map(([id, label]) => (
            <button
              role="tab"
              aria-selected={filter === id}
              key={id}
              className={filter === id ? "selected" : ""}
              onClick={() => {
                setFilter(id);
                setSelected(new Set());
              }}
            >
              {label}
              <span>
                {rows.filter((r) => id === "all" || r.status === id).length}
              </span>
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder="Search payees…"
          aria-label="Search payees"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {chosen.length > 0 && (
        <div className="bulk-bar">
          <strong>{chosen.length} selected</strong>
          {filter !== "removed" && (
            <>
              <select
                aria-label="Bulk property"
                value={bulkProperty}
                onChange={(e) => setBulkProperty(e.target.value)}
              >
                <option value="">Assign property…</option>
                {properties.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
              <select
                aria-label="Bulk account"
                value={bulkAccount}
                onChange={(e) => setBulkAccount(e.target.value)}
              >
                <option value="">Override account…</option>
                {catalog.accounts.map((a) => (
                  <option key={a.name}>{a.name}</option>
                ))}
              </select>
              <select
                aria-label="Bulk category"
                value={bulkCategory}
                onChange={(e) => setBulkCategory(e.target.value)}
              >
                <option value="">Assign category…</option>
                <CategoryOptions catalog={catalog} />
              </select>
              <input
                type="date"
                aria-label="Bulk payment date"
                value={bulkDate}
                onChange={(e) => setBulkDate(e.target.value)}
              />
              <button
                disabled={
                  busy ||
                  !(bulkProperty || bulkAccount || bulkDate || bulkCategory)
                }
                onClick={() =>
                  act("save", chosen, (r) => ({
                    ...fieldsOnly(r.data),
                    ...(bulkProperty
                      ? {
                          property: bulkProperty,
                          unit:
                            bulkProperty === r.data.property
                              ? r.data.unit
                              : "unresolved",
                        }
                      : {}),
                    ...(bulkDate ? { date: bulkDate } : {}),
                    ...(bulkCategory ? { category: bulkCategory } : {}),
                    ...(bulkAccount
                      ? { account: bulkAccount, account_override: true }
                      : {}),
                  }))
                }
              >
                Apply
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() => act("approve")}
              >
                <CheckCheck size={16} /> Approve
              </button>
            </>
          )}
          <button
            disabled={busy}
            onClick={() => act(filter === "removed" ? "restore" : "remove")}
          >
            {filter === "removed" ? (
              <RotateCcw size={16} />
            ) : (
              <Trash2 size={16} />
            )}{" "}
            {filter === "removed" ? "Restore" : "Remove"}
          </button>
        </div>
      )}
      {visible.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th className="check-cell">
                  <input
                    type="checkbox"
                    aria-label="Select all visible transactions"
                    checked={
                      visible.length > 0 &&
                      visible.every((r) => selected.has(r.id))
                    }
                    onChange={(e) =>
                      setSelected(
                        new Set(
                          e.target.checked ? visible.map((r) => r.id) : [],
                        ),
                      )
                    }
                  />
                </th>
                <th>Payee / source</th>
                <th>Payment date</th>
                <th>Property / account</th>
                <th>Category</th>
                <th className="number">Amount</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.id} className={active === r.id ? "active-row" : ""}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${r.data.payee || "unknown payee"}`}
                      checked={selected.has(r.id)}
                      onChange={() => toggle(r.id)}
                    />
                  </td>
                  <td>
                    <button className="payee-button" onClick={() => open(r)}>
                      {r.data.payee || "Unknown payee"}
                    </button>
                    <small>
                      {docs.find((d) => d.id === r.document_id)?.name}
                    </small>
                  </td>
                  <td>
                    {r.data.date || (
                      <span className="missing">Date required</span>
                    )}
                  </td>
                  <td>
                    {r.data.property || (
                      <span className="missing">Unassigned</span>
                    )}
                    <small>{r.data.account || "Choose an account"}</small>
                    {r.data.property && (
                      <small>
                        {r.data.unit === "whole_property"
                          ? "Whole property"
                          : r.data.unit === "unresolved" || !r.data.unit
                            ? "Unit unresolved"
                            : r.data.unit}
                      </small>
                    )}
                  </td>
                  <td>
                    {r.data.category || (
                      <span className="missing">Choose category</span>
                    )}
                  </td>
                  <td
                    className={
                      "number " +
                      (r.data.amount_minor && r.data.amount_minor > 0
                        ? "positive"
                        : "")
                    }
                  >
                    {money(r.data.amount_minor)}
                  </td>
                  <td>
                    <span className={"badge " + r.status}>
                      {r.status === "review"
                        ? r.issues.length
                          ? `${r.issues.length} to resolve`
                          : "Ready to approve"
                        : r.status}
                    </span>
                    {(r.duplicates.length > 0 ||
                      (r.historical_duplicates?.length || 0) > 0) && (
                      <small className="missing">Possible duplicate</small>
                    )}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`Review ${r.data.payee || "transaction"}`}
                      onClick={() => open(r)}
                    >
                      <ArrowRight size={17} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">
          <div className="empty-icon">
            <LayoutList size={30} />
          </div>
          <h2>
            {filter === "review"
              ? "Your next batch starts here"
              : "No transactions in this view"}
          </h2>
          <p>
            {docs.some((d) => ["queued", "extracting"].includes(d.status))
              ? "Documents are queued or being read. Transactions will appear here when extraction finishes."
              : "Upload invoices, tax receipts, or card statements to prepare transactions for review."}
          </p>
          <button onClick={onUpload}>
            <Plus size={17} /> Upload documents
          </button>
        </div>
      )}
      <div className="table-footer">
        <span>{visible.length} transactions</span>
        <span>Review and approval happen before Quicken entry.</span>
      </div>
      {draft && (
        <div className="drawer-overlay">
          <section
            className="review-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Review transaction"
          >
            <div className="drawer-heading">
              <div>
                <p className="eyebrow">TRANSACTION DETAILS</p>
                <h2>{draft.data.payee || "Unknown payee"}</h2>
              </div>
              <button
                aria-label="Close transaction"
                onClick={() => {
                  setDraft(null);
                  setActive(null);
                }}
              >
                <X size={20} />
              </button>
            </div>
            {current && current.revision !== draft.revision && (
              <div className="alert error">
                This transaction changed elsewhere.{" "}
                <button onClick={() => open(current)}>Reload latest</button>
              </div>
            )}
            {error && (
              <div className="alert error" role="alert">
                {error}
              </div>
            )}
            <div className="drawer-body">
              <SourceViewer doc={doc} initialPage={draft.data.page} />
              <div className="edit-fields">
                <p className="muted small">
                  {draft.data.source ||
                    "Check the source document alongside these fields."}
                </p>
                {draft.issues.length > 0 && (
                  <ul className="issues">
                    {draft.issues.map((issue, i) => (
                      <li key={i}>{issue}</li>
                    ))}
                  </ul>
                )}
                {draft.warnings.length > 0 && (
                  <details>
                    <summary>
                      Extraction notes ({draft.warnings.length})
                    </summary>
                    {draft.warnings.map((w, i) => (
                      <p key={i}>{w}</p>
                    ))}
                  </details>
                )}
                <fieldset disabled={draft.status === "removed" || busy}>
                  <label>
                    Payee
                    <input
                      value={draft.data.payee || ""}
                      onChange={(e) => update("payee", e.target.value || null)}
                    />
                  </label>
                  <div className="form-two">
                    <label>
                      Amount · USD
                      <input
                        key={draft.id}
                        type="number"
                        step="0.01"
                        defaultValue={
                          draft.data.amount_minor === null
                            ? ""
                            : draft.data.amount_minor / 100
                        }
                        onChange={(e) =>
                          update(
                            "amount_minor",
                            e.target.value === ""
                              ? null
                              : Math.round(Number(e.target.value) * 100),
                          )
                        }
                      />
                    </label>
                    <label>
                      Payment / transaction date
                      <input
                        type="date"
                        required
                        value={draft.data.date || ""}
                        onChange={(e) => update("date", e.target.value || null)}
                      />
                    </label>
                  </div>
                  <label>
                    Property or business
                    <select
                      aria-label="Property or business"
                      value={draft.data.property || ""}
                      onChange={(e) =>
                        update("property", e.target.value || null)
                      }
                    >
                      <option value="">Choose during review</option>
                      {properties.map((p) => (
                        <option key={p}>{p}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Unit
                    <select
                      aria-label="Unit"
                      value={draft.data.unit || "unresolved"}
                      disabled={!draft.data.property}
                      onChange={(e) => update("unit", e.target.value)}
                    >
                      <option value="unresolved">Unresolved</option>
                      <option value="whole_property">Whole property</option>
                      {(
                        catalog.properties?.find(
                          (p) => p.name === draft.data.property,
                        )?.units || []
                      ).map((unit) => (
                        <option key={unit} value={unit}>
                          {unit}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="muted small">
                    Unit selection keeps the same property account. Choose Whole
                    property for a shared expense; leave Unresolved when the
                    bill does not identify a rental.
                  </p>
                  {draft.data.unit_evidence && (
                    <p className="muted small">
                      Unit evidence: {draft.data.unit_evidence}
                    </p>
                  )}
                  <label>
                    Destination account
                    <select
                      value={draft.data.account || ""}
                      onChange={(e) => {
                        update("account", e.target.value || null);
                        update("account_override", true);
                      }}
                    >
                      <option value="">Choose an account</option>
                      {catalog.accounts.map((a) => (
                        <option key={a.name}>{a.name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={draft.data.account_override}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setDraft((d) =>
                          d
                            ? {
                                ...d,
                                data: {
                                  ...d.data,
                                  account_override: checked,
                                  account: checked
                                    ? d.data.account
                                    : catalog.routes.find(
                                        (r) =>
                                          r.property === d.data.property &&
                                          r.year ===
                                            Number(d.data.date?.slice(0, 4)),
                                      )?.account || null,
                                },
                              }
                            : d,
                        );
                      }}
                    />
                    Override the account selected by payment year
                  </label>
                  <label>
                    Category
                    <select
                      aria-label="Category"
                      value={draft.data.category || ""}
                      onChange={(e) =>
                        update("category", e.target.value || null)
                      }
                    >
                      <option value="">Choose a category</option>
                      <CategoryOptions catalog={catalog} />
                    </select>
                  </label>
                  <label>
                    Expense tag · optional
                    <select
                      value={draft.data.tag || ""}
                      onChange={(e) => update("tag", e.target.value || null)}
                    >
                      <option value="">No tag</option>
                      {catalog.tags
                        .filter(
                          (t) =>
                            !catalog.properties?.some((p) =>
                              p.units.includes(t.name),
                            ),
                        )
                        .map((t) => (
                          <option key={t.name}>{t.name}</option>
                        ))}
                    </select>
                  </label>
                  <p className="muted small">
                    Quicken tags:{" "}
                    {[
                      draft.data.tag,
                      ...(catalog.properties
                        ?.find((p) => p.name === draft.data.property)
                        ?.units.includes(draft.data.unit)
                        ? [draft.data.unit]
                        : []),
                    ]
                      .filter(Boolean)
                      .join(", ") || "None"}
                  </p>
                  <label>
                    Memo
                    <textarea
                      value={draft.data.memo}
                      onChange={(e) => update("memo", e.target.value)}
                    />
                  </label>
                  {draft.data.property_address && (
                    <p className="muted small">
                      Printed address:{" "}
                      {[
                        draft.data.property_address.street,
                        draft.data.property_address.city,
                        draft.data.property_address.state,
                      ]
                        .filter(Boolean)
                        .join(", ")}
                      {" · "}
                      {draft.data.property_address.role.replaceAll("_", " ")}
                    </p>
                  )}
                  {draft.data.parcel && (
                    <p className="muted">
                      Parcel {draft.data.parcel}.
                      {draft.data.property_assignment === "verified_parcel"
                        ? " Property matched from the verified parcel mapping."
                        : " Choose the property during review."}
                    </p>
                  )}
                  {draft.data.utility_account && (
                    <p className="muted small">
                      Printed utility account: {draft.data.utility_account}
                    </p>
                  )}
                  {(draft.historical_duplicates?.length || 0) > 0 && (
                    <div className="historical-matches">
                      <h4>Matching Quicken transactions</h4>
                      <p className="muted small">
                        These are possible duplicates in the imported history.
                        Save edits to refresh matches.
                      </p>
                      {draft.historical_duplicates!.map((match) => (
                        <div key={match.id} className="history-match">
                          <strong>{match.account}</strong>
                          <span>
                            {match.date} · {match.payee} ·{" "}
                            {money(match.amount_minor)}
                          </span>
                          <span>
                            {match.category}
                            {match.tag ? ` / ${match.tag}` : ""}
                          </span>
                          {match.memo && <span>{match.memo}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                  {(draft.duplicates.length > 0 ||
                    (draft.historical_duplicates?.length || 0) > 0) && (
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={draft.data.duplicate_acknowledged}
                        onChange={(e) =>
                          update("duplicate_acknowledged", e.target.checked)
                        }
                      />
                      I checked the possible duplicate; this is a separate
                      transaction.
                    </label>
                  )}
                </fieldset>
                <button
                  className="quiet"
                  onClick={async () => {
                    try {
                      setHistory(
                        await api(`/transactions/${draft.id}/history`),
                      );
                    } catch (e) {
                      await run(() => Promise.reject(e));
                    }
                  }}
                >
                  View change history <ChevronDown size={15} />
                </button>
                {history && (
                  <div className="history">
                    {history.map((h, i) => (
                      <p key={i}>
                        <strong>{h.action}</strong> · revision{" "}
                        {h.snapshot.revision}
                        <small>
                          {new Date(h.created * 1000).toLocaleString()}
                        </small>
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <footer className="drawer-footer">
              {draft.status === "removed" ? (
                <button onClick={() => act("restore", [draft])}>
                  <RotateCcw size={16} /> Restore to review
                </button>
              ) : (
                <>
                  <button
                    disabled={busy}
                    onClick={() => act("remove", [draft])}
                  >
                    <Trash2 size={16} /> Remove
                  </button>
                  <span />
                  <button
                    disabled={busy}
                    onClick={() =>
                      act("save", [draft], () => fieldsOnly(draft.data))
                    }
                  >
                    Save for review
                  </button>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      act("approve", [draft], () => fieldsOnly(draft.data))
                    }
                  >
                    <Check size={16} /> Save & approve
                  </button>
                </>
              )}
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}

function SourceViewer({
  doc,
  initialPage = 1,
}: {
  doc?: DocumentRecord;
  initialPage?: number;
}) {
  const [index, setIndex] = useState(Math.max(0, initialPage - 1));
  const page = doc?.pages[index] || doc?.pages[0];
  return (
    <div className="source-viewer">
      <div className="source-toolbar">
        <span>
          <FileText size={16} /> Source document
        </span>
        {doc && doc.pages.length > 1 && (
          <select
            aria-label="Source page"
            value={index}
            onChange={(e) => setIndex(Number(e.target.value))}
          >
            {doc.pages.map((p, i) => (
              <option key={p.id} value={i}>
                Page {i + 1}
              </option>
            ))}
          </select>
        )}
        {page && (
          <a href={`/api/pages/${page.id}/original`} download>
            Original ↗
          </a>
        )}
      </div>
      {page ? (
        <a
          href={`/api/pages/${page.id}/preview`}
          target="_blank"
          rel="noreferrer"
          title="Open full-size preview"
        >
          <img
            src={`/api/pages/${page.id}/preview`}
            alt={`Source: ${page.name}`}
          />
        </a>
      ) : (
        <p>No source image available</p>
      )}
    </div>
  );
}

function UploadDialog({
  onClose,
  onDone,
}: {
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [group, setGroup] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  return (
    <div className="modal-overlay">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Upload documents"
      >
        <div className="drawer-heading">
          <h2>Upload documents</h2>
          <button
            disabled={progress !== null}
            onClick={onClose}
            aria-label="Close upload"
          >
            <X size={20} />
          </button>
        </div>
        <label
          className="dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (progress === null) setFiles([...e.dataTransfer.files]);
          }}
        >
          <Upload size={28} />
          <strong>Choose photos or drop them here</strong>
          <span>JPEG, PNG, HEIC · up to 50 MB each</span>
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif"
            disabled={progress !== null}
            onChange={(e) => setFiles([...(e.target.files || [])])}
          />
        </label>
        {files.length > 0 && (
          <>
            <ol className="upload-list">
              {files.map((f, i) => (
                <li key={i}>
                  <span>
                    {f.name}
                    <small>{(f.size / 1024 / 1024).toFixed(1)} MB</small>
                  </span>
                  <button
                    disabled={i === 0 || progress !== null}
                    aria-label={`Move ${f.name} earlier`}
                    onClick={() =>
                      setFiles((old) => {
                        const next = [...old];
                        [next[i - 1], next[i]] = [next[i], next[i - 1]];
                        return next;
                      })
                    }
                  >
                    ↑
                  </button>
                  <button
                    disabled={progress !== null}
                    aria-label={`Remove ${f.name}`}
                    onClick={() =>
                      setFiles((old) => old.filter((_, n) => n !== i))
                    }
                  >
                    <X size={14} />
                  </button>
                </li>
              ))}
            </ol>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={group}
                disabled={progress !== null}
                onChange={(e) => setGroup(e.target.checked)}
              />
              These photos are pages of one document, in the order above
            </label>
          </>
        )}
        <p className="muted small">
          Originals stay in your server archive and sync to Windows when
          connected. Smaller copies are used for reading documents.
        </p>
        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}
        {progress !== null && (
          <>
            <progress max="100" value={progress} />
            <p role="status">
              {progress < 100 ? `Uploading ${progress}%` : "Saving originals…"}
            </p>
          </>
        )}
        <button
          className="primary"
          disabled={!files.length || progress !== null}
          onClick={async () => {
            setError("");
            setProgress(0);
            try {
              await upload(files, group, setProgress);
              await onDone();
            } catch (e) {
              setError(errorText(e));
              setProgress(null);
            }
          }}
        >
          <Upload size={17} />
          Upload {files.length || ""} {files.length === 1 ? "photo" : "photos"}
        </button>
      </section>
    </div>
  );
}

function Documents({
  error,
  docs,
  rows,
  run,
  onUpload,
}: {
  error: string;
  docs: DocumentRecord[];
  rows: Transaction[];
  run: Run;
  onUpload: () => void;
}) {
  const [active, setActive] = useState<DocumentRecord | null>(null);
  const [attempts, setAttempts] = useState<any[] | null>(null);
  return (
    <>
      <p className="muted">
        Original documents are retained, including ignored items and
        unsuccessful extractions.
      </p>
      {!docs.length ? (
        <div className="empty">
          <FileText size={30} />
          <h2>No documents yet</h2>
          <button onClick={onUpload}>Upload your first document</button>
        </div>
      ) : (
        <div className="document-grid">
          {docs.map((doc) => (
            <article className="document-card" key={doc.id}>
              <button
                className="document-thumbnail"
                onClick={() => {
                  setActive(doc);
                  setAttempts(null);
                }}
              >
                <img
                  loading="lazy"
                  src={`/api/pages/${doc.pages[0]?.id}/preview`}
                  alt={doc.name}
                />
              </button>
              <div className="document-card-body">
                <span className={"badge " + doc.status}>{doc.status}</span>
                <h3>{doc.name}</h3>
                <p>
                  {doc.pages.length} page{doc.pages.length === 1 ? "" : "s"} ·{" "}
                  {rows.filter((r) => r.document_id === doc.id).length} proposed
                  transactions
                </p>
                {doc.repeated_content && (
                  <p className="missing">
                    This image was uploaded more than once.
                  </p>
                )}
                {doc.error && <p className="field-error">{doc.error}</p>}
                <button
                  onClick={() => {
                    setActive(doc);
                    setAttempts(null);
                  }}
                >
                  Open document <ArrowRight size={15} />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {active && (
        <div className="modal-overlay">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="Source document"
          >
            <div className="drawer-heading">
              <h2>{active.name}</h2>
              <button
                onClick={() => setActive(null)}
                aria-label="Close document"
              >
                <X size={20} />
              </button>
            </div>
            {error && (
              <div className="alert error" role="alert">
                {error}
              </div>
            )}
            <SourceViewer doc={active} />
            {active.ignored.length > 0 && (
              <>
                <h3>Ignored items</h3>
                <ul>
                  {active.ignored.map((item, i) => (
                    <li key={i}>
                      {item.source || `Page ${item.page}`} · {item.reason}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <div className="actions">
              <button
                onClick={async () => {
                  await run(async () =>
                    setAttempts(await api(`/documents/${active.id}/attempts`)),
                  );
                }}
              >
                Extraction history
              </button>
              {!rows.some((r) => r.document_id === active.id) &&
                ["failed", "ready"].includes(active.status) && (
                  <button
                    onClick={async () => {
                      if (
                        await run(
                          () => post(`/documents/${active.id}/retry`),
                          "Document queued with current model settings.",
                        )
                      )
                        setActive(null);
                    }}
                  >
                    <RefreshCw size={16} />
                    Retry extraction
                  </button>
                )}
            </div>
            {attempts && (
              <div className="history">
                {attempts.map((a) => (
                  <details key={a.id}>
                    <summary>
                      {new Date(a.created * 1000).toLocaleString()} · model
                      settings revision {a.config_revision}
                    </summary>
                    <pre>{JSON.stringify(a.result, null, 2)}</pre>
                  </details>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}

function Settings({ catalog, run }: { catalog: Catalog; run: Run }) {
  const [config, setConfig] = useState<any>(null);
  const [key, setKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [checking, setChecking] = useState(false);
  useEffect(() => {
    api("/settings")
      .then(setConfig)
      .catch((e) => run(() => Promise.reject(e)));
  }, []);
  useEffect(() => setRoutes(catalog.routes), [JSON.stringify(catalog.routes)]);
  const saveConfig = () =>
    run(async () => {
      const { has_api_key, ...body } = config;
      await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ ...body, api_key: clearKey ? "" : key || null }),
      });
      setKey("");
      setClearKey(false);
      setConfig(await api("/settings"));
    }, "Model settings saved. New extractions will use this revision.");
  return (
    <div className="settings-grid">
      <section className="panel">
        <h2>Model connection</h2>
        <p className="muted">
          Configure the vision model used to read documents.
        </p>
        {config && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              saveConfig();
            }}
          >
            <div className="form-two">
              <label>
                API protocol
                <select
                  value={config.protocol}
                  onChange={(e) =>
                    setConfig({ ...config, protocol: e.target.value })
                  }
                >
                  <option value="chat-completions">Chat completions</option>
                  <option value="responses">Responses</option>
                </select>
              </label>
              <label>
                Transport
                <select
                  value={config.scheme}
                  onChange={(e) =>
                    setConfig({ ...config, scheme: e.target.value })
                  }
                >
                  <option>http</option>
                  <option>https</option>
                </select>
              </label>
            </div>
            <div className="form-two">
              <label>
                Host
                <input
                  required
                  value={config.host}
                  onChange={(e) =>
                    setConfig({ ...config, host: e.target.value })
                  }
                />
              </label>
              <label>
                Port
                <input
                  type="number"
                  required
                  min="1"
                  max="65535"
                  value={config.port}
                  onChange={(e) =>
                    setConfig({ ...config, port: Number(e.target.value) })
                  }
                />
              </label>
            </div>
            <label>
              Base path
              <input
                required
                value={config.base_path}
                onChange={(e) =>
                  setConfig({ ...config, base_path: e.target.value })
                }
              />
            </label>
            <label>
              Model name
              <input
                required
                value={config.model}
                onChange={(e) =>
                  setConfig({ ...config, model: e.target.value })
                }
              />
            </label>
            <label>
              API key · optional
              <input
                type="password"
                autoComplete="new-password"
                value={key}
                placeholder={
                  config.has_api_key
                    ? "Key configured. Leave blank to keep it."
                    : "No key configured"
                }
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            {config.has_api_key && (
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={clearKey}
                  onChange={(e) => setClearKey(e.target.checked)}
                />
                Remove the stored API key
              </label>
            )}
            <div className="form-two">
              <label>
                Timeout · seconds
                <input
                  type="number"
                  min="10"
                  max="600"
                  value={config.timeout}
                  onChange={(e) =>
                    setConfig({ ...config, timeout: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Concurrent extractions
                <input
                  type="number"
                  min="1"
                  max="4"
                  value={config.concurrency}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      concurrency: Number(e.target.value),
                    })
                  }
                />
              </label>
            </div>
            <label>
              Image maximum dimension · pixels
              <input
                type="number"
                min="800"
                max="4000"
                value={config.image_limit}
                onChange={(e) =>
                  setConfig({ ...config, image_limit: Number(e.target.value) })
                }
              />
            </label>
            <div className="actions">
              <button className="primary" type="submit">
                Save model settings
              </button>
              <button
                type="button"
                disabled={checking}
                onClick={async () => {
                  setChecking(true);
                  try {
                    await run(
                      () => post("/settings/check"),
                      "Connection and vision check passed for the saved settings.",
                    );
                  } finally {
                    setChecking(false);
                  }
                }}
              >
                {checking
                  ? "Reading test image…"
                  : "Test saved connection & vision"}
              </button>
            </div>
          </form>
        )}
      </section>
      <section className="panel">
        <h2>Quicken reference catalog</h2>
        <p className="muted">
          Import all accounts with Transactions, Account List, Category List and
          Memorized payees. The history supplies duplicate checks and reference
          examples.
        </p>
        <div className="catalog-counts">
          <span>
            <strong>{catalog.accounts.length}</strong> accounts
          </span>
          <span>
            <strong>{catalog.categories.length}</strong> categories
          </span>
          <span>
            <strong>{catalog.tags.length}</strong> tags
          </span>
        </div>
        <label className="file-button">
          Import QIF export
          <input
            type="file"
            accept=".qif,.QIF"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) {
                const body = new FormData();
                body.append("file", file);
                await run(
                  () => api("/catalog", { method: "POST", body }),
                  "Quicken reference catalog imported.",
                );
              }
              e.target.value = "";
            }}
          />
        </label>
        <p className="muted small">
          Refreshes reference data only. Existing Quicken transactions are not
          modified.
        </p>
        {catalog.coverage && (
          <div className="reference-coverage">
            <h3>Imported transaction coverage</h3>
            <p>
              {catalog.coverage.total.toLocaleString()} transactions ·{" "}
              {catalog.coverage.blank_payees} with blank payees retained
            </p>
            {(catalog.coverage.invalid_dates > 0 ||
              catalog.coverage.invalid_amounts > 0) && (
              <p role="alert">
                Some reference entries have unreadable dates or amounts. Their
                original records are retained; they cannot support exact
                duplicate matching.
              </p>
            )}
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Transactions</th>
                    <th>First date</th>
                    <th>Last date</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.coverage.accounts.map((a) => (
                    <tr key={a.account}>
                      <td>{a.account}</td>
                      <td>{a.count || "No history"}</td>
                      <td>{a.first_date || "—"}</td>
                      <td>{a.last_date || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <h3>Property directory</h3>
        <p className="muted small">
          One property identity across years. Unit labels remain separate from
          destination accounts; tags are not assigned from vendor names.
        </p>
        <div className="property-directory">
          {catalog.properties?.map((p) => (
            <div key={p.id} className="property-card">
              <strong>{p.name}</strong>
              {p.aliases.length > 0 && (
                <span>Also known as: {p.aliases.join(", ")}</span>
              )}
              {p.units.length > 0 && <span>Units: {p.units.join(", ")}</span>}
              {p.addresses?.map((a) => (
                <span key={a.street}>
                  Verified address: {a.street}, {a.city}, {a.state}
                </span>
              ))}
              {p.accounts.map((a) => (
                <span key={a.year}>
                  {a.year}: {a.account}
                </span>
              ))}
            </div>
          ))}
        </div>
        <h3>Property and year → account</h3>
        <p className="muted small">
          Known property aliases share a single name across years. R&K business
          expenses use these year mappings; auto insurance uses the exact R&K
          Properties account. Review and edit destinations here. Parcel mapping
          will be added later.
        </p>
        <div className="route-list">
          {routes.map((r, i) => (
            <div className="route-row" key={i}>
              <input
                aria-label={`Property ${i + 1}`}
                value={r.property}
                onChange={(e) =>
                  setRoutes((old) =>
                    old.map((x, n) =>
                      n === i ? { ...x, property: e.target.value } : x,
                    ),
                  )
                }
              />
              <input
                aria-label={`Year ${i + 1}`}
                type="number"
                value={r.year}
                onChange={(e) =>
                  setRoutes((old) =>
                    old.map((x, n) =>
                      n === i ? { ...x, year: Number(e.target.value) } : x,
                    ),
                  )
                }
              />
              <select
                aria-label={`Account ${i + 1}`}
                value={r.account}
                onChange={(e) =>
                  setRoutes((old) =>
                    old.map((x, n) =>
                      n === i ? { ...x, account: e.target.value } : x,
                    ),
                  )
                }
              >
                <option value="">Account</option>
                {catalog.accounts.map((a) => (
                  <option key={a.name}>{a.name}</option>
                ))}
              </select>
              <button
                aria-label={`Remove route ${i + 1}`}
                onClick={() =>
                  setRoutes((old) => old.filter((_, n) => n !== i))
                }
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="actions">
          <button
            onClick={() =>
              setRoutes((old) => [
                ...old,
                { property: "", year: new Date().getFullYear(), account: "" },
              ])
            }
          >
            <Plus size={16} />
            Add mapping
          </button>
          <button
            className="primary"
            onClick={() =>
              run(
                () =>
                  api("/routes", {
                    method: "PUT",
                    body: JSON.stringify(routes),
                  }),
                "Account mappings saved.",
              )
            }
          >
            Save account mappings
          </button>
        </div>
      </section>
    </div>
  );
}

function Companion({
  devices,
  approved,
  run,
}: {
  devices: any[];
  approved: number;
  run: Run;
}) {
  const [code, setCode] = useState("");
  return (
    <div className="settings-grid">
      <section className="panel">
        <Monitor size={28} />
        <h2>Connect your Windows app</h2>
        <p className="muted">
          Pair the companion on the computer with Quicken. It uploads your input
          folder and keeps a local copy of every source document.
        </p>
        {devices.map((d) => (
          <div className="device" key={d.id}>
            <div>
              <strong>{d.name}</strong>
              <p>
                <span className={d.connected ? "dot green" : "dot"} />
                {d.connected ? "Connected" : "Offline"} · {d.pending_archives}{" "}
                documents awaiting archive sync
              </p>
            </div>
            <button
              onClick={() =>
                run(
                  () => api(`/devices/${d.id}`, { method: "DELETE" }),
                  "Device access revoked.",
                )
              }
            >
              Revoke
            </button>
          </div>
        ))}
        {!devices.length && (
          <button
            className="primary"
            onClick={() =>
              run(async () => setCode((await post("/pair-code")).code))
            }
          >
            Create pairing code
          </button>
        )}
        {code && (
          <div className="pair-code">
            <code>{code}</code>
            <p>
              Enter this code in the Windows app within 10 minutes. It can be
              used once.
            </p>
          </div>
        )}
      </section>
      <section className="panel">
        <h2>Enter approved transactions</h2>
        <div className="approved-total">
          {approved}
          <span>approved and retained</span>
        </div>
        <p>
          Quicken entry is not available in this version. Your approved
          transactions remain saved here.
        </p>
        <button
          className="primary"
          disabled
          title="Quicken integration has not been implemented"
        >
          Enter approved transactions <ArrowRight size={17} />
        </button>
        <p className="muted small">
          When available, entry can be started here while the companion is
          connected, or from its Windows button.
        </p>
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
