# File IPC v2 — implementation and qualification

Status: historical. The v2 keyboard trigger was qualified on AutoCAD LT 2027
(native create/read/erase, layout/document switches, cancelled delayed trigger and
claimed late completion). That qualification does not cover the resident queue
worker; see `docs/queue-workflow.md` and the build's `native-qualification.json`.
Windows interop can be intermittent under host memory pressure; safe recovery is
qualified, not universal UI availability.

The original protocol typed a global wildcard trigger, autoloaded after a pickup
miss, and considered command-file disappearance a claim. AutoLISP read the file
before deleting it; Python deleted an unclaimed file in finally. That ordering
could not prove non-execution. Cached window targets and unchecked PostMessageW
return values also hid delivery failures. These are code findings, not proof of
which one caused a particular historical timeout.

V2 properties:

- Resolve and validate the AutoCAD MDIClient, process identity, visible/enabled
  state and modal state for every delivery. Check Windows PostMessage errors.
  No focus stealing, global keystrokes, ESC or automatic replay.
- Load the dispatcher before the first request in each document when autoload is
  enabled. Use a protocol marker and a request-specific function, not a wildcard.
- Publish complete JSON by same-directory rename. The ready filename is outside
  the legacy dispatcher's wildcard. The dispatcher must win a ready→claimed
  rename before reading. Python may instead win ready→cancelled. On NTFS the
  source has one owner; failure to rename never authorizes execution.
- Preserve pending intent, original claim/cancellation, result and delivery/error
  receipts. Require the original matching claim before accepting a v2 result.
- A timeout blocks all clients until explicit acknowledgement. A matching cancelled
  envelope proves no execution; a claim requires native reconciliation and a late
  completion receipt. No state is inferred from file age or disappearance.
- A delayed trigger can only address its original ID; it cannot consume a subsequent
  request. Partial keyboard input can still exist after failed delivery, so desktop
  inspection and confirmation of an idle prompt remain necessary before proceeding.

## Qualification and limits

Offline tests exercise at-most-once file ownership with concurrent native filesystem
renames, both arrival orders, result validation, client fencing, task cancellation,
late results, altered receipts, target changes, modal refusal and partial delivery.
AutoLISP is not executed by these tests. They do not prove Windows input reliability,
AutoCAD idle-state behavior or NTFS claim behavior in the live application.

Native qualification must use a disposable DWG and serialize access with the MCP:

1. Reconcile any legacy pending request using its original evidence and user state.
2. Check protocol, document identity and DBMOD. Repeated read-only probes must work
   while another application is foreground, after layout switches and after document
   switches. Record actual delivery target and elapsed times.
3. Create at most five disposable entities, verify handles/properties and capture
   a screenshot. Erase them in a separate bounded verified batch.
4. Deliberately cancel an unclaimed test request; invoke its old ID and verify no
   mutation. Deliberately delay a claimed read-only request past the caller timeout,
   observe the late receipt, acknowledge it, and prove it executed once.
5. Restore the working document and verify path/DBMOD before any production edit.

Do not dispatch another native request after any unexpected pickup failure.

## Deployment and rollback

Deploy the Python package and mcp_dispatch.lsp together, retaining file hashes and
backups. Restart the MCP Python process to acquire the new backend. V2 requests
bootstrap the new dispatcher per document; AUTOLOAD=0 requires manually loading
that exact file. The retained legacy function is compatible with older clients,
but clients must never compete for the desktop. A legacy unresolved pending marker
continues to block v2 initialization.

To roll back, stop dispatch, reconcile any pending operation, restore the backed-up
Python and Lisp files, and restart the server. Never delete a pending marker as a
rollback shortcut. Newly added modules can remain inert; do not remove receipts.

## Separate WSL fault

Windows process launch from WSL can fail independently with
`UtilAcceptVsock ... accept4 failed 110` (kernel allocation failures in
`vmbus_alloc_ring` / `hvs_probe`) while Windows AutoCAD and existing MCP desktop
observation keep working. No AutoCAD retry fixes that allocation; it is a host
memory-pressure fault for the machine's administrator, not an MCP recovery step.

References:

- [Microsoft: PostMessage returns before processing](https://learn.microsoft.com/en-us/windows/win32/winmsg/about-messages-and-message-queues)
- [Autodesk: vl-file-rename returns success or failure](https://help.autodesk.com/cloudhelp/2022/ENU/AutoCAD-AutoLISP-Reference/files/GUID-188B957C-4CDF-41C7-99BE-8080FA587F74.htm)
