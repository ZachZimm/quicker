# Quicker implementation plan

This plan records the planning discussion and inspection on September 6, 2026.
Product requirements are settled below. Technical choices are selected for the
first implementation; Quicken entry still requires validation on Windows.
The first implementation is now available. See IMPLEMENTATION.md for completed
work, verification, and the user-approved deferral of Quicken entry and parcel
mapping. This document records the intended design and expands IDEA.md.

## Windows automation refinement

Fresh exports created directly in Quicken are required as part of Windows-side
automation. Quicken remains authoritative even when transactions originate outside
Quicker. Refresh the server reference before every entry run, recheck duplicate
matches before claiming work, and export again afterward for reconciliation.
Never simulate synchronization by appending Quicker transactions to a one-time
export. Provide refresh without entry and idle background refresh that respects
use of the Windows desktop.

Export creation and its freshness-event contract are the first Windows milestone.
The current passive watcher is a fallback, not the completed automation. See
[WINDOWS_HANDOFF.md](WINDOWS_HANDOFF.md) for the implementation sequence, existing
modules, concurrency requirements and acceptance checks.

## Confirmed requirements

- Add new transactions to Quicken Classic Business & Personal on a separate
  Windows computer. Do not reorganize existing transactions.
- Run document processing and shared application logic on Linux.
- Make a desktop-friendly browser application the primary interface, including
  document upload, transaction review, correction, and approval. Support phones.
- Allow access over the network, protected by a username and password. Public
  internet deployment is not required for the first version. Use one shared
  account initially.
- Provide a Windows companion that sends files from a configured folder to the
  server, moves them into an archive directory, and handles local Quicken entry.
- Support invoice and bill photos, tax documents, and credit card statements.
- Ordinary source documents indicate that payment has occurred. Every approved
  transaction must have a payment/transaction date. If the document does not
  establish that date, the reviewer must supply it; do not substitute a bill's
  invoice date or due date automatically.
- Extract every credit card purchase, using its purchase date as the transaction
  date. Include refunds as positive transactions. Exclude card payments, fees,
  and interest. Leave property assignment unset until manual review, except for
  the auto insurance rule below.
- Exclude visibly crossed-out items, recording them as ignored items. Underlines,
  check marks, brackets, and adjacent notes alone do not indicate exclusion.
- Ignore other handwriting except handwritten tax payment confirmations and dates.
- Identified auto insurance defaults to `R&K Properties` for both property/business
  and destination account, with category `Insurance (Business):Truck`. This is an
  explicit exception to blank card properties and payment-year account routing.
  Generic insurer names alone do not establish auto coverage. Review can override
  the assignments; saving edits does not reapply the rule.
- For tax documents, process each stub separately. Only stubs with payment
  confirmation and a paid date should create transactions. Ignore unpaid stubs.
- Resolve parcel-to-property mappings later, using the other pages of tax
  receipts when supplied. Do not invent mappings from the current samples.
- Use the local LM Studio endpoint at `http://localhost:1234` and model identifier
  `qwen3.8-27b@q4_k_m` initially, without an API key. The application and model
  initially run on the same Linux machine. Make the model protocol, host, port,
  model name, and optional API key configurable.
- Keep the parsing provider replaceable, as described in IDEA.md.
- Preserve uncertainty and require manual approval before adding transactions.
- Default destination account selection to the payment/transaction year and
  assigned property or business. Allow the reviewer to override the destination.
- Support table-based review, bulk property/account assignment, and batch
  approval after required fields are complete.
- Allow proposed transactions to be removed from consideration and restored.
- Do not support splitting a purchase across properties or categories in v1.
- Provide an "Enter approved transactions" button in both the Windows companion
  and the browser. Browser initiation requires the client to be connected.
  Approval alone does not start entry.
- Retain documents indefinitely on the server and in the Windows archive,
  including documents uploaded through the browser.

## Selected implementation

| Area | Choice | Reason |
| --- | --- | --- |
| Linux application | Python, FastAPI, Pydantic | Typed request and extraction validation, with Python image processing and shared transaction contracts for Windows. |
| Browser | React, TypeScript, Vite | Editable transaction tables, source previews, selection, and bulk review. Serve the built application from the same origin as the server. |
| Persistence | SQLite, SQLAlchemy, Alembic migrations | A local database fits one shared account and one connected Quicken installation. Store documents as files outside the database. |
| Extraction jobs | Separate Python worker with a durable SQLite job queue | Model calls can continue independently of browser requests, with recoverable state across restarts. |
| Windows companion | Python, PySide6, packaged with PyInstaller on Windows | A small window/tray app for connection status, archival sync, and initiating entry. Share Python contracts with the server. |
| Quicken entry | Evaluate QIF import first; use pywinauto for required desktop interactions | The existing QIF is a useful reference format. Select import or register entry only after checking actual Quicken behavior. |
| Initial Linux deployment | Python virtual environment, systemd units for application and worker, local persistent data directory | This lets the worker reach the existing localhost model endpoint without container networking changes. |

These are project choices informed by the official documentation for
[FastAPI persistence](https://fastapi.tiangolo.com/tutorial/sql-databases/),
[React with Vite](https://react.dev/learn/build-a-react-app-from-scratch),
[SQLite deployment](https://www.sqlite.org/whentouse.html),
[PySide6](https://doc.qt.io/qtforpython-6/gettingstarted.html),
[PyInstaller packaging](https://pyinstaller.org/en/stable/operating-mode.html),
and [pywinauto](https://pywinauto.readthedocs.io/en/latest/getting_started.html).
They do not establish compatibility with Quicken's particular controls.

Keep SQLite on the Linux machine's local disk. Windows and browsers communicate
through the application, never by opening a shared database file. Use short
database transactions; never hold a write transaction during a model request,
file transfer, or desktop operation. Start with one extraction worker and make
model-request concurrency configurable after measuring representative batches.

### Module interfaces

- Document intake accepts files, stores originals, tracks archive receipts, and
  exposes document status. The browser and folder uploader share this interface.
- Extraction accepts document IDs and a model configuration revision, and returns
  validated document facts and candidate transactions. Model request formats,
  image preparation, response parsing, and bounded retries stay inside it.
- Review owns edits, exclusions, restoration, account routing, and approval.
  Its interface enforces required fields and revision checks for concurrent tabs.
- Delivery accepts an explicit request to enter approved revisions and returns
  run status. Windows and browser buttons use this same interface. Its Windows
  adapter owns Quicken-specific interactions and verification.

Use a real model adapter and a deterministic test adapter at the extraction
seam. Keep Quicken details out of extraction and review; use a fake delivery
adapter to exercise interruption behavior before testing actual Windows entry.

### Configuration and login

Store model settings on the server and expose them in authenticated settings:

- API protocol/provider adapter, initially the tested chat-completions protocol.
- Transport scheme, initially `http`, with `https` available for remote hosts.
- Host `localhost`, port `1234`, and configurable base path, initially `/v1`.
- Model identifier `qwen3.8-27b@q4_k_m`.
- Optional API key. An empty value means omit the authorization header.
- Request timeout, model concurrency, and image-processing limits.

Offer a connection and vision check. Save a configuration revision with each
extraction attempt, and use that revision throughout the attempt. Keep API keys
out of logs and browser responses. Replacing a key should not require exposing
the old value. OpenRouter and a Codex CLI proxy can use an appropriate adapter;
their image and response formats must be verified when configured.

Provision the shared username/password through an interactive server setup
command. Store a password hash; use expiring server-side browser sessions,
HttpOnly cookies, CSRF protection for mutations, and login rate limiting. Pair
the Windows app from the authenticated UI with a revocable device credential,
without creating another human account. Terminate HTTPS at a reverse proxy for
network access, with certificate trust configured during installation.

Configure input and archive folders, server address, and device credential on
Windows. Configure persistent data paths and backup destinations on Linux.
Back up the database using SQLite's backup mechanism and retain its referenced
document files. The Windows document archive does not replace database backups.

## Findings from the existing Quicken setup

Inspected local reference files:

- `/home/zach/gold/projects/quicker/quicker-reference.QIF`
- `/home/zach/gold/projects/quicker/quicker-screenshot 2026-09-06 011133.png`
- Four source document PNGs in the same directory.

The original export contained only 41 Bell Street transactions. The later
`quicker-full-reference.QIF` contains 36 account names, 161 categories, 51 tags,
183 memorized payees and 1,262 transactions across 35 registers, dated January
2024 through September 2026. Of these, 264 transactions belong to four R&K
accounts. The remaining Bill account has no exported register history.
All 287 blank-payee entries are retained in the reference catalog.

Accounts appear to organize records by property and year, including
`2026 Bell St.`, `2026 Holman Way`, and `2026 Viento Way`. R&K Properties has
several accounts, including `R&K Properties 2026`. Payment/transaction year will
determine the default account, with an override in review. Most listed accounts
are exported as `Bank`; one is `Bill`.

Tags serve several purposes. Examples include `1008 Bell`, `2 Bell`, `Utilities`,
`Water`, and `R&K`. Category and tag are separate from the destination account.
For example, the Bell account contains a Washoe County Treasurer expense using
category `Property Tax` and tag `Utilities`.

Existing categories and memorized payees contain alternative assignments for
similar expenses. Preserve exact existing names. Historical examples may inform
suggestions, but conflicting examples should remain visible during review.

No split transactions were found in this account's exported history or in the
memorized payee records. This does not establish that splits are never needed.

RustDesk is installed locally, but a CLI launch failed because this session has
no graphical display. No remote connection was established and no Quicken data
was changed.

## Model capability check

LM Studio reports vision support for the selected model. A request through
`/v1/chat/completions` also successfully processed the utility bill image.

For that check, an in-memory JPEG copy was resized to 2000 by 1500 pixels. The
original was unchanged. The request completed in 7.7 seconds and correctly read
NV Energy, the July 19, 2025 billing date, the printed $214.55 total balance,
$54.39 previous balance, and $142.79 electric charges. It ignored the handwritten
amount as instructed. This demonstrates working vision support, not general
extraction accuracy or an established image-size requirement.

## Workflow

1. Accept uploads from the browser and Windows folder ingestion into one queue.
   Assign stable document IDs and acknowledge durable storage before the Windows
   companion archives a source file. Retrying an acknowledged upload should not
   create another document.
2. Retain original uploads and produce smaller copies for model input and
   previews. Normalize orientation without modifying originals. Start with
   JPEG, PNG, and HEIC photos; report unsupported or unreadable files explicitly.
   Begin evaluation at a 2000-pixel maximum image dimension, retaining the option
   to use a larger image or crop when small print is unreadable. Upload phone
   originals with visible progress; downscaling must not discard the original.
3. Extract document facts separately from suggested Quicken assignments. One
   document can produce zero, one, or several transaction candidates. Preserve
   links to source images and identify ignored tax stubs, card payments, fees,
   and interest. Retain refunds as positive transaction candidates.
4. Validate extracted fields and apply explicit document rules. Keep invoice,
   due, statement, and payment dates distinct. Keep parcel identifiers as text,
   preserving leading zeros. Unknown parcel mappings must remain unresolved.
5. Present a shared review queue with source images, editable transaction fields,
   and reasons for missing or uncertain values. Credit card properties remain
   unset until the reviewer assigns them, except for identified auto insurance.
   Use exact account/category/tag names
   from the existing Quicken file. Require a payment/transaction date on every
   row before approval. Support bulk assignment and batch approval, while
   keeping each row's destination account visible and editable.
6. Save approvals. An explicit button in Windows or the browser requests entry,
   and the Windows companion retrieves that run's approved revisions. Choose
   the Quicken import or entry mechanism through an experiment on a disposable
   copy of the data file, verifying dates, amounts, accounts, categories, tags,
   and memos before adopting it.
7. Track entry progress per transaction. After an interruption, distinguish
   confirmed entries, pending entries, and entries whose outcome is unknown.
   Do not automatically resend an entry whose previous outcome is unknown.

Transaction fields are payee, signed amount, payment/transaction date,
category, optional tag, property or business assignment, destination Quicken
account, optional memo, source reference, review status, and delivery status.
Resolve the default destination using an explicit property/business-and-year
mapping to existing accounts, preserving their exact names. Keep destination
overrides separate from the date so choosing a different account does not
require changing the recorded payment date. If no account mapping exists, leave
the destination unresolved for review rather than inventing an account name.

Require payee, a valid nonzero signed amount, date, existing expense/income
category, and existing destination account before approval. A tag is optional.
For card purchases and refunds, require manual property/business assignment.
Store amounts exactly as integer minor units with currency, using USD for the
initial Quicken setup. Dates are calendar dates without timezone conversion.
Flag unsupported currencies and unreadable values for review.

Import the QIF into a separate reference catalog containing exact account,
category, tag, and memorized payee names. Preserve category types and treat
bracketed transfer destinations as account references rather than expense
categories. Refresh the catalog without editing historical Quicken data or
silently invalidating already approved transaction revisions.

Allow several photos to be grouped as one document, with a page order and a
shared source identity. This will allow a tax receipt's parcel/address page to
be associated with its stubs later. Extract parcel-to-address evidence separately
and require confirmation before adding a reusable mapping. Until those pages
arrive, permit manual assignment to an existing property/account in review.

### Permanent document archives

- Retain originals indefinitely on the server, including failed, ignored, and
  removed documents. Retain extraction attempts and reviewer edits separately.
- Move a Windows input file to the archive only after a server storage receipt.
  Use document IDs and checksums to avoid collisions and make retries safe.
  Exclude the archive folder from input scanning. Retry a failed local move
  without uploading a second document.
- Download browser/phone uploads into the Windows archive automatically whenever
  the client connects. Resume interrupted transfers, verify checksums before
  committing files, and report pending archival copies in the UI.
- Keep source files and application-generated previews separate. Identical
  content may share stored bytes while preserving each upload's record.
- Review remains available while Windows is offline. Archive sync catches up
  after reconnection; it does not imply permission to start Quicken entry.

### Removal and approval behavior

- Removing a proposed transaction excludes it from approval and entry, while
  retaining its source, edits, and stable ID. Provide a removed-items view with
  a restore action.
- Restoring returns the transaction to review and requires approval again.
- Reprocessing a document must not silently undo a reviewer's removal or edits.
- Changes to transaction data after approval require fresh approval before
  entry. Destination overrides must not be silently overwritten by later date
  edits; flag inconsistencies for review.
- Once entry has started, removal is not a reliable cancellation mechanism.
  Resolve delivery status first. Removing a proposal never deletes a transaction
  that has already been entered in Quicken.

Duplicate handling should identify exact repeated files and flag likely repeated
transactions across different images. The two credit card samples appear to
show the same page at different brightness. A matching payee/date/amount alone
should not silently discard a potentially legitimate second purchase.

### Starting entry and recovering from interruption

The Windows companion initiates an authenticated outbound connection, reports a
heartbeat and readiness, and polls for requested work. No incoming Windows port
is needed. The browser shows offline, connected, busy, and attention-required
states. An online heartbeat is necessary but does not establish desktop readiness.

Both entry buttons create the same server-side run. Freeze the selected approved
revisions when the run is created. Use request IDs and atomic transaction claims
so a repeated request or simultaneous browser/Windows clicks cannot claim the
same revision twice. Permit one active Quicken run for the configured data file.
Reject web initiation when the client is offline; do not save an old start
command to execute unexpectedly after a future reconnection.

Before entry, check that the expected Quicken file and usable desktop are present
and that relevant source documents are archived locally. Display progress in
both interfaces. Stop on unexpected dialogs or changed application state. A local
stop button stops before the next transaction and preserves current status.
Use a visible running indicator so the Windows user can see that entry is active.

Keep a durable local journal and server-side per-transaction status. A lost
heartbeat must not automatically release an in-progress entry for another run.
The desktop operation and server acknowledgement cannot be atomic: if Quicken
may have accepted an entry before a crash or disconnect, mark the outcome unknown
and require reconciliation before any retry. Do not promise exactly-once entry
unless the selected Quicken mechanism can verify it.

## Implementation stages

1. Build a Windows diagnostic and validate Quicken entry on a disposable data-file
   copy. Inspect the available import dialog and accessible controls. Quicken's
   published QIF import guidance is inconsistent about supported account types,
   so verify this installation directly. Determine the smallest
   reliable Windows integration and how to verify successful entry and recover
   from partial failure. Verify purchase, refund, date, destination account,
   category, tag, and memo behavior. Produce an import/entry decision with evidence.
2. Establish the Python project and browser build, database migrations, shared
   contracts, login, model configuration, and QIF reference import. A signed-in
   user can inspect exact existing account/category/tag names and test the model.
3. Build document intake, permanent storage, grouping, image preparation, and
   the durable extraction queue. Validate the supplied images against manually
   checked expected results. Expose failures and ignored items in the browser.
4. Build the review table, source viewer, bulk assignment, date requirements,
   account defaults and overrides, duplicate warnings, removal/restoration, and
   approval history. Exercise multi-tab edits and keep stale approvals from entry.
5. Package the Windows companion, add pairing, folder ingestion, archival sync,
   readiness reporting, and entry runs initiated from either UI. Exercise
   simultaneous starts, disconnects, and partial failures using the delivery
   test adapter before the disposable Quicken copy.
6. Package Linux installation and Windows builds; exercise the complete workflow
   against a representative batch and restore a backup. Add and verify parcel
   mappings once the additional tax receipt photos arrive. Their absence does
   not block a release with manual property assignment.

Verification should cover paid versus unpaid tax stubs, the handwriting
exception, card purchases and positive refunds with payments/fees/interest
excluded, unset card properties with the auto insurance exception, required
review dates, year-based account
defaults and overrides, bulk assignment, batch approval, removal/restoration,
repeated uploads, interrupted entry, and exact Quicken fields.
Compare reduced-resolution extraction against readable source documents before
settling the image policy.

Also verify that phone uploads reach both permanent archives after a client
reconnect, local archive failures cannot lose input files, model settings allow
an absent API key, a new host/model can be selected without code edits, documents
require login to view, and neither entry button runs unapproved or removed rows.
Use Python tests for domain and persistence behavior and Playwright for the
browser workflow. Validate real Quicken behavior on Windows; mocks cannot prove
that the installation accepts an import or preserves its fields.

## Remaining discovery and deferred work

- Validate the Quicken import/entry method and outcome verification on Windows.
  See the conflicting official guidance on
  [QIF account support](https://info.quicken.com/win/why-can-t-i-import-data-from-a-qif-file-into-a-ban)
  and [Quicken imports](https://www.quicken.com/support/how-do-i-import-data-quicken-windows/).
- Additional R&K transaction history may improve suggestions; it is not required
  to start with manual category and destination selection.
- Supply the additional tax receipt pages when available to establish parcel
  mappings. Do not infer them from the current stub-only image.
- Choose concrete installation paths, network hostname/certificate, and backup
  destination during deployment. Credentials will be entered at setup time.
- Splits, multiple human accounts, reorganization of existing transactions, and
  public internet hosting are outside the initial release.

## Full-history refinement

The user selected year-specific R&K destinations except for auto insurance, and
specific 2026 categories. The implemented choices and match conditions are listed
in README.md. Known property aliases share stable identities and exact yearly
account mappings; Bell unit labels remain distinct from the Bell register.
Reference import preserves dates, signed amounts and original QIF records, and
reports per-account coverage. Historical duplicate matches require review and
acknowledgement; reference imports can invalidate affected approvals. Merchant
normalization supports existing spelling variations while retaining printed text.
Interactive rule creation and a general explanation system remain deferred.

Bills and invoices now extract a structured property address. A verified service,
job or utility-customer address can set the property before review, with exact
house-number matching and normalized street abbreviations. The initial verified
address is 1008 Bell St, Reno, NV, mapped to Bell St. This does not supply a payment
date or override the explicit business rules. Card mailing addresses and tax
parcel mappings remain outside this mechanism.
