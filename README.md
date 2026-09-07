# Quicker

A local web application for turning invoice photos, paid tax stubs, and credit
card statements into reviewed transactions for Quicken. The browser is the main
workspace; the Windows companion uploads a folder and synchronizes originals to
a permanent archive.

**Quicken entry is intentionally unfinished.**
Entry buttons explain that limitation and cannot mark any transaction as entered.
You can manually assign a property and account, edit, remove/restore, and approve
transactions now.

## Run on Linux

Requires Python 3.12+, Node.js 22+, and a running vision-model endpoint. From the
repository root:

```bash
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
cp .env.example .env
uv run quicker setup --username admin
uv run quicker import-qif /path/to/quicker-reference.QIF
uv run quicker serve --host 0.0.0.0 --port 8765
```

In another terminal, start the durable extraction worker:

```bash
uv run python -m quicker.worker
```

Open `http://localhost:8765` on this computer, or use the Linux machine's LAN
address from another device. The development server uses HTTP. For the planned
HTTPS deployment, use the reverse-proxy configuration below.

Settings default to `http://localhost:1234/v1`, protocol `chat-completions`, and
model `qwen3.8-27b@q4_k_m`, with no API key. Protocol, transport, host, port, base
path, model, optional key, timeout, concurrency, and image size are editable in
Settings. A blank key on a new connection sends no Authorization header; the
explicit Remove key checkbox clears an existing key. The vision check uses a
small synthetic image. Save settings before testing them.

The app reads environment variables from its process. For custom `.env` values,
use `uv run --env-file .env quicker …` and `uv run --env-file .env python -m
quicker.worker`, or export them in the shell. The systemd units read `.env`
automatically. `QUICKER_DATA_DIR` defaults to `./data`; keep it on a local disk.
`QUICKER_WEB_DIST` can override the path to the built browser files.

If a shared account was provisioned during development, its generated credentials
are in the ignored `.local/first-login.txt`. `quicker setup` resets the shared
password and invalidates browser sessions. It does not delete documents.

## Use the workspace

1. Import an all-accounts QIF export with Transactions, Account List, Category List,
   and Memorized payees in Settings. Verify the transaction count and date range
   for each account in the coverage table. Blank-payee entries and original QIF
   records are retained. Importing never writes to Quicken.
2. Review the property directory and year/account mappings. Known aliases share
   a property identity; units remain separate labels. Exact Quicken account names
   are retained. R&K business defaults use payment year; auto insurance uses the
   exact `R&K Properties` account. All assignments remain editable.
3. Upload JPEG, PNG, or HEIC images. Choose whether files are separate documents
   or ordered pages of one document. Originals are retained; smaller JPEG copies
   are used for extraction and preview. Limits are 20 images, 50 MB per image,
   and 150 MB per upload batch.
4. Keep the worker running. It records attempts, retries failures up to three
   times, and exposes failed documents with a manual retry using current settings.
   A job uses the model settings revision saved when it was queued. Group only
   as many pages as fit the configured model's context. A truncated response is
   rejected in full and cannot create partial transactions.
5. Use the review table and source viewer to complete fields. Dates are required.
   Card purchases use purchase dates, refunds are positive, and payments, fees,
   interest, and visibly crossed-out items are ignored. Card properties are
   left for manual review except the business rules below. Generic insurer names
   alone do not establish auto coverage. Other handwriting is ignored except tax payment
   confirmations and dates. Underlines, check marks, and adjacent notes alone do
   not exclude a transaction.
6. Select rows for bulk property, account, category, or date assignment and batch
   approval. Possible duplicates within Quicker or in imported Quicken history
   require explicit acknowledgement. Historical matches show account, date,
   signed amount, payee and category. A new historical match on a later import
   returns affected approvals to review. Removing a row
   retains its edits and original document. Restoring returns it to review.
   Editing an approved transaction requires approval again.
7. Pair the Windows companion to populate its archive, including phone uploads.

Catalog assignments from extraction are suggestions. Required dates, valid
catalog names, exact amounts, and duplicate acknowledgement are enforced by the
server at approval time. The model can still make reading mistakes; review the
source before approval. No model output can initiate Quicken entry.

## Address-based property assignment

Bills and invoices can assign a property from a model-read service or job address.
For utility bills, the customer address is also eligible when no conflicting
service address is shown. The address must match a verified entry in
`server/quicker/profile.py`; house numbers are never fuzzy-matched. Street
abbreviations and unit suffixes are normalized, while conflicting cities or
states prevent a match. Mailing and supplier addresses, credit card statement
addresses, and tax stubs do not trigger this rule.

`1008 Bell St, Reno, NV` is verified as **Bell St.** The original address is shown
in review. Further verified addresses can be added to the same property directory.
Explicit business rules take precedence and conflicting evidence is flagged.
Assignments remain editable. The payment date is still required before the
payment-year destination account can be selected; billing and due dates are not
substituted.

## Bookkeeping defaults

The conventions for this setup live in `server/quicker/profile.py`; matching rules
live in `server/quicker/rules.py`. Preferred categories appear first in review.
Printed payees and source descriptions are preserved. Merchant aliases are used
for matching, including card descriptors and the existing City Of/City 0f variants.

| Recognized expense | Preferred category | Assignment |
| --- | --- | --- |
| Auto insurance | Insurance (Business):Truck | R&K Properties; exact R&K Properties account |
| iCloud renewal | ICloud | R&K Properties; payment-year account |
| HP All-In Plan | All In Plan | R&K Properties; payment-year account |
| HP Instant Ink | Printer Plan | R&K Properties; payment-year account |
| Sam's Club fuel | Truck Gas | R&K Properties; payment-year account |
| The Wash Shop / Wash Shop | Truck Wash | R&K Properties; payment-year account |
| USPS PO box renewal | P.O. Box-6 Months | R&K Properties; payment-year account |
| USPS postage | Postage and Delivery (Business) | R&K Properties; payment-year account |
| Explicit Spectrum Mobile/cell service | Cell Phones | R&K Properties; payment-year account |
| TMWA | Water | Property remains for review |
| City of Reno / City of Sparks sewer | Sewer | Property remains for review |
| Waste Management | Garbage | Property remains for review |
| Paid Washoe County tax stub | Property Tax | Verified parcel mapping; payment-year account |

Eight Washoe parcel mappings in `server/quicker/profile.py` were verified from
the property-location fields of the 2026 tax notices, `IMG_3320.HEIC` through
`IMG_3334.HEIC`, even-numbered files. The notice pages remain external reference
evidence. Only the second-page receipt images are imported as source documents.
Parcel matching preserves leading zeros, accepts spaces and hyphens, and applies
only to Washoe County Treasurer tax rows. Unknown parcels remain for review.

A Sam's Club charge without fuel details includes a confirmation warning;
recognized membership, grocery and merchandise charges do not get that default.
Generic Spectrum, State Farm and USPS charges remain for review when the service
is unclear. HP All-In Plan and Instant Ink remain separate services. These rules
run when proposals are created; saving a manual correction does not reapply them.
Missing catalog categories or accounts prevent approval rather than substituting
another destination. Historical records, transfers and opening balances remain
reference data and never become new proposed transactions.

## Windows companion

On Windows with Python 3.12 installed, run from the checkout:

```powershell
py -3.12 -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -e '.[client]'
.\.venv-windows\Scripts\quicker-client.exe
```

Create a pairing code in the browser's **Windows companion** tab. Enter the Linux
server address and code in the Windows app, choose separate input and archive
folders, pair, then click **Save & connect**. Pairing codes expire in ten minutes.
Only one active companion is supported; revoke an old device before replacement.

The app waits until input-file size and modification time are stable across two
scans. It uploads, checks the server receipt, writes and verifies the archive,
then removes the unchanged input file. It persists upload request IDs, resumes
interrupted downloads, and acknowledges archive copies only after checksums match.
Unsupported files remain in the input folder. Archives and input folders cannot
contain one another. Archive downloads never feed back into ingestion.

Closing the window keeps sync running in the tray when supported. Use the tray's
Quit action to stop. State and its revocable device credential live in
`%LOCALAPPDATA%\Quicker`. No incoming Windows network port is needed.

To build a standalone Windows folder, run:

```powershell
.\client\build.ps1
```

Distribute the entire `client/dist/Quicker` folder, including `Quicker.exe`.
Packaging must run on Windows. The core sync code is tested on Linux; a native
Windows build/run still needs validation on the target computer.

## Persistent deployment and backups

The application and worker can run as systemd units. After installing the
repository dependencies, building the browser, and configuring the shared login:

1. Set an absolute `QUICKER_DATA_DIR` in `.env` and `QUICKER_COOKIE_SECURE=true`
   for HTTPS access.
2. Adapt `deploy/Caddyfile.example` to a LAN hostname resolving to this server.
   Configure Caddy and trust its local certificate authority on client devices.
3. Run `sudo ./deploy/install-systemd.sh`. The units run as the invoking user,
   bind the application to loopback, and restart failed processes. These units
   are provided but are not installed automatically by development setup.

Logs are available with `journalctl -u quicker -u quicker-worker`. Model provider
errors are redacted before persistence; model keys are never returned to browsers.
The document directory and database contain private source and review data.

Create a consistent database snapshot and copy its immutable originals:

```bash
uv run quicker backup /path/to/new-backup-directory
```

To restore, stop the application and worker, preserve the current data directory,
copy the backup's `quicker.sqlite3` and `documents/` into a new data directory,
point `QUICKER_DATA_DIR` there, and restart. Do not copy stale `-wal` or `-shm`
files from a running installation. The client document archive does not contain
review history and is not a substitute for this backup.

## Development and verification

```bash
uv sync --extra dev --extra client
npm --prefix web ci
npm --prefix web run build
uv run playwright install chromium
uv run pytest -q
uv run ruff check server client tests
```

`npm --prefix web run dev` provides frontend hot reload and proxies `/api` to
port 8765. Database migrations ship with the Python package; use `quicker migrate`
or server startup to apply them. `alembic revision --autogenerate -m "…"` creates
new migration drafts; inspect them before applying.

Tests cover authentication/CSRF, catalog import, upload idempotency, original
access and range downloads, model settings, paid-tax/card rules, exact amounts,
review validation and stale revisions, atomic batch approval, duplicate checks,
remove/restore, worker recovery, companion archival integrity, and the real
browser workflow on desktop and phone layouts. Browser tests use an isolated
SQLite database and deterministic extraction, not private documents or Quicken.

Optional browser WebMCP support exposes read-only review inspection and status
navigation. It does not approve or enter transactions. Unsupported browsers
simply use the regular interface.

See [PLAN.md](PLAN.md) for the design and [IMPLEMENTATION.md](IMPLEMENTATION.md)
for verification results and remaining integration work.
