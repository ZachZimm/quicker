import { useState } from "react";
import { post, requestId } from "./api";
import type { AccountRequest } from "./api";

export function CreateAccountDialog({
  name,
  onCancel,
  onCreated,
}: {
  name: string;
  onCancel: () => void;
  onCreated: (request: AccountRequest) => void;
}) {
  const [kind, setKind] = useState("Bank");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [id] = useState(requestId);
  return (
    <div className="modal-overlay">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Create Quicken account"
      >
        <h2>Create an account in Quicken?</h2>
        <p>“{name}” is not in the existing account list.</p>
        <p>
          Confirm to request this account in the Windows companion's configured
          Quicken file. The account remains pending until Windows creates it and
          a fresh export verifies it. Transactions still need separate approval
          and entry.
        </p>
        <label>
          Account type
          <select
            value={kind}
            disabled={working}
            onChange={(event) => setKind(event.target.value)}
          >
            <option value="Bank">Bank</option>
            <option value="Cash">Cash</option>
            <option value="CCard">Credit card</option>
          </select>
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button
            aria-label="Close account creation"
            disabled={working}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="primary"
            disabled={working}
            onClick={async () => {
              setWorking(true);
              setError("");
              try {
                const request = await post<AccountRequest>(
                  "/account-requests",
                  {
                    request_id: id,
                    name,
                    account_type: kind,
                    confirmed: true,
                  },
                );
                onCreated(request);
              } catch (error) {
                setError(
                  error instanceof Error ? error.message : String(error),
                );
              } finally {
                setWorking(false);
              }
            }}
          >
            {working ? "Submitting…" : "Confirm account creation"}
          </button>
        </div>
      </section>
    </div>
  );
}
