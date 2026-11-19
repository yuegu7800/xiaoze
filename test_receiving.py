import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from core import MessageCursor, OWNER, PrivateRunner, ReadUnavailableError, ScopeError, TitleScopeError
from test_core import message


class ReceivingTests(unittest.TestCase):
    def test_title_diagnostic_phase_is_fixed_metadata_and_clears(self):
        with tempfile.TemporaryDirectory() as folder:
            transport = Mock()
            transport.read.side_effect = [TitleScopeError(reason='group_marker_missing', phase='message_frame'),
                                         (OWNER, [])]
            runner = PrivateRunner(transport, Mock(), Path(folder) / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
            self.assertIsNone(runner.read_available())
            self.assertIn('人数标记', runner.snapshot()['read_issue'])
            self.assertEqual(runner.snapshot()['read_phase'], 'message_frame')
            runner.read_available()
            self.assertEqual(runner.snapshot()['read_phase'], '')

    def test_read_issue_is_fixed_metadata_and_clears_on_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            transport = Mock()
            transport.read.side_effect = [ReadUnavailableError('private sentinel', reason='session_row_missing'),
                                         (OWNER, [])]
            runner = PrivateRunner(transport, Mock(), Path(folder) / 'preview.jsonl', lambda *_: None, {OWNER: 'owner'})
            self.assertIsNone(runner.read_available())
            self.assertIn('会话列表', runner.snapshot()['read_issue'])
            self.assertNotIn('private sentinel', json.dumps(runner.snapshot()))
            runner.read_available()
            self.assertEqual(runner.snapshot()['read_issue'], '')

    def test_empty_ocr_frame_cannot_replay_old_messages(self):
        cursor = MessageCursor()
        old = [message("第一条历史"), message("第二条历史")]
        cursor.seed(OWNER, old)
        self.assertEqual(cursor.take(OWNER, []), [])
        self.assertEqual(cursor.take(OWNER, old), [])
        self.assertEqual([m.content for m in cursor.take(OWNER, old + [message("真正的新文字")])], ["真正的新文字"])

    def test_ocr_whitespace_does_not_hide_a_new_tail(self):
        cursor = MessageCursor()
        cursor.seed(OWNER, [message("旧的文字"), message("阿澄你好")])
        new = cursor.take(OWNER, [message("旧 的 文 字"), message("阿澄 你好"), message("这一条是新的")])
        self.assertEqual([m.content for m in new], ["这一条是新的"])

    def test_small_ocr_correction_with_exact_anchor_preserves_new_message(self):
        cursor = MessageCursor()
        cursor.seed(OWNER, [message("同一个锚点"), message("昨天我们讨论的是那份数学作业的最后一道题目")])
        new = cursor.take(OWNER, [message("同一个锚点"), message("昨天我们讨论的是那份数学作业的最后一道题日"), message("阿澄你在吗")])
        self.assertEqual([m.content for m in new], ["阿澄你在吗"])

    def test_fuzzy_match_alone_or_different_sender_never_replays_a_screen(self):
        cursor = MessageCursor()
        old = "昨天我们讨论的是那份数学作业的最后一道题目"
        cursor.seed(OWNER, [message(old)])
        self.assertEqual(cursor.take(OWNER, [message(old[:-1] + "日"), message("无法确认的新旧文字")]), [])
        self.assertEqual(cursor.last_reason, "resynced")
        cursor.seed(OWNER, [message("精确锚点"), message(old, "甲")])
        self.assertEqual(cursor.take(OWNER, [message("精确锚点"), message(old[:-1] + "日", "乙"), message("另一个尾巴")]), [])

    def run_reads(self, reads, folder):
        class Ticks:
            count = 0
            def wait(self, seconds):
                self.count += 1
                return self.count > len(reads)
            def is_set(self):
                return False
        transport, model = Mock(), Mock()
        transport.read.side_effect = [(OWNER, [message("旧的文字")]), *reads]
        model.reply.return_value = "合成回复"
        events = []
        runner = None
        def notify(kind, text):
            events.append(kind)
            if kind == "ready":
                runner.resume("preview")
        runner = PrivateRunner(transport, model, Path(folder) / "preview.jsonl", notify, {OWNER: "owner"}, Ticks())
        runner.run()
        return runner, transport, model, events

    def test_one_transient_blank_title_recovers_and_processes_new_text(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, transport, model, events = self.run_reads([
                ReadUnavailableError("空标题"), (OWNER, [message("旧的文字"), message("新文字")])], folder)
            model.reply.assert_called_once_with(OWNER, "新文字", proactive=False)
            transport.send.assert_not_called()
            self.assertIn("waiting", events)
            self.assertEqual(runner.snapshot()["received_messages"], 1)
            self.assertTrue(runner.snapshot()["last_poll_at"])

    def test_three_blank_titles_wait_without_calling_model_or_permanent_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, transport, model, events = self.run_reads([ReadUnavailableError("空") for _ in range(3)], folder)
            model.reply.assert_not_called()
            self.assertEqual(runner.read_failures, 3)
            self.assertIn("waiting", events)
            self.assertNotIn("paused", events)

    def test_wrong_chat_waits_then_automatically_resumes_the_original_session(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, transport, model, events = self.run_reads([ScopeError("不是目标会话"), (OWNER, [message("新文字")])], folder)
            self.assertEqual(transport.read.call_count, 3)
            model.reply.assert_not_called()  # no overlap: do not replay an unknown screen
            self.assertIn("waiting", events)
            self.assertNotIn("paused", events)

    def test_switching_back_preserves_baseline_and_receives_new_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, transport, model, events = self.run_reads([
                ScopeError("切到私聊"), ReadUnavailableError("暂时读空"),
                (OWNER, [message("旧的文字"), message("切回来之后的新消息")])], folder)
            model.reply.assert_called_once_with(OWNER, "切回来之后的新消息", proactive=False)
            transport.send.assert_not_called()
            self.assertEqual(runner.read_failures, 0)
            self.assertEqual(runner.last_error_type, "")

    def test_snapshot_contains_no_message_body_or_api_key(self):
        with tempfile.TemporaryDirectory() as folder:
            runner, _, _, _ = self.run_reads([(OWNER, [message("旧的文字"), message("PRIVATE-BODY-SENTINEL")])], folder)
            snapshot = json.dumps(runner.snapshot())
            self.assertNotIn("PRIVATE-BODY-SENTINEL", snapshot)
            self.assertNotIn("api_key", snapshot)

    def test_multiple_live_rounds_keep_messages_arriving_during_generation(self):
        # Full visible snapshots include incoming messages before our last
        # outgoing reply. The second message arrived while generating reply 1.
        old, first, during, third = [message(t) for t in ("旧记录", "第一轮", "生成时补充", "第三轮")]
        with tempfile.TemporaryDirectory() as folder:
            class Ticks:
                count = 0
                def wait(self, seconds):
                    self.count += 1
                    return self.count > 4
                def is_set(self):
                    return False
            transport, model = Mock(), Mock()
            transport.read.side_effect = [
                (OWNER, [old]), (OWNER, [old, first]),
                (OWNER, [old, first, during]), (OWNER, []),
                (OWNER, [first, during, third])]
            model.reply.return_value = "测试回复"
            runner = None
            def notify(kind, text):
                if kind == "ready":
                    runner.resume("live")
            runner = PrivateRunner(transport, model, Path(folder) / "preview.jsonl",
                                   notify, {OWNER: "owner"}, Ticks())
            runner.run()
            self.assertEqual([c.args[1] for c in model.reply.call_args_list],
                             ["第一轮", "生成时补充", "第三轮"])
            self.assertEqual(transport.send.call_count, 3)
            self.assertEqual(model.remember.call_count, 3)
            self.assertEqual(runner.resync_count, 0)

    def test_known_unsent_title_change_waits_but_never_replays_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            transport, model = Mock(), Mock()
            transport.read.side_effect = [(OWNER, [message("旧消息"), message("新消息")]),
                                          (OWNER, [message("旧消息"), message("新消息")])]
            transport.send.side_effect = ScopeError("发送前切走了")
            model.reply.return_value = "不应补发的回复"
            runner = PrivateRunner(transport, model, Path(folder) / "preview.jsonl", Mock(), {OWNER: "owner"})
            runner.cursor.seed(OWNER, [message("旧消息")])
            runner.session_contact = OWNER
            runner.ready = True
            runner.resume("live")
            runner.process_once()
            self.assertTrue(runner.enabled.is_set())
            self.assertEqual(runner.state, "waiting")
            runner.process_once()
            transport.send.assert_called_once()
            model.remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
