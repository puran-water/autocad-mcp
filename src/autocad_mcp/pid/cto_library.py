"""CTO P&ID Symbol Library — DWG block catalog with DXF cache.

The CAD Tools Online (CTO) library lives at C:/PIDv4-CTO/ and contains
600+ ISA 5.1-2009 standard symbols as .dwg files organized by category.

For the ezdxf headless backend, symbols need DWG→DXF conversion via
ezdxf.addons.odafc (ODA File Converter) or manual batch conversion.
"""

from __future__ import annotations

import os
from pathlib import Path

CTO_ROOT = Path(os.environ.get("CTO_LIBRARY_PATH", "C:/PIDv4-CTO"))

# Category → list of (display_name, symbol_filename) pairs
CTO_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "ACTUATORS": [
        ("Bellows Spring", "ACT-BELLOWS_SPRING"),
        ("Motor", "ACT-MOTOR"),
        ("Solenoid", "ACT-SOLENOID"),
        ("Spring Diaphragm", "ACT-SPRING_DIAPHRAGM"),
    ],
    "ANNOTATION": [
        ("Equipment Tag", "ANNOT-EQUIP_TAG"),
        ("Equipment Description", "ANNOT-EQUIP_DESCR"),
        ("Flow Arrow", "ANNOT-FLOWARROW"),
        ("Line Number", "ANNOT-LINE_NUMBER"),
    ],
    "EQUIPMENT": [
        ("Clarifier", "EQUIP-CLARIFIER"),
        ("Filter", "EQUIP-FILTER"),
        ("Filter Press", "EQUIP-FILTER_PRESS"),
        ("Heat Exchanger", "EQUIP-HEAT_EXCH-GENERIC"),
        ("Motor", "EQUIP-MOTOR"),
        ("Screen Bar", "EQUIP-SCREENBAR"),
    ],
    "PUMPS-BLOWERS": [
        ("Centrifugal Pump 1", "PUMP-CENTRIF1"),
        ("Centrifugal Pump 2", "PUMP-CENTRIF2"),
        ("Diaphragm Pump", "PUMP-DIAPHRAGM"),
        ("Metering Pump", "PUMP-METERING"),
        ("Progressive Cavity", "PUMP-PROGRESSIVE_CAVITY"),
        ("Submersible Pump", "PUMP-SUBMERSIBLE"),
    ],
    "TANKS": [
        ("Vertical Open", "TANK-VERTICAL_OPEN"),
        ("Vertical Dome", "TANK-VERTICAL_DOME"),
        ("Horizontal", "TANK-HORIZONTAL"),
        ("Cone Bottom", "TANK-CONE_BOTTOM_DOME"),
    ],
    "VALVES": [
        ("Gate Valve", "VA-GATE"),
        ("Globe Valve", "VA-GLOBE"),
        ("Check Valve", "VA-CHECK"),
        ("Ball Valve", "VA-BALL"),
        ("Butterfly Valve", "VA-BUTTERFLY"),
        ("Knife Gate", "VA-KNIFEGATE"),
    ],
}


def list_categories() -> list[str]:
    """Return available CTO categories."""
    if CTO_ROOT.exists():
        return sorted(d.name for d in CTO_ROOT.iterdir() if d.is_dir())
    return sorted(CTO_CATEGORIES.keys())


def list_symbols(category: str) -> list[str]:
    """Return symbol names for a category (from disk if available)."""
    cat_dir = CTO_ROOT / category
    if cat_dir.exists():
        return sorted(f.stem for f in cat_dir.glob("*.dwg"))
    return [s[1] for s in CTO_CATEGORIES.get(category, [])]


def symbol_path(category: str, symbol: str) -> Path:
    """Return full path to a CTO symbol .dwg file."""
    return CTO_ROOT / category / f"{symbol}.dwg"


def symbol_dxf_path(category: str, symbol: str, cache_dir: Path | None = None) -> Path:
    """Return path to cached DXF version of a CTO symbol."""
    if cache_dir is None:
        cache_dir = CTO_ROOT / "_dxf_cache"
    return cache_dir / category / f"{symbol}.dxf"
