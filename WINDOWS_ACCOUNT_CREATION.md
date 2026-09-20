# Windows handoff: create confirmed Quicken accounts

The Linux/browser work adds latest-year account defaults, searchable register
dropdowns, and explicit confirmation for an account absent from the imported QIF.
The Windows account-creation adapter is still needed. Existing transaction entry
must continue to require an account verified in Quicken's exported account list.
Do not advertise this capability until it is implemented and tested on Windows.

## User flow and scope

In Review, the user types an account name, chooses Create or presses Enter, selects
Bank, Cash, or Credit card, and confirms. `POST /api/account-requests` stores a
durable request tied to the paired device and its configured QDF identity. The
transaction uses that exact name as an override and stays in review until a fresh
export contains the account. The browser distinguishes pending creation from an
existing account. It currently says that an updated Windows client is required.

Only create the named local/manual account, with no institution connection or
invented opening balance, opening-balance transaction, or historical entries.
Confirm the account type and exact name in a fresh export. A separate action still
approves and enters the source transaction. If a supported account cannot be
created without additional information, report what is needed and stop.

Automatic account selection now takes the highest mapped year for the property,
regardless of transaction date. Explicit account overrides survive date/property
changes. Creating an account with a recognizable year/property name lets the
normal QIF import infer its route; arbitrary names are overrides until mapped.

## Existing code to extend

- `client/quicker_client/sync.py`: capability heartbeat and polling loop.
- `client/quicker_client/operations.py`: durable journal, owner UUID, native export
  receipts, refresh/reconciliation and recovery.
- `client/quicker_client/desktop.py`: actual English Quicken UI automation.
- `server/quicker/accounts.py`: request API and proof/ownership checks.
- `tests/test_accounts.py`: server contract and browser acceptance tests.

Use the existing exclusive desktop session, configured QDF identity check, focus
and unexpected-dialog protections. Serialize account creation, exports, and entry
through the same local controller. Never run two UI automation operations at once.
Old clients do not consume these requests, so do not change protocol-1 entry
semantics. Add `"account_creation": 1` to the existing heartbeat only in the new
client, alongside `protocol`, `file_identity`, `file_name`, and readiness fields.

## API contract

All `/api/device/` requests use the existing paired Bearer token. Use the same
durable owner UUID as the existing desktop journal. These operations are separate
from `/api/device/operations` and its `DesktopRun` records.

1. Poll `GET /api/device/account-requests`. Each item has `id`, `name`,
   `account_type` (`Bank`, `Cash`, `CCard`), `file_identity`, `file_name`, `status`,
   `created`, `attempted`, and a user-facing `message`. Only the owning device's
   unfinished requests are returned. Check the QDF identity before any action.
2. For a queued request, create, upload and reconcile a **new native full export**
   using the existing `refresh` operation. Finish that run before claiming the
   account. Use its active export event ID as `event_id` below.
3. `POST /api/device/account-requests/{id}/claim` with
   `{"owner":"<uuid>","event_id":"<uuid>"}`. A fresh current export for the
   configured file is required. If the exact name/type already exists, this returns
   `status: "complete"`; perform no creation. Otherwise it returns `creating`.
   A conflicting case-insensitive name or type produces 409 and requires human
   resolution. Existing desktop operations and other account creations prevent
   a new claim. Before an attempt, the same owner can refresh and claim again to
   replace a stale pre-export. After an attempt, claims only return persisted state.
4. Prepare the UI and journal intent before its irreversible submit. Immediately
   before submit, `POST .../{id}/attempt` with `{"owner":"<uuid>"}`. Only its
   first successful response has `may_create: true`. The pre-export must still be
   current and no desktop run may be active. A repeat has `may_create: false`.
   Submit to Quicken **once**, only after receiving true and with no intervening
   interruption. If the response is lost, or the client restarts, do not submit.
5. After creation, run another complete native refresh/export/reconcile operation.
   `POST .../{id}/complete` with `{"owner":"<uuid>","event_id":"<uuid>"}`.
   The server requires a current active export from the same device/file, started
   after the recorded attempt, containing the exact account name and type. It then
   returns `complete`. A UI success message alone cannot complete the request.

An export must have been received within ten minutes and its generation must be
the current reference generation. Manual one-time QIF uploads are not creation
proof. Existing export validation and reduced-coverage checks remain in force.
Account names are trimmed, limited to 63 characters, cp1252-compatible, and exclude
control characters, brackets, and `^`. Identical names are deduplicated ignoring
case within the target QDF; request IDs cannot be reused for different content.

## Recovery

Persist request ID, target file identity, account name/type, owner, and local phase
before the attempt. A `creating` request with `attempted: true` must be reconciled
with a new export, never automatically created again. This applies to lost HTTP
responses as well as a crash after Quicken's submit.

If the account is absent after an uncertain attempt, leave it pending and explain
that creation could not be verified. The operator may finish creating the exact
account in Quicken and refresh to verify it. There is intentionally no automatic
attempt reset. An owner mismatch or changed QDF must stop, not take over. A typo
or conflicting account type should be resolved with the user; do not rename or
delete existing Quicken accounts automatically.

The server blocks transaction-entry starts while account creation is in progress,
but permits refresh operations needed for recovery. Release the local desktop
session before starting another refresh session if the adapter does not support
nesting. Prefer reusing the existing export implementation over a second exporter.

## Windows validation

Use the existing disconnected test QDF, never the production file for probes.

- Create Bank, Cash and Credit card accounts with exact names and expected types;
  verify no unwanted opening-balance transactions or online connections.
- Verify a pre-existing account satisfies a queued request without being created
  again, and differently cased/type-conflicting names stop without mutation.
- Test offline queueing, unsupported old clients, wrong QDF, revoked device,
  owner mismatch, active entry runs and unexpected dialogs.
- Test lost responses before/after attempt and creation, crashes before/after
  Quicken submission, repeated polling, and stale/reduced-coverage exports.
- Verify the browser's pending state clears after the post-export; then approve
  and enter a transaction in the new account and verify it through the normal
  transaction export/reconciliation flow.
- Build and install the companion preserving pairing and the local journal;
  document which Quicken version and account types were tested.
