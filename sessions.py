"""One desktop connection, serial routing, separate group/private state."""
from pathlib import Path
import threading
import time

from core import OWNER, ChatModel, PrivateRunner, PrivateScope, ScopedTransport, ScopeError
from groups import GroupChatModel, GroupPolicy, GroupRunner, GroupScope


class RoutedTransport(ScopedTransport):
    def __init__(self, backend, target, scope, parse_title, lock, can_send):
        super().__init__(backend, scope, parse_title, can_send)
        self.target, self.lock = target, lock

    def read(self):
        with self.lock:
            self.backend.select_target(self.target)
            return super().read()

    def send(self, name, text):
        with self.lock:
            if name != self.target or not self.can_send():
                raise ScopeError("目标变化或已暂停，本条不发送。")
            self.backend.select_target(self.target)
            # Every send rechecks the title and conversation type after routing.
            return super().send(name, text)


class MultiRunner:
    def __init__(self, backend, cfg, persona_path, data_path, parse_title, notify, stop_event=None, model_factory=None):
        self.backend, self.notify = backend, notify
        self.cancel = stop_event or threading.Event()
        self.enabled = threading.Event()
        self.ready = False
        self.mode, self.state = "preview", "idle"
        self.epoch = 0
        self.thread = None
        self.started_at = time.monotonic()
        self.lock = threading.RLock()
        self._proactive = False
        self._retry_at = {"private": 0, "group": 0}
        root = Path(data_path)
        private_model = model_factory or ChatModel
        group_model = model_factory or GroupChatModel
        if cfg["group"] == OWNER:
            raise ValueError("双会话的群名与私聊备注不能完全同名。")
        private_scope, group_scope = PrivateScope(), GroupScope(cfg["group"])
        policy = GroupPolicy(cfg["group_nickname"], cfg["group_behavior"], cfg["group_frequency"])
        can_send = lambda: self.enabled.is_set() and not self.cancel.is_set() and self.mode == "live"
        private_transport = RoutedTransport(backend, OWNER, private_scope, parse_title, self.lock, can_send)
        group_transport = RoutedTransport(backend, cfg["group"], group_scope, parse_title, self.lock, can_send)
        private = PrivateRunner(private_transport,
            private_model(cfg, private_scope, persona_path, root / "memory.json"),
            root / "preview.jsonl", lambda k, t: self.child_event("private", k, t),
            {OWNER: "owner"}, stop_event=self.cancel)
        group = GroupRunner(group_transport,
            group_model(cfg, group_scope, persona_path, root / "group-memory.json", policy),
            root / "group-preview.jsonl", lambda k, t: self.child_event("group", k, t),
            policy, stop_event=self.cancel)
        self.sessions = {"private": private, "group": group}

    @property
    def proactive(self):
        return self._proactive

    @proactive.setter
    def proactive(self, value):
        self._proactive = bool(value) and self.mode == "live"
        for runner in self.sessions.values():
            runner.proactive = self._proactive

    def emit(self, kind, text):
        self.state = kind
        self.notify(kind, text)

    def child_event(self, session, kind, text):
        label = "私聊" if session == "private" else "群聊"
        if kind == "ready":
            self.notify("session_ready", f"【{label}】{text}")
            return
        if kind in ("preview", "preview_silent", "error", "paused"):
            self.pause()
        self.emit(kind, f"【{label}】{text}")

    def pause(self):
        self.epoch += 1
        self.enabled.clear()
        self.state = "paused"
        for runner in self.sessions.values():
            runner.pause()

    def resume(self, mode="preview", proactive=False):
        if not self.ready:
            raise RuntimeError("请先连接微信。")
        if mode not in ("preview", "live"):
            raise ValueError("未知模式。")
        self.mode = mode
        self.epoch += 1
        self.proactive = proactive
        for runner in self.sessions.values():
            if runner.ready:
                runner.resume(mode, self.proactive)
        self.enabled.set()
        self.emit("listening", "群聊＋孤月私聊轮询中；" +
                  ("只生成一轮预览，之后两边一起暂停。" if mode == "preview" else "两边的新文字分别处理并回复。"))

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("连接线程已启动。")
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def poll_session(self, name):
        runner = self.sessions[name]
        if time.monotonic() < self._retry_at[name]:
            return
        if not runner.ready:
            if runner.initialize_session() and self.enabled.is_set():
                runner.resume(self.mode, self.proactive)
        elif self.enabled.is_set():
            runner.process_once()
        if runner.read_failures:
            self._retry_at[name] = time.monotonic() + min(15, 1.5 * 2 ** min(runner.read_failures, 4))
        else:
            self._retry_at[name] = 0

    def run(self):
        try:
            self.emit("connecting", "正在建立群聊与私聊的独立旧消息基线…")
            self.backend.connect()
            for name in self.sessions:
                self.poll_session(name)
                if self.cancel.is_set():
                    return
            self.ready = True
            self.emit("ready", "双会话已连接；未找到的目标会自动重试，请把指定群与孤月私聊置顶。")
            turn = 0
            while not self.cancel.wait(1.5):
                if time.monotonic() - self.started_at >= 86400:
                    self.pause()
                    self.emit("paused", "一天试用已结束。")
                    break
                if not self.enabled.is_set():
                    continue
                self.poll_session(tuple(self.sessions)[turn % len(self.sessions)])
                turn += 1
        except Exception as error:
            self.pause()
            self.emit("error", f"双会话连接失败（{type(error).__name__}），已停止；请检查微信窗口。")
        finally:
            self.pause()
            self.ready = False
            for runner in self.sessions.values():
                runner.ready = False
                if hasattr(runner.model, "close_character"):
                    runner.model.close_character()
            try:
                self.backend.close()
            except Exception:
                pass
            self.emit("stopped", "双会话监听已结束，可重新连接。")

    def snapshot(self):
        sessions = {name: runner.snapshot() for name, runner in self.sessions.items()}
        polls = [s["last_poll_at"] for s in sessions.values() if s["last_poll_at"]]
        return {"state": self.state, "mode": self.mode, "enabled": self.enabled.is_set(),
                "ready": self.ready, "last_poll_at": max(polls, default=None),
                "visible_messages": sum(s["visible_messages"] for s in sessions.values()),
                "received_messages": sum(s["received_messages"] for s in sessions.values()),
                "read_failures": sum(s["read_failures"] for s in sessions.values()),
                "sessions": sessions}
