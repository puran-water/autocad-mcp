"""Generate golden DXF files for regression testing.

Run with: python -m tests.generate_golden
Outputs to tests/golden/
"""

import os
from pathlib import Path

import ezdxf

GOLDEN_DIR = Path(__file__).parent / "golden"


def generate_basic_shapes():
    """Golden file: basic geometric shapes on multiple layers."""
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()

    # Create layers
    doc.layers.add("BORDER", color=7, linetype="Continuous")
    doc.layers.add("SHAPES", color=3, linetype="Continuous")
    doc.layers.add("ANNOTATION", color=4, linetype="Continuous")

    # Border rectangle
    msp.add_lwpolyline(
        [(0, 0), (200, 0), (200, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "BORDER"},
    )

    # Shapes
    msp.add_line((10, 10), (50, 50), dxfattribs={"layer": "SHAPES"})
    msp.add_circle((100, 50), 30, dxfattribs={"layer": "SHAPES"})
    msp.add_arc((150, 50), 20, 0, 180, dxfattribs={"layer": "SHAPES"})
    msp.add_lwpolyline(
        [(60, 20), (80, 20), (80, 40), (60, 40)],
        close=True,
        dxfattribs={"layer": "SHAPES"},
    )

    # Annotation
    msp.add_text("BASIC SHAPES", dxfattribs={
        "insert": (10, 90), "height": 5, "layer": "ANNOTATION",
    })

    doc.saveas(str(GOLDEN_DIR / "basic_shapes.dxf"))
    print(f"Generated: {GOLDEN_DIR / 'basic_shapes.dxf'}")


def generate_pid_example():
    """Golden file: simple P&ID layout."""
    doc = ezdxf.new("R2013")
    msp = doc.modelspace()

    # P&ID layers
    layers = [
        ("PID-EQUIPMENT", 6),
        ("PID-PROCESS-PIPING", 4),
        ("PID-ANNOTATION", 7),
        ("PID-VALVES", 2),
    ]
    for name, color in layers:
        doc.layers.add(name, color=color, linetype="Continuous")

    # Equipment: simplified tank (rectangle)
    msp.add_lwpolyline(
        [(20, 20), (60, 20), (60, 60), (20, 60)],
        close=True,
        dxfattribs={"layer": "PID-EQUIPMENT"},
    )
    # Equipment: simplified pump (circle)
    msp.add_circle((120, 40), 15, dxfattribs={"layer": "PID-EQUIPMENT"})

    # Process piping with orthogonal routing
    msp.add_lwpolyline(
        [(60, 40), (90, 40), (90, 40), (105, 40)],
        dxfattribs={"layer": "PID-PROCESS-PIPING"},
    )

    # Valve symbol (simplified diamond)
    msp.add_lwpolyline(
        [(87, 40), (90, 45), (93, 40), (90, 35)],
        close=True,
        dxfattribs={"layer": "PID-VALVES"},
    )

    # Tags
    msp.add_text("TK-101", dxfattribs={
        "insert": (25, 62), "height": 3, "layer": "PID-ANNOTATION",
    })
    msp.add_text("P-101", dxfattribs={
        "insert": (112, 58), "height": 3, "layer": "PID-ANNOTATION",
    })
    msp.add_text("V-101", dxfattribs={
        "insert": (85, 48), "height": 2, "layer": "PID-ANNOTATION",
    })

    doc.saveas(str(GOLDEN_DIR / "pid_example.dxf"))
    print(f"Generated: {GOLDEN_DIR / 'pid_example.dxf'}")


if __name__ == "__main__":
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    generate_basic_shapes()
    generate_pid_example()
    print("Done.")
