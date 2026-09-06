export let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch("/api" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message =
      typeof payload.detail === "string"
        ? payload.detail
        : JSON.stringify(payload.detail || response.statusText);
    if (response.status === 401 && path !== "/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(message);
  }
  return response.json();
}
export function post<T = any>(path: string, body: unknown = {}) {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
export function upload(
  files: File[],
  grouped: boolean,
  progress: (n: number) => void,
): Promise<any> {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    body.append("grouped", String(grouped));
    body.append("request_id", crypto.randomUUID());
    const request = new XMLHttpRequest();
    request.open("POST", "/api/upload");
    request.setRequestHeader("X-CSRF-Token", csrf);
    request.upload.onprogress = (e) => {
      if (e.lengthComputable) progress(Math.round((e.loaded / e.total) * 100));
    };
    request.onerror = () =>
      reject(
        new Error("Upload interrupted. Your originals remain on this device."),
      );
    request.onload = () => {
      let data;
      try {
        data = JSON.parse(request.responseText);
      } catch {
        reject(new Error("The server returned an unreadable response"));
        return;
      }
      if (request.status >= 200 && request.status < 300) resolve(data);
      else
        reject(
          new Error(
            typeof data.detail === "string" ? data.detail : "Upload failed",
          ),
        );
    };
    request.send(body);
  });
}
export type Catalog = {
  accounts: { name: string; type: string }[];
  categories: { name: string; type: string }[];
  tags: { name: string }[];
  routes: Route[];
  payees: unknown[];
  history: unknown[];
};
export type Route = { property: string; year: number; account: string };
export type Page = { id: string; name: string; ordinal: number };
export type DocumentRecord = {
  id: string;
  name: string;
  status: string;
  error: string | null;
  pages: Page[];
  ignored: { source: string; reason: string; page: number }[];
  repeated_content: boolean;
};
export type Fields = {
  payee: string | null;
  amount_minor: number | null;
  date: string | null;
  currency: string;
  category: string | null;
  tag: string | null;
  property: string | null;
  account: string | null;
  account_override: boolean;
  memo: string;
  duplicate_acknowledged: boolean;
};
export type Transaction = {
  id: string;
  document_id: string;
  revision: number;
  status: string;
  data: Fields & {
    source: string;
    page: number;
    parcel: string | null;
    kind: string;
    document_type: string;
  };
  issues: string[];
  warnings: string[];
  duplicates: string[];
};
export function fieldsOnly(data: Transaction["data"]): Fields {
  const {
    payee,
    amount_minor,
    date,
    currency,
    category,
    tag,
    property,
    account,
    account_override,
    memo,
    duplicate_acknowledged,
  } = data;
  return {
    payee,
    amount_minor,
    date,
    currency,
    category,
    tag,
    property,
    account,
    account_override,
    memo,
    duplicate_acknowledged,
  };
}
