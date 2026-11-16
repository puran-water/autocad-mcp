# Resident native workflow

Release state is the exact build's `native-qualification.json`, reported by
`system.runtime` and `system.status`. This document does not grant qualification.
The receipt names the build it qualified; any other build reports `different_build`
and must be qualified again (below) before production drawings.

## Execution path

MCP client → persistent Windows Python → private job directory → resident AutoLISP
worker → native journal/readback → matching receipt. Ordinary requests never use
keyboard input or activate the foreground window. Windows/Python and native Lisp
must have the same manifest hash. A file change requires restart and qualification.

A managed job is one fresh Windows LT process and one writable DWG. AutoCAD is
started through local WMI process creation so it does not inherit the MCP client's
kill-on-close Windows Job Object. This is OS process management; native drawing
operations still use AutoLISP, never external AutoCAD COM automation. The native
qualification closes the launching MCP connection and reattaches before edits.
An ordinary Python subprocess was observed to die with the SDK client and is not
used for managed AutoCAD startup. The native
`/b` startup loads the queue once; `/b` is last in the command line. A missing-font
or security dialog can still block startup. Observe the selected PID, resolve its
specific dialog and preserve the job record; do not launch again. New jobs refuse
busy/read-only drawings before launching, and native WRITESTAT is checked again.

Use `system.job` launch, observe, attach and close. Native initialization is deferred
until attach. Closing saves, stops the worker, checks DBMOD=0, and sends WM_CLOSE to
that exact PID/window. It never confirms discard prompts. A process that remains
open is reported for inspection. Existing user windows are not job targets.

## Recovery and cadence

All native requests use an OS lease plus an asynchronous lock. A request is atomically
claimed before it executes. Cancellation can prove no claim; claimed timeouts retain
the original intent. Read matching late receipts and actual handles before explicit
acknowledgement. The server never retries a mutation. A successful error envelope
retains its payload and receipt, so callers can distinguish saved/partially completed
operations from failures before execution.

Completed pending markers may disappear between the worker's existence check and
read. The worker treats that as normal archival, not a nil request. A sustained
native test found this race; release qualification must exercise repeated
completion/archival and reconnects, not just a few successful pings.

`revision.checkpoint` saves in place and makes a new hash-verified recovery copy.
The active DWG never changes, avoiding Save As/open cycles. Automatic recovery
artifacts do not imply approval pauses. Continue to the user's consolidated review
while preserving bounded edits, readbacks, screenshots and the change ledger.

## Deliberately unsupported paths

Automatic document switching and DXF conversion inside the resident worker are
blocked before dispatch. VLA/OPEN can suspend the source document's Lisp context.
Use a separate job for another DWG. Do not use arbitrary execute_lisp to evade that
boundary. Save As/same-document renewal is a separate guarded operation with retained
handoff intent and old/new worker evidence.

WSL Hyper-V socket allocation failures are independent of native File IPC. Keep the
Windows MCP process resident, use its job operations, and avoid per-batch Windows
subprocess launches from WSL. The launcher tests readiness before forwarding input;
a running MCP session is never transparently reconnected or replayed. Kernel updates
and host-memory remediation require a coordinated maintenance window. Cache release
and compaction are transient recovery, not a permanent server repair.

## Qualification required before deployment

Use a full multi-layout disposable DWG in an isolated process. Verify cold startup,
exact PID/document/build, 50 or more bounded journaled batches with readbacks and
screenshots, repeated in-place save/recovery copies, clean MCP reconnection, native
plots, same-document renewal, and clean job close. Exercise late claimed completion,
no-replay fencing, wrong-document/old-value rejection, and existing-output refusal.
Preserve failed-run evidence and qualify the exact final Python/Lisp build.

Source: [Autodesk command-line startup](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-LT/files/GUID-07B8B8AB-C6F7-4236-9D33-5343E4DC88F1.htm).

Process-lifetime basis: [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
and [local interactive process creation](https://learn.microsoft.com/en-us/windows/win32/wmisdk/creating-processes-remotely).
