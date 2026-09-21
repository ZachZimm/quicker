# Windows background client plan

## Goal and current state

Keep the companion running throughout the user's signed-in Windows session,
usually with no visible window. The browser remains the main review interface;
the companion handles document synchronization and Quicken desktop operations.

The startup, tray, notification and lifecycle changes below are proposed work.
They have not yet been implemented. The existing client already has a tray icon,
hides its window on close when the tray is available, connects with saved pairing
credentials, and prevents a second instance from starting. It still opens its
window on launch, shows a message for duplicate launches, keeps an unbounded
activity log, and quits immediately without waiting for its background threads.

The current export changes retain the 15-minute interval, defer background
exports while the foreground window covers its monitor, and restore the previous
application after automatic exports. Restoration respects user input, application
switches, and closed windows. Windows may refuse a focus change; failures are
logged. These changes have automated coverage but still need native Windows
verification.

## Startup and window behavior

- Add saved "Start with Windows" and "Start minimized to tray" settings. Register
  startup for the current user and provide a way to disable it from settings.
- Run in the interactive user's session at sign-in. Quicken automation requires
  that desktop, so use a normal tray application rather than a Windows service.
- Show setup for an unpaired installation. Once configured, start hidden and
  connect automatically. If the tray is unavailable, keep the window accessible.
- Closing the window should hide it while synchronization continues. Explicit
  Quit should stop the application. Configure Qt's application lifetime so
  hiding or closing a settings window does not accidentally terminate sync.
- Launching the executable again should bring the existing window forward,
  using local instance communication while retaining the single-instance lock.
- Right-click should open only the tray menu. Opening the window should require
  its menu action or the intended click gesture, not every tray activation event.

## Tray controls and status

Show connection state, the last successful native export, and the reason desktop
automation is waiting. Distinguish "Connected; waiting for Quicken" from an
unreachable server. Ordinary idle/fullscreen deferrals are normal waiting states.

Provide these actions:

- Open Quicker settings and activity.
- Open the web interface using the configured server.
- Refresh from Quicken / reconcile.
- Pause or resume automatic exports, with the paused state visible.
- Quit.

Pausing automatic exports must leave photo uploads and archive synchronization
running. Manual refresh and exports needed for explicitly requested transaction
entry or account creation remain available. Display their usual desktop-idle
instructions when the user requests them.

## Quiet operation and reconnects

- Keep successful syncs, routine exports, temporary connection failures, and
  desktop/fullscreen deferrals out of popup notifications.
- Notify once when user action is required, such as revoked pairing or an
  uncertain transaction outcome. Provide an action to open the relevant view.
  Deduplicate repeated failures until the condition changes or is resolved.
- Reconnect automatically after network loss and wake from sleep, using bounded
  retry backoff. Recheck Quicken, the configured data file, and desktop readiness
  before resuming automation.
- Run at most one overdue periodic export when eligible. Do not accumulate an
  export queue for intervals missed during sleep, disconnection, or fullscreen use.
- Keep the 15-minute interval and the initial connection refresh. Background
  exports require at least one minute of desktop idle time, Quicken in the
  background, an available desktop, and no unexpected dialog or fullscreen app.
- Retain focus restoration after successful and failed automatic exports.
  Do not replace a window selected by the user or restore a destroyed window.
- Document sync may continue while the session is locked; desktop automation
  must wait. Sleep or sign-out cannot support ongoing desktop operations.

## Long-running reliability and shutdown

- Add rotating on-disk logs and cap the activity window's retained messages.
  Record changes in status rather than appending identical heartbeat messages
  indefinitely. Exclude credentials and full document/export contents from logs.
- Detect an unexpected sync or heartbeat worker exit. Report the failure and
  recover through the existing durable journal without replaying an uncertain
  Quicken import. Avoid repeatedly restarting a permanently failing worker.
- On Quit or Windows session shutdown, stop accepting commands and scheduling
  work. Signal active workers to stop at a safe boundary, preserving receipts,
  journal state, and uncertainty before closing resources.
- Keep the UI responsive while stopping. Use a bounded shutdown wait and retain
  enough durable state for reconciliation if Windows ends the process first.
- Preserve pairing, settings, and journals across application updates. An explicit
  Quit must not immediately trigger an automatic application restart.

## Existing Quicken guarantees to preserve

Quicken remains authoritative for transactions entered by people and other tools.
Create native exports of all accounts, all dates, transactions and reference lists
at a new path for each export attempt. Never synthesize the reference by appending
Quicker transactions to an old file or by reading or modifying the QDF directly.

Before transaction entry, upload and activate a fresh export, recheck duplicates,
and claim exact approved revisions against the current reference. Journal intent
before submission. Export again afterward to verify results. Interrupted or
uncertain attempts require reconciliation; never blindly repeat an import.
Fresh export events remain distinct even when their file hashes are identical.
The optional QIF watcher uploads changed files but does not create exports or
establish proof of a fresh native export for entry.

Serialize native refresh, transaction entry, and account creation through the
existing controller and desktop mutex. Preserve file-identity, ownership, input,
focus, dialog, and reference-generation checks during recovery.

Account creation supports local/manual Bank accounts only. It requires an explicit
request, a fresh pre-export, a durable one-time attempt, and a fresh post-export
proving the exact name and type. Create no opening-balance transactions or online
connections. Existing accounts satisfy requests without another import. Uncertain
creation must be reconciled rather than repeated; if absent, the user may complete
the exact account manually and refresh. Account names are limited to 39 characters.
Transaction approval and entry remain separate from account creation.

The account API and proof rules live in `server/quicker/accounts.py`; the client
controller is `client/quicker_client/operations.py`. Their tests cover conflicts,
ownership, lost responses and recovery. Preserve those contracts while changing
the application's lifecycle.

## Implementation areas

- `client/quicker_client/app.py`: startup settings, tray interactions, instance
  activation, bounded activity display, notifications and graceful shutdown.
- `client/quicker_client/sync.py`: periodic scheduling, pause state, reconnects,
  status transitions and worker lifecycle.
- `client/quicker_client/desktop.py`: fullscreen detection, focus restoration,
  desktop readiness and interruption checks.
- `client/quicker_client/operations.py`: durable recovery and serialization.
- `client/build.ps1`: packaged startup behavior and upgrade validation.
- `tests/test_client_window.py`, `tests/test_companion.py`,
  `tests/test_desktop_focus.py`, `tests/test_desktop_operations.py`, and
  `tests/test_account_controller.py`: automated regression coverage.

## Acceptance checks

- A paired client starts hidden at Windows sign-in and begins synchronization.
  First-run setup and a missing tray leave an accessible window.
- Closing the window leaves sync running. Relaunch opens the existing instance.
  Right-click opens the tray menu without also opening the main window.
- Pause stops only automatic exports. Resume runs one overdue export when eligible.
- Test fullscreen video and borderless games on each monitor, including monitors
  with negative screen coordinates and differing display scales. A normally
  maximized window with a visible taskbar should allow an idle background export.
- After successful and failed exports, restore the original application when
  appropriate. Respect a user switching apps, typing, or closing the original window.
- Lock/unlock, sleep/wake, and server outages recover without a backlog of exports
  or repeated notifications. Quicken being closed is a visible waiting state.
- An overnight run keeps logs and memory bounded. A worker failure becomes visible
  and cannot silently leave a falsely healthy tray status.
- Quit during an upload, export, or entry preserves recoverable state. Restart
  reconciles uncertain imports without submitting them again.
- An update preserves pairing, settings, owner identity, and journals.

## Windows validation and deployment

Earlier native testing covered English Quicken Classic Business & Personal
27.1.69.29, complete exports, transaction entry and Bank account creation using a
disconnected test copy and isolated server data. Other versions and languages
remain unverified. Bank, Cash and CCard transaction entry is supported; investments,
splits, transfers and edits to existing transactions remain outside the current
scope. Payees allow 63 characters and source memos 36, leaving room for the
verification reference. Unsupported names or values must fail before mutation.

Use a disconnected test QDF for new mutation tests. Linux tests and offscreen Qt
checks do not establish native focus, tray, startup or shutdown behavior. Record
the Windows and Quicken versions used for the new acceptance checks.

Build on Windows with `client/build.ps1`, supplying `-PythonPath` if needed.
Install the entire `client/dist/Quicker` folder. Preserve `%LOCALAPPDATA%/Quicker`,
which holds pairing, configuration and durable journals. Keep server and client
versions compatible; server deployment instructions remain in README.md.
