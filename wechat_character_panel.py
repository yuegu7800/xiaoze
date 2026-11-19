"""Thin Tk view over the SAME character sessions used by the full runners."""
import tkinter as tk
import hashlib
import json
from tkinter import ttk, messagebox, simpledialog
from types import SimpleNamespace

from dialogue import reply_available
from feedback_panel import FeedbackPanel
from memory_panel import MemoryPanel


class WechatCharacterPanel:
    def __init__(self, app):
        self.app, self.runner = app, app.runner
        self.window = tk.Toplevel(app.window)
        self.window.title('小泽 · 微信记忆与逐轮反馈')
        self.window.geometry('880x680')
        self.sessions, self.rows = {}, set()
        self.chosen = tk.StringVar()
        self.picker = ttk.Combobox(self.window, textvariable=self.chosen, state='readonly')
        self.picker.pack(fill='x', padx=12, pady=10)
        self.picker.bind('<<ComboboxSelected>>', lambda _e: self.refresh())
        ttk.Label(self.window, text='这里就是完整微信程序正在使用的核心；发送操作不等于对方收到。\n选择一轮再评价；旧助手回复来源不明，不补造证据。核心删除不删除原兼容归档；原归档不会被再次自动导入。',
                  wraplength=820).pack(anchor='w', padx=12)
        frame = ttk.Frame(self.window)
        frame.pack(fill='both', expand=True, padx=12, pady=10)
        self.tree = ttk.Treeview(frame, columns=('person', 'input', 'reply'), show='headings', selectmode='browse')
        for key, title, width in (('person', '发言者', 100), ('input', '本轮输入（节选）', 280), ('reply', '小泽回复（节选）', 350)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width)
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        buttons = ttk.Frame(self.window)
        buttons.pack(fill='x', padx=12)
        ttk.Button(buttons, text='评价所选回复 / 本轮取数', command=self.feedback).pack(side='left', padx=3)
        ttk.Button(buttons, text='查看、纠正和删除记忆', command=self.memory).pack(side='left', padx=3)
        ttk.Button(buttons, text='把所选原话保留为待续话题', command=self.topic).pack(side='left', padx=3)
        ttk.Button(buttons, text='管理待续话题', command=self.manage_topics).pack(side='left', padx=3)
        self.notice = tk.StringVar(value='预览不提交人物记忆；正式回复仅在原后端返回发送操作成功后提交。')
        ttk.Label(self.window, textvariable=self.notice, wraplength=820).pack(anchor='w', padx=12, pady=12)
        self._stamp = None
        self.refresh()
        self._poll_id = self.window.after(400, self.poll)
        self.window.bind('<Destroy>', self.destroyed, add='+')

    def destroyed(self, event):
        if event.widget is self.window:
            try:
                self.window.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self.rows, self.sessions, self._feedback = set(), {}, {}
            self._stamp = None

    def available(self):
        runners = getattr(self.runner, 'sessions', {'single': self.runner})
        result = {}
        for runner in runners.values():
            for session in list(getattr(runner.model, 'character_sessions', {}).values()):
                result[session.scene.label] = session
        return result

    def session(self):
        # Picker labels are only UI keys; actions always use the captured
        # session/Core and stable turn ID, never the app's current mode fields.
        return self.sessions.get(self.chosen.get())

    def select_session(self, contact, kind):
        if self.app.runner is not self.runner:
            return False
        sessions = self.available()
        match = next((label for label, session in sessions.items()
                      if session.contact == contact and session.scene.kind == kind), None)
        if match is None:
            return False
        self.chosen.set(match)
        self._stamp = None
        self.refresh()
        return True

    def refresh(self):
        if self.app.runner is not self.runner:
            self.window.destroy()
            return
        self.sessions = self.available()
        self.picker.configure(values=list(self.sessions))
        if self.chosen.get() not in self.sessions:
            self.chosen.set(next(iter(self.sessions), ''))
        session = self.session()
        if not session:
            self.notice.set('尚无可用核心会话；收到新文字并生成预览后即可在这里查看。')
            return
        with session.core._lock:
            self.notice.set(session.import_status())
            data = session.core.snapshot(session.scene_id, compatibility=False)
            visible = [t for t in data['turns'] if not t['reply'] or reply_available(t, data)]
            stamp = (session.scene_id, hashlib.sha256(json.dumps(visible, ensure_ascii=False).encode()).hexdigest())
            if stamp == self._stamp:
                return
            selected = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            self.rows = {t['id'] for t in visible}
            for turn in visible:
                self.tree.insert('', 'end', iid=turn['id'], values=(session.scene.person(turn['speaker_id']).display,
                    turn['text'][:100] or '主动开口（本机时刻）', turn['reply'][:140] or '仅听到；没有回复'))
            if selected and selected[0] in self.rows:
                self.tree.selection_set(selected[0])
            self._stamp = stamp

    def poll(self):
        if self.window.winfo_exists():
            self.refresh()
            if self.window.winfo_exists():
                self._poll_id = self.window.after(400, self.poll)

    def selected(self):
        self.refresh()
        session = self.session()
        selection = self.tree.selection()
        if not session or not selection or selection[0] not in self.rows:
            self.notice.set('请先选中一轮；已删除、淘汰的旧操作不会指向新轮次。')
            return None, None
        turn = next((t for t in session.core.snapshot(session.scene_id)['turns'] if t['id'] == selection[0]), None)
        return session, turn

    def feedback(self):
        session, turn = self.selected()
        if not turn or not turn['reply']:
            self.notice.set('所选轮没有已提交回复；预览与只听不说不能评价成成功回复。')
            return
        key = (session.core, session.scene_id)
        panels = getattr(self, '_feedback', {})
        panel = panels.get(key)
        if panel and panel.window.winfo_exists():
            panel.select_turn(turn['id'])
            panel.window.lift()
        else:
            panels[key] = FeedbackPanel(SimpleNamespace(window=self.window), core=session.core, scene=session.scene,
                                       mode_label='微信完整版 · 发送操作记录', turn_id=turn['id'])
        self._feedback = panels

    def memory(self):
        session = self.session()
        if not session or not session.scene.people:
            self.notice.set('先收到一条可归属的新文字，再管理该会话记忆。')
            return
        facade = SimpleNamespace(window=self.window, core=session.core, scene=session.scene,
            practice_isolated=tk.BooleanVar(value=False), mode=tk.StringVar(value='微信完整版'),
            speaker=tk.StringVar(value=session.scene.people[0].display),
            memory_scope_label='微信完整版 · 人物核心\n' + session.scene.label,
            memory_scope_notice='仅管理当前会话的小泽核心，主人固定关系不可修改。删除会清理核心来源与依赖，不删除原兼容归档；原归档不会再次自动导入。',
            memory_active=lambda: self.app.runner is self.runner and self.window.winfo_exists(),
            memory_status=session.import_status,
            use_memory=tk.BooleanVar(value=True), use_topics=tk.BooleanVar(value=True), render=self.refresh)
        MemoryPanel(facade)

    def topic(self):
        session, turn = self.selected()
        if not turn or not turn['text']:
            return
        quote = simpledialog.askstring('待续话题', '从所选原话中复制一小段作为依据（不超过160字）', parent=self.window)
        if quote is None:
            return
        title = simpledialog.askstring('话题标题', '简短标题', parent=self.window)
        if not title:
            return
        direction = simpledialog.askstring('以后如何接话', '只是可继续的方向，不表示已经发生', parent=self.window)
        if not direction:
            return
        try:
            # mark_topic itself checks current source and quote before commit.
            if 'activity' not in session.core.snapshot(session.scene_id):
                session.core.enable_activity(session.scene_id)
            session.core.mark_topic(session.scene_id, turn['id'], quote, title, direction,
                                    target=turn['speaker_id'], confirm=True)
        except (RuntimeError, ValueError, OSError, StopIteration) as error:
            self.notice.set(str(error) if isinstance(error, (RuntimeError, ValueError)) else '话题未保存，检查文件权限。')
            return
        self.notice.set('话题已保留；相关时可自然接续，没有发送消息或启动新调度。')
        self.refresh()

    def manage_topics(self):
        session = self.session()
        if not session:
            return
        window = tk.Toplevel(self.window)
        window.title('当前微信会话 · 待续话题')
        window.geometry('650x360')
        tree = ttk.Treeview(window, columns=('target', 'title', 'state'), show='headings', selectmode='browse')
        for key, label in (('target', '人物'), ('title', '话题'), ('state', '状态')):
            tree.heading(key, text=label)
        tree.pack(fill='both', expand=True, padx=10, pady=10)
        notice = tk.StringVar(value='人工话题相关时才提供，不等于已说过或现实发生。')
        ttk.Label(window, textvariable=notice, wraplength=610).pack(anchor='w', padx=10)
        def refresh():
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for topic in session.core.snapshot(session.scene_id).get('activity', {}).get('topics', []):
                target = '全群' if topic['target'] == 'all' else session.scene.person(topic['target']).display
                tree.insert('', 'end', iid=topic['id'], values=(target, topic['title'], topic['status']))
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
        def change(action):
            selection = tree.selection()
            if not selection:
                return
            if action == 'delete' and not messagebox.askyesno('撤回话题', '删除会撤回该话题，并清理依赖它的回复。继续？', parent=window):
                return
            try:
                session.core.change_topic(session.scene_id, selection[0], action)
            except (RuntimeError, ValueError, OSError, StopIteration):
                notice.set('未提交，请刷新并核对话题或存储；没有自动重试。')
                return
            try:
                refresh()
                self.refresh()
                notice.set('已提交。没有发送消息或新增调度。')
            except (RuntimeError, ValueError, tk.TclError):
                notice.set('已提交但显示失败，请重新打开，不要重复提交。')
        controls = ttk.Frame(window)
        controls.pack(pady=10)
        for label, action in (('暂停', 'pause'), ('重新启用', 'activate'), ('结束', 'close'), ('删除并撤回', 'delete')):
            ttk.Button(controls, text=label, command=lambda a=action: change(a)).pack(side='left', padx=5)
        refresh()
        pending = {}
        def poll():
            if window.winfo_exists():
                refresh()
                pending['id'] = window.after(400, poll)
        def destroyed(event):
            if event.widget is window and 'id' in pending:
                window.after_cancel(pending['id'])
        window.bind('<Destroy>', destroyed, add='+')
        pending['id'] = window.after(400, poll)
        return window
