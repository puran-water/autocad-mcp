# Troubleshooting: execute_lisp / IPC dispatch times out after AutoCAD restarts

## Symptom

- Every `system(operation="execute_lisp", ...)` call (or any other IPC-dispatched
  operation) returns `{"ok": false, "error": "Timeout waiting for result (request_id=...)"}`.
- `system(operation="health")` still reports `{"ok": true, "backend": "file_ipc"}`.
  **This is misleading** — `health` just returns the cached backend status
  (`FileIPCBackend.status()`), it does not actually round-trip to AutoCAD.
- Manually typing `MCP-DISPATCH` (or `(c:mcp-dispatch)`) in the AutoCAD command
  line after the timeout answers `MCP: No pending commands`.
- Distinguishing clue: this used to work with fully automatic dispatch (no
  manual `MCP-DISPATCH` needed), and only recently started timing out. That
  rules out a structural limitation (e.g. "target window must be foreground")
  and points at something that changed mid-session — namely, AutoCAD itself
  being closed and reopened.

## Root cause

`FileIPCBackend` is a long-lived object inside the MCP stdio server process.
`initialize()` calls `find_autocad_window()` (and the recursive
`_find_command_line_hwnd()` search) exactly **once**, caches `self._hwnd` /
`self._command_hwnd`, and never re-checks them. `_type_dispatch_trigger()`
then calls `PostMessageW` against that cached hwnd on every dispatch.

If AutoCAD is closed and reopened during the session (new `acad.exe` process,
new top-level window), the cached hwnd becomes a dead handle. `PostMessageW`
to an invalid/closed window handle fails silently — it returns 0/`FALSE`, but
nothing in `_type_dispatch_trigger()` checks the return value or logs it — so
the keystrokes are never delivered anywhere, and AutoCAD never runs
`(c:mcp-dispatch)`. Because the request JSON file *is* written correctly to
the IPC directory (confirmed by watching `C:\temp\autocad_mcp_cmd_*.json`
appear and then get cleaned up ~10s later with no matching
`autocad_mcp_result_*.json` ever appearing), this is not a directory/path
mismatch and not a permissions issue — it's purely a stale cached handle.

Verified directly with a Win32 `IsWindow()` check from PowerShell:

```powershell
Add-Type -Name Win32Chk -Namespace WinApi -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
'@
[WinApi.Win32Chk]::IsWindow([IntPtr]<hwnd from system(operation="status").payload.hwnd>)
```

Returned `False` for the cached hwnd; `Get-Process acad | Select MainWindowHandle`
showed a completely different, currently-valid handle.

## Fix (workaround, no restart of the MCP server needed)

```
system(operation="init")
```

This forces a fresh `find_autocad_window()` / `_find_command_line_hwnd()`
lookup. The first `init` after an AutoCAD restart will usually still fail,
with:

```
AutoCAD LT detected but mcp_dispatch.lsp not loaded.
In AutoCAD command line, type:
  (load "<path>/lisp-code/mcp_dispatch.lsp")
```

— because the LISP dispatcher script itself isn't auto-loaded on a fresh
AutoCAD session (unless `lisp-code/` is a trusted auto-load path). Load it
once in AutoCAD's command line, then call `system(operation="init")` again;
`execute_lisp` works normally afterward.

## Possible follow-up (not implemented here)

`initialize()`/`_dispatch_unlocked()` could call `win32gui.IsWindow(self._hwnd)`
before attempting a dispatch and transparently re-run `find_autocad_window()`
if it's stale, instead of requiring the caller to notice the timeout pattern
and manually call `system(operation="init")`. Left as a follow-up since it
touches the same dispatch path this branch (`fix/full-autocad-command-line-detection`)
is already working on.
