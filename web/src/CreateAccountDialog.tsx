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
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [id] = useState(requestId);
  const nameError =
    name.length > 39
      ? "Shorten the account name to 39 characters, then try again."
      : "";
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
          Confirm to create a Bank account in the Windows companion's configured
          Quicken file. The account remains pending until Windows creates it and
          a fresh export verifies it. Transactions still need separate approval
          and entry.
        </p>
        {(nameError || error) && <p role="alert">{nameError || error}</p>}
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
            disabled={working || !!nameError}
            onClick={async () => {
              setWorking(true);
              setError("");
              try {
                const request = await post<AccountRequest>(
                  "/account-requests",
                  {
                    request_id: id,
                    name,
                    account_type: "Bank",
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
