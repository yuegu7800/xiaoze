from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import Mock
from wechat_private_bridge import *
from test_core import message


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = create_core(Path(self.tmp.name) / 'memory.json')
        self.backend = Mock()
        self.backend.read_title.return_value = '主人测试'
        self.backend._current_title = '主人测试'
        self.backend._current_is_group = False
        self.messages = [message('历史')]
        self.backend.get_messages.side_effect = lambda *a, **k: self.messages
        self.transport = ScopedTransport(self.backend, OwnerScope('主人测试'), lambda t: (t, False, None))
        self.complete = Mock(return_value='温柔的虚构回复')
        self.b = PrivateBridge(self.core, self.transport, self.complete, '主人测试')
        self.b.connect()
        self.b.enable('live')
        self.messages.append(message('新消息'))

    def turns(self):
        return self.core.snapshot(SCENE)['turns']

    def test_preview_and_mode_switch_never_commit_or_send_old_preview(self):
        self.b.enable('preview')
        self.messages.append(message('预览新消息'))
        self.b.step()
        self.assertTrue(self.b.last['generated'])
        self.assertEqual(self.turns(), [])
        self.backend.send_text.assert_not_called()
        self.b.enable('live')
        self.b.step()
        self.assertEqual(self.complete.call_count, 1)

    def test_confirmed_mock_commit_has_owner_rules_and_deletion_chain(self):
        self.transport.send = Mock(return_value=SendEvidence('mock服务器接受；不证明收到或已读'))
        self.b.step()
        self.assertTrue(self.b.last['local_committed'])
        self.assertIn('不挖苦', self.complete.call_args.args[0][0]['content'])
        turn = self.turns()[0]
        self.assertIn(turn['id'], turn['reply_trace']['source_ids'])
        rid = self.core.mark_turn(SCENE, turn['id'], '新消息', '虚构事件')
        self.core.forget_memory(SCENE, rid)
        self.assertFalse(any(t['reply'] for t in self.turns()))

    def test_true_boolean_is_not_delivery(self):
        self.b.step()
        self.assertEqual(self.b.last['result'], 'uncertain')
        self.assertEqual(self.turns(), [])
        self.assertEqual(self.b.mode, 'paused')
        self.b.step()
        self.backend.send_text.assert_called_once()

    def test_generation_cancellation_boundaries(self):
        for operation in ('pause', 'disconnect', 'close'):
            with self.subTest(operation=operation):
                self.setUp()
                self.complete.side_effect = lambda m: (getattr(self.b, operation)(), '迟到')[1]
                self.b.step()
                self.backend.send_text.assert_not_called()
                self.assertEqual(self.turns(), [])

    def test_mode_change_during_generation_invalidates(self):
        self.complete.side_effect = lambda m: (self.b.pause(), '迟到')[1]
        self.b.step()
        self.b.enable('preview')
        self.b.step()
        self.backend.send_text.assert_not_called()
        self.assertEqual(self.complete.call_count, 1)

    def test_target_change_before_send(self):
        self.complete.side_effect = lambda m: (setattr(self.backend.read_title, 'return_value', '错误目标'), '回复')[1]
        self.b.step()
        self.backend.send_text.assert_not_called()
        self.assertEqual(self.turns(), [])

    def test_group_or_unknown_rejected_before_body_read(self):
        for title, group in [('主人测试', True), ('未知', False)]:
            self.transport.parse_title = lambda t: (title, group, None)
            self.backend.get_messages.reset_mock()
            self.b.step()
            self.backend.get_messages.assert_not_called()
            self.complete.assert_not_called()
            self.b.mode = 'live'

    def test_baseline_echo_duplicates_empty_and_resync(self):
        self.messages = [message('历史'), message('自回声', 'self')]
        self.b.step()
        self.assertEqual(self.complete.call_count, 0)
        self.messages = []
        self.b.step()
        self.messages = [message('历史'), message('新消息')]
        self.transport.send = Mock(return_value=SendEvidence('mock accepted'))
        self.b.step()
        self.b.step()
        self.assertEqual(self.complete.call_count, 1)
        self.messages = [message('滚动无锚点')]
        self.b.step()
        self.assertEqual(self.complete.call_count, 1)

    def test_new_message_during_generation_processed_once(self):
        def complete(m):
            if self.complete.call_count == 1:
                self.messages.append(message('生成期间到达'))
            return '回复'
        self.complete.side_effect = complete
        self.transport.send = Mock(return_value=SendEvidence('mock accepted'))
        self.b.step()
        self.b.step()
        self.b.step()
        self.assertEqual(self.complete.call_count, 2)
        self.assertEqual(self.transport.send.call_count, 2)

    def test_model_failure_no_retry(self):
        self.complete.side_effect = RuntimeError('mock')
        self.b.step()
        self.b.step()
        self.assertEqual(self.complete.call_count, 1)
        self.assertFalse(self.b.last['send_attempted'])
        self.assertEqual(self.turns(), [])

    def test_send_failures_never_commit_or_retry(self):
        for error, result in [(SendNotStarted('blocked before backend'), 'known_unsent'), (SendUncertainError('unknown'), 'uncertain'), (OSError('unknown'), 'uncertain')]:
            with self.subTest(result=result):
                self.setUp()
                self.transport.send = Mock(side_effect=error)
                self.b.step()
                self.assertEqual(self.b.last['result'], result)
                self.b.step()
                self.transport.send.assert_called_once()
                self.assertEqual(self.turns(), [])

    def test_send_confirmed_local_failure_never_resends(self):
        self.transport.send = Mock(return_value=SendEvidence('mock accepted'))
        self.core.memory.save_scene = Mock(side_effect=OSError('disk'))
        self.b.step()
        self.assertEqual(self.b.last['result'], 'confirmed')
        self.assertFalse(self.b.last['local_committed'])
        self.b.step()
        self.transport.send.assert_called_once()

    def test_prepare_single_use_and_epoch_and_no_preview_history(self):
        reply, commit = self.core.prepare_exchange(SCENE, 'owner', '虚构', self.complete)
        self.assertEqual(self.turns(), [])
        commit()
        with self.assertRaises(Exception):
            commit()
        reply, late = self.core.prepare_exchange(SCENE, 'owner', '第二条', self.complete)
        self.core.invalidate()
        with self.assertRaises(Exception):
            late()
        self.assertEqual(len(self.turns()), 1)


class BridgeCancellationTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    turns = BridgeTests.turns

    def blocked_step(self, hook, operation):
        """No sleeps: cancellation must return while the I/O event is still blocked."""
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def wait():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test I/O was never released')
        hook(wait)
        worker = threading.Thread(target=self.b.step)
        worker.start()
        self.assertTrue(entered.wait(2), 'step did not enter controlled I/O')
        def cancel():
            try:
                getattr(self.b, operation)()
            finally:
                cancelled.set()
        stopper = threading.Thread(target=cancel)
        stopper.start()
        timely = cancelled.wait(0.5)
        try:
            if timely:
                self.assertTrue(self.b.busy)
                # Neither reentrant polling nor a new mode may revive this run.
                self.b.step()
                with self.assertRaises(RuntimeError):
                    self.b.enable('live')
        finally:
            release.set()
            worker.join(3)
            stopper.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(timely, 'cancel waited for slow I/O')
        self.assertFalse(self.b.busy)
        self.assertEqual(self.b.mode, 'paused')
        self.assertEqual(self.b.connected, operation == 'pause')
        self.assertEqual(self.b.closed, operation == 'close')

    def test_cancel_during_receiving_never_calls_completion(self):
        for operation in ('pause', 'disconnect', 'close'):
            with self.subTest(operation=operation):
                self.setUp()
                def hook(wait):
                    def read():
                        wait()
                        return '主人测试', list(self.messages)
                    self.transport.read = Mock(side_effect=read)
                self.blocked_step(hook, operation)
                self.complete.assert_not_called()
                self.backend.send_text.assert_not_called()
                self.assertEqual(self.turns(), [])

    def test_cancel_during_presend_identity_returns_without_waiting(self):
        for operation in ('pause', 'disconnect', 'close'):
            with self.subTest(operation=operation):
                self.setUp()
                def hook(wait):
                    original = self.transport.identity
                    def identity(*args, **kwargs):
                        if self.complete.called:
                            wait()
                        return original(*args, **kwargs)
                    self.transport.identity = Mock(side_effect=identity)
                self.blocked_step(hook, operation)
                self.complete.assert_called_once()
                self.backend.send_text.assert_not_called()
                self.assertEqual(self.turns(), [])

    def use_visual_transport(self):
        self.transport = VisualTransport(self.backend, OwnerScope('主人测试'), lambda t: (t, False, None))
        self.b.transport = self.transport
        self.transport.can_send = self.b.can_send

    def test_cancel_during_transport_final_foreground_check(self):
        for operation in ('pause', 'disconnect', 'close'):
            with self.subTest(operation=operation):
                self.setUp()
                self.use_visual_transport()
                def hook(wait):
                    def title(*args, **kwargs):
                        if kwargs.get('foreground'):
                            wait()
                        return '主人测试'
                    self.backend.read_title.side_effect = title
                self.blocked_step(hook, operation)
                self.backend.send_text.assert_not_called()
                self.assertEqual(self.b.last['result'], 'known_unsent')
                self.assertEqual(self.turns(), [])

    def test_cancel_after_backend_entry_is_uncertain_even_for_scope_error(self):
        for operation in ('pause', 'disconnect', 'close'):
            for outcome in (True, False, OSError('mock partial effect'), ScopeError('mock partial effect')):
                with self.subTest(operation=operation, outcome=type(outcome).__name__):
                    self.setUp()
                    self.use_visual_transport()
                    def hook(wait):
                        def send(*args):
                            wait()
                            if isinstance(outcome, Exception):
                                raise outcome
                            return outcome
                        self.backend.send_text.side_effect = send
                    self.blocked_step(hook, operation)
                    self.assertEqual(self.b.last['result'], 'uncertain')
                    self.backend.send_text.assert_called_once()
                    self.complete.assert_called_once()
                    self.assertEqual(self.turns(), [])
                    self.b.step()
                    self.backend.send_text.assert_called_once()

    def test_cancelled_read_cannot_revive_when_explicitly_enabled_again(self):
        original = self.transport.read
        def hook(wait):
            def read():
                wait()
                return original()
            self.transport.read = Mock(side_effect=read)
        self.blocked_step(hook, 'pause')
        self.transport.read = original
        self.b.enable('live')
        self.b.step()
        self.complete.assert_not_called()
        self.messages.append(message('重新启用后的新文字'))
        self.b.step()
        self.complete.assert_called_once()
        self.backend.send_text.assert_called_once()

    def test_cancel_before_core_preparation_uses_captured_ticket(self):
        original = self.core.prepare_exchange
        def hook(wait):
            def prepare(*args, **kwargs):
                self.assertIsInstance(kwargs.get('ticket'), int)
                wait()
                return original(*args, **kwargs)
            self.core.prepare_exchange = Mock(side_effect=prepare)
        self.blocked_step(hook, 'pause')
        self.complete.assert_not_called()
        self.backend.send_text.assert_not_called()
        self.assertEqual(self.turns(), [])

    def test_late_connect_or_enable_baseline_cannot_reopen_permission(self):
        for setup in ('connect', 'enable'):
            for operation in ('pause', 'disconnect', 'close'):
                with self.subTest(setup=setup, operation=operation):
                    self.setUp()
                    if setup == 'connect':
                        self.b.disconnect()
                    entered, release = threading.Event(), threading.Event()
                    errors = []
                    def read():
                        entered.set()
                        if not release.wait(3):
                            raise AssertionError('unreleased baseline')
                        return '主人测试', list(self.messages)
                    self.transport.read = Mock(side_effect=read)
                    def work():
                        try:
                            self.b.connect() if setup == 'connect' else self.b.enable('live')
                        except Exception as error:
                            errors.append(error)
                    worker = threading.Thread(target=work)
                    worker.start()
                    self.assertTrue(entered.wait(2))
                    try:
                        getattr(self.b, operation)()
                        self.b.step()
                        with self.assertRaises(RuntimeError):
                            self.b.connect()
                    finally:
                        release.set()
                        worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], Cancelled)
                    self.assertEqual(self.b.mode, 'paused')
                    self.assertFalse(self.b.busy)
                    self.assertEqual(self.b.closed, operation == 'close')
                    expected_connected = setup == 'enable' and operation == 'pause'
                    self.assertEqual(self.b.connected, expected_connected)
                    self.complete.assert_not_called()
                    self.backend.send_text.assert_not_called()


if __name__ == '__main__':
    unittest.main()
