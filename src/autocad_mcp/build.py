"""Loaded code identity and exact-build native qualification receipts."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def manifest(root=ROOT):
    paths = sorted((root / "src/autocad_mcp").rglob("*.py")) + sorted(
        (root / "lisp-code").glob("*.lsp")
    )
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": files}


LOADED = manifest()


def require_unchanged():
    if manifest()["sha256"] != LOADED["sha256"]:
        raise RuntimeError("SERVER_BUILD_CHANGED_RESTART_REQUIRED")


def qualification():
    path = ROOT / "native-qualification.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"state": "unqualified", "build": LOADED["sha256"]}
    if record.get("build") != LOADED["sha256"]:
        return {"state": "different_build", "build": LOADED["sha256"]}
    return record
