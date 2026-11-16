"""Checked, targeted AutoCAD input. No global keys, focus changes or ESC."""

from __future__ import annotations


def post_expression(hwnd: int, expression: str, gui=None, process=None) -> dict:
    if gui is None:
        import win32gui as gui
    if process is None:
        import win32process as process

    if not gui.IsWindow(hwnd) or not gui.IsWindowEnabled(hwnd):
        raise RuntimeError("AUTOCAD_WINDOW_UNAVAILABLE")
    title = gui.GetWindowText(hwnd)
    if "autocad" not in title.lower() or ".dwg" not in title.lower():
        raise RuntimeError("AUTOCAD_DRAWING_WINDOW_REQUIRED")
    _, pid = process.GetWindowThreadProcessId(hwnd)
    targets = []

    def collect(child, _):
        if (
            gui.GetClassName(child) == "MDIClient"
            and gui.GetParent(child) == hwnd
            and gui.IsWindowVisible(child)
            and gui.IsWindowEnabled(child)
            and process.GetWindowThreadProcessId(child)[1] == pid
        ):
            targets.append(child)
        return True

    # Resolve for every expression; drawing/application switches can invalidate handles.
    gui.EnumChildWindows(hwnd, collect, None)
    if len(targets) != 1:
        raise RuntimeError("AUTOCAD_COMMAND_TARGET_AMBIGUOUS")
    target = targets[0]
    # A modal owned window disables its owner. Refuse the whole frame above, and
    # refuse visible native dialogs even when an unusual host leaves it enabled.
    dialogs = []

    def dialog(window, _):
        if (
            gui.IsWindowVisible(window)
            and gui.GetClassName(window) == "#32770"
            and process.GetWindowThreadProcessId(window)[1] == pid
        ):
            dialogs.append(window)
        return True

    gui.EnumWindows(dialog, None)
    if dialogs:
        raise RuntimeError("AUTOCAD_MODAL_DIALOG_OPEN")
    # pywin32 checks PostMessage's return value and raises on invalid handle,
    # access denied, or queue exhaustion. Do not swallow or retry partial delivery.
    sent = 0
    try:
        for character in expression + "\r":
            gui.PostMessage(target, 0x0102, ord(character), 1)
            sent += 1
    except Exception as exc:
        raise RuntimeError(f"IPC_INPUT_DELIVERY_FAILED after {sent} characters: {exc}") from exc
    return {"hwnd": hwnd, "target": target, "pid": pid, "title": title, "characters": sent}
