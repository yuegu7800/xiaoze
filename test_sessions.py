import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from core import OWNER, ReadUnavailableError, ScopeError
from groups import DEFAULT_GROUP
from sessions import MultiRunner, RoutedTransport
from test_core import message, parse
from visual_adapter import exact_session_hit


class FakeDesktop:
    """Synthetic WeChat; never touches a window, clipboard or the network."""
    def __init__(self):
        self.current = OWNER
        self.frames = {OWNER: [message("私聊旧记录")],
                       DEFAULT_GROUP: [message("群聊旧记录", "朋友")]}
        self.missing = set()
        self.sent, self.selected = [], []
        self.connect, self.close = Mock(), Mock()

    def select_target(self, target):
        self.selected.append(target)
        if target in self.missing:
            raise ReadUnavailableError("模拟目标不在列表中")
        self.current = target

    def read_title(self, foreground=False):
        return self.current + "(3)" if self.current == DEFAULT_GROUP else self.current

    def get_messages(self, chat, assume_switched=True):
        self._current_title = self.current
        self._current_is_group = self.current == DEFAULT_GROUP
        return self.frames[self.current]

    def send_text(self, target, text):
        if self.current != target:
            raise AssertionError("跨会话发送")
        self.sent.append((target, text))
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        persona = root / "persona.txt"
        persona.write_text("阿澄，唯一主人是孤月大人。", encoding="utf-8")
        cfg = {"group": DEFAULT_GROUP, "group_nickname": "阿澄", "group_behavior": "mention",
               "group_frequency": "active", "api_key": "synthetic"}
        self.desktop = FakeDesktop()
        self.events = []
        self.runner = MultiRunner(self.desktop, cfg, persona, root, parse,
                                  lambda k, t: self.events.append((k, t)))
        self.private = self.runner.sessions["private"]
        self.group = self.runner.sessions["group"]
        self.group.group_policy.quiet_seconds = 0
        self.private.model.reply = Mock(return_value="私聊回复")
        self.group.model.reply = Mock(return_value="群聊回复")

    def connect(self, mode="live"):
        for name in self.runner.sessions:
            self.runner.poll_session(name)
        self.runner.ready = True
        self.runner.resume(mode)

    def test_both_sessions_receive_and_reply_with_separate_histories(self):
        self.connect()
        self.desktop.frames[OWNER].append(message("私聊新的问题"))
        self.desktop.frames[DEFAULT_GROUP].append(message("阿澄群聊的新问题", "朋友"))
        self.runner.poll_session("group")
        self.runner.poll_session("private")
        self.assertEqual(self.desktop.sent, [(DEFAULT_GROUP, "群聊回复"), (OWNER, "私聊回复")])
        private = self.private.model.memory_path.read_text(encoding="utf-8")
        group = self.group.model.memory_path.read_text(encoding="utf-8")
        self.assertNotIn("群聊的新问题", private)
        self.assertNotIn("私聊新的问题", group)
        self.assertNotIn("私聊旧记录", private)
        self.assertEqual(self.runner.snapshot()["received_messages"], 2)

    def test_send_routes_back_after_a_switch_during_generation(self):
        self.connect()
        def reply(*args, **kwargs):
            self.desktop.current = OWNER
            self.desktop.frames[OWNER].append(message("模型生成时发来的私聊"))
            return "群回复"
        self.group.model.reply.side_effect = reply
        self.desktop.frames[DEFAULT_GROUP].append(message("阿澄在吗", "朋友"))
        self.runner.poll_session("group")
        self.runner.poll_session("private")
        self.assertEqual(self.desktop.sent, [(DEFAULT_GROUP, "群回复"), (OWNER, "私聊回复")])
        self.private.model.reply.assert_called_once_with(OWNER, "模型生成时发来的私聊", proactive=False)

    def test_one_missing_target_does_not_stop_the_other(self):
        self.desktop.missing.add(OWNER)
        self.connect()
        self.assertFalse(self.private.ready)
        self.assertTrue(self.group.ready)
        self.assertTrue(self.runner.enabled.is_set())
        self.desktop.frames[DEFAULT_GROUP].append(message("阿澄看这条", "朋友"))
        self.runner.poll_session("group")
        self.assertEqual(self.desktop.sent, [(DEFAULT_GROUP, "群聊回复")])
        self.desktop.missing.clear()
        self.runner._retry_at["private"] = 0
        self.runner.poll_session("private")  # establish baseline on first successful visit
        self.desktop.frames[OWNER].append(message("恢复后的私聊"))
        self.runner.poll_session("private")
        self.assertEqual(self.desktop.sent[-1], (OWNER, "私聊回复"))

    def test_global_preview_pauses_both_and_sends_nothing(self):
        self.connect("preview")
        self.desktop.frames[OWNER].append(message("预览私聊"))
        self.desktop.frames[DEFAULT_GROUP].append(message("阿澄预览群聊", "朋友"))
        self.runner.poll_session("private")
        self.runner.poll_session("group")
        self.assertFalse(self.runner.enabled.is_set())
        self.assertTrue(all(not r.enabled.is_set() for r in self.runner.sessions.values()))
        self.group.model.reply.assert_not_called()
        self.assertEqual(self.desktop.sent, [])
        self.assertFalse(self.private.model.memory_path.exists())

    def test_manual_pause_cancels_generation_in_both_sessions(self):
        self.connect()
        self.private.model.reply.side_effect = lambda *a, **k: (self.runner.pause() or "不要发送")
        self.desktop.frames[OWNER].append(message("请暂停"))
        self.runner.poll_session("private")
        self.assertEqual(self.desktop.sent, [])
        self.assertFalse(self.runner.enabled.is_set())

    def test_send_failure_pauses_everything_without_retry(self):
        self.connect()
        self.desktop.send_text = Mock(return_value=False)
        self.desktop.frames[OWNER].append(message("一条新消息"))
        self.runner.poll_session("private")
        self.runner.poll_session("private")
        self.desktop.send_text.assert_called_once()
        self.assertFalse(self.runner.enabled.is_set())

    def test_wrong_destination_is_rejected_before_switching(self):
        self.connect()
        self.desktop.selected.clear()
        with self.assertRaises(ScopeError):
            self.private.transport.send(DEFAULT_GROUP, "不能串到群里")
        self.assertEqual(self.desktop.selected, [])
        self.assertEqual(self.desktop.sent, [])

    def test_wrong_conversation_type_is_rejected_after_switching(self):
        self.connect()
        self.desktop.read_title = lambda **kwargs: DEFAULT_GROUP  # a same-name private chat
        with self.assertRaises(ScopeError):
            self.group.transport.send(DEFAULT_GROUP, "不应该发送")
        self.assertEqual(self.desktop.sent, [])

    def test_run_owns_one_backend_connection_and_closes_once(self):
        class Ticks:
            count = 0
            def wait(self, delay):
                self.count += 1
                return self.count > 2
            def is_set(self):
                return False
        self.runner.cancel = Ticks()
        self.runner.run()
        self.desktop.connect.assert_called_once()
        self.desktop.close.assert_called_once()
        self.assertEqual(self.events[-1][0], "stopped")

    def test_exact_unique_list_label_required(self):
        items = [{"text": "孤月大人测试"}, {"text": "孤月大人"}]
        self.assertEqual(exact_session_hit(items, OWNER), items[1])
        self.assertIsNone(exact_session_hit(items[:1], OWNER))
        self.assertIsNone(exact_session_hit([items[1], items[1]], OWNER))

    def test_explicit_truncated_list_label_requires_a_unique_configured_target(self):
        truncated = {"text": "硬邦帮·大学牲学术..."}
        self.assertEqual(exact_session_hit([truncated], DEFAULT_GROUP), truncated)
        self.assertIsNone(exact_session_hit([truncated], DEFAULT_GROUP,
            [DEFAULT_GROUP, "硬邦帮·大学牲学术另一个群"]))
        self.assertIsNone(exact_session_hit([{"text": "硬邦帮·大学牲学术"}], DEFAULT_GROUP))
        self.assertIsNone(exact_session_hit([{"text": "硬邦..."}], DEFAULT_GROUP))

    def test_snapshot_does_not_include_bodies_or_api_key(self):
        self.connect()
        self.desktop.frames[OWNER].append(message("PRIVATE-SENTINEL"))
        self.runner.poll_session("private")
        encoded = json.dumps(self.runner.snapshot())
        self.assertNotIn("PRIVATE-SENTINEL", encoded)
        self.assertNotIn("api_key", encoded)


if __name__ == "__main__":
    unittest.main()
