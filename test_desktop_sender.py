"""Reproduce global-key misrouting with the actual old sender, no desktop."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import OWNER, ScopeError, ScopedTransport, SendUncertainError
from groups import GroupScope
from test_core import parse
from test_visual_adapter import upstream, UPSTREAM
from visual_adapter import current_chat_backend


GROUP = '虚构测试群'


class DesktopSenderTests(unittest.TestCase):
    def setUp(self):
        self.focus, self.current, self.enabled = 101, GROUP, True
        self.events = []
        self.mouse = SimpleNamespace(
            click=Mock(side_effect=lambda *a: self.events.append(('click', self.focus))),
            hotkey=Mock(side_effect=lambda *a: self.events.append(('paste', self.focus))),
            press=Mock(side_effect=lambda *a: self.events.append(('enter', self.focus))))
        self.clipboard = SimpleNamespace(copy=Mock())
        self.wait = Mock()
        # Extract only the original sender function, not module initialization.
        tree = ast.parse((UPSTREAM / 'wx_backend' / 'visual_backend.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'VisualBackend')
        sender = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'send_text')
        ns = {'time': SimpleNamespace(sleep=self.wait), 'logger': Mock()}
        exec(compile(ast.Module(body=[sender], type_ignores=[]), '<isolated-old-sender>', 'exec'), ns)
        self.legacy_send = ns['send_text']
        outer = self
        class Native:
            _hwnd = 101
            get_messages = upstream.get_messages
            send_text = outer.legacy_send
            _input_box_rect = lambda self: (10, 20, 200, 100)
            _foreground = lambda self: True  # Native reports True even if Windows denied focus.
            def read_title(self, foreground=False):
                return outer.current + '(3)' if outer.current == GROUP else outer.current
        self.Native = Native
        self.native_u32 = SimpleNamespace(GetForegroundWindow=lambda: self.focus)
        self.context = patch.dict(upstream.__dict__, u32=self.native_u32)
        self.context.start()
        self.addCleanup(self.context.stop)
        self.modules = patch.dict('sys.modules', pyautogui=self.mouse, pyperclip=self.clipboard)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.sleep = patch('time.sleep', self.wait)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        backend = current_chat_backend(Native, parse, {GROUP: GroupScope(GROUP)})()
        self.transport = ScopedTransport(backend, GroupScope(GROUP), parse, lambda: self.enabled)

    def test_original_sender_sends_to_focused_private_window_despite_group_argument(self):
        self.focus = 202  # owner floating window; main still displays group title
        self.assertTrue(self.Native().send_text(GROUP, '虚构群回复'))
        self.assertEqual(self.events[-1], ('enter', 202))

    def test_adapter_rejects_same_fault_before_click_or_clipboard(self):
        self.focus = 202
        with self.assertRaises(SendUncertainError):
            self.transport.send(GROUP, '虚构群回复')
        self.assertEqual(self.events, [])
        self.clipboard.copy.assert_not_called()

    def test_title_switch_after_click_cannot_paste_or_press_enter(self):
        def click(*args):
            self.current = OWNER
        self.mouse.click.side_effect = click
        with self.assertRaises(SendUncertainError):
            self.transport.send(GROUP, '虚构群回复')
        self.mouse.hotkey.assert_not_called()
        self.mouse.press.assert_not_called()
        self.clipboard.copy.assert_not_called()

    def test_focus_loss_after_paste_never_sends_enter_or_retries(self):
        def paste(*args):
            self.focus = 202
        self.mouse.hotkey.side_effect = paste
        with self.assertRaises(SendUncertainError):
            self.transport.send(GROUP, '虚构群回复')
        self.mouse.hotkey.assert_called_once()
        self.mouse.press.assert_not_called()

    def test_pause_after_clipboard_copy_is_checked_before_paste(self):
        self.clipboard.copy.side_effect = lambda text: setattr(self, 'enabled', False)
        with self.assertRaises(SendUncertainError):
            self.transport.send(GROUP, '虚构群回复')
        self.mouse.hotkey.assert_not_called()
        self.mouse.press.assert_not_called()

    def test_focus_loss_during_slow_title_check_cancels_before_clipboard(self):
        backend = self.transport.backend
        original = backend.check_title
        def title(target):
            result = original(target)
            self.focus = 202
            return result
        backend.check_title = title
        with self.assertRaises(SendUncertainError):
            self.transport.send(GROUP, '虚构群回复')
        self.assertEqual(self.events, [])

    def test_normal_sender_still_supports_multiple_rounds_in_same_target(self):
        self.transport.send(GROUP, '虚构回复一')
        self.transport.send(GROUP, '虚构回复二')
        self.assertEqual(self.mouse.press.call_args_list, [unittest.mock.call('enter')] * 2)
        self.assertTrue(all(hwnd == 101 for _, hwnd in self.events))


if __name__ == '__main__':
    unittest.main()
