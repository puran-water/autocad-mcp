import math

import pytest

from autocad_mcp.revision import (
    RevisionError,
    cloud_covers_bounds,
    expected_state,
    normalize_cloud,
    normalize_ops,
)


L_SHAPE = [[0, 0], [8, 0], [8, 2], [2, 2], [2, 8], [0, 8]]


def cloud(**kwargs):
    return dict(kind="cloud", item=1, arc=0.4, text_height=0.3, **kwargs)


def test_legacy_rectangle_and_clockwise_outline_have_outward_winding():
    rect = normalize_cloud(cloud(min=[0, 0], max=[8, 2]))
    assert rect["points"] == [[0, 0], [8, 0], [8, 2], [0, 2]]
    result = normalize_cloud(cloud(points=list(reversed(L_SHAPE)), marker_position=[10, 3]))
    assert result["points"] == L_SHAPE
    assert result["marker_position"] == [10, 3]
    assert result["min"] == [0, 0] and result["max"] == [8, 8]


@pytest.mark.parametrize(
    "fields,error",
    [
        ({"points": [[0, 0], [8, 0], [7, 2], [0, 2]]}, "ORTHOGONAL"),
        ({"points": [[0, 0], [8, 0], [8, 4], [4, 4], [4, -2], [0, -2]]}, "INTERSECTION"),
        ({"points": [[0, 0], [8, 0], [4, 0], [4, 2], [0, 2]]}, "INTERSECTION"),
        ({"min": [1, 0], "max": [0, 2]}, "BOUNDS"),
        ({"min": [0, 0], "max": [1000, 1000]}, "TOO_COMPLEX"),
        ({"points": L_SHAPE, "marker_position": [math.nan, 0]}, "FINITE"),
    ],
)
def test_bad_cloud_rejected_before_dispatch(fields, error):
    with pytest.raises(RevisionError, match=error):
        normalize_ops([cloud(**fields)])


def test_polygon_coverage_excludes_notch_and_accepts_boundary():
    assert cloud_covers_bounds(L_SHAPE, [0, 0], [8, 2])
    assert cloud_covers_bounds(L_SHAPE, [0, 1], [2, 8])
    assert not cloud_covers_bounds(L_SHAPE, [3, 3], [4, 4])
    assert not cloud_covers_bounds(L_SHAPE, [1, 1], [4, 4])
    # All four target corners are inside this U; its interior crosses the notch.
    u = [[0, 0], [8, 0], [8, 8], [6, 8], [6, 2], [2, 2], [2, 8], [0, 8]]
    assert not cloud_covers_bounds(u, [1, 1], [7, 7])
    with pytest.raises(RevisionError, match="TARGET_BOUNDS"):
        cloud_covers_bounds(L_SHAPE, [0, 0], [math.nan, 1])


def test_expected_state_matches_lisp_assoc_for_repeated_vertices():
    entity = {"dxf": [[0, "LWPOLYLINE"], [10, [1, 2]], [10, [8, 9]], [8, "PIPING"]]}
    assert expected_state(entity) == {"0": "LWPOLYLINE", "8": "PIPING", "10": [1, 2]}
