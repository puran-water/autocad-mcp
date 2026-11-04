import pytest

from autocad_mcp.revision import RevisionError, normalize_ops


def operation():
    return dict(kind="attributes", item=1, handle="ABC", expect={"0": "INSERT"},
                attributes={"function": "AIC", "alarm": "AAH"},
                expected_attributes={"FUNCTION": "AI", "ALARM": ""})


def test_attribute_group_preserves_explicit_old_values_and_normalizes_tags():
    original = operation()
    result = normalize_ops([original])[0]
    assert result["attributes"] == {"FUNCTION": "AIC", "ALARM": "AAH"}
    assert result["expected_attributes"] == {"FUNCTION": "AI", "ALARM": ""}
    assert "function" in original["attributes"]


@pytest.mark.parametrize("field", ["attributes", "expected_attributes"])
@pytest.mark.parametrize("value", [{}, None, [], {str(i): "x" for i in range(17)}])
def test_empty_or_unbounded_maps_refused(field, value):
    op = operation(); op[field] = value
    with pytest.raises(RevisionError, match="ATTRIBUTE_MAP_1_TO_16_REQUIRED"):
        normalize_ops([op])


@pytest.mark.parametrize("field", ["attributes", "expected_attributes"])
@pytest.mark.parametrize("value", [{"A": None}, {"": "x"}, {"A": 1}, {1: "x"}])
def test_attribute_keys_and_values_are_strings(field, value):
    op = operation(); op[field] = value
    with pytest.raises(RevisionError, match="ATTRIBUTE_STRINGS_REQUIRED"):
        normalize_ops([op])


def test_all_changed_attributes_need_exact_old_value_set():
    op = operation(); del op["expected_attributes"]["ALARM"]
    with pytest.raises(RevisionError, match="EXPECTED_ATTRIBUTE_REQUIRED"):
        normalize_ops([op])


def test_case_aliases_cannot_write_one_attribute_twice():
    op = operation(); op["attributes"] = {"alarm": "A", "ALARM": "B"}
    with pytest.raises(RevisionError, match="ATTRIBUTE_TAG_NOT_UNIQUE"):
        normalize_ops([op])


def test_noninsert_target_refused():
    op = operation(); op["expect"]["0"] = "LINE"
    with pytest.raises(RevisionError, match="INSERT_REQUIRED"):
        normalize_ops([op])
