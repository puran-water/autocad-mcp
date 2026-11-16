"""Persist native screenshots without routing base64 back through the agent."""

import base64
import hashlib
import os
from pathlib import Path
import tempfile


def screenshot_target(value):
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".png":
        raise ValueError("SCREENSHOT_ABSOLUTE_PNG_PATH_REQUIRED")
    if path.exists() or path.is_symlink():
        raise ValueError("SCREENSHOT_TARGET_EXISTS")
    if not path.parent.is_dir():
        raise ValueError("SCREENSHOT_DIRECTORY_MISSING")
    return path


def save_screenshot(encoded, value):
    """Publish complete bytes exclusively; a race never overwrites evidence."""
    path = screenshot_target(value)
    data = base64.b64decode(encoded, validate=True)
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("SCREENSHOT_NOT_PNG")
    fd, temporary = tempfile.mkstemp(prefix=".pcad-screenshot-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard linking is atomic and fails if a competing writer created path.
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
