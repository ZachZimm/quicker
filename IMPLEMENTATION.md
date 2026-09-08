# Implementation status

The first implementation covers document ingestion through manual approval and
permanent archival. Verified parcel and rental-unit assignments are implemented;
Quicken interaction remains unfinished.

## Implemented

- FastAPI application, SQLite persistence, packaged Alembic migration, and
  separate extraction worker with durable claims, bounded retries, settings
  snapshots, failure history, and protection against expired workers publishing.
- One shared login with hashed passwords, expiring sessions, CSRF checks,
  rate-limited login, and authenticated original/preview access.
- Configurable model protocol, HTTP/HTTPS transport, host, port, base path, model,
  optional API key, timeout, image size, output limit, and extraction concurrency. Chat
  completions and Responses adapters have contract tests; the selected local
  model has been exercised through chat completions.
- Full QIF reference history with dates, signed amounts, blank payees, stable
  record identifiers and original records. Import coverage includes counts and
  date ranges per account. The full local export verifies 1,262 transactions,
  including 287 blank-payee entries, with no unreadable dates or amounts.
- Explicit property directory with aliases, separate unit labels, exact yearly
  destinations, preserved manual routes and audited alias migrations.
- Eight verified Washoe parcel mappings, explicit rental/whole-property/unresolved
  selections, separate expense and rental tags, and WM service-customer mappings.
- Automatic utility document classification, per-location subtotal extraction,
  separate billing/service customer IDs, invoice context, and exclusion of
  aggregate totals, components and instructional examples. Payment dates remain
  separate from invoice dates and service periods.
- Preferred 2026 categories and recurring business rules with year-specific
  R&K accounts; auto insurance keeps the exact-account exception. Rules preserve
  printed payees, and merchant aliases support matching.
- Historical duplicate matching uses property/unit scope, exact amounts, nearby
  dates and printed service periods without inventing payment dates. Explicit
  existing-transaction links exclude rows from entry. New evidence invalidates
  approvals; missing linked history returns rows to review.
- Immutable, downloadable QIF backups with guarded reference activation. Browser,
  CLI and paired companion uploads share one import path. Partial, stale or
  malformed-history exports need review before replacing the active reference.
- Manual missed-row creation and re-extraction comparison on existing documents.
  Revision checks and retry receipts prevent stale or repeated additions. Current
  and earlier audited row versions identify already represented source rows,
  including removed rows. Extraction never replaces reviewed work.
- Printed service/job and utility-customer addresses can assign a verified
  property, with address evidence shown in review. Mailing addresses, ambiguous
  matches and card statement addresses cannot assign a property. Payment dates
  remain mandatory, and business-rule conflicts are flagged.
- Ordered multi-photo document uploads, immutable originals, reduced image
  previews/model inputs, upload receipts, and grouped document extraction.
- A separate visual check on each page identifies crossed-out items for
  exclusion. Ambiguous marks stay in review with a warning. Underlines, check
  marks, brackets, and nearby notes do not count as crossed-out items.
- Auto insurance defaults to R&K Properties for property/business and the exact
  destination account, with category Insurance (Business):Truck. Review edits
  take precedence; generic insurer names alone do not trigger the rule.
- Browser transaction table and source viewer, bulk assignment, required dates,
  atomic batch approval, duplicate acknowledgement, stale-edit rejection,
  reversible removal, and audit history. Changes after approval require approval
  again. Failed requests are visible inside open review dialogs.
- Windows companion UI with pairing, folder ingestion, durable upload receipts,
  checksum-verified local archives, resumable downloads, heartbeat, and tray
  behavior, plus optional QIF file watching and versioned server backup. The
  companion retains the local QIF file. Browser uploads also sync to the Windows archive after reconnection.
- Browser and Windows entry buttons visibly disabled. The server entry endpoint
  returns an explicit not-implemented response without mutating transactions.
- Source installation instructions, Windows packaging script, systemd templates,
  local HTTPS reverse-proxy example, and database/original backup command.

## Verification

The September 7 backend completion run passed all 121 tests, including the new
reference, correction and browser workflows. The production browser build,
Python lint, deployment-script syntax, and diff whitespace checks also passed.

Automated checks include Python/API tests, real Chromium workflow tests at desktop
and phone sizes, server-to-companion HTTP archival round trips, a restored backup,
protocol adapter contracts, and an offscreen PySide6 window check on Linux.
Frontend TypeScript compilation and the production build pass. Python lint and
shell syntax checks are included in the final verification.

Earlier live extraction against the supplied photos and configured local model produced:

| Sample | Result |
| --- | --- |
| Utility bill | One expense of $214.55; the local model read 1008 Bell St, Reno, NV, which assigned Bell St.; payment date and destination account remain blank for review; handwritten correction ignored. |
| Tax installment sheet | Two paid installments, $144.16 and $137.66, both dated August 10, 2026; two unpaid stubs ignored; parcel `03127109` preserved as text. |
| Credit card statement | 14 purchases, totaling $4,178.73; the crossed-out $22.06 MHS Incline Village purchase and card payment excluded; the $422 auto insurance purchase uses the exact R&K Properties account; six additional unedited proposals now use R&K Properties 2026 with preferred categories. The remaining seven card properties are unassigned. |

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

The later WM/sewer batch contains six photos imported as five documents with 18
review rows. The configured endpoint failed to load the model and returned HTTP
400, so this batch initially used audited visual transcription. The backend
completion pass rechecked the restored model against these sources. Its matching
results and failed attempts are retained alongside the original reviewed rows.
The existing full 1,262-transaction QIF is now also stored as a downloadable,
active export backup. Holman Way's undated $251.86 sewer bill matches an existing
February 1, 2026 payment within its printed service period; the match is a review
suggestion, and no document payment date was filled in.

The completion pass retained nine successful live comparisons, covering all five
WM/sewer documents, two tax sheets, the earlier utility bill and the card statement.
All 18 WM/sewer amounts and service-location customer IDs matched the saved rows,
for $1,620.06. The card comparison again produced 14 eligible purchases totaling
$4,178.73, excluding the payment and crossed-out $22.06 purchase. Source rows were
checked before and after storing comparisons and remained unchanged.

Four earlier truncated validation attempts are also retained. Increasing the
response limit alone did not fix the model's intermittent truncation. Inspection
found an 8,192-token loaded context with substantial reasoning-token use. The LM
Studio native adapter supports request-level reasoning control, verified against
the installed server. Reasoning off completed the remaining utility and card
samples; the saved workspace now uses that protocol and setting. This does not
change the model server's global settings. The protocol remains selectable, and
other models can keep their default reasoning mode.

## Next Windows milestone

Automate creation of fresh, complete exports from the actual Quicken data file.
The current passive file watcher does not create exports. The user requires
synchronization to include transactions entered outside Quicker. Pre-entry
export, duplicate revalidation and post-entry export/reconciliation are required;
appending Quicker transactions to an old export is not sufficient.

Implement export-event freshness alongside the desktop adapter, including fresh
exports whose contents are unchanged. Then implement entry claims and recovery.
[WINDOWS_HANDOFF.md](WINDOWS_HANDOFF.md) records the concrete sequence and tests
for the Windows task. No Windows export or entry automation is claimed complete.

## Deliberately unfinished or not verified here

- Some physical rental-to-Quicken-tag mappings remain unresolved, including the
  two Bell WM locations. Only supported identities receive automatic unit tags.
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
