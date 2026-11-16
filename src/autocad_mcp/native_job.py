"""Start an isolated, document-bound AutoCAD LT job using its native /b startup.

No keyboard automation, no document switching, no second launch on timeout.
Run on Windows: python -m autocad_mcp.native_job request.json
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys

from autocad_mcp.build import LOADED, require_unchanged
from autocad_mcp.config import LISP_DIR
from autocad_mcp.ipc_protocol import publish
from autocad_mcp.native_save import fingerprint, FORMATS
from autocad_mcp.worker import normalize_drawing
from autocad_mcp.worker_control import control


def start_independent_process(argv, directory):
    """Local WMI launch survives MCP SDK kill-on-close process Job Objects.

    This is Windows process management, not external AutoCAD COM automation.
    No remote host, elevation, scheduled task, shell input, or fallback launch.
    """
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        service = win32com.client.GetObject(
            r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"
        )
        startup = service.Get("Win32_ProcessStartup").SpawnInstance_()
        startup.Properties_("WinstationDesktop").Value = r"winsta0\default"
        startup.Properties_("ShowWindow").Value = 1
        process = service.Get("Win32_Process")
        inputs = process.Methods_("Create").InParameters.SpawnInstance_()
        inputs.Properties_("CommandLine").Value = subprocess.list2cmdline(argv)
        inputs.Properties_("CurrentDirectory").Value = str(directory)
        inputs.Properties_("ProcessStartupInformation").Value = startup
        output = service.ExecMethod("Win32_Process", "Create", inputs)
        result = output.Properties_("ReturnValue").Value
        pid = output.Properties_("ProcessId").Value
        if result != 0 or not pid:
            raise RuntimeError(f"NATIVE_JOB_LAUNCH_FAILED:{result}; no retry")
        return int(pid)
    finally:
        pythoncom.CoUninitialize()


@contextmanager
def exclusive_read_check(path):
    """Refuse a DWG already open anywhere before starting a script-driven job."""
    if sys.platform != "win32":
        raise RuntimeError("WINDOWS_NATIVE_JOB_REQUIRED")
    import win32file
    import win32con

    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_ATTRIBUTE_NORMAL,
            None,
        )
    except Exception as exc:
        raise RuntimeError("DRAWING_BUSY_OR_READ_ONLY; no AutoCAD process started") from exc
    try:
        yield
    finally:
        handle.Close()


def launch(drawing, directory, executable):
    require_unchanged()
    drawing = Path(normalize_drawing(drawing))
    directory, executable = Path(directory), Path(executable)
    if not directory.is_absolute() or not executable.is_absolute():
        raise ValueError("ABSOLUTE_JOB_PATHS_REQUIRED")
    if not executable.is_file() or executable.name.lower() != "acadlt.exe":
        raise ValueError("AUTOCAD_LT_EXECUTABLE_REQUIRED")
    with exclusive_read_check(drawing):
        pass
    original = fingerprint(drawing)
    if original["version"].encode() not in FORMATS:
        raise ValueError("DWG_FORMAT_NOT_QUALIFIED")
    directory.mkdir(exist_ok=False, parents=True)
    prepared = control(directory, "prepare", {"drawing": str(drawing)}, LISP_DIR)
    record = dict(
        state="prepared",
        drawing=str(drawing),
        ipc_dir=str(directory),
        executable=str(executable),
        session=prepared["session"],
        build=LOADED,
        baseline=original,
        script=prepared["script"],
    )
    publish(directory / "job.json", record)
    # /b must be last. A fresh process opens the one verified target itself.
    # A concurrent opener is still caught by the native WRITESTAT guard.
    argv = [str(executable), str(drawing), "/nologo", "/nossm", "/b", prepared["script"]]
    record.update(state="launch_submitted", argv=argv)
    publish(directory / "job.json", record)
    pid = start_independent_process(argv, directory)
    record.update(
        state="launched_awaiting_native_identity",
        pid=pid,
        argv=argv,
        launch_method="local_wmi_independent",
    )
    record["mcp_env"] = dict(
        AUTOCAD_MCP_BACKEND="file_ipc",
        AUTOCAD_MCP_IPC_DIR=str(directory),
        AUTOCAD_MCP_PID=str(pid),
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
    )
    publish(directory / "job.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    value = json.loads(args.request.read_text())
    print(json.dumps(launch(value["drawing"], value["ipc_dir"], value["executable"])))


if __name__ == "__main__":
    main()


def read_job(path):
    """A job record is explicit selection, never an implicit most-recent drawing."""
    path = Path(path)
    if not path.is_absolute() or path.name != "job.json":
        raise ValueError("ABSOLUTE_JOB_JSON_REQUIRED")
    record = json.loads(path.read_text())
    if Path(record["ipc_dir"]).resolve() != path.parent.resolve():
        raise ValueError("JOB_DIRECTORY_MISMATCH")
    if record["build"]["sha256"] != LOADED["sha256"]:
        raise RuntimeError("JOB_BUILD_MISMATCH")
    if type(record.get("pid")) is not int or record["pid"] <= 0:
        raise ValueError("JOB_PROCESS_REQUIRED")
    normalize_drawing(record["drawing"])
    return record


async def manage(data):
    """Launch/attach one document without switching a live Lisp context."""
    import asyncio
    import os
    from autocad_mcp import client, config
    from autocad_mcp.backends import file_ipc
    from autocad_mcp.desktop import inspect_pending, ipc_lease, observe as desktop_observe
    from autocad_mcp.worker import discover, observe, request_stop

    require_unchanged()
    action = data.get("action", "observe")
    if action == "launch":
        return dict(ok=True, payload=launch(data["drawing"], data["ipc_dir"], data["executable"]))
    record = read_job(data["path"])
    directory = Path(record["ipc_dir"])
    hwnd = file_ipc.find_autocad_window(record["pid"])
    try:
        token = discover(directory)
    except RuntimeError:
        token = record["session"]["session"]
    state = observe(directory, token)
    if action == "observe":
        return dict(
            ok=True,
            payload=dict(
                job=record,
                worker=state,
                ipc=inspect_pending(directory),
                desktop=desktop_observe(hwnd) if hwnd else None,
            ),
        )
    if not hwnd:
        raise RuntimeError("JOB_AUTOCAD_WINDOW_MISSING")
    if action == "attach":
        async with client._init_lock:
            if client._backend and client._backend.name == "file_ipc":
                old = client._backend._ipc_dir
                if inspect_pending(old)["pending"] or (old / "pcad-handoff.json").exists():
                    raise RuntimeError("CURRENT_JOB_UNCERTAIN_RECONCILE_REQUIRED")
            backend = file_ipc.FileIPCBackend(worker_session=token)
            backend._ipc_dir = directory
            old_pin = os.environ.get("AUTOCAD_MCP_PID")
            os.environ["AUTOCAD_MCP_PID"] = str(record["pid"])
            try:
                await backend._worker_ready()
                if normalize_drawing(backend._worker_state["drawing"]) != normalize_drawing(
                    record["drawing"]
                ):
                    raise RuntimeError("JOB_DRAWING_MISMATCH")
                result = await backend.initialize()
                if not result.ok:
                    raise RuntimeError(result.error)
            except BaseException:
                if old_pin is None:
                    os.environ.pop("AUTOCAD_MCP_PID", None)
                else:
                    os.environ["AUTOCAD_MCP_PID"] = old_pin
                raise
            config.IPC_DIR = file_ipc.IPC_DIR = directory
            os.environ["AUTOCAD_MCP_IPC_DIR"] = str(directory)
            os.environ["AUTOCAD_MCP_BACKEND"] = "file_ipc"
            client._backend = backend
            return dict(
                ok=True,
                payload=dict(attached=data["path"], drawing=record["drawing"], pid=record["pid"]),
            )
    if action == "close":
        import win32gui
        import win32api
        import win32event
        import win32con
        from autocad_mcp.native_save import save_current

        backend = client._backend
        if (
            not backend
            or backend.name != "file_ipc"
            or backend._ipc_dir != directory
            or backend._hwnd != hwnd
        ):
            raise RuntimeError("ATTACH_JOB_BEFORE_CLOSE")
        saved = await save_current(backend, expected=record["drawing"])
        if not saved.ok:
            return saved.to_dict()
        token = backend._worker_session
        with ipc_lease(directory):
            request_stop(directory, token)
        for _ in range(100):
            state = observe(directory, token)
            if state.get("state") == "stopped":
                break
            await asyncio.sleep(0.05)
        if state.get("state") != "stopped" or state.get("dbmod") != 0:
            raise RuntimeError("JOB_CLOSE_REQUIRES_STOPPED_CLEAN_DOCUMENT")
        handle = win32api.OpenProcess(win32con.SYNCHRONIZE, False, record["pid"])
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            for _ in range(200):
                if win32event.WaitForSingleObject(handle, 0) == win32con.WAIT_OBJECT_0:
                    record["state"] = "closed_clean"
                    record["final_save"] = saved.to_dict()
                    publish(directory / "job.json", record)
                    client._backend = None
                    return dict(ok=True, payload=record)
                await asyncio.sleep(0.05)
            return dict(
                ok=False, error="JOB_CLOSE_PENDING_INSPECT_DESKTOP", payload=saved.to_dict()
            )
        finally:
            handle.Close()
    raise ValueError("UNKNOWN_JOB_ACTION")
