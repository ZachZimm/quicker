# Implementation status

The first implementation covers document ingestion through manual approval and
permanent archival. Quicken interaction and parcel mapping are deferred at the
user's request.

## Implemented

- FastAPI application, SQLite persistence, packaged Alembic migration, and
  separate extraction worker with durable claims, bounded retries, settings
  snapshots, failure history, and protection against expired workers publishing.
- One shared login with hashed passwords, expiring sessions, CSRF checks,
  rate-limited login, and authenticated original/preview access.
- Configurable model protocol, HTTP/HTTPS transport, host, port, base path, model,
  optional API key, timeout, image size, and extraction concurrency. Chat
  completions and Responses adapters have contract tests; the selected local
  model has been exercised through chat completions.
- QIF catalog import for exact account/category/tag names and existing payee
  examples. Editable property/year account routes and explicit review overrides.
- Ordered multi-photo document uploads, immutable originals, reduced image
  previews/model inputs, upload receipts, and grouped document extraction.
- A separate visual check on each page identifies crossed-out items for
  exclusion. Ambiguous marks stay in review with a warning. Underlines, check
  marks, brackets, and nearby notes do not count as crossed-out items.
- Browser transaction table and source viewer, bulk assignment, required dates,
  atomic batch approval, duplicate acknowledgement, stale-edit rejection,
  reversible removal, and audit history. Changes after approval require approval
  again. Failed requests are visible inside open review dialogs.
- Windows companion UI with pairing, folder ingestion, durable upload receipts,
  checksum-verified local archives, resumable downloads, heartbeat, and tray
  behavior. Browser uploads also sync to the Windows archive after reconnection.
- Browser and Windows entry buttons visibly disabled. The server entry endpoint
  returns an explicit not-implemented response without mutating transactions.
- Source installation instructions, Windows packaging script, systemd templates,
  local HTTPS reverse-proxy example, and database/original backup command.

## Verification

The final local test run passed all 29 tests. The production browser build,
Python lint, deployment-script syntax, and diff whitespace checks also passed.

Automated checks include Python/API tests, real Chromium workflow tests at desktop
and phone sizes, server-to-companion HTTP archival round trips, a restored backup,
protocol adapter contracts, and an offscreen PySide6 window check on Linux.
Frontend TypeScript compilation and the production build pass. Python lint and
shell syntax checks are included in the final verification.

Live extraction against the supplied photos and configured local model produced:

| Sample | Result |
| --- | --- |
| Utility bill | One expense of $214.55; payment date remains blank for review; handwritten correction ignored. |
| Tax installment sheet | Two paid installments, $144.16 and $137.66, both dated August 10, 2026; two unpaid stubs ignored; parcel `03127109` preserved as text. |
| Credit card statement | 14 purchases, totaling $4,178.73; the crossed-out $22.06 MHS Incline Village purchase and card payment excluded; every property left unassigned. |

The initial verbose extraction prompt exhausted the model's available response
budget on the card statement. A compact output contract allowed a complete
transcription, though subsequent runs can still reach the response limit.
Combining transcription and mark recognition missed the sample strike-through;
a separate visual pass correctly identified it.
Truncated or malformed responses are still rejected in full rather than creating
partial transactions. Large page groups may need a larger model context or fewer
pages. Successful sample extraction does not remove the need for manual review.

The live examples are stored under the ignored application data directory, and
remain unapproved. Test screenshots and generated login credentials are under
ignored `.local/`; private photos and credentials are not part of source control.

## Deliberately unfinished or not verified here

- Automatic parcel-to-property mapping. Review supports manual assignment now.
- Actual Quicken entry, desktop readiness checks, delivery-run state, and
  reconciliation of uncertain Quicken outcomes. No imported/entered success can
  be reported by the current placeholder.
- Windows-native executable packaging and operation on the target Windows
  computer. The packaging script is provided; core sync and the Qt window were
  tested on Linux.
- systemd/Caddy installation and certificate trust on client devices. Development
  processes run directly, with an HTTP LAN preview. Persistent HTTPS deployment
  is documented but not installed automatically.
- A native browser WebMCP runtime is not available in this environment. The
  optional feature-detected tools do not affect the regular browser workflow.
- Public internet deployment, multiple human accounts, split transactions, and
  modifications to existing Quicken transactions remain outside v1.
