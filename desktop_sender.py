"""Guards for the legacy foreground sender, not a background transport.

Desktop operations are injected for offline fault tests. Foreground/title
checks reduce known misroutes but cannot make shared human desktop input
atomic. In particular, a keypress is not evidence of delivery.
"""
from core import ScopeError


def checked_desktop_send(backend, target, text, can_send, mouse, clipboard,
                         foreground_window, pause, hold_after_paste=0.0):
    expected_hwnd = backend._hwnd

    def check():
        if not can_send():
            raise ScopeError('已暂停，取消桌面发送；不会自动重发。')
        if not expected_hwnd or foreground_window() != expected_hwnd:
            raise ScopeError('微信主窗口未获得焦点或焦点已变化，取消桌面发送；不会自动重发。')
        # Reading the main window title alone does not identify the window
        # receiving global Ctrl+V/Enter. Check both, again after the slow OCR.
        backend.check_title(target)
        if foreground_window() != expected_hwnd or not can_send():
            raise ScopeError('核对期间焦点或许可变化，取消桌面发送；不会自动重发。')

    if not can_send():
        raise ScopeError('已暂停，本条不发送。')
    backend._foreground()
    check()
    rect = backend._input_box_rect()
    if rect is None:
        raise ScopeError('无法定位微信输入框，本条不发送。')
    check()
    mouse.click(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    pause(0.3)
    check()
    clipboard.copy(text)
    pause(0.1)
    check()
    mouse.hotkey('ctrl', 'v')
    pause(0.2)
    if hold_after_paste and hold_after_paste > 0:
        pause(float(hold_after_paste))
    check()
    mouse.press('enter')
    return True
