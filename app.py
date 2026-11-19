"""小泽微信完整入口，沿用原有群聊、私聊及双会话流程。"""
import json
from datetime import datetime
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parent
UPSTREAM = Path(r"C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\xiaoli\xiaoli_desktop")
PERSONA = ROOT / "persona.txt"
DATA = ROOT / "data"

from core import OWNER, PrivateScope, ScopedTransport, ChatModel, PrivateRunner, atomic_json, verify_api, prepare_api_config
from groups import DEFAULT_GROUP, PARTICIPATION, GroupScope, GroupPolicy, GroupChatModel, GroupRunner
from relationships import PeopleMemory, set_owner_aliases
from visual_adapter import current_chat_backend, compatible_title_parser
from sessions import MultiRunner

SESSION_MODES = {"孤月私聊": "private", "指定群聊": "group", "群聊＋孤月私聊": "both"}


def encrypt(text):
    import base64
    import win32crypt
    return "dpapi:" + base64.b64encode(win32crypt.CryptProtectData(
        text.encode("utf-8"), "acheng-wechat", None, None, None, 0)).decode("ascii") if text else ""


def decrypt(text):
    import base64
    import win32crypt
    if not text:
        return ""
    if not text.startswith("dpapi:"):
        raise RuntimeError("配置中的密钥不是加密格式，请在窗口重新填写。")
    return win32crypt.CryptUnprotectData(base64.b64decode(text[6:]), None, None, None, 0)[1].decode("utf-8")


class App:
    def __init__(self):
        # Runtime initialization only: import/--check must not touch saved data
        # or prepare the desktop backend. Preserve the original runtime paths.
        DATA.mkdir(exist_ok=True)
        os.chdir(DATA)
        os.environ["XIAOLI_HOME"] = str(DATA)
        self.window = tk.Tk()
        self.window.title("小泽 · 微信群聊与私聊")
        self.window.geometry("900x850")
        self.window.minsize(800, 720)
        self.window.option_add("*Font", "{Microsoft YaHei UI} 10")
        self.runner = None
        self.events = queue.Queue()
        self.last_snapshot = {"state": "idle", "enabled": False, "ready": False}
        self.last_health_write = 0.0
        self.cfg = {"endpoint": "https://api.deepseek.com/chat/completions", "model": "deepseek-flash",
                    "api_key": "", "contacts": {OWNER: "owner"}, "session_mode": "private",
                    "group": DEFAULT_GROUP, "group_nickname": "小泽", "group_behavior": "social",
                    "group_frequency": "occasional"}
        if (DATA / "config.json").exists():
            self.cfg.update(json.loads((DATA / "config.json").read_text(encoding="utf-8")))
            try:
                self.cfg["api_key"] = decrypt(self.cfg["api_key"])
            except Exception:
                self.cfg["api_key"] = ""
        box = ttk.Frame(self.window, padding=20)
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="小泽，来聊两句", font=("Microsoft YaHei UI", 19, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(box, text="支持指定群与孤月私聊一起监听，两个会话分别记录记忆。\n双会话会自动轮流切换微信；临时读不到时等待恢复。", wraplength=820).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.values = {}
        self.api_entries = []
        for row, (key, label) in enumerate((("endpoint", "模型接口"), ("model", "模型 ID"), ("api_key", "API Key")), 2):
            self.values[key] = tk.StringVar(value=self.cfg[key])
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            entry = ttk.Entry(box, textvariable=self.values[key], show="*" if key == "api_key" else "")
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            self.api_entries.append(entry)
        ttk.Label(box, text="已保存的 API 可继续使用，Key 不会从 AIRI 自动导入。\n消息正文和当前会话的近期记录会发送给上面配置的模型服务。", wraplength=820, foreground="#555555").grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 12))
        self.session_mode = tk.StringVar(value=next((label for label, mode in SESSION_MODES.items()
            if mode == self.cfg["session_mode"]), "孤月私聊"))
        self.group_name = tk.StringVar(value=self.cfg["group"])
        self.group_nickname = tk.StringVar(value=self.cfg["group_nickname"])
        participation = (self.cfg["group_behavior"], self.cfg["group_frequency"])
        label = next((label for label, pair in PARTICIPATION.items() if pair == participation),
                     "偶尔插嘴（推荐）" if participation[0] == "social" else "被 @ 或叫名字时回复")
        self.group_behavior = tk.StringVar(value=label)
        ttk.Label(box, text="会话类型").grid(row=6, column=0, sticky="w", pady=4)
        self.session_picker = ttk.Combobox(box, textvariable=self.session_mode,
            values=list(SESSION_MODES), state="readonly")
        self.session_picker.grid(row=6, column=1, sticky="ew", pady=4)
        self.session_picker.bind("<<ComboboxSelected>>", lambda event: self.session_changed())
        ttk.Label(box, text="指定群完整名称").grid(row=7, column=0, sticky="w", pady=4)
        self.group_entry = ttk.Entry(box, textvariable=self.group_name)
        self.group_entry.grid(row=7, column=1, sticky="ew", pady=4)
        ttk.Label(box, text="群昵称／参与方式").grid(row=8, column=0, sticky="w", pady=4)
        group_line = ttk.Frame(box)
        group_line.grid(row=8, column=1, sticky="ew", pady=4)
        self.nickname_entry = ttk.Entry(group_line, textvariable=self.group_nickname, width=20)
        self.nickname_entry.pack(side="left", padx=(0, 12))
        self.behavior_picker = ttk.Combobox(group_line, textvariable=self.group_behavior,
            values=list(PARTICIPATION), state="readonly", width=27)
        self.behavior_picker.pack(side="left", fill="x", expand=True)
        top = ttk.Frame(box)
        top.grid(row=9, column=0, columnspan=2, sticky="w", pady=8)
        self.save_btn = ttk.Button(top, text="保存 API 配置", command=self.save)
        self.save_btn.pack(side="left", padx=(0, 10))
        self.verify_btn = ttk.Button(top, text="验证 API", command=self.verify)
        self.verify_btn.pack(side="left", padx=(0, 10))
        self.connect_btn = ttk.Button(top, text="连接并监听（预览）", command=self.connect)
        self.connect_btn.pack(side="left", padx=(0, 10))
        self.preview_btn = ttk.Button(top, text="继续一轮预览", command=self.preview, state="disabled")
        self.preview_btn.pack(side="left", padx=(0, 10))
        self.disconnect_btn = ttk.Button(top, text="断开／修改 API", command=self.disconnect, state="disabled")
        self.disconnect_btn.pack(side="left", padx=(0, 10))
        controls = ttk.Frame(box)
        controls.grid(row=10, column=0, columnspan=2, sticky="w", pady=6)
        self.live_btn = ttk.Button(controls, text="启用实际回复", command=self.live, state="disabled")
        self.live_btn.pack(side="left", padx=(0, 10))
        ttk.Button(controls, text="暂停回复", command=self.pause).pack(side="left", padx=(0, 12))
        self.proactive = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="冷场较久时主动开新话题", variable=self.proactive, command=self.update_proactive).pack(side="left")
        self.status = tk.StringVar(value="尚未连接微信。先填 API，再连接；连接不会自动发消息。")
        status_box = ttk.Frame(box)
        status_box.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 8))
        ttk.Label(status_box, textvariable=self.status, wraplength=820, foreground="#24594b").pack(anchor="w")
        self.health = tk.StringVar(value="未监听 · 请先连接")
        ttk.Label(status_box, textvariable=self.health, wraplength=820, foreground="#555555").pack(anchor="w", pady=(4, 0))
        self.output = tk.Text(box, height=10, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.output.grid(row=12, column=0, columnspan=2, sticky="nsew")
        box.rowconfigure(12, weight=1)
        ttk.Label(box, text="当前为前台视觉兼容模式，会占用鼠标、键盘和剪贴板，不是后台微信接口。\n只处理基线后的新文字。双会话请将指定群和孤月私聊置顶；运行时勿同时操作微信。\n单会话切走后等待，切回恢复。请开启群昵称显示，保持微信可见、桌面解锁。", wraplength=820, foreground="#666666").grid(row=13, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.session_changed()
        menu = tk.Menu(self.window)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="旧显示名绑定 / 兼容归档", command=self.show_people)
        tools_menu.add_command(label="小泽记忆、逐轮反馈与话题", command=self.show_character)
        tools_menu.add_command(label="朋友圈文字草稿", command=self.show_moments)
        tools_menu.add_command(label="运行诊断", command=self.show_diagnostics)
        menu.add_cascade(label="关系与动态", menu=tools_menu)
        self.window.configure(menu=menu)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.after(200, self.poll)

    def save(self):
        if self.runner:
            messagebox.showinfo("请先断开", "点击「断开／修改 API」后即可修改和保存，无需退出窗口。")
            return False
        cfg = dict(self.cfg)
        behavior, frequency = PARTICIPATION[self.group_behavior.get()]
        cfg.update({key: value.get().strip() for key, value in self.values.items()})
        cfg.update(session_mode=SESSION_MODES[self.session_mode.get()],
                   group=self.group_name.get().strip(), group_nickname=self.group_nickname.get().strip(),
                   group_behavior=behavior, group_frequency=frequency)
        endpoint = cfg["endpoint"].rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        if not endpoint.startswith("https://") or not cfg["model"] or not cfg["api_key"]:
            messagebox.showinfo("还差 API 配置", "请在本机填写 HTTPS 接口、模型 ID 和 API Key。")
            return False
        cfg["endpoint"] = endpoint
        try:
            cfg = prepare_api_config(cfg)
            PrivateScope(cfg["contacts"])
            if cfg["session_mode"] in ("group", "both"):
                GroupScope(cfg["group"])
                GroupPolicy(cfg["group_nickname"], cfg["group_behavior"], cfg["group_frequency"])
            stored = dict(cfg, api_key=encrypt(cfg["api_key"]))
            atomic_json(DATA / "config.json", stored)
        except (RuntimeError, ValueError) as error:
            messagebox.showerror("API 配置未保存", str(error))
            return False
        except Exception:
            messagebox.showerror("未保存", "Windows 密钥加密或配置校验失败，未保存。")
            return False
        self.cfg = cfg
        self.status.set("API 已加密保存，尚未验证。建议先点「验证 API」。")
        return True

    def session_changed(self):
        group = self.session_mode.get() in ("指定群聊", "群聊＋孤月私聊")
        self.group_entry.configure(state="normal" if group else "disabled")
        self.nickname_entry.configure(state="normal" if group else "disabled")
        self.behavior_picker.configure(state="readonly" if group else "disabled")

    def lock_settings(self, locked):
        for entry in self.api_entries:
            entry.configure(state="disabled" if locked else "normal")
        self.session_picker.configure(state="disabled" if locked else "readonly")
        if locked:
            self.group_entry.configure(state="disabled")
            self.nickname_entry.configure(state="disabled")
            self.behavior_picker.configure(state="disabled")
        else:
            self.session_changed()

    def target_label(self):
        if self.cfg["session_mode"] == "both":
            return "群聊「" + self.cfg["group"] + "」和私聊「孤月大人」"
        return "群聊「" + self.cfg["group"] + "」" if self.cfg["session_mode"] == "group" else "私聊「孤月大人」"

    def verify(self):
        if not self.save():
            return
        cfg = dict(self.cfg)
        self.verify_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.lock_settings(True)
        self.status.set("正在验证密钥和模型列表，不读取微信消息、不发送回复…")
        def check():
            try:
                ok, text = verify_api(cfg)
            except RuntimeError as error:
                ok, text = False, str(error)
            except Exception as error:
                ok, text = False, "验证程序出现 " + type(error).__name__ + "，已停止；未发送微信消息。"
            self.events.put(("api_ok" if ok else "api_bad", text))
        threading.Thread(target=check, daemon=True).start()

    def connect(self):
        if self.runner or not self.save():
            return
        try:
            if str(UPSTREAM) not in sys.path:
                sys.path.insert(0, str(UPSTREAM))
            from wx_backend.visual_backend import VisualBackend
            from wx_backend.visual_regions import parse_title
            parse_title = compatible_title_parser(parse_title)
            import pyautogui
            pyautogui.FAILSAFE = True
            if self.cfg["session_mode"] in ("group", "both"):
                scope = GroupScope(self.cfg["group"])
                policy = GroupPolicy(self.cfg["group_nickname"], self.cfg["group_behavior"], self.cfg["group_frequency"])
                from wechat_character import make_model
                model = (make_model(self.cfg, scope, PERSONA, DATA / "group-memory.json", policy)
                         if self.cfg["session_mode"] == "group" else None)
            else:
                scope = PrivateScope(self.cfg["contacts"])
                from wechat_character import make_model
                model = make_model(self.cfg, scope, PERSONA, DATA / "memory.json")
            scopes = {self.cfg["group"]: scope} if self.cfg["session_mode"] in ("group", "both") else {
                name: scope for name in self.cfg["contacts"]}
            if self.cfg["session_mode"] == "both":
                scopes[OWNER] = PrivateScope()
            backend = current_chat_backend(VisualBackend, parse_title, scopes)()
            def notify(kind, text):
                self.events.put((kind, text))
            if self.cfg["session_mode"] == "both":
                self.runner = MultiRunner(backend, self.cfg, PERSONA, DATA, parse_title, notify, model_factory=make_model)
            else:
                transport = ScopedTransport(backend, scope, parse_title,
                    can_send=lambda: bool(self.runner and self.runner.enabled.is_set()
                        and not self.runner.cancel.is_set() and self.runner.mode == "live"))
                if self.cfg["session_mode"] == "group":
                    self.runner = GroupRunner(transport, model, DATA / "group-preview.jsonl", notify, policy)
                else:
                    self.runner = PrivateRunner(transport, model, DATA / "preview.jsonl", notify, self.cfg["contacts"])
            self.connect_btn.configure(state="disabled")
            self.save_btn.configure(state="disabled")
            self.verify_btn.configure(state="disabled")
            self.disconnect_btn.configure(state="normal")
            self.lock_settings(True)
            self.status.set("正在连接微信并建立旧消息基线…")
            self.runner.start()
        except Exception:
            self.runner = None
            self.connect_btn.configure(state="normal")
            self.save_btn.configure(state="normal")
            self.verify_btn.configure(state="normal")
            self.lock_settings(False)
            messagebox.showerror("连接未启动", "依赖加载或配置失败，请保持微信打开，必要时重新启动本窗口。")

    def preview(self):
        if self.runner and self.runner.ready:
            self.runner.resume("preview")
            hint = ("可以发不带名字的新话题，合适时会接话，也可能选择旁听。"
                    if self.cfg["session_mode"] in ("group", "both") and self.cfg["group_behavior"] == "social"
                    else "群聊请 @机器人实际群昵称。")
            self.status.set("正在等待" + self.target_label() + "的新文字。" + hint + "生成一轮预览后暂停。")

    def disconnect(self):
        runner = self.runner
        if not runner:
            return
        panel = getattr(self, "character_panel", None)
        if panel and panel.window.winfo_exists():
            panel.window.destroy()
        runner.pause()
        runner.cancel.set()
        self.preview_btn.configure(state="disabled")
        self.live_btn.configure(state="disabled")
        self.disconnect_btn.configure(state="disabled")
        self.status.set("正在断开，尚未发送的回复会取消…")
        def finish():
            if runner.thread and runner.thread.is_alive():
                self.window.after(150, finish)
                return
            self.close_character_sessions(runner)
            self.last_snapshot = runner.snapshot()
            self.runner = None
            self.events = queue.Queue()
            self.connect_btn.configure(state="normal")
            self.save_btn.configure(state="normal")
            self.verify_btn.configure(state="normal")
            self.lock_settings(False)
            self.status.set("已断开。可以修改 API 配置，保存后重新连接。")
        finish()

    def live(self):
        if self.runner and self.runner.ready and messagebox.askyesno("启用实际回复", "接下来会以电脑微信登录账号向" + self.target_label() + "发送回复。\n请核对目标会话。双会话模式会自动切换两个会话，群聊参与成员应知晓机器人在试用。\n\n启用实际回复？"):
            self.runner.resume("live", self.proactive.get())
            self.status.set(self.target_label() + "实际回复已启用。需要停止时点「暂停回复」。")

    def pause(self):
        if self.runner:
            self.runner.pause()
        self.status.set("已暂停回复。尚未发送的生成结果会取消。")

    def update_proactive(self):
        if self.runner:
            self.runner.proactive = self.proactive.get() and self.runner.mode == "live"

    def poll(self):
        while not self.events.empty():
            kind, text = self.events.get_nowait()
            if kind in ("api_ok", "api_bad"):
                self.save_btn.configure(state="normal")
                self.verify_btn.configure(state="normal")
                self.connect_btn.configure(state="normal")
                self.lock_settings(False)
            if kind == "ready":
                self.preview_btn.configure(state="normal")
                self.live_btn.configure(state="normal")
                if self.runner and self.runner.ready and not self.runner.cancel.is_set() and self.runner.epoch == 0:
                    self.runner.resume("preview")
            if kind == "stopped":
                if self.runner:
                    self.last_snapshot = self.runner.snapshot()
                self.runner = None
                self.connect_btn.configure(state="normal")
                self.save_btn.configure(state="normal")
                self.verify_btn.configure(state="normal")
                self.disconnect_btn.configure(state="disabled")
                self.preview_btn.configure(state="disabled")
                self.live_btn.configure(state="disabled")
                self.lock_settings(False)
            if kind in ("preview", "preview_silent"):
                self.live_btn.configure(state="normal")
            if kind in ("preview", "sent"):
                self.status.set("预览完成、监听已暂停：点击「启用实际回复」开始自动回复，或「继续一轮预览」。" if kind == "preview" else "已执行发送操作，继续等新消息；这不是对方收到或已读的确认。")
            else:
                self.status.set(text)
            self.output.configure(state="normal")
            self.output.insert("end", {"preview": "【预览·未发送】", "sent": "【已执行发送操作】"}.get(kind, "【状态】") + text + "\n\n")
            if int(self.output.index("end-1c").split(".")[0]) > 400:
                self.output.delete("1.0", "100.0")
            self.output.see("end")
            self.output.configure(state="disabled")
        snapshot = self.runner.snapshot() if self.runner else self.last_snapshot
        listening = ("正式监听" if snapshot.get("mode") == "live" else "预览监听") if snapshot.get("enabled") else "未监听／已暂停"
        last_poll = snapshot.get("last_poll_at")
        poll_label = datetime.fromisoformat(last_poll).strftime("%H:%M:%S") if last_poll else "尚无"
        if "sessions" in snapshot:
            parts = []
            for name, session in snapshot["sessions"].items():
                last = session.get("last_poll_at")
                scanned = datetime.fromisoformat(last).strftime("%H:%M:%S") if last else "尚无"
                condition = "等待恢复" if session.get("state") == "waiting" else "已就绪" if session.get("ready") else "待建立基线"
                parts.append(f"{'私聊' if name == 'private' else '群聊'}：{condition}，扫描 {scanned}，新文字 {session.get('received_messages', 0)} 条")
            self.health.set(listening + " · " + "；".join(parts))
        else:
            self.health.set(f"{listening} · 最近扫描 {poll_label} · 可见文字 {snapshot.get('visible_messages', 0)} 条 · 本轮新文字 {snapshot.get('received_messages', 0)} 条")
        if time.monotonic() - self.last_health_write >= 5:
            self.write_status()
        self.window.after(200, self.poll)

    def write_status(self, state=None):
        snapshot = self.runner.snapshot() if self.runner else dict(self.last_snapshot)
        if state:
            snapshot.update(state=state, enabled=False, ready=False)
        try:
            atomic_json(DATA / "runtime-status.json", dict(snapshot, status=snapshot["state"],
                pid=os.getpid(), session_mode=self.cfg["session_mode"],
                target=self.target_label(),
                updated_at=datetime.now().astimezone().isoformat()))
        except OSError:
            self.health.set("运行诊断暂时无法写入磁盘；请检查 data 文件夹是否可写。")
        self.last_health_write = time.monotonic()

    def show_diagnostics(self):
        snapshot = self.runner.snapshot() if self.runner else self.last_snapshot
        # Show the configured target alongside this window's actual snapshot;
        # never dump cfg (it contains credentials).
        target = "当前配置目标：" + self.target_label() + "\n"
        if self.runner:
            explanation = "当前连接的实时状态。\n"
        elif snapshot.get("state") == "idle":
            explanation = ("当前窗口尚未建立连接，没有本窗口的识别失败记录。\n"
                           "请在出现问题的同一个主窗口点击诊断；不要为查看诊断另开窗口。\n"
                           "若这是重启后的窗口，先点击「连接并监听（预览）」建立新连接。\n\n")
        else:
            explanation = "当前已经断开；下方是本窗口最后一份监听状态，不代表仍在监听。\n\n"
        messagebox.showinfo("运行诊断", target + explanation + json.dumps(snapshot, ensure_ascii=False, indent=2), parent=self.window)

    def close(self):
        if self.runner:
            self.runner.pause()
            self.runner.cancel.set()
            self.close_character_sessions(self.runner)
        self.write_status("closed")
        self.window.destroy()

    @staticmethod
    def close_character_sessions(runner):
        for child in getattr(runner, "sessions", {"single": runner}).values():
            if hasattr(child.model, "close_character"):
                child.model.close_character()

    def show_character(self, contact=None, kind=None):
        if not self.runner:
            messagebox.showinfo("小泽核心", "请先连接完整微信入口；收到新文字后可查看该会话的记忆与反馈。")
            return
        panel = getattr(self, "character_panel", None)
        if panel and panel.window.winfo_exists():
            if contact is not None and not panel.select_session(contact, kind):
                messagebox.showinfo('尚无对应核心', '该会话尚无核心记录；请在原会话收到文字后再查看。没有切换到其他会话。', parent=self.window)
                return
            panel.window.lift()
            return
        if contact is not None:
            children = getattr(self.runner, 'sessions', {'single': self.runner}).values()
            if not any(s.contact == contact and s.scene.kind == kind for child in children
                       for s in getattr(child.model, 'character_sessions', {}).values()):
                messagebox.showinfo('尚无对应核心', '当前连接中没有该群的核心会话；不会创建资料或跳到其他群/私聊。', parent=self.window)
                return
        from wechat_character_panel import WechatCharacterPanel
        self.character_panel = WechatCharacterPanel(self)
        if contact is not None:
            self.character_panel.select_session(contact, kind)

    def show_people(self):
        group = self.group_name.get().strip()
        try:
            people = PeopleMemory(DATA / "people-memory.sqlite3", DATA / "relationships.json")
        except (ValueError, OSError):
            messagebox.showerror("无法读取关系", "请检查本机 data/relationships.json。")
            return
        window = tk.Toplevel(self.window)
        window.title("旧显示名绑定 / 兼容归档")
        window.geometry("760x610")
        box = ttk.Frame(window, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="当前群：" + group, wraplength=700).pack(anchor="w")
        ttk.Label(box, text='这里维护本群显示名绑定、查看旧兼容归档。新请求使用同会话的小泽核心；旧归档偏好不会随核心维护同步。核心删除不删除这里的原件，也不会自动重导。', wraplength=700).pack(anchor='w', pady=5)
        ttk.Button(box, text='打开本群实际使用的小泽核心', command=lambda: self.show_character(group, 'group')).pack(anchor='w')
        ttk.Label(box, text="主人称呼固定为孤月大人。下方填他在本群的准确显示名，用分号分隔；只用于相处语气。", wraplength=700).pack(anchor="w", pady=(8, 4))
        aliases = people.bindings["groups"].get(group, {}).get("owner_aliases", [])
        aliases_var = tk.StringVar(value="; ".join(aliases))
        ttk.Entry(box, textvariable=aliases_var).pack(fill="x")
        def save_aliases():
            if self.runner:
                messagebox.showinfo("先断开会话", "请先在主窗口断开，修改关系后重新连接。", parent=window)
                return
            try:
                set_owner_aliases(DATA / "relationships.json", group,
                                  aliases_var.get().replace("；", ";").split(";"))
            except (ValueError, OSError) as error:
                messagebox.showerror("关系未保存", str(error), parent=window)
                return
            window.destroy()
            self.show_people()
        ttk.Button(box, text="保存显示名绑定", command=save_aliases).pack(anchor="w", pady=8)
        names = list(dict.fromkeys([*aliases, *people.people(group)]))
        selected = tk.StringVar(value=names[0] if names else "")
        chooser = ttk.Combobox(box, textvariable=selected, values=names, state="readonly")
        chooser.pack(fill="x", pady=6)
        output = tk.Text(box, wrap="word", font=("Microsoft YaHei UI", 10))
        output.pack(fill="both", expand=True)
        def display(event=None):
            profile = people.profile(group, selected.get())
            count = profile["interactions"]
            lines = ["关系：" + ("主人" if profile["relation"] == "owner" else "朋友"),
                     "相处方式：" + profile["attitude"], f"记录的发言批次内消息：{count}",
                     "旧归档偏好（非当前核心快照）：" + ("；".join(profile["preferences"].values()) or "暂无"),
                     "\n近期／相关的本人发言（作为记忆证据，不代表已核实事实）："]
            lines.extend("· " + item["text"] for item in profile["self_reported_evidence"])
            output.configure(state="normal")
            output.delete("1.0", "end")
            output.insert("end", "\n".join(lines))
            output.configure(state="disabled")
        chooser.bind("<<ComboboxSelected>>", display)
        display()

    def show_moments(self):
        window = tk.Toplevel(self.window)
        window.title("朋友圈文字草稿")
        window.geometry("720x450")
        box = ttk.Frame(window, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="按小泽的人格生成文字草稿，不读取群聊或私聊记忆，不执行发布。", wraplength=660).pack(anchor="w")
        topic = tk.StringVar()
        ttk.Label(box, text="主题或可公开素材（可以留空）").pack(anchor="w", pady=(10, 4))
        ttk.Entry(box, textvariable=topic).pack(fill="x")
        output = tk.Text(box, wrap="word", font=("Microsoft YaHei UI", 11), height=8)
        output.pack(fill="both", expand=True, pady=10)
        status = tk.StringVar(value="生成后保存在本机 data/moments-drafts.json，可自行编辑或复制。")
        ttk.Label(box, textvariable=status, wraplength=660).pack(anchor="w")
        results = queue.Queue()
        def generate():
            from moments import MomentsDrafts
            cfg = dict(self.cfg)
            cfg.update({key: value.get().strip() for key, value in self.values.items()})
            try:
                generator = MomentsDrafts(cfg, PERSONA, DATA / "moments-drafts.json")
            except (RuntimeError, ValueError):
                status.set("请先在主窗口配置有效的模型接口和 Key。")
                return
            requested_topic = topic.get()
            button.configure(state="disabled")
            status.set("正在生成草稿，不会发布…")
            def work():
                try:
                    results.put((True, generator.generate(requested_topic)))
                except Exception:
                    results.put((False, "草稿生成失败，请检查模型配置与网络；未发布。"))
            threading.Thread(target=work, daemon=True).start()
        def poll_draft():
            while not results.empty():
                ok, text = results.get_nowait()
                button.configure(state="normal")
                if ok:
                    output.delete("1.0", "end")
                    output.insert("end", text)
                status.set("草稿已保存在本机，未发布。" if ok else text)
            if window.winfo_exists():
                window.after(200, poll_draft)
        button = ttk.Button(box, text="生成草稿", command=generate)
        button.pack(anchor="w", pady=6)
        poll_draft()


if __name__ == "__main__":
    # Avoid two new adapter windows replying at once.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, False, "Local\\AchengWechatAdapter")
    if not mutex:
        raise SystemExit("无法创建单实例锁。")
    if ctypes.get_last_error() == 183:
        raise SystemExit("小泽微信窗口（含兼容旧入口）已运行，请使用现有窗口。")
    App().window.mainloop()
