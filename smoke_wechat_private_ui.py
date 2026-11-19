"""Real temporary Tk widgets; mock bridge only, never opens WeChat."""
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock, patch
from wechat_private_ui import Window

root = tk.Tk()
root.withdraw()
with patch('wechat_private_ui.threading.Thread') as thread:
    window = Window(root)
    root.update()
    assert window.bridge is None
    assert not window.working
    thread.assert_not_called()
    bridge = Mock()
    bridge.target = '虚构主人'
    bridge.mode = 'paused'
    bridge.busy = False
    window.bridge = bridge
    window.pause()
    bridge.pause.assert_called_once()
    with patch('wechat_private_ui.messagebox.askyesno', return_value=False):
        window.enable('live')
    bridge.enable.assert_not_called()
    window.enable('preview')
    bridge.enable.assert_called_once_with('preview')
    window.disconnect()
    bridge.disconnect.assert_called_once()
    assert window.bridge is None
    window.bridge = bridge
    window.close()
    bridge.close.assert_called_once()
print('PASS: 6 Tk mock checks (startup, pause, declined live, preview, disconnect, close)')

# Real UI cancellation handlers while an actual bridge worker is blocked on
# controlled mock I/O. The watchdog releases I/O only AFTER checking that the
# Tk handler returned; no timing sleeps or WeChat/backend imports.
from pathlib import Path
import tempfile
import threading
from wechat_private_bridge import PrivateBridge, VisualTransport, OwnerScope, create_core, SCENE
from test_core import message

for phase in ('read', 'identity', 'backend_send'):
    for operation in ('pause', 'disconnect', 'close'):
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            root.withdraw()
            window = Window(root)
            root.after_cancel(window.timer)
            backend = Mock()
            backend.read_title.return_value = '虚构主人'
            backend._current_title = '虚构主人'
            backend._current_is_group = False
            messages = [message('虚构历史')]
            backend.get_messages.side_effect = lambda *a, **k: list(messages)
            transport = VisualTransport(backend, OwnerScope('虚构主人'), lambda t: (t, False, None))
            core = create_core(Path(folder) / 'mock-memory.json')
            complete = Mock(return_value='虚构回复')
            bridge = PrivateBridge(core, transport, complete, '虚构主人')
            bridge.connect()
            bridge.enable('live')
            messages.append(message('虚构新文字'))
            entered, release, returned = threading.Event(), threading.Event(), threading.Event()
            def wait():
                entered.set()
                assert release.wait(3), 'UI test never released mock I/O'
            if phase == 'read':
                original = transport.read
                def read():
                    wait()
                    return original()
                transport.read = read
            elif phase == 'identity':
                original = transport.identity
                def identity(*args, **kwargs):
                    if complete.called:
                        wait()
                    return original(*args, **kwargs)
                transport.identity = identity
            else:
                def send(*args):
                    wait()
                    return True
                backend.send_text.side_effect = send
            window.bridge = bridge
            window.working = True
            def work():
                bridge.step()
                window.events.put(bridge)
            worker = threading.Thread(target=work)
            worker.start()
            assert entered.wait(2), 'worker never reached mock I/O'
            timely = []
            def watchdog():
                timely.append(returned.wait(0.5))
                release.set()
            guard = threading.Thread(target=watchdog)
            guard.start()
            try:
                if operation == 'close':
                    window.close()
                else:
                    label = '暂停' if operation == 'pause' else '断开'
                    button = next(child for child in root.winfo_children()
                                  if isinstance(child, ttk.Button) and child.cget('text') == label)
                    button.invoke()
            finally:
                returned.set()
                guard.join(3)
                worker.join(3)
            assert timely == [True], (phase, operation, 'UI cancellation waited for slow I/O')
            assert not worker.is_alive() and not guard.is_alive()
            assert bridge.mode == 'paused' and not bridge.busy
            assert core.snapshot(SCENE)['turns'] == []
            if phase == 'backend_send':
                backend.send_text.assert_called_once()
                assert bridge.last['result'] == 'uncertain'
            else:
                backend.send_text.assert_not_called()
            if phase == 'read':
                complete.assert_not_called()
            if operation != 'close':
                window.poll()  # process the late worker event on the Tk thread
                root.update_idletasks()
                assert not window.working
                assert bridge.mode == 'paused'
                if phase == 'backend_send':
                    assert '结果不确定' in window.status.get()
                window.close()
print('PASS: 9 Event-coordinated Tk cancellation cases (read/title/send x pause/disconnect/close)')
