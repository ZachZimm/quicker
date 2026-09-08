# Windows implementation handoff

## Objective

Implement and validate the companion's Quicken automation on the Windows machine.
Start with automated creation of fresh, complete QIF exports. Quicken is the source
of truth because people and other tools may add transactions independently of
Quicker. Do not maintain the reference by appending Quicker's own transactions to
an old export.

## Required workflow

1. Identify the intended Quicken data file and check that the desktop is usable.
   Serialize export and entry operations so two runs cannot control Quicken at
   once. Stop on an unexpected dialog, a locked desktop or a different data file.
2. Before each explicitly requested entry run, create a new export directly from
   Quicken containing all accounts, all dates, transactions and reference lists.
   Write to a new output path for that export attempt. Never treat an older file
   left at a reused path as proof that an export succeeded.
3. Verify export completion, then upload the exact file to the server. Confirm
   durable storage and activation as the current reference. A transfer receipt
   or heartbeat alone does not prove a fresh export was created. If export,
   upload or activation fails or needs review, stop before entry.
4. Recompute duplicate matches and entry eligibility against that export before
   claiming approved revisions. A new match returns affected rows to review.
   Preserve explicit "Already in Quicken" links. Approval does not authorize
   blindly retrying an entry with an uncertain outcome.
5. Enter only the approved revisions that remain eligible. Journal each attempted
   desktop action and its result. Coordinate the server run with the local
   journal so reconnects and repeated button clicks cannot resend work.
6. Create and upload another fresh export after entry, then reconcile expected
   changes against actual Quicken records. After interruption, refresh and
   reconcile before deciding whether an uncertain entry can be retried. An
   ambiguous match must remain unresolved rather than being reported as entered.

Provide a "Refresh from Quicken" action for updates without entry. Refresh on
companion connection when Quicken is ready, and support periodic refresh while
idle. Background refresh must defer when the user is interacting with Quicken;
it must not interrupt an entry run. File-change notifications may request a
refresh, but cannot establish which transactions were added. Quicken data files
must not be edited or parsed directly by Quicker.

## Freshness and concurrency contract to implement

Immutable export bytes and export events are different things. Two successful
fresh exports may have identical hashes. Preserve one blob per hash, but record
each completed export event with a durable request ID, paired device, configured
data-file identity, timestamps, checksum and reference activation result. Retry
of the same event must be idempotent; a new export of identical content must still
confirm freshness.

The current upload API deduplicates bytes and the companion skips hashes it has
already acknowledged. That is correct for passive file watching but is not yet a
pre-entry freshness protocol. Extend it alongside the Windows export adapter.
Do not use a previously cached upload receipt as a fresh export event.

Bind each entry run to its successful pre-entry export event, current reference
digest and approved candidate revisions. Check these atomically when claiming
work. If another reference activation or review edit intervenes, refresh or
revalidate instead of continuing with stale eligibility. An `entry_eligible`
value obtained from a prior GET is not a durable claim.

A fresh export cannot prevent changes made afterward. Detect unexpected Quicken
state changes, stop when the user takes over, and reconcile against the final
export. Do not promise exactly-once entry from date/payee/amount matching alone.

## Existing implementation

- `client/quicker_client/sync.py`: paired HTTP client, checksum verification,
  durable photo receipts, resumable archives and passive QIF file watching.
- `client/quicker_client/app.py`: PySide6 window, pairing and tray behavior.
- `server/quicker/reference_exports.py`: immutable QIF backups, coverage checks,
  guarded activation and reference history.
- `server/quicker/matching.py` and `review.py`: property/unit-aware duplicate
  suggestions, existing-transaction links and approval invalidation.
- `server/quicker/app.py`: device reference GET/POST and browser backup review.
  Actual entry remains explicitly unimplemented.
- `tests/test_completion.py`, `test_companion.py`, `test_browser.py` and
  `test_backup.py`: sync, matching, review and recovery coverage.
- `client/build.ps1`: Windows packaging script.

Keep desktop automation behind a replaceable adapter. Validate controls on the
installed Quicken version before selecting an automation mechanism. The first
entry experiments must use a disposable copy of the Quicken data file. The Linux
suite and offscreen Qt check do not establish Windows compatibility.

## Acceptance checks

- A transaction entered manually in Quicken appears in a fresh server reference
  and blocks approval of a matching source row until the match is resolved.
- Changes from other import tools are included without Quicker supplying them.
- A successful export with unchanged contents records a new freshness event.
- Failed/cancelled exports, partial files and stale leftover files cannot start
  entry. Reduced-coverage exports remain available as backups but require review.
- A lost upload response retries the same event without duplicate entry or
  reactivating an older reference.
- Concurrent browser/companion starts cannot claim the same revision twice.
- A reference change between export and claim forces revalidation.
- A crash after Quicken accepts a transaction but before acknowledgement causes
  reconciliation against a fresh export, never blind resubmission.
- Post-entry verification checks account, amount, date, payee, category and tags.
  Include both Bell rentals, shared property expenses and the R&K exception.
- Manual refresh works without entering anything. Background refresh defers when
  the desktop is unavailable or in use, with visible status.

## Starting point

Read this file, README.md and IMPLEMENTATION.md. Inspect the installed Quicken
version and export dialog, then implement the export adapter and freshness-event
contract before enabling either entry button. Credentials and private application
data are excluded from Git and must be configured on the Windows machine.
