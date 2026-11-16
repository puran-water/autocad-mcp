"""Worker lifecycle without backend initialization, simulated typing or replay."""

from pathlib import Path

from autocad_mcp.desktop import ipc_lease, inspect_pending
from autocad_mcp.ipc_protocol import read_json
from autocad_mcp.worker import discover, observe, prepare, request_stop


def bootstrap(session, lisp_dir, ipc_dir="C:/temp", build_hash=None):
    from autocad_mcp.revision import literal
    from autocad_mcp.build import LOADED

    library = Path(lisp_dir).as_posix()
    return (
        "((lambda (/ pcadSecurity pcadLoad) "
        '(setq pcadSecurity (getvar "SECURELOAD")) (setvar "SECURELOAD" 0) '
        "(setq pcadLoad (vl-catch-all-apply '(lambda () "
        f"(load {literal(library + '/mcp_dispatch.lsp')}) "
        f"(load {literal(library + '/mcp_worker.lsp')})) nil)) "
        '(setvar "SECURELOAD" pcadSecurity) '
        f"(setq *mcp-ipc-dir* {literal(Path(ipc_dir).as_posix().rstrip('/') + '/')} "
        f"*mcp-worker-build* {literal(build_hash or LOADED['sha256'])}) "
        "(if (vl-catch-all-error-p pcadLoad) (princ (vl-catch-all-error-message pcadLoad)) "
        f"(mcp-worker-run {literal(session)}))))\n"
    )


def control(directory, action, data, lisp_dir):
    directory = Path(directory)
    with ipc_lease(directory):
        if action == "observe":
            result = dict(ipc=inspect_pending(directory), input_transport="native_queue_worker")
            handoff = directory / "pcad-handoff.json"
            result["handoff"] = read_json(handoff) if handoff.exists() else None
            try:
                token = data.get("session") or discover(directory)
            except RuntimeError:
                result["worker"] = {"state": "absent"}
            else:
                result["worker"] = observe(directory, token)
            return result
        if action == "prepare":
            manifest = prepare(
                directory,
                data["drawing"],
                idle_timeout_s=data.get("idle_timeout_s", 1800),
                max_duration_s=data.get("max_duration_s", 14400),
            )
            script = directory / f"pcad-worker-{manifest['session']}.scr"
            with script.open("x", encoding="utf-8") as stream:
                stream.write(bootstrap(manifest["session"], lisp_dir, directory, manifest["build"]))
            return dict(
                session=manifest,
                script=str(script),
                instruction="In the intended drawing at Command:, run _.SCRIPT and select this file once. No requests are sent until the bound native worker is ready.",
            )
        if action == "stop":
            return request_stop(directory, data.get("session") or discover(directory))
        raise ValueError("UNKNOWN_WORKER_ACTION")
