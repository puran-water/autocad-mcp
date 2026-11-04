"""Bounded, journaled native revision operations, also usable via existing execute_lisp.

No drawing-specific code. All mutation targets require a full expected document path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
from pathlib import Path

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.config import LISP_DIR


class RevisionError(ValueError):
    pass


def literal(value):
    """Encode data as an AutoLISP literal; never interpolate executable user strings."""
    if value is None:
        return "nil"
    if value is True:
        return "T"
    if value is False:
        return "nil"
    if isinstance(value, str):
        if any(ord(c) < 32 for c in value):
            # AutoLISP supports these escape sequences in string literals.
            if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
                raise RevisionError("CONTROL_CHARACTER_UNSUPPORTED")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise RevisionError("NONFINITE_NUMBER")
        return repr(value)
    if isinstance(value, dict):
        return "(" + " ".join(f"({literal(k)} . {literal(v)})" for k, v in value.items()) + ")"
    if isinstance(value, (list, tuple)):
        return "(" + " ".join(literal(v) for v in value) + ")"
    raise RevisionError(f"UNSUPPORTED_VALUE:{type(value).__name__}")


def expected_state(entity, codes=(0, 330, 410, 8, 10, 41, 42, 43, 50)):
    """First DXF occurrence, matching AutoLISP assoc (polylines repeat group 10)."""
    fields = {}
    for code, value in entity["dxf"]:
        fields.setdefault(code, value)
    return {str(code): fields[code] for code in codes if code in fields}


def normalize_cloud(op):
    """A bounded simple orthogonal outline, CCW so positive bulges face outward."""

    def point(p):
        if (
            not isinstance(p, (list, tuple))
            or len(p) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
        ):
            raise RevisionError("CLOUD_FINITE_2D_POINT_REQUIRED")
        return list(p)

    for key in ("arc", "text_height"):
        value = op.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise RevisionError("CLOUD_POSITIVE_ARC_AND_TEXT_HEIGHT_REQUIRED")
    if "points" in op:
        points = [point(p) for p in op["points"]]
        if points and points[0] == points[-1]:
            points.pop()
    else:
        lo, hi = point(op.get("min")), point(op.get("max"))
        if lo[0] >= hi[0] or lo[1] >= hi[1]:
            raise RevisionError("INVALID_CLOUD_BOUNDS")
        points = [lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]
    if not 4 <= len(points) <= 32:
        raise RevisionError("CLOUD_OUTLINE_4_TO_32_VERTICES_REQUIRED")
    edges = list(zip(points, points[1:] + points[:1]))
    for a, b in edges:
        if (a[0] == b[0]) == (a[1] == b[1]):
            raise RevisionError("CLOUD_ORTHOGONAL_NONZERO_EDGES_REQUIRED")
    for i, (a, b) in enumerate(edges):
        c = edges[(i + 1) % len(edges)][1]
        if sum((b[k] - a[k]) * (c[k] - b[k]) for k in (0, 1)) < 0:
            raise RevisionError("CLOUD_SELF_INTERSECTION")
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges[i + 1 :], i + 1):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if all(
                max(min(a[k], b[k]), min(c[k], d[k])) <= min(max(a[k], b[k]), max(c[k], d[k]))
                for k in (0, 1)
            ):
                raise RevisionError("CLOUD_SELF_INTERSECTION")
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    if not area2:
        raise RevisionError("CLOUD_ZERO_AREA")
    if sum(math.ceil(math.dist(a, b) / op["arc"]) for a, b in edges) > 4096:
        raise RevisionError("CLOUD_TOO_COMPLEX")
    op["points"] = points if area2 > 0 else list(reversed(points))
    op["min"] = [min(p[k] for p in points) for k in (0, 1)]
    op["max"] = [max(p[k] for p in points) for k in (0, 1)]
    if "marker_position" in op:
        op["marker_position"] = point(op["marker_position"])
    return op


def cloud_covers_bounds(points, lo, hi):
    """Coverage by the inner orthogonal polygon; its bounding box alone is insufficient."""
    if any(
        not isinstance(p, (list, tuple))
        or len(p) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
        for p in (lo, hi)
    ) or any(lo[k] > hi[k] for k in (0, 1)):
        raise RevisionError("INVALID_TARGET_BOUNDS")
    outline = normalize_cloud(dict(points=points, arc=1e9, text_height=1))["points"]
    edges = list(zip(outline, outline[1:] + outline[:1]))

    def inside(x, y):
        if any(
            min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1])
            for a, b in edges
        ):
            return True
        return (
            sum(
                a[0] > x and min(a[1], b[1]) <= y < max(a[1], b[1])
                for a, b in edges
                if a[0] == b[0]
            )
            % 2
            == 1
        )

    axes = []
    for k in (0, 1):
        cuts = sorted({lo[k], hi[k], *(p[k] for p in outline if lo[k] < p[k] < hi[k])})
        axes.append(cuts + [(a + b) / 2 for a, b in zip(cuts, cuts[1:])])
    return all(inside(x, y) for x in axes[0] for y in axes[1])


def normalize_ops(ops):
    if not isinstance(ops, list) or not 1 <= len(ops) <= 7:
        raise RevisionError("BATCH_SIZE_1_TO_7_REQUIRED")
    result = []
    touched = set()
    for original in ops:
        op = dict(original)
        if op.get("kind") == "cloud":
            op = normalize_cloud(op)
        if not op.get("item"):
            raise RevisionError("ITEM_REQUIRED")
        if op.get("handle"):
            handle = op["handle"].upper()
            if not re.fullmatch(r"[0-9A-F]+", handle):
                raise RevisionError("INVALID_HANDLE")
            if handle in touched:
                raise RevisionError("ONE_OPERATION_PER_HANDLE_PER_BATCH")
            touched.add(handle)
            op["handle"] = handle
            expect = op.get("expect", {})
            if "0" not in expect and 0 not in expect:
                raise RevisionError("EXPECTED_TYPE_REQUIRED")
        for key in ("expect", "set"):
            if key in op:
                op[key] = {int(k): v for k, v in op[key].items()}
        if op.get("kind") == "spline-gap":
            if not op.get("handle") or op.get("expect", {}).get(0) != "SPLINE":
                raise RevisionError("SPLINE_REQUIRED")
            if not isinstance(op.get("expected_dxf"), list) or not op["expected_dxf"]:
                raise RevisionError("EXPECTED_CURVE_REQUIRED")
            gap = op.get("gap")
            if (not isinstance(gap, list) or len(gap) != 2
                    or any(not isinstance(p, list) or len(p) != 3
                           or any(type(v) not in (int, float) or not math.isfinite(v) for v in p)
                           for p in gap)):
                raise RevisionError("FINITE_GAP_POINTS_REQUIRED")
        if op.get("kind") == "rotate":
            expected = op.get("expect", {})
            if not op.get("handle") or expected.get(0) != "INSERT":
                raise RevisionError("INSERT_REQUIRED")
            if not {10, 50, 210} <= set(expected):
                raise RevisionError("EXPECTED_ROTATION_ANCHOR_NORMAL_REQUIRED")
            anchor = expected[10]
            if (not isinstance(anchor, (list, tuple)) or len(anchor) != 3
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in anchor)
                    or expected[210] != [0, 0, 1]):
                raise RevisionError("PLANAR_INSERT_REQUIRED")
            for value in (expected[50], op.get("target")):
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < math.tau:
                    raise RevisionError("ROTATION_RADIANS_0_TO_2PI_REQUIRED")
        if op.get("kind") == "attributes":
            if not op.get("handle") or op.get("expect", {}).get(0) != "INSERT":
                raise RevisionError("INSERT_REQUIRED")
            for key in ("attributes", "expected_attributes"):
                values = op.get(key)
                if not isinstance(values, dict) or not 1 <= len(values) <= 16:
                    raise RevisionError("ATTRIBUTE_MAP_1_TO_16_REQUIRED")
                if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str)
                       for k, v in values.items()):
                    raise RevisionError("ATTRIBUTE_STRINGS_REQUIRED")
                if len({k.upper() for k in values}) != len(values):
                    raise RevisionError("ATTRIBUTE_TAG_NOT_UNIQUE")
                op[key] = {k.upper(): v for k, v in values.items()}
            if set(op["attributes"]) != set(op["expected_attributes"]):
                raise RevisionError("EXPECTED_ATTRIBUTE_REQUIRED")
        if op.get("kind") == "patch":
            if not set(op.get("set", {})) <= set(op.get("expect", {})):
                raise RevisionError("EXPECTED_FIELD_REQUIRED")
            if 6 in op.get("set", {}):
                name = op["set"][6]
                if not isinstance(name, str) or not name.strip():
                    raise RevisionError("INVALID_DXF_LINETYPE")
            if 48 in op.get("set", {}):
                scale = op["set"][48]
                if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
                    raise RevisionError("INVALID_DXF_LINETYPE_SCALE")
            if 370 in op.get("set", {}):
                weight = op["set"][370]
                if type(weight) is not int or weight not in {
                    -3,
                    -2,
                    -1,
                    0,
                    5,
                    9,
                    13,
                    15,
                    18,
                    20,
                    25,
                    30,
                    35,
                    40,
                    50,
                    53,
                    60,
                    70,
                    80,
                    90,
                    100,
                    106,
                    120,
                    140,
                    158,
                    200,
                    211,
                }:
                    raise RevisionError("INVALID_DXF_LINEWEIGHT")
            if 1 in op.get("set", {}) and op["expect"].get(0) == "MTEXT":
                # Long MTEXT needs its group-3 chunks handled as a whole, never partial.
                if op["expect"].get(0) == "MTEXT":
                    raise RevisionError("LONG_MTEXT_REQUIRES_TEXT_OPERATION")
        result.append(op)
    return result


def compile_call(operation, data, *, lisp_path=None):
    drawing = data.get("drawing")
    if not drawing or not (re.match(r"^[A-Za-z]:[/\\]", drawing) or drawing.startswith("\\\\")):
        raise RevisionError("EXPECTED_WINDOWS_DOCUMENT_PATH_REQUIRED")
    if operation == "batch":
        ops = normalize_ops(data["operations"])
        batch_id = data["id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", batch_id):
            raise RevisionError("INVALID_BATCH_ID")
        digest = hashlib.sha256(
            json.dumps(ops, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        args = [drawing, batch_id, digest, data["journal"], ops]
        fn = "pcad:batch"
    elif operation == "rename_layers":
        pairs = data["pairs"]
        if not isinstance(pairs, list) or not 1 <= len(pairs) <= 7:
            raise RevisionError("LAYER_BATCH_1_TO_7_REQUIRED")
        if any(not isinstance(p, list) or len(p) != 2
               or any(not isinstance(n, str) or not n.strip() for n in p)
               for p in pairs):
            raise RevisionError("LAYER_NAME_PAIRS_REQUIRED")
        if len({p[0].upper() for p in pairs}) != len(pairs) or len({p[1].upper() for p in pairs}) != len(pairs):
            raise RevisionError("DUPLICATE_LAYER_NAME")
        args, fn = [drawing, pairs, data["journal"]], "pcad:rename-layers"
    elif operation == "frames":
        args, fn = [drawing], "pcad:frames"
    elif operation == "read":
        handles = data["handles"]
        if not 1 <= len(handles) <= 100:
            raise RevisionError("READ_LIMIT_1_TO_100")
        args, fn = [drawing, handles], "pcad:read-handles"
    elif operation == "export_page":
        args = [
            drawing,
            data["path"],
            data.get("offset", 0),
            data.get("limit", 250),
            data.get("definitions", False),
        ]
        fn = "pcad:export-page"
    elif operation == "plot":
        args, fn = [drawing, data["layout"], data["path"]], "pcad:plot"
    elif operation == "save":
        args, fn = [drawing, data.get("path"), data.get("format", 48)], "pcad:save"
    else:
        raise RevisionError("UNKNOWN_REVISION_OPERATION")
    load = str(lisp_path or LISP_DIR / "revision_tools.lsp").replace("\\", "/")
    return f"(progn (load {literal(load)}) (pcad:run '{fn} '{literal(args)}))"


def unpack(result):
    if not result.ok:
        return result
    try:
        payload = json.loads(result.payload)
    except (TypeError, ValueError):
        return CommandResult(ok=False, error="INVALID_REVISION_RESPONSE", payload=result.payload)
    if isinstance(payload, dict) and payload.get("ok") is False:
        return CommandResult(ok=False, error=payload.get("error"), payload=payload)
    return CommandResult(ok=True, payload=payload)


async def execute(backend, operation, data):
    if backend.name != "file_ipc":
        return CommandResult(ok=False, error="NATIVE_BACKEND_REQUIRED")
    return unpack(await backend.execute_lisp(compile_call(operation, data)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["compile", "batch", "read", "export_page", "plot", "save", "frames"]
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--lisp-path", help="Windows helper path when compiling from WSL")
    args = parser.parse_args()
    data = json.loads(args.request.read_text(encoding="utf-8"))
    if args.operation == "compile":
        output = compile_call(data.pop("operation"), data, lisp_path=args.lisp_path)
    else:
        from autocad_mcp.backends.file_ipc import FileIPCBackend

        async def run():
            backend = FileIPCBackend()
            init = await backend.initialize()
            if not init.ok:
                return init
            return await execute(backend, args.operation, data)

        result = asyncio.run(run())
        output = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    if args.operation != "compile" and not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
