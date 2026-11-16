"""Read-only structural preflight before sending freehand AutoLISP to a DWG.

This is not an AutoLISP compiler or a transaction guarantee. It catches broken
reader syntax and the guarded runner's known arity before any native dispatch.
"""


class LispPreflightError(ValueError):
    pass


class _Symbol(str):
    pass


def validate(code: str) -> None:
    if not isinstance(code, str) or not code.strip():
        raise LispPreflightError("LISP_PREFLIGHT_EMPTY")
    tokens = []
    i = 0
    while i < len(code):
        c = code[i]
        if c.isspace():
            i += 1
        elif code.startswith(";|", i):
            depth = 1
            i += 2
            while i < len(code) and depth:
                if code.startswith(";|", i):
                    depth += 1
                    i += 2
                elif code.startswith("|;", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise LispPreflightError("LISP_PREFLIGHT_UNTERMINATED_COMMENT")
        elif c == ";":
            end = code.find("\n", i)
            i = len(code) if end < 0 else end + 1
        elif c == '"':
            i += 1
            while i < len(code):
                if code[i] == "\\":
                    i += 2
                elif code[i] == '"':
                    i += 1
                    tokens.append(("string", ""))
                    break
                else:
                    i += 1
            else:
                raise LispPreflightError("LISP_PREFLIGHT_UNTERMINATED_STRING")
        elif c in "()'":
            tokens.append((c, c))
            i += 1
        else:
            start = i
            while i < len(code) and not code[i].isspace() and code[i] not in "()'\";":
                i += 1
            tokens.append(("symbol", code[start:i]))

    cursor = 0

    def read():
        nonlocal cursor
        if cursor == len(tokens):
            raise LispPreflightError("LISP_PREFLIGHT_INCOMPLETE_FORM")
        kind, value = tokens[cursor]
        cursor += 1
        if kind == "(":
            result = []
            while cursor < len(tokens) and tokens[cursor][0] != ")":
                result.append(read())
            if cursor == len(tokens):
                raise LispPreflightError("LISP_PREFLIGHT_UNCLOSED_LIST")
            cursor += 1
            return result
        if kind == ")":
            raise LispPreflightError("LISP_PREFLIGHT_UNEXPECTED_CLOSE")
        if kind == "'":
            return [_Symbol("quote"), read()]
        return _Symbol(value) if kind == "symbol" else value

    def check(form):
        if not isinstance(form, list) or not form:
            return
        head = form[0].lower() if isinstance(form[0], _Symbol) else None
        if head == "quote":
            return  # Quoted data is not an evaluated function call.
        if head == "pcad:run" and len(form) != 3:
            raise LispPreflightError("LISP_PREFLIGHT_PCAD_RUN_REQUIRES_TWO_ARGUMENTS")
        for child in form:
            check(child)

    try:
        forms = []
        while cursor < len(tokens):
            forms.append(read())
        if not forms:
            raise LispPreflightError("LISP_PREFLIGHT_EMPTY")
        for form in forms:
            check(form)
    except RecursionError as exc:
        raise LispPreflightError("LISP_PREFLIGHT_NESTING_TOO_DEEP") from exc
