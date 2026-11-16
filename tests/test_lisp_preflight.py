from unittest.mock import AsyncMock

import pytest

from autocad_mcp.backends.base import CommandResult
from autocad_mcp.backends.file_ipc import FileIPCBackend
from autocad_mcp.lisp_preflight import LispPreflightError, validate


@pytest.mark.parametrize("code", [
    '(pcad:run \'(lambda () "done")) nil)',
    '(pcad:run \'(lambda () "done")))',
    '(pcad:run \'(lambda () "done"))',
    '(pcad:run \'(lambda () "done") nil extra)',
    '(progn (entdel e)) (',
    "'(a b",
    "(list ')",
    '(princ "unterminated)',
    ';| unfinished comment',
    '; comment only',
    '',
])
async def test_invalid_source_never_writes_or_dispatches(tmp_path, monkeypatch, code):
    backend = FileIPCBackend(transport="legacy")
    backend._ipc_dir = tmp_path
    dispatch = AsyncMock()
    monkeypatch.setattr(backend, "_dispatch", dispatch)
    result = await backend.execute_lisp(code)
    assert not result.ok and result.error.startswith("LISP_PREFLIGHT_")
    assert result.payload == {"dispatched": False}
    dispatch.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("code", [
    '(pcad:run \'(lambda () "done") nil)',
    '(pcad:run (quote (lambda (/ x) (setq x 1) x)) nil)',
    '(progn (load "C:/Temp/library.lsp") (pcad:run \'pcad:batch \'("drawing")))',
    '(list "parentheses () and ; and \\\" quotes") ; unmatched ) in comment\n(princ)',
    ';| outer ( ;| nested ) |; still outer |; (princ)',
    "'(pcad:run)",
    '(quote (pcad:run))',
    "'((0 . \"LINE\") (10 1.0 2.0 0.0))",
])
def test_valid_reader_structure(code):
    validate(code)


async def test_valid_source_is_preserved_and_dispatched_once(tmp_path, monkeypatch):
    backend = FileIPCBackend(transport="legacy")
    backend._ipc_dir = tmp_path
    dispatch = AsyncMock(return_value=CommandResult(ok=True, payload="done"))
    monkeypatch.setattr(backend, "_dispatch", dispatch)
    code = '(pcad:run \'(lambda () "done") nil)'
    assert (await backend.execute_lisp(code)).ok
    dispatch.assert_awaited_once()
    assert next(tmp_path.iterdir()).read_text() == code


def test_unknown_functions_are_not_claimed_to_be_compiled():
    validate('(unknown-native-function 1)')


def test_unclosed_form_is_reported_without_disclosing_source():
    with pytest.raises(LispPreflightError) as failure:
        validate('(princ "private drawing text") (')
    assert "private" not in str(failure.value)
