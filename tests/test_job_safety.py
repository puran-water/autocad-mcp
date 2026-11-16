import json
import os
import sys
import types
from pathlib import Path

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.native_save import recovery_copy
from autocad_mcp.revision import unpack


def test_failure_payload_and_receipt_survive_tool_formatting():
    result = CommandResult(
        False, {"request_id": "123", "claimed": True}, "uncertain", {"archive": "proof"}
    )
    assert result.to_dict()["payload"]["claimed"]
    assert result.to_dict()["receipt"]["archive"] == "proof"
    native = CommandResult(
        True, json.dumps({"ok": False, "error": "OLD_VALUE"}), receipt={"request_id": "x"}
    )
    assert unpack(native).to_dict()["receipt"] == native.receipt


def test_recovery_copy_is_identical_exclusive_and_preserves_original(tmp_path):
    source = tmp_path / "work.dwg"
    destination = tmp_path / "recovery.dwg"
    source.write_bytes(b"AC1032" + b"saved native data" * 1000)
    answer = recovery_copy(source, destination)
    assert source.read_bytes() == destination.read_bytes()
    assert answer["bytes"] == source.stat().st_size
    with pytest.raises(ValueError, match="TARGET_EXISTS"):
        recovery_copy(source, destination)
    assert not list(tmp_path.glob(".pcad-recovery-*"))


def test_copy_race_never_overwrites_another_checkpoint(tmp_path, monkeypatch):
    source = tmp_path / "work.dwg"
    destination = tmp_path / "recovery.dwg"
    source.write_bytes(b"AC1032saved")
    link = os.link

    def race(a, b):
        Path(b).write_bytes(b"another writer")
        link(a, b)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(FileExistsError):
        recovery_copy(source, destination)
    assert destination.read_bytes() == b"another writer"
    assert not list(tmp_path.glob(".pcad-recovery-*"))


def test_build_change_requires_restart_and_receipt_matches_exact_build(tmp_path, monkeypatch):
    import autocad_mcp.build as module

    (tmp_path / "src/autocad_mcp").mkdir(parents=True)
    (tmp_path / "lisp-code").mkdir()
    code = tmp_path / "src/autocad_mcp/server.py"
    code.write_text("first")
    first = module.manifest(tmp_path)
    code.write_text("second")
    second = module.manifest(tmp_path)
    assert first["sha256"] != second["sha256"]
    monkeypatch.setattr(module, "LOADED", first)
    monkeypatch.setattr(module, "manifest", lambda: second)
    with pytest.raises(RuntimeError, match="RESTART_REQUIRED"):
        module.require_unchanged()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "native-qualification.json").write_text(
        json.dumps({"build": second["sha256"], "state": "qualified"})
    )
    assert module.qualification()["state"] == "different_build"


def test_multiple_autocad_processes_require_explicit_pin(monkeypatch):
    import autocad_mcp.backends.file_ipc as module

    monkeypatch.setattr(sys, "platform", "win32")
    gui = types.SimpleNamespace(
        IsWindowVisible=lambda h: True,
        GetWindowText=lambda h: "AutoCAD LT - drawing.dwg",
        GetClassName=lambda h: "AfxMDIFrame140u",
        EnumWindows=lambda callback, rows: [callback(h, rows) for h in (11, 22)],
    )
    proc = types.SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, h * 10))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", proc)
    monkeypatch.delenv("AUTOCAD_MCP_PID", raising=False)
    with pytest.raises(RuntimeError, match="AMBIGUOUS"):
        module.find_autocad_window()
    monkeypatch.setenv("AUTOCAD_MCP_PID", "220")
    assert module.find_autocad_window() == 22
    monkeypatch.setenv("AUTOCAD_MCP_PID", "999")
    assert module.find_autocad_window() is None


def test_job_selection_rejects_wrong_directory_build_or_process(tmp_path):
    from autocad_mcp.native_job import read_job
    from autocad_mcp.build import LOADED

    path = tmp_path / "job.json"
    record = dict(ipc_dir=str(tmp_path), build=LOADED, pid=123, drawing="C:/work/a.dwg")
    path.write_text(json.dumps(record))
    assert read_job(path)["pid"] == 123
    for key, value, message in [
        ("ipc_dir", str(tmp_path / "other"), "DIRECTORY_MISMATCH"),
        ("build", {"sha256": "old"}, "BUILD_MISMATCH"),
        ("pid", 0, "PROCESS_REQUIRED"),
    ]:
        path.write_text(json.dumps(dict(record, **{key: value})))
        with pytest.raises((ValueError, RuntimeError), match=message):
            read_job(path)


def test_job_busy_file_fails_before_creating_intent_or_starting_process(tmp_path, monkeypatch):
    import autocad_mcp.native_job as module
    from contextlib import contextmanager

    exe = tmp_path / "acadlt.exe"
    exe.touch()
    output = tmp_path / "job"

    @contextmanager
    def busy(path):
        raise RuntimeError("DRAWING_BUSY_OR_READ_ONLY")
        yield

    monkeypatch.setattr(module, "exclusive_read_check", busy)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(RuntimeError, match="BUSY"):
        module.launch("C:/work/busy.dwg", output, exe)
    assert not output.exists()


def test_independent_launcher_uses_local_wmi_with_supported_startup_properties(
    monkeypatch, tmp_path
):
    from autocad_mcp.native_job import start_independent_process
    import subprocess

    values = {}

    def props(allowed, prefix):
        def access(name):
            assert name in allowed
            return values.setdefault(prefix + name, types.SimpleNamespace(Value=None))

        return access

    startup = types.SimpleNamespace(
        Properties_=props({"WinstationDesktop", "ShowWindow"}, "startup.")
    )
    inputs = types.SimpleNamespace(
        Properties_=props(
            {"CommandLine", "CurrentDirectory", "ProcessStartupInformation"}, "input."
        )
    )
    output = types.SimpleNamespace(
        Properties_=lambda name: types.SimpleNamespace(
            Value={"ReturnValue": 0, "ProcessId": 321}[name]
        )
    )

    class Service:
        def Get(self, name):
            if name == "Win32_ProcessStartup":
                return types.SimpleNamespace(SpawnInstance_=lambda: startup)
            assert name == "Win32_Process"
            return types.SimpleNamespace(
                Methods_=lambda name: types.SimpleNamespace(
                    InParameters=types.SimpleNamespace(SpawnInstance_=lambda: inputs)
                )
            )

        def ExecMethod(self, path, method, parameters):
            assert (path, method, parameters) == ("Win32_Process", "Create", inputs)
            return output

    def get(moniker):
        assert moniker == r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"
        return Service()

    package = types.ModuleType("win32com")
    package.client = types.SimpleNamespace(GetObject=get)
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", package.client)
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
    )
    argv = ["C:/Program Files/AutoCAD/acadlt.exe", "C:/drawings/test.dwg", "/b", "C:/script.scr"]
    assert start_independent_process(argv, tmp_path) == 321
    assert values["startup.WinstationDesktop"].Value == r"winsta0\default"
    assert values["input.CommandLine"].Value == subprocess.list2cmdline(argv)
