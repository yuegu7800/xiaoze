"""Safe manual entry; no legacy app/config import."""
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from wechat_private_bridge import PrivateBridge, OwnerScope, VisualTransport, create_core

ROOT = Path(__file__).resolve().parent
UPSTREAM = Path(r'C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\xiaoli\xiaoli_desktop')


class Window:
    def __init__(self, root):
        self.root, self.bridge = root, None
        self.events = queue.Queue()
        self.working = False
        root.title('小泽 · 主人单一微信私聊')
        self.values = {}
        for key, label in [('target', '绑定主人私聊完整标题'), ('endpoint', '原有 HTTPS /chat/completions 地址'), ('model', '原有模型 ID'), ('api_key', 'API Key（仅内存）')]:
            ttk.Label(root, text=label).pack(anchor='w')
            self.values[key] = tk.StringVar()
            ttk.Entry(root, textvariable=self.values[key], show='*' if key == 'api_key' else '', width=80).pack(fill='x')
        ttk.Label(root, text='登录账号：未核实。标题/OCR不能认证账号；请人工确认当前账号及唯一主人。\n默认断开/暂停。连接仅建立历史基线；两种模式需分别启用。').pack()
        for label, fn in [('连接并绑定', self.connect), ('启用一轮预览', lambda: self.enable('preview')), ('启用真实回复', lambda: self.enable('live')), ('暂停', self.pause), ('断开', self.disconnect)]:
            ttk.Button(root, text=label, command=lambda f=fn: self.action(f)).pack(side='top', anchor='w')
        self.status = tk.StringVar(value='未连接 · 暂停 · 账号未核实')
        ttk.Label(root, textvariable=self.status, wraplength=700).pack(fill='x')
        self.output = tk.Text(root, width=85, height=14, state='disabled')
        self.output.pack(fill='both', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = root.after(200, self.poll)

    def action(self, fn):
        try:
            fn()
        except Exception:
            self.status.set('操作失败，已停止；请检查配置/目标/依赖。未自动重试。')
            if self.bridge:
                self.bridge.pause()

    def connect(self):
        if self.bridge:
            raise RuntimeError('请先断开')
        from core import prepare_api_config
        from character_service import ApiCompletion
        cfg = prepare_api_config({k: self.values[k].get() for k in ('endpoint', 'model', 'api_key')})
        target = self.values['target'].get().strip()
        scope = OwnerScope(target)
        if not messagebox.askyesno('人工确认绑定', '标题不是账号认证。请在微信核对登录账号和主人私聊，再绑定「' + target + '」。仅建立基线，不生成。'):
            return
        # Lazy import only after user explicitly requests connection.
        sys.path.insert(0, str(UPSTREAM))
        from wx_backend.visual_backend import VisualBackend
        from wx_backend.visual_regions import parse_title
        from visual_adapter import current_chat_backend
        backend = current_chat_backend(VisualBackend, parse_title, {target: scope})()
        if not backend.connect():
            raise RuntimeError('连接失败')
        core = create_core(ROOT / '.wechat-xiaoze' / 'memory.json')
        bridge = PrivateBridge(core, VisualTransport(backend, scope, parse_title), ApiCompletion(cfg), target)
        bridge.connect()
        self.bridge = bridge
        self.status.set('已连接 · 暂停 · 绑定：' + target + ' · 账号未核实')

    def enable(self, mode):
        if not self.bridge:
            raise RuntimeError('未连接')
        if mode == 'live' and not messagebox.askyesno('真实回复', '将向绑定主人私聊发送新消息的回复。按键后无法确认送达会暂停，请人工核对。启用？'):
            return
        self.bridge.enable(mode)
        self.status.set('已连接 · ' + mode + ' · 绑定：' + self.bridge.target + ' · 账号未核实')

    def pause(self):
        if self.bridge:
            self.bridge.pause()
        self.status.set('已撤销回复许可；迟到生成不发送。已进入底层发送的按键不能撤回，请核对。')

    def disconnect(self):
        if self.bridge:
            self.bridge.disconnect()
            if self.bridge.busy:
                self.status.set('已断开许可；等待在途操作退出后再点断开。已进入底层发送不能撤回，请核对。')
                return
        self.bridge = None
        self.status.set('未连接 · 暂停 · 账号未核实')

    def poll(self):
        while not self.events.empty():
            bridge = self.events.get_nowait()
            self.working = False
            if bridge is self.bridge:
                s = bridge.last
                result_label = {'known_unsent': '已知未发送', 'failed_known_unsent': '生成或发送前检查失败，未发送', 'uncertain': '结果不确定，请核对', 'confirmed': '有证据的成功（范围见说明）', 'preview': '预览，未发送', 'cancelled': '已取消，未发送'}.get(s.get('result'), '未处理')
                self.status.set(f'绑定：{bridge.target} · {bridge.mode} · 账号未核实 · 已生成={s.get("generated")} · 本地提交={s.get("local_committed")} · 发送尝试={s.get("send_attempted")} · {result_label} · {s.get("proof")}')
                if s.get('reply'):
                    self.output.configure(state='normal')
                    self.output.delete('1.0', 'end')
                    self.output.insert('end', s['reply'])
                    self.output.configure(state='disabled')
        if self.bridge and self.bridge.mode != 'paused' and not self.working:
            bridge = self.bridge
            self.working = True
            def work():
                try:
                    bridge.step()
                finally:
                    self.events.put(bridge)
            threading.Thread(target=work, daemon=True).start()
        self.timer = self.root.after(500, self.poll)

    def close(self):
        if self.bridge:
            self.bridge.close()
        self.root.after_cancel(self.timer)
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    Window(root)
    root.mainloop()
