import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import (OWNER, PrivateScope, ScopeError, ScopedTransport,
                  MessageCursor, ChatModel, PrivateRunner, persona_for, verify_api, prepare_api_config)


def message(text, sender=OWNER, kind="text"):
    return SimpleNamespace(sender=sender, content=text, type=kind)


def parse(title):
    return (title[:-3], True, 3) if title.endswith("(3)") else (title, False, None)


class Tests(unittest.TestCase):
    def test_api_validation_distinguishes_authentication_and_model_selection(self):
        cfg = {"endpoint": "https://api.deepseek.com/chat/completions", "model": "test-model",
               "api_key": "sk-synthetic-test-credential"}
        with patch("requests.get", return_value=Mock(status_code=401)) as get:
            ok, text = verify_api(cfg)
            self.assertFalse(ok)
            self.assertIn("401", text)
            self.assertNotIn(cfg["api_key"], text)
            self.assertEqual(get.call_args.args[0], "https://api.deepseek.com/models")
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"id": "other-model"}]}
        with patch("requests.get", return_value=response):
            ok, text = verify_api(cfg)
            self.assertFalse(ok)
            self.assertIn("认证成功", text)
            response.json.return_value = {"data": [{"id": "test-model"}]}
            self.assertTrue(verify_api(cfg)[0])

    def test_copy_errors_fail_before_network_without_exposing_secret(self):
        base = {"endpoint": "https://api.deepseek.com/chat/completions", "model": "test-model"}
        examples = [("sk-synthetic复制", "非 ASCII"), ("sk-synthetic\ntext", "换行"),
                    ("sk-***********", "遮挡"),
                    ("00000000-0000-0000-0000-000000000000", "Tracking ID")]
        with patch("requests.get") as get:
            for key, expected in examples:
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(RuntimeError, expected) as error:
                        verify_api(dict(base, api_key=key))
                    self.assertNotIn(key, str(error.exception))
            get.assert_not_called()
        self.assertEqual(prepare_api_config(dict(base, api_key="  sk-synthetic-test  "))["api_key"], "sk-synthetic-test")
        other = dict(base, endpoint="https://example.test/v1/chat/completions",
                     api_key="00000000-0000-0000-0000-000000000000")
        self.assertEqual(prepare_api_config(other)["api_key"], other["api_key"])

    def transport(self, allowed=True):
        raw = Mock()
        raw.read_title.return_value = OWNER
        raw.get_messages.return_value = [message("新消息"), message("old.txt", kind="file"),
                                         message("自己说的", sender="self")]
        raw._current_title, raw._current_is_group = OWNER, False
        raw.send_text.return_value = True
        self.raw = raw
        return ScopedTransport(raw, PrivateScope(), parse, lambda: allowed)

    def test_owner_is_unique(self):
        with self.assertRaises(ValueError):
            PrivateScope({OWNER: "friend"})
        with self.assertRaises(ValueError):
            PrivateScope({OWNER: "owner", "小林": "owner"})
        self.assertEqual(PrivateScope({OWNER: "owner", "小林": "friend"}).check("小林", False), "friend")

    def test_no_group_unknown_or_fuzzy_match(self):
        scope = PrivateScope()
        for name, group in ((OWNER, True), (OWNER, None), ("孤月 大人", False), ("", False), ("其他人", False)):
            with self.assertRaises(ScopeError):
                scope.check(name, group)

    def test_wrong_chat_body_is_not_read(self):
        transport = self.transport()
        self.raw.read_title.return_value = "其他人"
        with self.assertRaises(ScopeError):
            transport.read()
        self.raw.get_messages.assert_not_called()

    def test_group_body_is_not_read(self):
        transport = self.transport()
        self.raw.read_title.return_value = OWNER + "(3)"
        with self.assertRaises(ScopeError):
            transport.read()
        self.raw.get_messages.assert_not_called()

    def test_only_text_and_no_contact_switch(self):
        transport = self.transport()
        contact, messages = transport.read()
        self.assertEqual([m.content for m in messages], ["新消息"])
        self.raw.get_messages.assert_called_once_with(None, assume_switched=True)
        self.raw._switch_chat.assert_not_called()

    def test_chat_change_during_read_discarded(self):
        transport = self.transport()
        self.raw._current_title = "其他人"
        with self.assertRaises(ScopeError):
            transport.read()

    def test_send_rechecks_destination_and_pause(self):
        transport = self.transport(False)
        with self.assertRaises(ScopeError):
            transport.send(OWNER, "你好")
        self.raw.send_text.assert_not_called()
        transport = self.transport()
        self.raw.read_title.return_value = "其他人"
        with self.assertRaises(ScopeError):
            transport.send(OWNER, "你好")
        self.raw.send_text.assert_not_called()

    def test_ambiguous_send_fails_without_retry(self):
        transport = self.transport()
        self.raw.send_text.return_value = False
        with self.assertRaises(ScopeError):
            transport.send(OWNER, "你好")
        self.raw.send_text.assert_called_once()

    def test_startup_and_identical_window_are_not_new(self):
        cursor = MessageCursor()
        self.assertEqual(cursor.take(OWNER, [message("旧消息")]), [])
        self.assertEqual(cursor.take(OWNER, [message("旧消息")]), [])

    def test_append_and_repeated_text_occurrences(self):
        cursor = MessageCursor()
        cursor.seed(OWNER, [message("a")])
        new = cursor.take(OWNER, [message("a"), message("a")])
        self.assertEqual(len(new), 1)

    def test_scroll_and_ocr_correction_do_not_replay_history(self):
        cursor = MessageCursor()
        cursor.seed(OWNER, [message("old1"), message("old2")])
        self.assertEqual(cursor.take(OWNER, [message("old2"), message("new")])[-1].content, "new")
        self.assertEqual(cursor.take(OWNER, [message("unrelated old history")]), [])
        self.assertEqual(cursor.take(OWNER, [message("OCR correction")]), [])

    def test_new_message_after_empty_window_and_contact_isolation(self):
        cursor = MessageCursor()
        cursor.seed(OWNER, [])
        self.assertEqual(cursor.take(OWNER, [message("new")])[0].content, "new")
        self.assertEqual(cursor.take("朋友", [message("old")]), [])

    def test_model_isolation_http_payload_and_preview_does_not_save(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / "persona.txt"
            persona.write_text("阿澄", encoding="utf-8")
            scope = PrivateScope({OWNER: "owner", "小林": "friend"})
            model = ChatModel({"model": "test-model", "endpoint": "https://example.test/chat/completions",
                               "api_key": "synthetic-test"}, scope, persona, root / "memory.json")
            model.history = {OWNER: [{"role": "user", "content": "owner-private-test"}]}
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": "朋友你好"}}]}
            with patch("requests.post", return_value=response) as post:
                self.assertEqual(model.reply("小林", "你好"), "朋友你好")
                payload = post.call_args.kwargs["json"]
                self.assertNotIn("owner-private-test", json.dumps(payload))
                self.assertIn("不是孤月大人", payload["messages"][0]["content"])
            self.assertFalse((root / "memory.json").exists())
            model.remember("小林", "你好", "朋友你好")
            restored = ChatModel(model.cfg, scope, persona, root / "memory.json")
            self.assertEqual(restored.history["小林"][-1]["content"], "朋友你好")

    def test_http_failure_never_echoes_server_body_or_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            persona = root / "persona.txt"
            persona.write_text("阿澄", encoding="utf-8")
            model = ChatModel({"model": "test", "endpoint": "https://example.test/chat/completions", "api_key": "secret-synthetic"},
                PrivateScope(), persona, root / "memory.json")
            with patch("requests.post", return_value=Mock(status_code=401, text="sensitive-server-body")):
                with self.assertRaisesRegex(RuntimeError, "HTTP 401") as error:
                    model.reply(OWNER, "你好")
                self.assertNotIn("sensitive-server-body", str(error.exception))
                self.assertNotIn("secret-synthetic", str(error.exception))

    def run_one_turn(self, folder, during_generation=None):
        class OneTick:
            calls = 0
            def wait(self, timeout):
                self.calls += 1
                return self.calls > 1
            def is_set(self):
                return False
        transport = Mock()
        transport.read.side_effect = [(OWNER, [message("old")]),
                                     (OWNER, [message("old"), message("new")])]
        model = Mock()
        runner = None
        def notify(kind, text):
            if kind == "ready":
                runner.resume("preview")
        runner = PrivateRunner(transport, model, Path(folder) / "preview.jsonl", notify,
                               {OWNER: "owner"}, stop_event=OneTick())
        def reply(*args, **kwargs):
            if during_generation:
                during_generation(runner)
            return "synthetic-reply"
        model.reply.side_effect = reply
        runner.run()
        return transport, model

    def test_preview_writes_once_without_sending_or_formal_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            transport, model = self.run_one_turn(folder)
            transport.send.assert_not_called()
            model.remember.assert_not_called()
            records = (Path(folder) / "preview.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0])["input"], "new")

    def test_pause_then_resume_live_cannot_send_previous_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            def change_mode(runner):
                runner.pause()
                runner.resume("live")
            transport, model = self.run_one_turn(folder, change_mode)
            transport.send.assert_not_called()
            model.remember.assert_not_called()
            self.assertFalse((Path(folder) / "preview.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
