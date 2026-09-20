import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { FileText, RotateCcw, Trash2 } from "lucide-react";
import type { Catalog, DocumentRecord, Fields, Transaction } from "./api";
import { editable } from "./reviewEditing";
import type { ReviewEdits } from "./reviewEditing";

type Column = { key: keyof Fields; label: string };
const columns: Column[] = [
  { key: "date", label: "Payment date" },
  { key: "payee", label: "Payee" },
  { key: "property", label: "Property" },
  { key: "unit", label: "Unit" },
  { key: "category", label: "Category" },
  { key: "account", label: "Account" },
  { key: "amount_minor", label: "Amount" },
];
const optional: Column[] = [
  { key: "tag", label: "Expense tag" },
  { key: "memo", label: "Memo" },
];
const display = (row: Transaction, key: keyof Fields) => {
  const value = row.data[key];
  if (key === "amount_minor")
    return value === null
      ? "Amount required"
      : new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: row.data.currency,
        }).format(Number(value) / 100);
  if (key === "unit")
    return value === "whole_property"
      ? "Whole property"
      : value === "unresolved"
        ? "Unit unresolved"
        : String(value);
  return String(
    value || (key === "memo" || key === "tag" ? "None" : "Choose…"),
  );
};

function moveCell(element: HTMLElement, key: string, shift = false) {
  const cell = element.closest<HTMLTableCellElement>("td[data-field]");
  const row = cell?.parentElement;
  if (!cell || !row) return false;
  let target: HTMLElement | null = null;
  if (key === "ArrowUp" || key === "ArrowDown") {
    const sibling =
      key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
    target =
      sibling?.querySelector<HTMLElement>(
        `td[data-field="${cell.dataset.field}"] [data-grid-cell]`,
      ) || null;
  } else {
    const cells = Array.from(
      row.closest("tbody")!.querySelectorAll<HTMLElement>("[data-grid-cell]"),
    );
    const current = cell.querySelector<HTMLElement>("[data-grid-cell]")!;
    const step = key === "ArrowLeft" || (key === "Tab" && shift) ? -1 : 1;
    target = cells[cells.indexOf(current) + step] || null;
  }
  if (!target && key === "Tab")
    target = row.querySelector<HTMLElement>(
      shift
        ? 'input[type="checkbox"]:not(:disabled)'
        : ".register-actions button:not(:disabled)",
    );
  target?.focus();
  return !!target;
}

function Cell({
  row,
  column,
  catalog,
  edits,
  busy,
}: {
  row: Transaction;
  column: Column;
  catalog: Catalog;
  edits: ReviewEdits;
  busy: boolean;
}) {
  const { key, label } = column;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const original = useRef(row);
  const selectOnFocus = useRef(true);
  const button = useRef<HTMLButtonElement>(null);
  const error = edits.drafts[row.id]?.invalid[key];
  const canEdit =
    editable(row) && !busy && !(key === "unit" && !row.data.property);
  const choices =
    key === "property"
      ? [...new Set(catalog.routes.map((r) => r.property))]
      : key === "unit"
        ? [
            "Unit unresolved",
            "Whole property",
            ...(catalog.properties?.find((p) => p.name === row.data.property)
              ?.units || []),
          ]
        : key === "category"
          ? catalog.categories.map((c) => c.name)
          : key === "account"
            ? ["Use automatic account", ...catalog.accounts.map((a) => a.name)]
            : key === "tag"
              ? catalog.tags
                  .filter(
                    (t) =>
                      !catalog.properties?.some((p) =>
                        p.units.includes(t.name),
                      ),
                  )
                  .map((t) => t.name)
              : null;
  const start = (typed?: string) => {
    if (!canEdit) return;
    edits.begin(row);
    original.current = row;
    selectOnFocus.current = typed === undefined;
    const value =
      key === "amount_minor"
        ? row.data.amount_minor === null
          ? ""
          : (row.data.amount_minor / 100).toFixed(2)
        : key === "unit"
          ? display(row, key)
          : String(row.data[key] ?? "");
    setText(typed ?? value);
    setEditing(true);
    if (typed !== undefined) change(typed);
  };
  const change = (value: string) => {
    setText(value);
    let parsed: Fields[keyof Fields] = value || null;
    let message = "";
    if (key === "amount_minor" && value) {
      if (
        !/^[+-]?\d+(\.\d{0,2})?$/.test(value) ||
        Math.abs(Number(value)) > 999999999.99
      )
        message =
          "Enter an amount with at most two decimal places. Expenses are negative.";
      else parsed = Math.round(Number(value) * 100);
    }
    if (
      key === "date" &&
      value &&
      (!/^\d{4}-\d{2}-\d{2}$/.test(value) ||
        !Number.isFinite(Date.parse(value)) ||
        new Date(value).toISOString().slice(0, 10) !== value)
    )
      message = "Enter a valid date as YYYY-MM-DD.";
    if (choices && value && !choices.includes(value))
      message = "Choose a value from the list.";
    if (key === "payee" && value.length > 300)
      message = "Payee must be 300 characters or fewer.";
    if (key === "memo" && value.length > 2000)
      message = "Memo must be 2,000 characters or fewer.";
    edits.invalid(row.id, key, message);
    if (message) return;
    if (key === "account" && value === "Use automatic account")
      edits.update(row.id, "account_override", false);
    else
      edits.update(
        row.id,
        key,
        key === "memo"
          ? value
          : key === "unit"
            ? value === "Whole property"
              ? "whole_property"
              : value === "Unit unresolved" || !value
                ? "unresolved"
                : value
            : parsed,
      );
  };
  const save = () => {
    void edits.save(row.id).catch(() => {});
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      edits.restoreCell(row.id, original.current, key);
      setEditing(false);
      button.current?.focus();
    } else if (event.key === "Enter" || event.key === "Tab") {
      if (error) {
        event.preventDefault();
        return;
      }
      setEditing(false);
      if (event.key === "Enter") {
        event.preventDefault();
        button.current?.focus();
        save();
      } else if (moveCell(event.currentTarget, "Tab", event.shiftKey))
        event.preventDefault();
    }
  };
  const id = `register-${row.id}-${key}`;
  const missing = !row.data[key] && !["memo", "tag"].includes(key);
  return (
    <td
      data-field={key}
      className={`register-cell ${key === "amount_minor" ? "number" : ""} ${error ? "cell-error" : ""}`}
    >
      <button
        ref={button}
        data-grid-cell
        className={`cell-value ${editing ? "editing" : ""} ${missing ? "missing" : ""}`}
        aria-label={`${label}: ${display(row, key)}`}
        aria-disabled={!canEdit}
        onClick={() => start()}
        onKeyDown={(event) => {
          if (
            ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(
              event.key,
            )
          ) {
            if (moveCell(event.currentTarget, event.key))
              event.preventDefault();
          } else if (event.key === "F2") {
            event.preventDefault();
            start();
          } else if (
            event.key.length === 1 &&
            event.key !== " " &&
            !event.ctrlKey &&
            !event.metaKey &&
            !event.altKey
          ) {
            event.preventDefault();
            start(event.key);
          }
        }}
      >
        {display(row, key)}
      </button>
      {editing && (
        <input
          autoFocus
          aria-label={`Edit ${label}`}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          type="text"
          readOnly={busy}
          inputMode={key === "amount_minor" ? "decimal" : undefined}
          placeholder={key === "date" ? "YYYY-MM-DD" : undefined}
          list={choices ? `${id}-options` : undefined}
          value={text}
          onChange={(event) => change(event.target.value)}
          onFocus={(event) => {
            if (selectOnFocus.current) event.target.select();
          }}
          onBlur={() => {
            if (!error) setEditing(false);
          }}
          onKeyDown={onKey}
        />
      )}
      {choices && editing && (
        <datalist id={`${id}-options`}>
          {choices.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      )}
      {key === "account" && (
        <small>
          {row.data.account_override ? "Override" : "Automatic by payment year"}
        </small>
      )}
      {missing && <small>{label} required</small>}
      {error && (
        <small id={`${id}-error`} role="alert">
          {error}
        </small>
      )}
    </td>
  );
}

export function ReviewRegister({
  rows,
  docs,
  catalog,
  edits,
  selected,
  toggle,
  selectAll,
  busy,
  open,
  act,
}: {
  rows: Transaction[];
  docs: DocumentRecord[];
  catalog: Catalog;
  edits: ReviewEdits;
  selected: Set<string>;
  toggle: (id: string) => void;
  selectAll: (ids: string[]) => void;
  busy: boolean;
  open: (row: Transaction) => void;
  act: (action: string, rows: Transaction[]) => void;
}) {
  const [extras, setExtras] = useState(false);
  const [resets, setResets] = useState<Record<string, number>>({});
  const selectable = rows.filter(
    (r) => !["entering", "entered"].includes(r.status),
  );
  return (
    <>
      <div className="register-help">
        <span>
          Click a cell to edit. Tab moves between fields. Enter saves; leaving a
          row saves automatically. Expenses are negative.
        </span>
        <label>
          <input
            type="checkbox"
            checked={extras}
            onChange={(e) => setExtras(e.target.checked)}
          />{" "}
          Tag and memo
        </label>
      </div>
      <div className="table-scroll register-scroll">
        <table
          className="review-register"
          aria-label="Review transaction register"
        >
          <thead>
            <tr>
              <th className="check-cell">
                <input
                  type="checkbox"
                  aria-label="Select all visible transactions"
                  disabled={busy}
                  checked={
                    selectable.length > 0 &&
                    selectable.every((r) => selected.has(r.id))
                  }
                  onChange={(e) =>
                    selectAll(
                      e.target.checked ? selectable.map((r) => r.id) : [],
                    )
                  }
                />
              </th>
              {[...columns, ...(extras ? optional : [])].map((c) => (
                <th key={c.key} scope="col">
                  {c.label}
                </th>
              ))}
              <th scope="col">Status</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const draft = edits.drafts[row.id];
              const pending = edits.pending.has(row.id);
              const dirty = edits.changed(row.id);
              return (
                <tr
                  key={`${row.id}-${resets[row.id] || 0}`}
                  data-transaction-id={row.id}
                  className={edits.focused === row.id ? "active-row" : ""}
                  onFocusCapture={() => edits.setFocused(row.id)}
                  onBlur={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget)) {
                      edits.setFocused(null);
                      void edits.save(row.id).catch(() => {});
                    }
                  }}
                >
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${row.data.payee || "unknown payee"}`}
                      disabled={
                        busy || ["entering", "entered"].includes(row.status)
                      }
                      checked={selected.has(row.id)}
                      onChange={() => toggle(row.id)}
                    />
                  </td>
                  {[...columns, ...(extras ? optional : [])].map((column) => (
                    <Cell
                      key={column.key}
                      row={row}
                      column={column}
                      catalog={catalog}
                      edits={edits}
                      busy={busy}
                    />
                  ))}
                  <td className="register-status">
                    <span className={`badge ${row.status}`}>
                      {row.status === "review"
                        ? row.issues.length
                          ? `${row.issues.length} to resolve`
                          : "Ready to approve"
                        : row.status === "existing"
                          ? "Already in Quicken"
                          : row.status}
                    </span>
                    <small role="status">
                      {pending
                        ? "Saving…"
                        : draft?.error
                          ? "Save failed"
                          : Object.keys(draft?.invalid || {}).length
                            ? "Correct highlighted cells"
                            : dirty
                              ? "Unsaved"
                              : ""}
                    </small>
                    {draft?.error && <small role="alert">{draft.error}</small>}
                    {row.issues.length > 0 && (
                      <details>
                        <summary>Review issues</summary>
                        <ul>
                          {row.issues.map((issue) => (
                            <li key={issue}>{issue}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {editable(row) &&
                      (row.duplicates.length > 0 ||
                        !!row.historical_duplicates?.length) && (
                        <button
                          className="link missing"
                          onClick={() => open(row)}
                        >
                          Possible duplicate
                        </button>
                      )}
                    {(dirty ||
                      draft?.error ||
                      Object.keys(draft?.invalid || {}).length > 0) && (
                      <div className="row-recovery">
                        <button
                          disabled={pending || busy}
                          onClick={() => {
                            void edits.save(row.id).catch(() => {});
                          }}
                        >
                          Save row
                        </button>
                        <button
                          disabled={pending || busy}
                          onClick={async () => {
                            if (await edits.reload(row.id))
                              setResets((r) => ({
                                ...r,
                                [row.id]: (r[row.id] || 0) + 1,
                              }));
                          }}
                        >
                          Discard edits / reload
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="register-actions">
                    <button
                      aria-label={`Details and source for ${row.data.payee || "transaction"}`}
                      title={docs.find((d) => d.id === row.document_id)?.name}
                      disabled={busy}
                      onClick={() => open(row)}
                    >
                      <FileText size={15} /> Details
                    </button>
                    {!["entering", "entered"].includes(row.status) && (
                      <button
                        disabled={busy}
                        aria-label={`${["removed", "existing"].includes(row.status) ? "Restore" : "Remove"} ${row.data.payee || "transaction"}`}
                        onClick={() =>
                          act(
                            ["removed", "existing"].includes(row.status)
                              ? "restore"
                              : "remove",
                            [row],
                          )
                        }
                      >
                        {["removed", "existing"].includes(row.status) ? (
                          <RotateCcw size={15} />
                        ) : (
                          <Trash2 size={15} />
                        )}
                        {["removed", "existing"].includes(row.status)
                          ? "Restore"
                          : "Remove"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
