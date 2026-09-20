import { useEffect, useRef, useState } from "react";
import { api, fieldsOnly, post } from "./api";
import type { Catalog, Fields, Transaction } from "./api";

export const automaticAccount = (catalog: Catalog, property: string | null) =>
  catalog.routes
    .filter((r) => r.property === property)
    .sort((a, b) => b.year - a.year)[0]?.account || null;

export const editable = (row: Transaction) =>
  ["review", "approved"].includes(row.status);

// Shared by the register and the detail form. The server validates these rules too.
export function changeField(
  row: Transaction,
  key: keyof Fields,
  value: Fields[keyof Fields],
  catalog: Catalog,
): Transaction {
  const data = { ...row.data, [key]: value };
  if (key === "property" && value !== row.data.property) {
    data.unit = "unresolved";
    data.unit_evidence = null;
  }
  if (key === "unit") data.unit_evidence = "Assigned during review.";
  if (key === "account") data.account_override = true;
  if (
    [
      "payee",
      "date",
      "amount_minor",
      "property",
      "unit",
      "account",
      "account_override",
    ].includes(key)
  )
    data.duplicate_acknowledged = false;
  if (
    ["property", "date", "account_override"].includes(key) &&
    !data.account_override
  )
    data.account = automaticAccount(catalog, data.property);
  return { ...row, data };
}

type Draft = {
  base: Transaction;
  row: Transaction;
  invalid: Record<string, string>;
  error?: string;
};
const sameFields = (a: Transaction, b: Transaction) =>
  JSON.stringify(fieldsOnly(a.data)) === JSON.stringify(fieldsOnly(b.data));

export function useReviewEdits(rows: Transaction[], catalog: Catalog) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const ref = useRef(drafts);
  const remote = useRef(new Map(rows.map((r) => [r.id, r])));
  remote.current = new Map(rows.map((r) => [r.id, r]));
  const saving = useRef(new Map<string, Promise<Transaction>>());
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [focused, setFocused] = useState<string | null>(null);
  const put = (id: string, draft: Draft) => {
    ref.current = { ...ref.current, [id]: draft };
    setDrafts(ref.current);
  };
  const latest = (id: string) => {
    const d = ref.current[id];
    const r = remote.current.get(id) || d?.base;
    if (!r) throw new Error("Transaction no longer exists.");
    // Keep local edits and freshly saved responses ahead of stale poll results.
    return d &&
      (!sameFields(d.base, d.row) ||
        Object.keys(d.invalid).length ||
        d.error ||
        d.row.revision > r.revision)
      ? d.row
      : r;
  };
  const begin = (row: Transaction) => {
    const current = latest(row.id);
    const old = ref.current[row.id];
    if (
      !old ||
      (sameFields(old.base, old.row) &&
        !Object.keys(old.invalid).length &&
        !old.error)
    )
      put(row.id, { base: current, row: current, invalid: {} });
  };
  const update = (
    id: string,
    key: keyof Fields,
    value: Fields[keyof Fields],
  ) => {
    const d = ref.current[id];
    put(id, {
      ...d,
      row: changeField(d.row, key, value, catalog),
      error: undefined,
    });
  };
  const invalid = (id: string, key: string, message: string) => {
    const d = ref.current[id];
    const errors = { ...d.invalid };
    if (message) errors[key] = message;
    else delete errors[key];
    put(id, { ...d, invalid: errors });
  };
  const restoreCell = (id: string, row: Transaction, key: string) => {
    const d = ref.current[id];
    const errors = { ...d.invalid };
    delete errors[key];
    put(id, {
      ...d,
      row: { ...d.row, data: row.data },
      invalid: errors,
      error: undefined,
    });
  };
  const save = async (id: string): Promise<Transaction> => {
    const existing = saving.current.get(id);
    if (existing) {
      await existing;
      return save(id);
    }
    const d = ref.current[id];
    if (d && Object.keys(d.invalid).length)
      throw new Error("Correct the highlighted cells before continuing.");
    if (!d || sameFields(d.base, d.row)) return latest(id);
    const request = (async () => {
      try {
        const [saved] = await post<Transaction[]>("/review", {
          action: "save",
          rows: [
            { id, revision: d.base.revision, fields: fieldsOnly(d.row.data) },
          ],
        });
        const now = ref.current[id];
        // An edit made during the request gets the new revision, then another save.
        put(id, {
          base: saved,
          row: sameFields(now.row, d.row)
            ? saved
            : { ...saved, data: now.row.data },
          invalid: now.invalid,
        });
        return saved;
      } catch (error) {
        put(id, {
          ...ref.current[id],
          error: error instanceof Error ? error.message : String(error),
        });
        throw error;
      } finally {
        saving.current.delete(id);
        setPending(new Set(saving.current.keys()));
      }
    })();
    saving.current.set(id, request);
    setPending(new Set(saving.current.keys()));
    await request;
    return save(id);
  };
  const accept = (saved: Transaction[]) => {
    for (const row of saved) put(row.id, { base: row, row, invalid: {} });
  };
  const forRemoval = async (id: string) => {
    // Removal also works when a cell contains invalid text. Keep the persisted
    // transaction and its history; discard its local draft only after removal succeeds.
    while (saving.current.has(id))
      await saving.current.get(id)?.catch(() => {});
    const d = ref.current[id];
    return d && (!sameFields(d.base, d.row) || Object.keys(d.invalid).length)
      ? d.base
      : latest(id);
  };
  const reload = async (id: string) => {
    // Fetch again, rather than discarding against a possibly stale poll snapshot.
    try {
      const rows = await api<Transaction[]>("/transactions");
      const row = rows.find((r) => r.id === id);
      if (!row) throw new Error("Transaction no longer exists.");
      accept([row]);
      return true;
    } catch (error) {
      put(id, { ...ref.current[id], error: String(error) });
      return false;
    }
  };
  const dirty = Object.values(drafts).some(
    (d) => !sameFields(d.base, d.row) || Object.keys(d.invalid).length,
  );
  useEffect(() => {
    if (!dirty && !pending.size) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, pending.size]);
  return {
    rows: rows.map((r) => latest(r.id)),
    drafts,
    pending,
    focused,
    setFocused,
    begin,
    update,
    invalid,
    restoreCell,
    save,
    accept,
    forRemoval,
    reload,
    changed: (id: string) =>
      !!drafts[id] && !sameFields(drafts[id].base, drafts[id].row),
  };
}

export type ReviewEdits = ReturnType<typeof useReviewEdits>;
