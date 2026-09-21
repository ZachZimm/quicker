# Windows background client

## Goal and current state

Keep the companion running throughout the user's signed-in Windows session,
usually with no visible window. The browser remains the main review interface;
the companion handles document synchronization and Quicken desktop operations.

The startup, tray, notification and lifecycle changes below are implemented.
Paired installations default to current-user Windows sign-in startup and a hidden
tray window; both settings can be disabled. First-run setup or an unavailable tray
keeps the window accessible. Relaunch opens the existing instance through a local
socket restricted to the current user. The same controls are available in the
window's Quicker menu and the tray menu; only double-click opens the tray window.

Pause is saved and affects periodic exports only. Status includes connection,
desktop waiting reason and the last verified native export. Routine conditions do
not produce notifications. Pairing rejection and uncertain operations notify once
per active condition, with a click opening settings or the web interface.

Network retries back off from 5 to 60 seconds. Unexpected worker exits stop both
workers before recovery, with at most three automatic restarts (5, 15, 60 seconds)
until a manual reconnect or ten minutes of continued operation. Explicit Quit
never restarts the application. Shutdown waits responsively for up to 15 seconds;
unfinished attempts retain their journals for reconciliation. The activity view
retains 500 blocks. `companion.log` rotates at 1 MiB with three backups in the state
directory, and credentials are redacted. Startup registration is the `Quicker`
value under the current user's Windows `Run` key; it points at the installed
executable with `--startup`. Launch after moving the installation updates that path.

New setups default to `Quicker Input` in the operating system's Desktop location,
which includes OneDrive-redirection. Saved input paths take precedence. On this
Windows installation the existing input directory and saved configuration were
moved together from Documents to `C:\Users\kjole\OneDrive\Desktop\Quicker Input`;
the archive location and pairing were retained.

The current export changes retain the 15-minute interval, defer background
exports while the foreground window covers its monitor, and restore the previous
application after automatic exports. Restoration respects user input, application
switches, and closed windows. Windows may refuse a focus change; failures are
logged. Single-monitor native Windows verification is recorded below; the broader
acceptance checks remain outstanding.

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

### Background lifecycle validation

On the same Windows 11/Quicken installation, the rebuilt executable started hidden
with saved pairing, registered current-user startup, and reopened the same process
on a second launch. Closing settings kept the process running. Native automatic
exports completed; a server-requested refresh also completed while automatic
exports were paused (reference generation 14). Pause/resume and explicit Quit were
exercised through the shared window/tray menu. Quit stopped the process without an
automatic restart. Existing configuration and journals were retained. The full
suite passed 235 tests, including fault-injected shutdown/recovery, and Ruff passed.
Actual Windows sign-out/sign-in, sleep/wake, multi-monitor games, and overnight
operation were not exercised during this deployment.

### September 20, 2026 validation

Validated upstream `48d8b5e` on Windows 11 Home, build 26200, with English Quicken
Classic Business & Personal 27.1.69.29 and one monitor. A dedicated foreground
test window exercised normal, maximized (taskbar visible), and fullscreen modes.
After the actual 60-second idle threshold, fullscreen deferred a background
session without changing focus. A controlled session failure and a complete native
QIF export both restored the previous application. These checks only exported
data; they did not import transactions or create accounts.

Native testing found that Windows can complete foreground activation asynchronously.
The client now observes the result for up to 250 milliseconds before warning,
without retrying activation or overriding another foreground application. Tests
cover delayed activation and an intervening application switch. The backup test
now checks POSIX permission bits only on POSIX systems; Windows does not expose
ACL permissions through those bits.

After rebuilding the web assets, the full Windows suite passed (222 tests), along
with the two added activation regressions. Ruff, TypeScript, the Vite production
build, and Windows packaging passed. Multi-monitor/fullscreen-game, lock/wake,
overnight, startup, and tray-lifecycle acceptance checks are not established by
this earlier run. The subsequent lifecycle implementation adds automated coverage
for hidden/visible startup decisions, tray activation, pause with uploads and
explicit refresh, bounded logs, worker failures, notification deduplication,
shutdown deadlines, and shutdown after an attempt acknowledgement without replay.

Rebuilt and restarted `client/dist/Quicker/Quicker.exe` with existing pairing,
configuration and journals preserved. The packaged client connected to the
deployed Linux server at `100.77.107.36:8999` and completed a server-requested
refresh: `Fresh Quicken export verified`, reference generation 11. No account
requests or entry operations remained pending. These follow-up changes affect
Windows focus handling, tests and this record; they require no Linux restart.

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
