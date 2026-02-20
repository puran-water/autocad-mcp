"""Headless DXF backend using ezdxf — no AutoCAD needed."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import ezdxf
import structlog

from autocad_mcp.backends.base import AutoCADBackend, BackendCapabilities, CommandResult
from autocad_mcp.screenshot import MatplotlibScreenshotProvider

log = structlog.get_logger()


class EzdxfBackend(AutoCADBackend):
    """Pure-Python DXF generation via ezdxf."""

    def __init__(self):
        self._doc: ezdxf.document.Drawing | None = None
        self._msp = None  # modelspace
        self._save_path: str | None = None
        self._screenshot = MatplotlibScreenshotProvider()
        self._entity_counter = 0

    @property
    def name(self) -> str:
        return "ezdxf"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            can_read_drawing=True,
            can_modify_entities=True,
            can_create_entities=True,
            can_screenshot=True,
            can_save=True,
            can_plot_pdf=False,
            can_zoom=False,  # No viewport in headless
            can_query_entities=True,
            can_file_operations=True,
            can_undo=False,
        )

    async def initialize(self) -> CommandResult:
        self._doc = ezdxf.new("R2013")
        self._msp = self._doc.modelspace()
        self._screenshot.doc = self._doc
        return CommandResult(ok=True, payload={"backend": "ezdxf", "version": ezdxf.__version__})

    async def status(self) -> CommandResult:
        entity_count = len(self._msp) if self._msp else 0
        return CommandResult(ok=True, payload={
            "backend": "ezdxf",
            "version": ezdxf.__version__,
            "has_document": self._doc is not None,
            "entity_count": entity_count,
            "save_path": self._save_path,
            "capabilities": {k: v for k, v in self.capabilities.__dict__.items()},
        })

    def _next_id(self) -> str:
        self._entity_counter += 1
        return f"ezdxf_{self._entity_counter}"

    def _ensure_layer(self, layer: str | None):
        if layer and layer not in self._doc.layers:
            self._doc.layers.add(layer)

    # --- Drawing management ---

    async def drawing_info(self) -> CommandResult:
        if not self._doc:
            return CommandResult(ok=False, error="No document open")
        layers = [l.dxf.name for l in self._doc.layers]
        entity_count = len(self._msp)
        blocks = [b.name for b in self._doc.blocks if not b.name.startswith("*")]
        return CommandResult(ok=True, payload={
            "entity_count": entity_count,
            "layers": layers,
            "blocks": blocks,
            "dxf_version": self._doc.dxfversion,
            "save_path": self._save_path,
        })

    async def drawing_save(self, path: str | None = None) -> CommandResult:
        if not self._doc:
            return CommandResult(ok=False, error="No document open")
        save_path = path or self._save_path
        if not save_path:
            return CommandResult(ok=False, error="No save path specified")
        self._doc.saveas(save_path)
        self._save_path = save_path
        return CommandResult(ok=True, payload={"path": save_path})

    async def drawing_save_as_dxf(self, path: str) -> CommandResult:
        return await self.drawing_save(path)

    async def drawing_create(self, name: str | None = None) -> CommandResult:
        self._doc = ezdxf.new("R2013")
        self._msp = self._doc.modelspace()
        self._screenshot.doc = self._doc
        self._entity_counter = 0
        self._save_path = f"{name}.dxf" if name else None
        return CommandResult(ok=True, payload={"name": name or "untitled"})

    async def drawing_purge(self) -> CommandResult:
        if not self._doc:
            return CommandResult(ok=False, error="No document open")
        # ezdxf doesn't have a direct purge; just report
        return CommandResult(ok=True, payload={"purged": True})

    async def drawing_get_variables(self, names: list[str] | None = None) -> CommandResult:
        if not self._doc:
            return CommandResult(ok=False, error="No document open")
        result = {}
        header = self._doc.header
        for name in (names or []):
            try:
                result[name] = str(header[name])
            except (KeyError, ezdxf.DXFKeyError):
                result[name] = None
        return CommandResult(ok=True, payload=result)

    # --- Entity operations ---

    async def create_line(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "LINE", "handle": e.dxf.handle})

    async def create_circle(self, cx, cy, radius, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_circle((cx, cy), radius, dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "CIRCLE", "handle": e.dxf.handle})

    async def create_polyline(self, points, closed=False, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        pts = [(p[0], p[1]) for p in points]
        e = self._msp.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "LWPOLYLINE", "handle": e.dxf.handle})

    async def create_rectangle(self, x1, y1, x2, y2, layer=None) -> CommandResult:
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return await self.create_polyline(pts, closed=True, layer=layer)

    async def create_arc(self, cx, cy, radius, start_angle, end_angle, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_arc((cx, cy), radius, start_angle, end_angle, dxfattribs={"layer": layer or "0"})
        return CommandResult(ok=True, payload={"entity_type": "ARC", "handle": e.dxf.handle})

    async def create_ellipse(self, cx, cy, major_x, major_y, ratio, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_ellipse(
            (cx, cy), major_axis=(major_x - cx, major_y - cy, 0), ratio=ratio,
            dxfattribs={"layer": layer or "0"},
        )
        return CommandResult(ok=True, payload={"entity_type": "ELLIPSE", "handle": e.dxf.handle})

    async def create_mtext(self, x, y, width, text, height=2.5, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_mtext(text, dxfattribs={
            "insert": (x, y),
            "char_height": height,
            "width": width,
            "layer": layer or "0",
        })
        return CommandResult(ok=True, payload={"entity_type": "MTEXT", "handle": e.dxf.handle})

    async def entity_list(self, layer=None) -> CommandResult:
        entities = []
        for e in self._msp:
            if layer and e.dxf.get("layer", "0") != layer:
                continue
            entities.append({
                "type": e.dxftype(),
                "handle": e.dxf.handle,
                "layer": e.dxf.get("layer", "0"),
            })
        return CommandResult(ok=True, payload={"entities": entities, "count": len(entities)})

    async def entity_count(self, layer=None) -> CommandResult:
        if layer:
            count = sum(1 for e in self._msp if e.dxf.get("layer", "0") == layer)
        else:
            count = len(self._msp)
        return CommandResult(ok=True, payload={"count": count})

    async def entity_get(self, entity_id) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            info = {"type": e.dxftype(), "handle": e.dxf.handle, "layer": e.dxf.get("layer", "0")}
            # Add type-specific info
            if e.dxftype() == "LINE":
                info["start"] = list(e.dxf.start)[:2]
                info["end"] = list(e.dxf.end)[:2]
            elif e.dxftype() == "CIRCLE":
                info["center"] = list(e.dxf.center)[:2]
                info["radius"] = e.dxf.radius
            return CommandResult(ok=True, payload=info)
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_erase(self, entity_id) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                # Try "last" keyword
                if entity_id == "last" and len(self._msp) > 0:
                    entities = list(self._msp)
                    e = entities[-1]
                else:
                    return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            self._msp.delete_entity(e)
            return CommandResult(ok=True, payload={"erased": entity_id})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_copy(self, entity_id, dx, dy) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            copy = e.copy()
            self._msp.add_entity(copy)
            copy.translate(dx, dy, 0)
            return CommandResult(ok=True, payload={"handle": copy.dxf.handle})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_move(self, entity_id, dx, dy) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            e.translate(dx, dy, 0)
            return CommandResult(ok=True, payload={"moved": entity_id})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_rotate(self, entity_id, cx, cy, angle) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            from ezdxf.math import Matrix44
            m = Matrix44.z_rotate(math.radians(angle))
            # Translate to origin, rotate, translate back
            e.translate(-cx, -cy, 0)
            e.transform(m)
            e.translate(cx, cy, 0)
            return CommandResult(ok=True, payload={"rotated": entity_id})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_scale(self, entity_id, cx, cy, factor) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            from ezdxf.math import Matrix44
            m = Matrix44.scale(factor, factor, factor)
            e.translate(-cx, -cy, 0)
            e.transform(m)
            e.translate(cx, cy, 0)
            return CommandResult(ok=True, payload={"scaled": entity_id})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_mirror(self, entity_id, x1, y1, x2, y2) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            copy = e.copy()
            self._msp.add_entity(copy)
            # Mirror across line (x1,y1)-(x2,y2) using reflection matrix
            dx, dy = x2 - x1, y2 - y1
            length_sq = dx * dx + dy * dy
            if length_sq == 0:
                return CommandResult(ok=False, error="Mirror line has zero length")
            from ezdxf.math import Matrix44
            # Reflect: translate to origin, reflect, translate back
            # Reflection matrix across line through origin with direction (dx, dy):
            #   [[cos2a, sin2a], [sin2a, -cos2a]] where a = atan2(dy, dx)
            a = math.atan2(dy, dx)
            cos2a = math.cos(2 * a)
            sin2a = math.sin(2 * a)
            m = Matrix44([
                cos2a, sin2a, 0, 0,
                sin2a, -cos2a, 0, 0,
                0, 0, 1, 0,
                0, 0, 0, 1,
            ])
            copy.translate(-x1, -y1, 0)
            copy.transform(m)
            copy.translate(x1, y1, 0)
            return CommandResult(ok=True, payload={"handle": copy.dxf.handle})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_offset(self, entity_id, distance) -> CommandResult:
        # ezdxf doesn't have a native offset command; approximate for simple cases
        return CommandResult(ok=False, error="Offset not supported on ezdxf backend")

    async def entity_array(self, entity_id, rows, cols, row_dist, col_dist) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            handles = []
            for r in range(rows):
                for c in range(cols):
                    if r == 0 and c == 0:
                        continue  # Skip original position
                    copy = e.copy()
                    self._msp.add_entity(copy)
                    copy.translate(c * col_dist, r * row_dist, 0)
                    handles.append(copy.dxf.handle)
            return CommandResult(ok=True, payload={"copies": len(handles), "handles": handles})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def entity_fillet(self, entity_id1, entity_id2, radius) -> CommandResult:
        return CommandResult(ok=False, error="Fillet not supported on ezdxf backend")

    async def entity_chamfer(self, entity_id1, entity_id2, dist1, dist2) -> CommandResult:
        return CommandResult(ok=False, error="Chamfer not supported on ezdxf backend")

    async def create_hatch(self, entity_id, pattern="ANSI31") -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None:
                return CommandResult(ok=False, error=f"Entity {entity_id} not found")
            hatch = self._msp.add_hatch()
            hatch.set_pattern_fill(pattern, scale=1.0)
            # Try to use the entity as a boundary path
            hatch.paths.add_polyline_path(
                [(p[0], p[1]) for p in e.get_points(format="xy")],
                is_closed=True,
            )
            return CommandResult(ok=True, payload={"entity_type": "HATCH", "handle": hatch.dxf.handle})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    # --- Layer operations ---

    async def layer_list(self) -> CommandResult:
        layers = []
        for l in self._doc.layers:
            layers.append({
                "name": l.dxf.name,
                "color": l.dxf.get("color", 7),
                "linetype": l.dxf.get("linetype", "Continuous"),
                "is_frozen": l.is_frozen(),
                "is_locked": l.is_locked(),
            })
        return CommandResult(ok=True, payload={"layers": layers})

    async def layer_create(self, name, color="white", linetype="CONTINUOUS") -> CommandResult:
        if name in self._doc.layers:
            return CommandResult(ok=True, payload={"name": name, "existed": True})
        color_int = self._color_to_int(color)
        self._doc.layers.add(name, color=color_int, linetype=linetype)
        return CommandResult(ok=True, payload={"name": name, "color": color_int})

    async def layer_set_current(self, name) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        self._doc.header["$CLAYER"] = name
        return CommandResult(ok=True, payload={"current_layer": name})

    async def layer_set_properties(self, name, color=None, linetype=None, lineweight=None) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        layer = self._doc.layers.get(name)
        if color is not None:
            layer.color = self._color_to_int(color)
        if linetype is not None:
            layer.dxf.linetype = linetype
        return CommandResult(ok=True, payload={"name": name})

    async def layer_freeze(self, name) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        self._doc.layers.get(name).freeze()
        return CommandResult(ok=True, payload={"name": name, "frozen": True})

    async def layer_thaw(self, name) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        self._doc.layers.get(name).thaw()
        return CommandResult(ok=True, payload={"name": name, "frozen": False})

    async def layer_lock(self, name) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        self._doc.layers.get(name).lock()
        return CommandResult(ok=True, payload={"name": name, "locked": True})

    async def layer_unlock(self, name) -> CommandResult:
        if name not in self._doc.layers:
            return CommandResult(ok=False, error=f"Layer '{name}' does not exist")
        self._doc.layers.get(name).unlock()
        return CommandResult(ok=True, payload={"name": name, "locked": False})

    # --- Block operations ---

    async def block_list(self) -> CommandResult:
        blocks = [b.name for b in self._doc.blocks if not b.name.startswith("*")]
        return CommandResult(ok=True, payload={"blocks": blocks})

    async def block_insert(self, name, x, y, scale=1.0, rotation=0.0, block_id=None) -> CommandResult:
        if name not in self._doc.blocks:
            return CommandResult(ok=False, error=f"Block '{name}' not defined")
        e = self._msp.add_blockref(name, (x, y), dxfattribs={
            "xscale": scale, "yscale": scale, "zscale": scale,
            "rotation": rotation,
        })
        if block_id:
            try:
                e.add_attrib("ID", block_id)
            except Exception:
                pass
        return CommandResult(ok=True, payload={"entity_type": "INSERT", "handle": e.dxf.handle})

    async def block_insert_with_attributes(self, name, x, y, scale=1.0, rotation=0.0, attributes=None) -> CommandResult:
        if name not in self._doc.blocks:
            return CommandResult(ok=False, error=f"Block '{name}' not defined")
        block = self._doc.blocks[name]
        e = self._msp.add_blockref(name, (x, y), dxfattribs={
            "xscale": scale, "yscale": scale, "zscale": scale,
            "rotation": rotation,
        })
        if attributes:
            # Try add_auto_attribs first (uses ATTDEF templates)
            try:
                e.add_auto_attribs(attributes)
            except Exception:
                # Fallback: add manual attribs
                for tag, value in attributes.items():
                    try:
                        e.add_attrib(tag, value, (x, y))
                    except Exception:
                        pass
        return CommandResult(ok=True, payload={"entity_type": "INSERT", "handle": e.dxf.handle})

    async def block_get_attributes(self, entity_id) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None or e.dxftype() != "INSERT":
                return CommandResult(ok=False, error="Not an INSERT entity")
            attribs = {}
            for attrib in e.attribs:
                attribs[attrib.dxf.tag] = attrib.dxf.text
            return CommandResult(ok=True, payload={"attributes": attribs})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def block_update_attribute(self, entity_id, tag, value) -> CommandResult:
        try:
            e = self._doc.entitydb.get(entity_id)
            if e is None or e.dxftype() != "INSERT":
                return CommandResult(ok=False, error="Not an INSERT entity")
            for attrib in e.attribs:
                if attrib.dxf.tag.upper() == tag.upper():
                    attrib.dxf.text = value
                    return CommandResult(ok=True, payload={"tag": tag, "value": value})
            return CommandResult(ok=False, error=f"Attribute '{tag}' not found")
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def block_define(self, name, entities) -> CommandResult:
        block = self._doc.blocks.new(name=name)
        for ent_def in entities:
            etype = ent_def.get("type", "LINE")
            if etype == "LINE":
                block.add_line(
                    (ent_def.get("x1", 0), ent_def.get("y1", 0)),
                    (ent_def.get("x2", 0), ent_def.get("y2", 0)),
                )
            elif etype == "CIRCLE":
                block.add_circle(
                    (ent_def.get("cx", 0), ent_def.get("cy", 0)),
                    ent_def.get("radius", 1),
                )
            elif etype == "ATTDEF":
                block.add_attdef(
                    ent_def.get("tag", "TAG"),
                    (ent_def.get("x", 0), ent_def.get("y", 0)),
                    dxfattribs={"height": ent_def.get("height", 2.5)},
                )
        return CommandResult(ok=True, payload={"block": name, "entity_count": len(entities)})

    # --- Annotation ---

    async def create_text(self, x, y, text, height=2.5, rotation=0.0, layer=None) -> CommandResult:
        self._ensure_layer(layer)
        e = self._msp.add_text(text, dxfattribs={
            "insert": (x, y),
            "height": height,
            "rotation": rotation,
            "layer": layer or "0",
        })
        return CommandResult(ok=True, payload={"entity_type": "TEXT", "handle": e.dxf.handle})

    async def create_dimension_linear(self, x1, y1, x2, y2, dim_x, dim_y) -> CommandResult:
        try:
            dim = self._msp.add_linear_dim(
                base=(dim_x, dim_y),
                p1=(x1, y1),
                p2=(x2, y2),
            )
            dim.render()
            return CommandResult(ok=True, payload={"entity_type": "DIMENSION"})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def create_dimension_aligned(self, x1, y1, x2, y2, offset) -> CommandResult:
        try:
            dim = self._msp.add_aligned_dim(
                p1=(x1, y1),
                p2=(x2, y2),
                distance=offset,
            )
            dim.render()
            return CommandResult(ok=True, payload={"entity_type": "DIMENSION"})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def create_dimension_angular(self, cx, cy, x1, y1, x2, y2) -> CommandResult:
        try:
            # Calculate angle arc midpoint for dimension location
            a1 = math.atan2(y1 - cy, x1 - cx)
            a2 = math.atan2(y2 - cy, x2 - cx)
            amid = (a1 + a2) / 2
            r = max(math.hypot(x1 - cx, y1 - cy), math.hypot(x2 - cx, y2 - cy)) * 0.7
            dim = self._msp.add_angular_dim_cra(
                center=(cx, cy),
                radius=r,
                start_angle=math.degrees(a1),
                end_angle=math.degrees(a2),
                distance=r * 1.2,
            )
            dim.render()
            return CommandResult(ok=True, payload={"entity_type": "DIMENSION"})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def create_dimension_radius(self, cx, cy, radius, angle) -> CommandResult:
        try:
            rad = math.radians(angle)
            px = cx + radius * math.cos(rad)
            py = cy + radius * math.sin(rad)
            dim = self._msp.add_radius_dim(
                center=(cx, cy),
                mpoint=(px, py),
            )
            dim.render()
            return CommandResult(ok=True, payload={"entity_type": "DIMENSION"})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    async def create_leader(self, points, text) -> CommandResult:
        try:
            pts = [(p[0], p[1]) for p in points]
            leader = self._msp.add_leader(pts)
            # Add text at the last point
            last = pts[-1]
            self._msp.add_mtext(text, dxfattribs={
                "insert": (last[0] + 2, last[1]),
                "char_height": 2.5,
                "width": 30,
            })
            return CommandResult(ok=True, payload={"entity_type": "LEADER"})
        except Exception as ex:
            return CommandResult(ok=False, error=str(ex))

    # --- P&ID ---

    async def pid_setup_layers(self) -> CommandResult:
        pid_layers = [
            ("PID-EQUIPMENT", 6, "CONTINUOUS"),
            ("PID-PROCESS-PIPING", 4, "CONTINUOUS"),
            ("PID-UTILITY-PIPING", 3, "CONTINUOUS"),
            ("PID-INSTRUMENTS", 5, "CONTINUOUS"),
            ("PID-ELECTRICAL", 1, "CONTINUOUS"),
            ("PID-ANNOTATION", 7, "CONTINUOUS"),
            ("PID-VALVES", 2, "CONTINUOUS"),
        ]
        for name, color, lt in pid_layers:
            if name not in self._doc.layers:
                self._doc.layers.add(name, color=color, linetype=lt)
        return CommandResult(ok=True, payload={"layers_created": len(pid_layers)})

    async def pid_list_symbols(self, category) -> CommandResult:
        """List CTO symbols from disk or built-in catalog."""
        from autocad_mcp.pid.cto_library import CTO_ROOT, list_symbols
        symbols = list_symbols(category)
        return CommandResult(ok=True, payload={"category": category, "symbols": symbols, "count": len(symbols)})

    async def pid_insert_symbol(self, category, symbol, x, y, scale=1.0, rotation=0.0) -> CommandResult:
        """Insert a CTO symbol as a simple block placeholder."""
        self._ensure_layer("PID-EQUIPMENT")
        # In headless mode, create a placeholder rectangle with the symbol name
        half = 5 * scale
        pts = [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]
        e = self._msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PID-EQUIPMENT"})
        self._msp.add_text(symbol, dxfattribs={
            "insert": (x, y), "height": 1.5 * scale, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"symbol": symbol, "handle": e.dxf.handle})

    async def pid_insert_valve(self, x, y, valve_type, rotation=0.0, attributes=None) -> CommandResult:
        """Insert a valve symbol (simplified for headless)."""
        self._ensure_layer("PID-VALVES")
        # Simplified diamond shape for valve
        size = 3.0
        pts = [(x - size, y), (x, y + size), (x + size, y), (x, y - size)]
        e = self._msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PID-VALVES"})
        self._msp.add_text(valve_type, dxfattribs={
            "insert": (x, y - size - 2), "height": 1.5, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"valve_type": valve_type, "handle": e.dxf.handle})

    async def pid_insert_instrument(self, x, y, instrument_type, rotation=0.0, tag_id="", range_value="") -> CommandResult:
        """Insert an instrument symbol (simplified for headless)."""
        self._ensure_layer("PID-INSTRUMENTS")
        # Circle with crosshair for instrument
        e = self._msp.add_circle((x, y), 4, dxfattribs={"layer": "PID-INSTRUMENTS"})
        self._msp.add_line((x - 4, y), (x + 4, y), dxfattribs={"layer": "PID-INSTRUMENTS"})
        label = tag_id if tag_id else instrument_type
        self._msp.add_text(label, dxfattribs={
            "insert": (x, y - 6), "height": 1.5, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"instrument_type": instrument_type, "handle": e.dxf.handle})

    async def pid_insert_pump(self, x, y, pump_type, rotation=0.0, attributes=None) -> CommandResult:
        """Insert a pump symbol (simplified for headless)."""
        self._ensure_layer("PID-EQUIPMENT")
        # Circle with triangle for pump
        e = self._msp.add_circle((x, y), 6, dxfattribs={"layer": "PID-EQUIPMENT"})
        rad = math.radians(rotation)
        tip_x = x + 8 * math.cos(rad)
        tip_y = y + 8 * math.sin(rad)
        self._msp.add_lwpolyline(
            [(x + 6 * math.cos(rad + 0.5), y + 6 * math.sin(rad + 0.5)),
             (tip_x, tip_y),
             (x + 6 * math.cos(rad - 0.5), y + 6 * math.sin(rad - 0.5))],
            close=True,
            dxfattribs={"layer": "PID-EQUIPMENT"},
        )
        self._msp.add_text(pump_type, dxfattribs={
            "insert": (x, y - 8), "height": 1.5, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"pump_type": pump_type, "handle": e.dxf.handle})

    async def pid_insert_tank(self, x, y, tank_type, scale=1.0, attributes=None) -> CommandResult:
        """Insert a tank symbol (simplified for headless)."""
        self._ensure_layer("PID-EQUIPMENT")
        w = 10 * scale
        h = 15 * scale
        pts = [(x - w, y), (x + w, y), (x + w, y + h), (x - w, y + h)]
        e = self._msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PID-EQUIPMENT"})
        self._msp.add_text(tank_type, dxfattribs={
            "insert": (x, y + h + 2), "height": 2.0 * scale, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"tank_type": tank_type, "handle": e.dxf.handle})

    async def pid_draw_process_line(self, x1, y1, x2, y2) -> CommandResult:
        self._ensure_layer("PID-PROCESS-PIPING")
        e = self._msp.add_line((x1, y1), (x2, y2), dxfattribs={"layer": "PID-PROCESS-PIPING"})
        return CommandResult(ok=True, payload={"entity_type": "LINE", "handle": e.dxf.handle})

    async def pid_connect_equipment(self, x1, y1, x2, y2) -> CommandResult:
        """Connect two points with orthogonal routing."""
        self._ensure_layer("PID-PROCESS-PIPING")
        mid_x = (x1 + x2) / 2
        pts = [(x1, y1), (mid_x, y1), (mid_x, y2), (x2, y2)]
        e = self._msp.add_lwpolyline(pts, dxfattribs={"layer": "PID-PROCESS-PIPING"})
        return CommandResult(ok=True, payload={"entity_type": "LWPOLYLINE", "handle": e.dxf.handle})

    async def pid_add_flow_arrow(self, x, y, rotation=0.0) -> CommandResult:
        self._ensure_layer("PID-ANNOTATION")
        # Simple triangle arrow
        rad = math.radians(rotation)
        size = 2.0
        p1 = (x + size * math.cos(rad), y + size * math.sin(rad))
        p2 = (x + size * 0.5 * math.cos(rad + 2.4), y + size * 0.5 * math.sin(rad + 2.4))
        p3 = (x + size * 0.5 * math.cos(rad - 2.4), y + size * 0.5 * math.sin(rad - 2.4))
        e = self._msp.add_lwpolyline([p1, p2, p3], close=True, dxfattribs={"layer": "PID-ANNOTATION"})
        return CommandResult(ok=True, payload={"entity_type": "LWPOLYLINE", "handle": e.dxf.handle})

    async def pid_add_equipment_tag(self, x, y, tag, description="") -> CommandResult:
        self._ensure_layer("PID-ANNOTATION")
        e = self._msp.add_text(tag, dxfattribs={
            "insert": (x, y), "height": 2.5, "layer": "PID-ANNOTATION",
        })
        result = {"entity_type": "TEXT", "handle": e.dxf.handle, "tag": tag}
        if description:
            e2 = self._msp.add_text(description, dxfattribs={
                "insert": (x, y - 3.5), "height": 1.8, "layer": "PID-ANNOTATION",
            })
            result["description_handle"] = e2.dxf.handle
        return CommandResult(ok=True, payload=result)

    async def pid_add_line_number(self, x, y, line_num, spec) -> CommandResult:
        self._ensure_layer("PID-ANNOTATION")
        text = f"{line_num}-{spec}"
        e = self._msp.add_text(text, dxfattribs={
            "insert": (x, y), "height": 2.0, "layer": "PID-ANNOTATION",
        })
        return CommandResult(ok=True, payload={"entity_type": "TEXT", "handle": e.dxf.handle})

    # --- View ---

    async def get_screenshot(self) -> CommandResult:
        data = self._screenshot.capture()
        if data:
            return CommandResult(ok=True, payload=data)
        return CommandResult(ok=False, error="Screenshot render failed")

    # --- Helpers ---

    @staticmethod
    def _color_to_int(color: str | int) -> int:
        if isinstance(color, int):
            return color
        color_map = {
            "red": 1, "yellow": 2, "green": 3, "cyan": 4,
            "blue": 5, "magenta": 6, "white": 7, "grey": 8, "gray": 8,
        }
        return color_map.get(color.lower(), 7)
