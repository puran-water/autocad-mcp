import math

import pytest

from autocad_mcp.revision import RevisionError, compile_call, normalize_ops


def operation():
    return dict(kind="rotate", item=1, handle="abc", target=0,
                expect={"0": "INSERT", "10": [12, -3, 0], "50": math.pi / 2,
                        "210": [0, 0, 1], "41": -1.83, "42": 1.83, "43": 1.83})


def test_compile_preserves_absolute_target_anchor_and_mirror_guards():
    op = operation()
    normalized = normalize_ops([op])[0]
    assert normalized["target"] == 0
    assert normalized["expect"][10] == [12, -3, 0]
    assert normalized["expect"][41] == -1.83
    assert normalized["expect"][50] == math.pi / 2
    code = compile_call("batch", dict(drawing="C:/test.dwg", id="rotation",
                        journal="C:/rotation.json", operations=[op]))
    assert '"rotate"' in code and '"ABC"' in code


@pytest.mark.parametrize("code", [10, 50, 210])
def test_missing_anchor_rotation_or_normal_refused(code):
    op = operation(); del op["expect"][str(code)]
    with pytest.raises(RevisionError, match="EXPECTED_ROTATION_ANCHOR_NORMAL_REQUIRED"):
        normalize_ops([op])


@pytest.mark.parametrize("angle", [None, True, "0", -0.1, math.tau, math.nan, math.inf])
@pytest.mark.parametrize("field", ["target", "old"])
def test_invalid_angles_refused(angle, field):
    op = operation()
    if field == "old":
        op["expect"]["50"] = angle
    else:
        op["target"] = angle
    with pytest.raises(RevisionError, match="ROTATION_RADIANS_0_TO_2PI_REQUIRED"):
        normalize_ops([op])


@pytest.mark.parametrize("anchor", [None, [0, 0], [0, math.nan, 0], [True, 0, 0]])
def test_invalid_anchors_refused(anchor):
    op = operation(); op["expect"]["10"] = anchor
    with pytest.raises(RevisionError, match="PLANAR_INSERT_REQUIRED"):
        normalize_ops([op])


def test_nonplanar_and_noninsert_targets_refused():
    op = operation(); op["expect"]["210"] = [0, 1, 0]
    with pytest.raises(RevisionError, match="PLANAR_INSERT_REQUIRED"):
        normalize_ops([op])
    op = operation(); op["expect"]["0"] = "TEXT"
    with pytest.raises(RevisionError, match="INSERT_REQUIRED"):
        normalize_ops([op])
