from types import SimpleNamespace
import pytest
from autocad_mcp.native_input import post_expression


class Windows:
    def __init__(self):
        self.target = 20
        self.posts = []
        self.disabled = set()
        self.dialogs = []
        self.fail_at = None

    def IsWindow(self, h):
        return h in (10, self.target)

    def IsWindowEnabled(self, h):
        return h not in self.disabled

    def IsWindowVisible(self, h):
        return True

    def GetWindowText(self, h):
        return "Autodesk AutoCAD LT - [test.dwg]"

    def GetClassName(self, h):
        return "MDIClient" if h == self.target else "#32770"

    def GetParent(self, h):
        return 10

    def EnumChildWindows(self, h, fn, data):
        fn(self.target, data)

    def EnumWindows(self, fn, data):
        for h in self.dialogs:
            fn(h, data)

    def PostMessage(self, *args):
        if len(self.posts) == self.fail_at:
            raise OSError("Access denied")
        self.posts.append(args)


PROC = SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 2))


def test_target_is_rediscovered_each_call_and_never_focuses():
    gui = Windows()
    first = post_expression(10, "(ping)", gui, PROC)
    gui.target = 30
    second = post_expression(10, "(ping)", gui, PROC)
    assert first["target"] == 20 and second["target"] == 30
    assert [p[2] for p in gui.posts[:7]] == list(map(ord, "(ping)\r"))
    assert all(p[1] == 0x0102 for p in gui.posts)
    assert 27 not in [p[2] for p in gui.posts]


@pytest.mark.parametrize("blocked", ["frame", "dialog", "child"])
def test_unavailable_window_never_posts(blocked):
    gui = Windows()
    if blocked == "frame":
        gui.disabled.add(10)
    if blocked == "child":
        gui.disabled.add(20)
    if blocked == "dialog":
        gui.dialogs.append(40)
    with pytest.raises(RuntimeError):
        post_expression(10, "(ping)", gui, PROC)
    assert gui.posts == []


def test_partial_delivery_raises_and_does_not_append_enter_or_retry():
    gui = Windows()
    gui.fail_at = 3
    with pytest.raises(RuntimeError, match="after 3 characters"):
        post_expression(10, "(ping)", gui, PROC)
    assert len(gui.posts) == 3 and 13 not in [p[2] for p in gui.posts]


def test_target_process_identity_required():
    gui = Windows()
    wrong = SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, h))
    with pytest.raises(RuntimeError, match="AMBIGUOUS"):
        post_expression(10, "(ping)", gui, wrong)
    assert not gui.posts
