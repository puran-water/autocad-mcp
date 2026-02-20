"""Tests for the ezdxf headless backend — no AutoCAD needed."""

import math
import os
import tempfile

import ezdxf
import pytest

from autocad_mcp.backends.ezdxf_backend import EzdxfBackend


@pytest.fixture
async def backend():
    """Initialized ezdxf backend."""
    b = EzdxfBackend()
    result = await b.initialize()
    assert result.ok
    return b


# ---------------------------------------------------------------------------
# Drawing management
# ---------------------------------------------------------------------------


class TestDrawingManagement:
    async def test_initialize(self, backend):
        assert backend._doc is not None
        assert backend._msp is not None

    async def test_status(self, backend):
        r = await backend.status()
        assert r.ok
        assert r.payload["backend"] == "ezdxf"
        assert r.payload["has_document"] is True
        assert r.payload["entity_count"] == 0

    async def test_drawing_info_empty(self, backend):
        r = await backend.drawing_info()
        assert r.ok
        assert r.payload["entity_count"] == 0
        assert "0" in r.payload["layers"]  # Default layer

    async def test_drawing_create(self, backend):
        r = await backend.drawing_create("TestDrawing")
        assert r.ok
        assert r.payload["name"] == "TestDrawing"
        assert backend._save_path == "TestDrawing.dxf"

    async def test_drawing_save(self, backend):
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            path = f.name
        try:
            r = await backend.drawing_save(path)
            assert r.ok
            assert r.payload["path"] == path
            assert os.path.exists(path)
            # Verify it's a valid DXF
            doc = ezdxf.readfile(path)
            assert doc.dxfversion is not None
        finally:
            os.unlink(path)

    async def test_drawing_save_no_path(self, backend):
        r = await backend.drawing_save()
        assert not r.ok
        assert "No save path" in r.error

    async def test_drawing_purge(self, backend):
        r = await backend.drawing_purge()
        assert r.ok

    async def test_drawing_get_variables(self, backend):
        r = await backend.drawing_get_variables(["$ACADVER"])
        assert r.ok
        assert "$ACADVER" in r.payload

    async def test_drawing_get_variables_missing(self, backend):
        r = await backend.drawing_get_variables(["$NONEXISTENT_VAR_XYZ"])
        assert r.ok
        assert r.payload["$NONEXISTENT_VAR_XYZ"] is None


# ---------------------------------------------------------------------------
# Entity creation
# ---------------------------------------------------------------------------


class TestEntityCreation:
    async def test_create_line(self, backend):
        r = await backend.create_line(0, 0, 100, 100)
        assert r.ok
        assert r.payload["entity_type"] == "LINE"
        assert r.payload["handle"]

    async def test_create_line_on_layer(self, backend):
        r = await backend.create_line(0, 0, 50, 50, layer="TEST")
        assert r.ok
        # Verify layer was auto-created
        assert "TEST" in backend._doc.layers

    async def test_create_circle(self, backend):
        r = await backend.create_circle(50, 50, 25)
        assert r.ok
        assert r.payload["entity_type"] == "CIRCLE"

    async def test_create_polyline(self, backend):
        pts = [[0, 0], [10, 0], [10, 10], [0, 10]]
        r = await backend.create_polyline(pts, closed=True)
        assert r.ok
        assert r.payload["entity_type"] == "LWPOLYLINE"

    async def test_create_rectangle(self, backend):
        r = await backend.create_rectangle(0, 0, 100, 50)
        assert r.ok
        assert r.payload["entity_type"] == "LWPOLYLINE"

    async def test_create_arc(self, backend):
        r = await backend.create_arc(50, 50, 30, 0, 90)
        assert r.ok
        assert r.payload["entity_type"] == "ARC"

    async def test_create_ellipse(self, backend):
        r = await backend.create_ellipse(50, 50, 100, 50, 0.5)
        assert r.ok
        assert r.payload["entity_type"] == "ELLIPSE"

    async def test_create_mtext(self, backend):
        r = await backend.create_mtext(10, 10, 50, "Hello World", height=3.0)
        assert r.ok
        assert r.payload["entity_type"] == "MTEXT"

    async def test_create_text(self, backend):
        r = await backend.create_text(10, 10, "Label", height=2.5, rotation=45)
        assert r.ok
        assert r.payload["entity_type"] == "TEXT"


# ---------------------------------------------------------------------------
# Entity query
# ---------------------------------------------------------------------------


class TestEntityQuery:
    async def test_entity_list_empty(self, backend):
        r = await backend.entity_list()
        assert r.ok
        assert r.payload["count"] == 0
        assert r.payload["entities"] == []

    async def test_entity_list_after_create(self, backend):
        await backend.create_line(0, 0, 10, 10)
        await backend.create_circle(5, 5, 3)
        r = await backend.entity_list()
        assert r.ok
        assert r.payload["count"] == 2

    async def test_entity_list_by_layer(self, backend):
        await backend.create_line(0, 0, 10, 10, layer="A")
        await backend.create_line(0, 0, 20, 20, layer="B")
        await backend.create_circle(5, 5, 3, layer="A")
        r = await backend.entity_list(layer="A")
        assert r.ok
        assert r.payload["count"] == 2

    async def test_entity_count(self, backend):
        await backend.create_line(0, 0, 10, 10)
        await backend.create_line(0, 0, 20, 20)
        r = await backend.entity_count()
        assert r.ok
        assert r.payload["count"] == 2

    async def test_entity_count_by_layer(self, backend):
        await backend.create_line(0, 0, 10, 10, layer="X")
        await backend.create_line(0, 0, 20, 20, layer="Y")
        r = await backend.entity_count(layer="X")
        assert r.ok
        assert r.payload["count"] == 1

    async def test_entity_get_line(self, backend):
        cr = await backend.create_line(10, 20, 30, 40)
        handle = cr.payload["handle"]
        r = await backend.entity_get(handle)
        assert r.ok
        assert r.payload["type"] == "LINE"
        assert r.payload["start"] == [10.0, 20.0]
        assert r.payload["end"] == [30.0, 40.0]

    async def test_entity_get_circle(self, backend):
        cr = await backend.create_circle(50, 60, 25)
        handle = cr.payload["handle"]
        r = await backend.entity_get(handle)
        assert r.ok
        assert r.payload["type"] == "CIRCLE"
        assert r.payload["center"] == [50.0, 60.0]
        assert r.payload["radius"] == 25.0

    async def test_entity_get_not_found(self, backend):
        r = await backend.entity_get("NONEXISTENT")
        assert not r.ok


# ---------------------------------------------------------------------------
# Entity modification
# ---------------------------------------------------------------------------


class TestEntityModification:
    async def test_entity_erase(self, backend):
        cr = await backend.create_line(0, 0, 10, 10)
        handle = cr.payload["handle"]
        r = await backend.entity_erase(handle)
        assert r.ok
        count = await backend.entity_count()
        assert count.payload["count"] == 0

    async def test_entity_erase_last(self, backend):
        await backend.create_line(0, 0, 10, 10)
        await backend.create_circle(5, 5, 3)
        r = await backend.entity_erase("last")
        assert r.ok
        count = await backend.entity_count()
        assert count.payload["count"] == 1

    async def test_entity_copy(self, backend):
        cr = await backend.create_line(0, 0, 10, 10)
        handle = cr.payload["handle"]
        r = await backend.entity_copy(handle, 50, 50)
        assert r.ok
        assert r.payload["handle"]  # New entity handle
        count = await backend.entity_count()
        assert count.payload["count"] == 2

    async def test_entity_move(self, backend):
        cr = await backend.create_line(0, 0, 10, 10)
        handle = cr.payload["handle"]
        r = await backend.entity_move(handle, 100, 100)
        assert r.ok
        info = await backend.entity_get(handle)
        assert info.ok
        assert info.payload["start"] == [100.0, 100.0]
        assert info.payload["end"] == [110.0, 110.0]

    async def test_entity_rotate(self, backend):
        cr = await backend.create_line(10, 0, 20, 0)
        handle = cr.payload["handle"]
        # Rotate 90 degrees around origin
        r = await backend.entity_rotate(handle, 0, 0, 90)
        assert r.ok
        info = await backend.entity_get(handle)
        assert info.ok
        # After 90-degree rotation around origin, (10,0)->(20,0) becomes (0,10)->(0,20)
        assert abs(info.payload["start"][0]) < 0.01
        assert abs(info.payload["start"][1] - 10.0) < 0.01

    async def test_entity_scale(self, backend):
        cr = await backend.create_line(10, 10, 20, 20)
        handle = cr.payload["handle"]
        # Scale 2x from origin
        r = await backend.entity_scale(handle, 0, 0, 2)
        assert r.ok
        info = await backend.entity_get(handle)
        assert info.ok
        assert abs(info.payload["start"][0] - 20.0) < 0.01
        assert abs(info.payload["end"][0] - 40.0) < 0.01


# ---------------------------------------------------------------------------
# Layer operations
# ---------------------------------------------------------------------------


class TestLayerOperations:
    async def test_layer_list_default(self, backend):
        r = await backend.layer_list()
        assert r.ok
        names = [l["name"] for l in r.payload["layers"]]
        assert "0" in names  # Default layer always present

    async def test_layer_create(self, backend):
        r = await backend.layer_create("PIPING", color="red", linetype="CONTINUOUS")
        assert r.ok
        assert r.payload["name"] == "PIPING"
        assert r.payload["color"] == 1  # Red = ACI 1

    async def test_layer_create_duplicate(self, backend):
        await backend.layer_create("DUP")
        r = await backend.layer_create("DUP")
        assert r.ok
        assert r.payload["existed"] is True

    async def test_layer_set_current(self, backend):
        await backend.layer_create("ACTIVE")
        r = await backend.layer_set_current("ACTIVE")
        assert r.ok
        assert backend._doc.header["$CLAYER"] == "ACTIVE"

    async def test_layer_set_current_nonexistent(self, backend):
        r = await backend.layer_set_current("NOPE")
        assert not r.ok

    async def test_layer_freeze_thaw(self, backend):
        await backend.layer_create("FREEZE_ME")
        r = await backend.layer_freeze("FREEZE_ME")
        assert r.ok
        layer = backend._doc.layers.get("FREEZE_ME")
        assert layer.is_frozen()

        r = await backend.layer_thaw("FREEZE_ME")
        assert r.ok
        assert not layer.is_frozen()

    async def test_layer_lock_unlock(self, backend):
        await backend.layer_create("LOCK_ME")
        r = await backend.layer_lock("LOCK_ME")
        assert r.ok
        layer = backend._doc.layers.get("LOCK_ME")
        assert layer.is_locked()

        r = await backend.layer_unlock("LOCK_ME")
        assert r.ok
        assert not layer.is_locked()

    async def test_layer_set_properties(self, backend):
        await backend.layer_create("PROPS")
        r = await backend.layer_set_properties("PROPS", color="blue")
        assert r.ok


# ---------------------------------------------------------------------------
# Block operations
# ---------------------------------------------------------------------------


class TestBlockOperations:
    async def test_block_define(self, backend):
        entities = [
            {"type": "LINE", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
            {"type": "LINE", "x1": 10, "y1": 0, "x2": 10, "y2": 10},
            {"type": "CIRCLE", "cx": 5, "cy": 5, "radius": 2},
        ]
        r = await backend.block_define("TESTBLOCK", entities)
        assert r.ok
        assert r.payload["block"] == "TESTBLOCK"
        assert r.payload["entity_count"] == 3

    async def test_block_define_with_attdef(self, backend):
        entities = [
            {"type": "CIRCLE", "cx": 0, "cy": 0, "radius": 5},
            {"type": "ATTDEF", "tag": "ID", "x": 0, "y": -8, "height": 2.0},
        ]
        r = await backend.block_define("TAGGED_BLOCK", entities)
        assert r.ok

    async def test_block_list(self, backend):
        await backend.block_define("BLK_A", [{"type": "LINE", "x1": 0, "y1": 0, "x2": 5, "y2": 5}])
        await backend.block_define("BLK_B", [{"type": "CIRCLE", "cx": 0, "cy": 0, "radius": 1}])
        r = await backend.block_list()
        assert r.ok
        assert "BLK_A" in r.payload["blocks"]
        assert "BLK_B" in r.payload["blocks"]

    async def test_block_insert(self, backend):
        await backend.block_define("INS_BLK", [{"type": "LINE", "x1": 0, "y1": 0, "x2": 5, "y2": 5}])
        r = await backend.block_insert("INS_BLK", 100, 200, scale=2.0, rotation=45)
        assert r.ok
        assert r.payload["entity_type"] == "INSERT"
        assert r.payload["handle"]

    async def test_block_insert_not_defined(self, backend):
        r = await backend.block_insert("NOSUCHBLOCK", 0, 0)
        assert not r.ok
        assert "not defined" in r.error

    async def test_block_insert_with_attributes(self, backend):
        entities = [
            {"type": "CIRCLE", "cx": 0, "cy": 0, "radius": 5},
            {"type": "ATTDEF", "tag": "TAG_NUM", "x": 0, "y": -8, "height": 2.0},
            {"type": "ATTDEF", "tag": "DESC", "x": 0, "y": -11, "height": 1.5},
        ]
        await backend.block_define("ATTR_BLK", entities)
        r = await backend.block_insert_with_attributes(
            "ATTR_BLK", 50, 50, attributes={"TAG_NUM": "P-101", "DESC": "Pump"}
        )
        assert r.ok
        handle = r.payload["handle"]

        # Verify attributes
        ar = await backend.block_get_attributes(handle)
        assert ar.ok
        assert ar.payload["attributes"]["TAG_NUM"] == "P-101"
        assert ar.payload["attributes"]["DESC"] == "Pump"

    async def test_block_update_attribute(self, backend):
        entities = [
            {"type": "CIRCLE", "cx": 0, "cy": 0, "radius": 5},
            {"type": "ATTDEF", "tag": "LABEL", "x": 0, "y": -8, "height": 2.0},
        ]
        await backend.block_define("UPD_BLK", entities)
        ir = await backend.block_insert_with_attributes(
            "UPD_BLK", 50, 50, attributes={"LABEL": "OLD"}
        )
        handle = ir.payload["handle"]

        r = await backend.block_update_attribute(handle, "LABEL", "NEW")
        assert r.ok

        ar = await backend.block_get_attributes(handle)
        assert ar.payload["attributes"]["LABEL"] == "NEW"


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------


class TestEntityModificationExtended:
    async def test_entity_mirror(self, backend):
        cr = await backend.create_line(10, 0, 20, 0)
        handle = cr.payload["handle"]
        r = await backend.entity_mirror(handle, 0, 0, 0, 1)  # Mirror across Y axis
        assert r.ok
        assert r.payload["handle"]  # New mirrored entity
        count = await backend.entity_count()
        assert count.payload["count"] == 2

    async def test_entity_array(self, backend):
        cr = await backend.create_circle(0, 0, 5)
        handle = cr.payload["handle"]
        r = await backend.entity_array(handle, 2, 3, 20, 30)
        assert r.ok
        assert r.payload["copies"] == 5  # 2*3 - 1 original

    async def test_entity_offset_unsupported(self, backend):
        cr = await backend.create_line(0, 0, 10, 10)
        handle = cr.payload["handle"]
        r = await backend.entity_offset(handle, 5)
        assert not r.ok
        assert "not supported" in r.error.lower()

    async def test_entity_fillet_unsupported(self, backend):
        r = await backend.entity_fillet("a", "b", 5)
        assert not r.ok

    async def test_entity_chamfer_unsupported(self, backend):
        r = await backend.entity_chamfer("a", "b", 5, 5)
        assert not r.ok


class TestAnnotation:
    async def test_create_dimension_linear(self, backend):
        r = await backend.create_dimension_linear(0, 0, 100, 0, 50, 20)
        assert r.ok
        assert r.payload["entity_type"] == "DIMENSION"

    async def test_create_dimension_aligned(self, backend):
        r = await backend.create_dimension_aligned(0, 0, 100, 50, 10)
        assert r.ok

    async def test_create_dimension_angular(self, backend):
        r = await backend.create_dimension_angular(0, 0, 10, 0, 0, 10)
        assert r.ok
        assert r.payload["entity_type"] == "DIMENSION"

    async def test_create_dimension_radius(self, backend):
        await backend.create_circle(50, 50, 25)
        r = await backend.create_dimension_radius(50, 50, 25, 45)
        assert r.ok
        assert r.payload["entity_type"] == "DIMENSION"

    async def test_create_leader(self, backend):
        r = await backend.create_leader([[0, 0], [10, 10], [20, 10]], "Note text")
        assert r.ok
        assert r.payload["entity_type"] == "LEADER"


# ---------------------------------------------------------------------------
# P&ID
# ---------------------------------------------------------------------------


class TestPID:
    async def test_pid_setup_layers(self, backend):
        r = await backend.pid_setup_layers()
        assert r.ok
        assert r.payload["layers_created"] == 7
        # Verify layers exist
        layer_names = [l.dxf.name for l in backend._doc.layers]
        assert "PID-EQUIPMENT" in layer_names
        assert "PID-PROCESS-PIPING" in layer_names
        assert "PID-VALVES" in layer_names

    async def test_pid_draw_process_line(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_draw_process_line(0, 0, 100, 0)
        assert r.ok
        assert r.payload["entity_type"] == "LINE"

    async def test_pid_connect_equipment(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_connect_equipment(0, 0, 100, 50)
        assert r.ok
        assert r.payload["entity_type"] == "LWPOLYLINE"

    async def test_pid_add_flow_arrow(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_add_flow_arrow(50, 25, rotation=0)
        assert r.ok

    async def test_pid_add_equipment_tag(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_add_equipment_tag(50, 50, "P-101", "Centrifugal Pump")
        assert r.ok
        assert r.payload["tag"] == "P-101"
        assert "description_handle" in r.payload

    async def test_pid_add_line_number(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_add_line_number(25, 5, "001", "2-CS-150")
        assert r.ok

    async def test_pid_insert_symbol(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_insert_symbol("PUMPS-BLOWERS", "PUMP-CENTRIF1", 50, 50)
        assert r.ok
        assert r.payload["symbol"] == "PUMP-CENTRIF1"

    async def test_pid_insert_valve(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_insert_valve(50, 50, "GATE")
        assert r.ok
        assert r.payload["valve_type"] == "GATE"

    async def test_pid_insert_instrument(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_insert_instrument(50, 50, "FLOW", tag_id="FIT-101")
        assert r.ok
        assert r.payload["instrument_type"] == "FLOW"

    async def test_pid_insert_pump(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_insert_pump(50, 50, "CENTRIFUGAL")
        assert r.ok
        assert r.payload["pump_type"] == "CENTRIFUGAL"

    async def test_pid_insert_tank(self, backend):
        await backend.pid_setup_layers()
        r = await backend.pid_insert_tank(50, 50, "VERTICAL")
        assert r.ok
        assert r.payload["tank_type"] == "VERTICAL"

    async def test_pid_list_symbols(self, backend):
        r = await backend.pid_list_symbols("VALVES")
        assert r.ok
        assert r.payload["category"] == "VALVES"
        assert isinstance(r.payload["symbols"], list)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    async def test_capabilities(self, backend):
        caps = backend.capabilities
        assert caps.can_create_entities is True
        assert caps.can_read_drawing is True
        assert caps.can_modify_entities is True
        assert caps.can_screenshot is True
        assert caps.can_save is True
        assert caps.can_plot_pdf is False  # ezdxf can't plot
        assert caps.can_zoom is False  # No viewport
        assert caps.can_undo is False  # ezdxf doesn't track undo

    async def test_backend_name(self, backend):
        assert backend.name == "ezdxf"


# ---------------------------------------------------------------------------
# Color helper
# ---------------------------------------------------------------------------


class TestColorToInt:
    def test_named_colors(self):
        assert EzdxfBackend._color_to_int("red") == 1
        assert EzdxfBackend._color_to_int("yellow") == 2
        assert EzdxfBackend._color_to_int("green") == 3
        assert EzdxfBackend._color_to_int("cyan") == 4
        assert EzdxfBackend._color_to_int("blue") == 5
        assert EzdxfBackend._color_to_int("magenta") == 6
        assert EzdxfBackend._color_to_int("white") == 7

    def test_int_passthrough(self):
        assert EzdxfBackend._color_to_int(3) == 3
        assert EzdxfBackend._color_to_int(255) == 255

    def test_unknown_defaults_white(self):
        assert EzdxfBackend._color_to_int("chartreuse") == 7

    def test_case_insensitive(self):
        assert EzdxfBackend._color_to_int("RED") == 1
        assert EzdxfBackend._color_to_int("Blue") == 5


# ---------------------------------------------------------------------------
# Save & reload (golden file round-trip)
# ---------------------------------------------------------------------------


class TestSaveRoundTrip:
    async def test_save_and_reload(self, backend):
        """Create entities, save, reload, verify structure."""
        await backend.create_line(0, 0, 100, 0, layer="BORDER")
        await backend.create_line(100, 0, 100, 50, layer="BORDER")
        await backend.create_circle(50, 25, 15, layer="SHAPES")

        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as f:
            path = f.name
        try:
            await backend.drawing_save(path)

            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            entities = list(msp)
            assert len(entities) == 3

            types = sorted(e.dxftype() for e in entities)
            assert types == ["CIRCLE", "LINE", "LINE"]

            layer_names = [l.dxf.name for l in doc.layers]
            assert "BORDER" in layer_names
            assert "SHAPES" in layer_names
        finally:
            os.unlink(path)
