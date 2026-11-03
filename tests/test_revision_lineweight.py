import pytest

from autocad_mcp.revision import RevisionError, normalize_ops


def patch(weight):
    return {
        "kind": "patch",
        "item": 1,
        "handle": "ABCD",
        "expect": {"0": "LINE", "370": None},
        "set": {"370": weight},
    }


@pytest.mark.parametrize("weight", [-3, -2, -1, 0, 13, 35, 211])
def test_standard_weights_and_inheritance_are_supported(weight):
    assert normalize_ops([patch(weight)])[0]["set"][370] == weight


@pytest.mark.parametrize("weight", [True, 0.13, 13.0, "13", None, -4, 14, 212])
def test_invalid_native_lineweight_is_rejected_before_dispatch(weight):
    with pytest.raises(RevisionError, match="INVALID_DXF_LINEWEIGHT"):
        normalize_ops([patch(weight)])
