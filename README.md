# Quicker

A local web application for turning invoice photos, paid tax stubs, and credit
card statements into reviewed transactions for Quicken. The browser is the main
workspace; the Windows companion uploads a folder and synchronizes originals to
a permanent archive.

**Quicken entry and automatic parcel mapping are intentionally unfinished.**
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

1. Import a QIF export with Account List and Category List in Settings. Importing
   only updates Quicker's reference catalog; it never writes to Quicken.
2. Review the property/year account mappings initially derived from account
   names. Rename property labels to align different years as needed. Account names
   remain exact. Mappings use payment year unless a reviewer overrides the account.
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
   left for manual review except identified auto insurance, which defaults to
   `R&K Properties` for both property/business and destination account, with
   category `Insurance (Business):Truck`. This exact account overrides year-based
   routing; all assignments remain editable. Generic insurer names alone do not
   establish auto coverage. Other handwriting is ignored except tax payment
   confirmations and dates. Underlines, check marks, and adjacent notes alone do
   not exclude a transaction.
6. Select rows for bulk property, account, category, or date assignment and batch
   approval. Possible duplicates require explicit acknowledgement. Removing a row
   retains its edits and original document. Restoring returns it to review.
   Editing an approved transaction requires approval again.
7. Pair the Windows companion to populate its archive, including phone uploads.

Catalog assignments from extraction are suggestions. Required dates, valid
catalog names, exact amounts, and duplicate acknowledgement are enforced by the
server at approval time. The model can still make reading mistakes; review the
source before approval. No model output can initiate Quicken entry.

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
