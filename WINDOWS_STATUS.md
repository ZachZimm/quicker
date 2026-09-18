# Windows client status — September 17, 2026

Starting revision: `870746d`. Implementation branch: `codex/windows-automation`.

## Verified on Windows

- Pairing, authenticated heartbeats, reference reads and archive sync work with the
  supplied Linux server. All 17 pending source pages were archived and acknowledged.
- QtCore startup is fixed. PyInstaller had selected an incompatible ICU DLL from
  an unrelated PATH directory. The build now isolates DLL discovery and restores
  the original PATH afterward. The packaged app starts and connects successfully.
- After Quicken's normal restart, both apps run without elevation. No Windows
  security settings were changed.
- Created `Quicker-Automation-Test.QDF` using Quicken's copy/template feature, which
  disconnected its connected services. Test entries used this copy and isolated
  server databases; production reference data never received the test transactions.
- Complete native exports include all accounts, dates 1901–2099, transactions,
  account/category lists and memorized payees, always at a new output path.
- Live entry verified both Bell rentals with expense and unit tags, shared-property
  expenses, an R&K refund, and the exact R&K insurance account/category exception.
  All successful cases matched a subsequent native export exactly. An initial
  overlong probe exposed QIF memo truncation; input validation now prevents it.
- Reopened the original `2026QuickenData.QDF` and configured the companion for it.
  The disconnected test copy remains in Documents / Quicker Test.
- Browser and Windows actions share one durable run protocol. Claims lock exact
  approved revisions; attempts are journaled before submission. Restart and lost
  response tests reconcile fresh exports without resending transactions.

## Validation

138 Python/API/browser/client tests pass (137 in the full run plus the corrected
browser selector rerun). These include real Chromium review and delivery workflows,
HTTP archive round trips, an offscreen Qt window, and crash/retry tests. Actual
Quicken import/export tests additionally validate the Windows adapter. TypeScript
compilation, production bundling, Python lint, diff whitespace and dependency-lock
consistency pass. Private logs, exports and isolated databases remain in `.local/`.

## Linux deployment

The user will deploy the matching server changes. In the Linux repository:

```bash
git fetch origin
git switch codex/windows-automation
git pull --ff-only
uv sync --extra dev
npm --prefix web ci
npm --prefix web run build
```

Restart the server and extraction worker with the existing launch/service setup.
Keep the current environment and `QUICKER_DATA_DIR`; do not rerun account setup.
Database migration runs automatically. Pairing remains valid; Windows automation
controls become available when the server advertises protocol 1. A production
refresh must then verify the live connection.

Windows build: `client/build.ps1` (use `-PythonPath` if no Python launcher exists).
Executable: `client/dist/Quicker/Quicker.exe`; install the entire folder.
Preserve `%LOCALAPPDATA%/Quicker`, which contains pairing, configuration and journals.

## Limits and recovery

- Validated on English Quicken Classic Business & Personal 27.1.69.29. Unexpected
  dialogs fail closed. Both apps need the same privilege level and an unlocked desktop.
- Bank, Cash and CCard entry is supported. Investments, splits, transfers and changes
  to existing Quicken transactions remain outside v1.
- Payees may contain 63 characters. Source memos may contain 36 characters, leaving
  27 for the verification reference within the observed 63-character QIF memo limit.
  Unsupported ANSI characters or ambiguous separators stop before import.
- Uncertain entries remain locked. Refresh and reconcile first. When a fresh export
  contains no marker, the browser permits an explicitly confirmed return to review.
  Duplicate or mismatched markers require correction in Quicken and another refresh.
- Input takeover, focus loss, a different QDF, unexpected dialogs, locked desktop,
  partial exports and reference conflicts stop entry. Background refresh waits for
  an idle desktop and does not interrupt someone using Quicken.
- Preserve local companion state during recovery; another installation cannot take
  over an in-flight run's owner automatically.
