import math

import pytest

from autocad_mcp.revision import RevisionError, normalize_ops


def patch(code, value):
    return {
        "kind": "patch", "item": 1, "handle": "ABCD",
        "expect": {"0": "LINE", str(code): None}, "set": {str(code): value},
    }


@pytest.mark.parametrize("name", ["ByLayer", "ByBlock", "Continuous", "Electric_signal_1"])
def test_linetype_names_preserve_explicit_absent_precondition(name):
    op = normalize_ops([patch(6, name)])[0]
    assert op["set"][6] == name and op["expect"][6] is None


@pytest.mark.parametrize("name", [None, True, 6, "", " \t\n"])
def test_invalid_names_are_rejected_before_dispatch(name):
    with pytest.raises(RevisionError, match="INVALID_DXF_LINETYPE"):
        normalize_ops([patch(6, name)])


@pytest.mark.parametrize("scale", [0, -1, True, "1", None, math.nan, math.inf, -math.inf])
def test_invalid_scale_is_rejected_before_dispatch(scale):
    with pytest.raises(RevisionError, match="INVALID_DXF_LINETYPE_SCALE"):
        normalize_ops([patch(48, scale)])


@pytest.mark.parametrize("scale", [.1, 1, 5.0])
def test_positive_entity_scale_is_supported(scale):
    assert normalize_ops([patch(48, scale)])[0]["set"][48] == scale


@pytest.mark.parametrize("code,value", [(6, "Continuous"), (48, 1)])
def test_style_edit_without_old_value_is_refused(code, value):
    op = patch(code, value)
    del op["expect"][str(code)]
    with pytest.raises(RevisionError, match="EXPECTED_FIELD_REQUIRED"):
        normalize_ops([op])
