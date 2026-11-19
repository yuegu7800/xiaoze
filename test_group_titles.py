"""Synthetic group rename/count-marker checks; no native backend imports."""
import ast
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock

from core import OWNER, PrivateScope, ScopedTransport, TitleScopeError
from groups import DEFAULT_GROUP, GroupScope
from sessions import MultiRunner
from test_core import message
from visual_adapter import compatible_title_parser, current_chat_backend


# Extract only the existing parser and its regex, not any upstream initializer.
source = Path(r"C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\xiaoli\xiaoli_desktop\wx_backend\visual_regions.py")
tree = ast.parse(source.read_text(encoding="utf-8"))
definitions = [node for node in tree.body if
    isinstance(node, ast.FunctionDef) and node.name == "parse_title" or
    isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_TITLE_GROUP_RE" for t in node.targets)]
namespace = {"re": re}
exec(compile(ast.Module(body=definitions, type_ignores=[]), "<isolated-title-parser>", "exec"), namespace)
parse_title = namespace["parse_title"]
analyze_blocks = lambda *a, **kw: None  # reader-copy dependency; never called


class FakeBase:
    def __init__(self):
        self.current = OWNER
        self.group = "虚构改名后的群"
        self.titles = {OWNER: OWNER, self.group: self.group + "（ 3 ）"}
        self.frames = {OWNER: [], self.group: []}
        self._session_coords = {}
        self.selected = []
        self.sent = []
        self.connect = self.close = lambda: None

    def read_title(self, foreground=False):
        return self.titles[self.current]

    def _switch_chat(self, chat, force=False):
        self.selected.append(chat)
        self.current = chat
        return True

    def get_messages(self, chat, assume_switched=True):
        # Just as the real joint OCR reader does: parse again and set cache.
        self._current_title, self._current_is_group, _ = parse_title(self.read_title())
        return self.frames[self.current]

    def send_text(self, target, text):
        self.sent.append((target, text))
        return True


class GroupTitleTests(unittest.TestCase):
    def setUp(self):
        self.parse = compatible_title_parser(parse_title)
        self.group = "虚构改名后的群"

    def backend(self):
        scopes = {OWNER: PrivateScope(), self.group: GroupScope(self.group)}
        return current_chat_backend(FakeBase, self.parse, scopes)()

    def test_fullwidth_failure_reproduced_and_count_formats_use_exact_name(self):
        self.assertFalse(parse_title(self.group + "（3）")[1])
        for suffix in ("(3)", "（3）", "（ 3 ）", " ( 3 )", "（125）"):
            with self.subTest(suffix=suffix):
                name, is_group, count = self.parse(self.group + suffix)
                self.assertEqual(name, self.group)
                self.assertIs(is_group, True)
                self.assertIn(count, (3, 125))
        self.assertFalse(parse_title(self.group + "（3）")[1])  # upstream unchanged

    def test_no_marker_mismatched_parentheses_or_name_fuzzing(self):
        for raw in (self.group, self.group + "（3)", self.group + "(人数)",
                    self.group + "（3）其他", self.group + "(3)消息"):
            with self.subTest(raw=raw):
                self.assertFalse(self.parse(raw)[1])
        self.assertEqual(self.parse("虚构 改名后的群（3）")[0], "虚构 改名后的群")

    def test_configured_renamed_group_routes_and_receives_alongside_private(self):
        backend = self.backend()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / "persona.txt"
            persona.write_text("小泽，温柔尊重孤月大人。", encoding="utf-8")
            cfg = {"group": self.group, "group_nickname": "小泽", "group_behavior": "mention",
                   "group_frequency": "active", "api_key": "synthetic"}
            runner = MultiRunner(backend, cfg, persona, root, self.parse, lambda *_: None)
            for name in runner.sessions:
                runner.poll_session(name)
                self.assertTrue(runner.sessions[name].ready)
            runner.ready = True
            runner.resume("live")
            group = runner.sessions["group"]
            private = runner.sessions["private"]
            group.group_policy.quiet_seconds = 0
            group.model.reply = Mock(return_value="虚构群回复")
            private.model.reply = Mock(return_value="虚构私聊回复")
            backend.frames[self.group].append(message("小泽在吗", "虚构朋友"))
            backend.frames[OWNER].append(message("私聊测试"))
            runner.poll_session("group")
            runner.poll_session("private")
            self.assertEqual(backend.sent, [(self.group, "虚构群回复"), (OWNER, "虚构私聊回复")])
            self.assertNotIn(DEFAULT_GROUP, backend.selected)
            self.assertEqual(runner.snapshot()["sessions"]["group"]["bound_group"], self.group)

    def test_reader_uses_shared_parser_without_modifying_original_globals(self):
        backend = self.backend()
        backend.select_target(self.group)
        transport = ScopedTransport(backend, GroupScope(self.group), self.parse)
        self.assertEqual(transport.read(), (self.group, []))
        self.assertIs(backend._current_is_group, True)
        self.assertIs(FakeBase.get_messages.__globals__["parse_title"], parse_title)
        self.assertIs(backend.get_messages.__func__.__globals__["parse_title"], self.parse)

    def test_name_same_but_missing_marker_rejected_before_read_or_send(self):
        backend = self.backend()
        backend.current = self.group
        backend.titles[self.group] = self.group
        transport = ScopedTransport(backend, GroupScope(self.group), self.parse)
        backend.get_messages = Mock(side_effect=AssertionError("must not read"))
        for operation in (transport.read, lambda: transport.send(self.group, "不能发送")):
            with self.assertRaises(TitleScopeError) as error:
                operation()
            self.assertEqual(error.exception.reason, "group_marker_missing")
        backend.get_messages.assert_not_called()
        self.assertEqual(backend.sent, [])

    def test_different_group_name_and_joint_frame_failure_have_distinct_phases(self):
        backend = self.backend()
        backend.current = self.group
        transport = ScopedTransport(backend, GroupScope(self.group), self.parse)
        backend.titles[self.group] = "虚构别的群（3）"
        with self.assertRaises(TitleScopeError) as error:
            transport.read()
        self.assertEqual((error.exception.reason, error.exception.phase), ("group_name_mismatch", "title_before"))
        self.assertNotIn("虚构别的群", str(error.exception))
        backend.titles[self.group] = self.group + "（3）"
        def incomplete_frame(*a, **kw):
            backend._current_title, backend._current_is_group = self.group, False
            return []
        backend.get_messages = incomplete_frame
        with self.assertRaises(TitleScopeError) as error:
            transport.read()
        self.assertEqual((error.exception.reason, error.exception.phase), ("group_marker_missing", "message_frame"))


if __name__ == "__main__":
    unittest.main()
