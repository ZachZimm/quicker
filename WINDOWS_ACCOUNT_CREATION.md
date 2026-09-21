# Windows account creation: implementation and contract

The Linux/browser work adds latest-year account defaults, searchable register
dropdowns, and explicit confirmation for an account absent from the imported QIF.
The Windows account-creation adapter is implemented and validated on English
Quicken Classic Business & Personal 27.1.69.29. Per the user's revised scope,
creation supports **Bank only**. Existing transaction entry continues to require
an account verified in Quicken's exported account list.

## User flow and scope

In Review, the user types an account name, chooses Create or presses Enter, and
confirms. There is no account-type prompt; the browser sends Bank and the API
rejects other types. `POST /api/account-requests` stores a
durable request tied to the paired device and its configured QDF identity. The
transaction uses that exact name as an override and stays in review until a fresh
export contains the account. The browser distinguishes pending creation from an
existing account. Older companions leave requests queued with an update message.

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
   `account_type` (`Bank`), `file_identity`, `file_name`, `status`,
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
Account names are trimmed, limited to 39 characters, cp1252-compatible, and exclude
control characters, brackets, and `^`. Identical names are deduplicated ignoring
case within the target QDF; request IDs cannot be reused for different content.
The original 63-character limit was corrected after native testing showed that
Quicken truncates QIF account names to 39 characters. Both API and client reject
longer names before mutation; the browser asks the user to shorten the name.

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

- Create Bank accounts with exact names and expected types;
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

## Implementation and native results (September 20, 2026)

- The companion advertises `account_creation: 1`, polls requests, prioritizes
  in-flight creations and spaces retries by at least 60 seconds. Refresh, import
  and entry use the existing desktop mutex and QDF/focus/input guards.
- Creation imports a QIF containing only `!Account`, the exact name and `TBank`.
  Only Account List is checked in Quicken's QIF Import dialog. No transaction,
  opening balance, category, memorized payee or online-service data is supplied.
- Created `2029 Example St.` in the disconnected test QDF. A fresh full export proved
  exactly one Bank account was added and all transaction history was unchanged.
  Quicken's Account Details confirmed Checking, no institution or account number,
  no transaction-download setup and no online bill pay. Repeated processing did
  not create another account. A separately approved one-cent test expense was
  subsequently entered and verified through the normal transaction protocol.
- A 39-character name including an accented character, semicolon and ampersand
  round-tripped exactly, again with unchanged transaction history. An earlier
  overlong probe remains only in the disconnected test copy as evidence of
  Quicken's truncation; it was not renamed or deleted.
- Fixed QIF account selection when a register is open: explicitly open the custom
  dropdown before selecting All accounts, preserving dropdown focus and avoiding
  an Enter key that could submit the dialog prematurely.
- Controller/API tests cover offline queueing, old-client capability, unsupported
  types/names, existing accounts, case/type conflicts, ownership, QDF mismatch,
  revoked devices, unexpected dialogs, active entry, reduced export coverage,
  lost responses and crashes before/after submission. Browser validation covers
  confirmation without a type selector and clearing pending status after proof.
