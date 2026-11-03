"""Windows desktop observation and IPC lease. Never uses global keyboard injection."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys


@contextmanager
def ipc_lease(directory):
    """Cross-process OS lock; process death releases it, pending receipts survive."""
    path = Path(directory) / "pcad-desktop.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("DESKTOP_BUSY_OTHER_CLIENT") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("DESKTOP_BUSY_OTHER_CLIENT") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def observe(hwnd):
    import win32gui
    import win32process

    if not hwnd or not win32gui.IsWindow(hwnd):
        raise ValueError("AUTOCAD_WINDOW_MISSING")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    windows = []

    def collect(window, _):
        if (
            win32gui.IsWindowVisible(window)
            and win32process.GetWindowThreadProcessId(window)[1] == pid
        ):
            windows.append(
                dict(
                    hwnd=window,
                    title=win32gui.GetWindowText(window),
                    class_name=win32gui.GetClassName(window),
                    enabled=bool(win32gui.IsWindowEnabled(window)),
                )
            )
        return True

    win32gui.EnumWindows(collect, None)
    return dict(
        hwnd=hwnd,
        pid=pid,
        title=win32gui.GetWindowText(hwnd),
        enabled=bool(win32gui.IsWindowEnabled(hwnd)),
        windows=windows,
    )


def inspect_pending(directory):
    path = Path(directory) / "pcad-pending.json"
    if not path.exists():
        return {"pending": None}
    pending = json.loads(path.read_text())
    result = Path(directory) / f"autocad_mcp_result_{pending['request_id']}.json"
    receipt = None
    if result.exists():
        try:
            receipt = json.loads(result.read_text(encoding="utf-8-sig"), strict=False)
        except (ValueError, UnicodeError):
            pass
    return {"pending": pending, "receipt": receipt}


def acknowledge(directory, request_id):
    """Acknowledge a reconciled COMPLETE native result, preserving it in the archive."""
    with ipc_lease(directory):
        state = inspect_pending(directory)
        pending, receipt = state["pending"], state["receipt"]
        if (
            not pending
            or pending["request_id"] != request_id
            or not receipt
            or receipt.get("request_id") != request_id
        ):
            raise ValueError("MATCHING_COMPLETION_RECEIPT_REQUIRED")
        archive = Path(directory) / "pcad-receipts"
        archive.mkdir(exist_ok=True)
        target = archive / f"{request_id}.json"
        target.write_text(json.dumps(state, indent=2))
        (Path(directory) / "pcad-pending.json").unlink()
        return {"acknowledged": request_id, "archive": str(target)}


def click_dialog_button(*, dialog, button, pid, title, text):
    """Explicit acknowledgement after inspection; refuses stale/reused window handles."""
    import win32gui
    import win32process

    if (
        not win32gui.IsWindow(dialog)
        or win32process.GetWindowThreadProcessId(dialog)[1] != pid
        or win32gui.GetWindowText(dialog) != title
        or win32gui.GetClassName(dialog) != "#32770"
        or not win32gui.IsChild(dialog, button)
        or win32gui.GetClassName(button) != "Button"
        or win32gui.GetWindowText(button) != text
        or not win32gui.IsWindowEnabled(button)
    ):
        raise ValueError("DIALOG_IDENTITY_MISMATCH")
    win32gui.PostMessage(button, 0x00F5, 0, 0)  # BM_CLICK, targeted to the inspected button
    return {"posted": True, "dialog": dialog, "button": button, "text": text}


def main():
    import argparse
    import base64

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["observe", "screenshot", "acknowledge", "button"])
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from autocad_mcp.backends.file_ipc import find_autocad_window
    from autocad_mcp.config import IPC_DIR

    request = json.loads(args.request.read_text()) if args.request else {}
    hwnd = request.get("hwnd") or find_autocad_window()
    if args.operation == "observe":
        result = {"desktop": observe(hwnd), "ipc": inspect_pending(IPC_DIR)}
    elif args.operation == "screenshot":
        from autocad_mcp.screenshot import Win32ScreenshotProvider

        if not args.output:
            parser.error("--output required")
        data = Win32ScreenshotProvider(hwnd).capture()
        if not data:
            raise RuntimeError("SCREENSHOT_FAILED")
        args.output.write_bytes(base64.b64decode(data))
        result = {"screenshot": str(args.output), "hwnd": hwnd}
    elif args.operation == "button":
        result = click_dialog_button(**request)
    else:
        result = acknowledge(IPC_DIR, request["request_id"])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
