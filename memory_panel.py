"""Local maintenance and read-only recall diagnostics. No model or transport."""
import tkinter as tk
import hashlib
import json
from tkinter import ttk, messagebox
from memory_view import CATEGORY_NAMES, STATUS_NAMES, library_view, overview, record_detail, preview_text
from dialogue import hidden_sources

CATEGORY_LABELS = {"经历（本地聊天中提到）": "event", "明确偏好": "preference", "边界": "boundary", "临时状态（至次日零点）": "temporary"}
STATUS_LABELS = STATUS_NAMES
STATUS_FILTERS = {"当前有效": "active", "待确认": "pending", "历史 / 到期": "history", "全部状态": None,
                  **{label: key for key, label in STATUS_NAMES.items() if key not in ("active", "pending")}}


class MemoryPanel:
    def __init__(self, app):
        self.app, self.scene, self.core = app, app.scene, app.core
        self.window = tk.Toplevel(app.window)
        self.window.title("小泽 · 当前场景记忆 / 证据")
        self.window.geometry(f"1080x{min(800, max(600, self.window.winfo_screenheight() - 100))}")
        self.window.minsize(900, 600)
        self.window.transient(app.window)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        isolated = "独立体验：" + app.practice.get() if app.practice_isolated.get() else "普通试聊"
        self.scope = getattr(app, "memory_scope_label", app.mode.get() + " · " + isolated + "\n" + self.scene.label)
        notice = getattr(app, "memory_scope_notice", "仅当前模式/场景；主人固定关系不属于可修改记忆。")
        ttk.Label(self.window, text=self.scope + "\n" + notice, padding=8, wraplength=1000).pack(anchor="w")
        filters = ttk.Frame(self.window, padding=(10, 4))
        filters.pack(fill="x")
        self.people = self.person_labels()
        initial = next((label for label, pid in self.people.items()
                        if pid is not None and self.scene.person(pid).display == app.speaker.get()), '全部人物')
        self.person_filter = tk.StringVar(value=initial)
        self.category_filter = tk.StringVar(value="全部类别")
        self.status_filter = tk.StringVar(value="当前有效")
        self.keyword = tk.StringVar()
        for variable, values, width in ((self.person_filter, list(self.people), 24),
                                         (self.category_filter, ["全部类别", *CATEGORY_NAMES.values()], 14),
                                         (self.status_filter, list(STATUS_FILTERS), 15)):
            control = ttk.Combobox(filters, textvariable=variable, values=values, state="readonly", width=width)
            control.pack(side="left", padx=(0, 5))
            if variable is self.person_filter:
                self.person_box = control
            control.bind("<<ComboboxSelected>>", self.refresh)
        search = ttk.Entry(filters, textvariable=self.keyword, width=24)
        search.pack(side="left", fill="x", expand=True)
        search.bind("<Return>", self.refresh)
        ttk.Button(filters, text="搜索 / 刷新", command=self.refresh).pack(side="left", padx=5)
        self.counts = tk.StringVar()
        ttk.Label(self.window, textvariable=self.counts, padding=(10, 4), wraplength=1030).pack(anchor="w")
        self.tabs = ttk.Notebook(self.window)
        self.tabs.pack(fill="both", expand=True, padx=10)
        library, profile, preview, mark = (ttk.Frame(self.tabs, padding=6) for _ in range(4))
        for frame, title in ((library, "记忆与维护"), (profile, "人物概览"), (preview, "离线召回预览"), (mark, "标记候选")):
            self.tabs.add(frame, text=title)
        self.library_tab, self.preview_tab, self.mark_tab = library, preview, mark
        self.view = ttk.Treeview(library, columns=("person", "kind", "status", "value"), show="headings", height=6, selectmode="browse")
        for key, label, width in (("person", "人物", 140), ("kind", "类别", 90), ("status", "状态", 100), ("value", "内容（来源、时间、期限见详情）", 630)):
            self.view.heading(key, text=label)
            self.view.column(key, width=width)
        self.view.pack(fill="both", expand=True)
        self.view.bind("<<TreeviewSelect>>", self.selected)
        self.evidence = self.text_area(library, height=8)
        actions = ttk.Frame(library)
        actions.pack(fill="x", pady=3)
        for label, command in (("确认候选", self.accept), ("拒绝候选", self.reject),
                               ("更正内容…", lambda: self.edit("correction")), ("偏好改变…", lambda: self.edit("update")),
                               ("停止召回", lambda: self.action("suppress")), ("删除长期记录", lambda: self.action("delete")),
                               ("预览关联删除…", self.forget)):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 3))
        self.edit_value, self.edit_hint = tk.StringVar(), tk.StringVar(value="选择条目后点击更正；未保存的输入会保留。")
        self.edit_id, self.edit_change, self.edit_initial = None, "correction", ""
        ttk.Label(library, textvariable=self.edit_hint, wraplength=1000).pack(anchor="w")
        edit_row = ttk.Frame(library)
        edit_row.pack(fill="x", pady=4)
        ttk.Entry(edit_row, textvariable=self.edit_value).pack(side="left", fill="x", expand=True)
        ttk.Button(edit_row, text="保存所绑定条目的修改", command=self.save_edit).pack(side="left", padx=4)
        ttk.Button(edit_row, text="放弃未保存编辑", command=self.discard_edit).pack(side="left")
        ttk.Label(profile, text="概览使用上方人物选择；不受类别/状态/关键词影响。不保存传记。").pack(anchor="w")
        self.profile = self.text_area(profile, height=14)
        self.preview_person = tk.StringVar(value=initial if initial != '全部人物' else '')
        preview_row = ttk.Frame(preview)
        preview_row.pack(fill="x")
        self.preview_box = ttk.Combobox(preview_row, textvariable=self.preview_person, values=[k for k, v in self.people.items() if v is not None], state="readonly", width=24)
        self.preview_box.pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(preview_row, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(preview_row, text="本地预览（不联网）", command=self.preview).pack(side="left")
        ttk.Label(preview, text="独立问题框；使用主窗口召回/话题开关。不提取新资料，不改动刚完成对话的证据。", wraplength=990).pack(anchor="w", pady=5)
        self.preview_output = self.text_area(preview, height=17)
        self.preview_report = None
        # A result belongs to the selected person and query at click time.
        # Editing either control must not leave the old report under new inputs.
        self.query.trace_add("write", lambda *_: self.clear_preview())
        self.preview_person.trace_add("write", lambda *_: self.clear_preview())
        self.turn = tk.StringVar()
        self.turn_box = ttk.Combobox(mark, textvariable=self.turn, state="readonly", width=90)
        self.turn_box.pack(fill="x", pady=6)
        self.turn_box.bind('<<ComboboxSelected>>', self.choose_turn)
        self.turn_id, self.mark_bound_id = None, None
        self.category = tk.StringVar(value=next(iter(CATEGORY_LABELS)))
        ttk.Combobox(mark, textvariable=self.category, values=list(CATEGORY_LABELS), state="readonly", width=30).pack(anchor="w")
        self.quote, self.content = tk.StringVar(), tk.StringVar()
        self._turn_sync = False
        self.turn.trace_add('write', lambda *_: None if self._turn_sync else self.choose_turn())
        for label, value in (("证据：用户原话连续片段（1—160字）", self.quote), ("保留的内容（1—280字；身份与范围不可改）", self.content)):
            ttk.Label(mark, text=label).pack(anchor="w", pady=(10, 3))
            ttk.Entry(mark, textvariable=value).pack(fill="x")
        ttk.Button(mark, text="加入候选（不联网）", command=self.mark).pack(anchor="e", pady=8)
        ttk.Label(mark, text="确认是保留决定，不是核实现实。普通寒暄不必长期保存；有限规则漏掉的内容可在此选择。", wraplength=990).pack(anchor="w")
        self.message = tk.StringVar(value="停止召回保留正文；关联删除先预览。条目满可删除不再需要的资料；来源标记预算不会随条目删除自动释放。")
        ttk.Label(self.window, textvariable=self.message, wraplength=1030, padding=8).pack(anchor="w")
        self.records, self.turns = {}, {}
        self._poll_id = None
        self._stamp = None
        self.window.bind('<Destroy>', self.destroyed, add='+')
        self.refresh()
        self._poll_id = self.window.after(500, self.poll)

    def person_labels(self):
        people = list(self.scene.people)
        # Stable IDs disambiguate duplicate displays and the reserved all label.
        duplicates = {p.display for p in people if sum(q.display == p.display for q in people) > 1}
        labels = {'全部人物': None}
        for p in people:
            label = p.display + ' [' + p.id + ']' if p.display in duplicates or p.display == '全部人物' else p.display
            while label in labels:
                label += ' [' + p.id + ']'
            labels[label] = p.id
        return labels

    def destroyed(self, event):
        if event.widget is self.window and self._poll_id is not None:
            try:
                self.window.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None

    def poll(self):
        if not self.window.winfo_exists():
            return
        if not getattr(self.app, 'memory_active', lambda: True)():
            self.window.destroy()
            return
        data = self.core.snapshot(self.scene.id, compatibility=False)
        stamp = (hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                 tuple((p.id, p.display) for p in self.scene.people))
        if stamp != self._stamp:
            self.refresh()
            self._stamp = stamp
        self._poll_id = self.window.after(500, self.poll)

    def choose_turn(self, _event=None):
        proposed = self.turn_labels.get(self.turn.get())
        if proposed != self.mark_bound_id and (self.quote.get() or self.content.get()):
            if not messagebox.askyesno('原话绑定', '输入仍属于原条目。清空证据及内容并明确改选这条原话？', parent=self.window):
                self._turn_sync = True
                self.turn.set(next((k for k, v in self.turn_labels.items() if v == self.turn_id), ''))
                self._turn_sync = False
                return
            self.quote.set('')
            self.content.set('')
        self.turn_id = self.mark_bound_id = proposed

    @staticmethod
    def text_area(parent, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        widget = tk.Text(frame, wrap="word", height=height, state="disabled")
        scroll = ttk.Scrollbar(frame, command=widget.yview)
        scroll.pack(side="right", fill="y")
        widget.configure(yscrollcommand=scroll.set)
        widget.pack(side="left", fill="both", expand=True)
        return widget

    @staticmethod
    def display(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def close(self):
        self.window.grab_release()
        self.window.destroy()
        self.app.render()

    def refresh(self, _event=None):
        old_filter = self.people.get(self.person_filter.get(), 'missing')
        old_preview = self.people.get(self.preview_person.get(), 'missing')
        self.people = self.person_labels()
        for variable, pid in ((self.person_filter, old_filter), (self.preview_person, old_preview)):
            variable.set(next((label for label, value in self.people.items() if value == pid), ''))
        if (old_filter not in ('missing', None) and old_filter not in self.people.values()
                or old_preview not in ('missing', None) and old_preview not in self.people.values()):
            self.message.set('原人物选择已失效；问题和编辑输入保留，请明确重选人物。')
        self.person_box.configure(values=list(self.people))
        self.preview_box.configure(values=[k for k, v in self.people.items() if v is not None])
        data = self.core.snapshot(self.scene.id)
        chosen = self.view.selection()
        self.records = {r["id"]: r for r in data["memories"]}
        kind = next((k for k, label in CATEGORY_NAMES.items() if label == self.category_filter.get()), None)
        result = library_view(data, person_id=self.people.get(self.person_filter.get(), '__missing__'), category=kind,
                              status=STATUS_FILTERS[self.status_filter.get()], keyword=self.keyword.get())
        self.counts.set(f"筛选 {result['matched']} 条 · 本场景全库 {result['total']}/{result['capacity']}（含历史） · 待确认 {result['pending']}/{result['pending_capacity']} · 来源去重 {result['processed']}/{result['source_capacity']}，撤回标记 {result['blocked']}/{result['source_capacity']}。" + result["empty"])
        if hasattr(self.app, 'memory_status'):
            self.counts.set(self.counts.get() + '\n' + self.app.memory_status())
        for item in self.view.get_children():
            self.view.delete(item)
        for r in result["records"]:
            self.view.insert("", "end", iid=r["id"], values=(self.scene.person(r["person_id"]).display, CATEGORY_NAMES[r["category"]], STATUS_NAMES[r["status"]], r["content"]))
        if chosen and self.view.exists(chosen[0]):
            self.view.selection_set(chosen[0])
            self.selected()
        else:
            self._display(result["empty"] or "请选择条目查看最小证据、更正链、来源、时间与期限。筛选不会自动选中另一条。")
        hidden = hidden_sources(data)
        self.turns = {t["id"]: t for t in data["turns"] if t["kind"] == "local_exchange" and t['text'] and t['id'] not in hidden}
        self.turn_labels = {f"{i+1}. {self.scene.person(t['speaker_id']).display}：{t['text'][:45]}": t["id"] for i, t in enumerate(self.turns.values())}
        self.turn_box.configure(values=list(self.turn_labels))
        self._turn_sync = True
        self.turn.set(next((label for label, tid in self.turn_labels.items() if tid == self.turn_id), ''))
        self._turn_sync = False
        if self.turn_id and self.turn_id not in self.turns:
            self.message.set('原话已失效或滚出窗口；输入保留但不能提交。请明确重选原话。')
        if self.edit_id and self.edit_id not in self.records:
            self.edit_hint.set('原编辑条目已失效；输入保留但不能提交，请放弃或明确重选。')
        selected_pid = self.people.get(self.person_filter.get(), '__missing__')
        people = [selected_pid] if selected_pid not in (None, '__missing__') else [p.id for p in self.scene.people] if selected_pid is None else []
        self.display(self.profile, "\n\n——\n\n".join(overview(self.scene, data, p) for p in people))
        self.clear_preview()

    def clear_preview(self):
        self.preview_report = None
        self.display(self.preview_output, "请输入问题后主动预览；刷新或维护后请重新预览。其他场景不在范围内。")

    def _display(self, value):
        self.display(self.evidence, value)

    def selected(self, _event=None):
        items = self.view.selection()
        if items and items[0] in self.records:
            self._display(record_detail(self.scene, list(self.records.values()), self.records[items[0]]))

    def run(self, callback, *, prepare_view=None, focus_result=False, success_message="已更新本地记忆；在途的旧回复会作废。"):
        try:
            result = callback()
        except (ValueError, RuntimeError, OSError, StopIteration) as error:
            self.message.set((str(error) if isinstance(error, (ValueError, RuntimeError)) else "操作未完成：检查选择或本地文件权限。") + " 未保存的编辑保留，可核对后重试。")
            return False
        try:
            if prepare_view:
                prepare_view()
            self.refresh()
            if focus_result and result and self.view.exists(result):
                self.view.selection_set(result)
                self.selected()
            self.app.render()
            self.message.set(success_message)
        except (tk.TclError, RuntimeError, ValueError, KeyError):
            self.message.set("本地提交已完成，但显示刷新失败；重新打开查看，不要重复提交。")
        return True

    def chosen(self):
        items = self.view.selection()
        if len(items) != 1 or items[0] not in self.records:
            self.message.set("请只选择一条可见记录；筛选后不会替你选择其他条目。")
            return None
        current = self.core.snapshot(self.scene.id, compatibility=False)
        if not any(r['id'] == items[0] for r in current['memories']):
            self.message.set('原记录已失效，请刷新并明确重选；未保存输入保留。')
            return None
        return items[0]

    def accept(self):
        record_id = self.chosen()
        if record_id:
            def accept_record():
                self.core.change_memory(self.scene.id, record_id, "accept")
                return record_id
            self.run(accept_record, prepare_view=lambda: self.status_filter.set("当前有效"), focus_result=True,
                     success_message="候选已确认；已切换当前有效列表，仍以原ID核对条目。")

    def reject(self):
        record_id = self.chosen()
        hint = ("删除候选并阻止同源再次提取？近期用户原话保留；相关话题、草稿、派生助手正文及反馈会清理，"
                "来源未知的旧助手正文也保守清理。若有管理中v1原件/备份，会清空整个本场景。")
        if record_id and self.records[record_id]["status"] == "pending" and messagebox.askyesno("拒绝候选", hint, parent=self.window):
            if self.run(lambda: self.core.change_memory(self.scene.id, record_id, "reject")) and self.edit_id == record_id:
                self.discard_edit()

    def discard_edit(self):
        self.edit_id, self.edit_initial = None, ""
        self.edit_value.set("")
        self.edit_hint.set("选择条目后点击更正；未保存的输入会保留。")

    def edit(self, change):
        record_id = self.chosen()
        if not record_id:
            return
        if self.edit_id and self.edit_value.get() != self.edit_initial:
            if not messagebox.askyesno("未保存编辑", "替换当前未保存的编辑？取消可保留原输入。", parent=self.window):
                return
        self.edit_id, self.edit_change = record_id, change
        self.edit_initial = self.records[record_id]["content"]
        self.edit_value.set(self.edit_initial)
        self.edit_hint.set(("更正" if change == "correction" else "偏好改变") + "绑定ID：" + record_id + "（1—300字；保持选中此条再保存）")

    def save_edit(self):
        if not self.edit_id or self.chosen() != self.edit_id:
            self.message.set("编辑仍绑定原ID。请恢复筛选并选回该条再保存，或明确放弃编辑。")
            return
        record_id, content, change = self.edit_id, self.edit_value.get(), self.edit_change
        current = next((r for r in self.core.snapshot(self.scene.id, compatibility=False)['memories'] if r['id'] == record_id), None)
        if not current or current['content'] != self.edit_initial:
            self.message.set('编辑绑定的内容已变化；输入保留，请重新核对条目后明确编辑。')
            return
        if self.run(lambda: self.core.change_memory(self.scene.id, record_id, "edit", content, change=change)):
            self.discard_edit()

    def action(self, action):
        record_id = self.chosen()
        if not record_id:
            return
        hint = ("删除所选长期条目和证据，但保留近期用户原话。清理相关派生助手正文/反馈；来源未知的旧助手正文也会保守清理。管理中v1原件/备份会清空整个本场景。"
                if action == "delete" else "停止召回但保留条目与正文；旧来源/派生内容不再进入请求。")
        if messagebox.askyesno("核对范围", hint + "相关话题和草稿也可能失效。", parent=self.window):
            if self.run(lambda: self.core.change_memory(self.scene.id, record_id, action)) and self.edit_id == record_id:
                self.discard_edit()

    def forget(self):
        record_id = self.chosen()
        if not record_id:
            return
        try:
            p = self.core.forget_preview(self.scene.id, record_id)
        except (ValueError, RuntimeError, StopIteration):
            self.message.set("条目状态已变化，预览未完成；请刷新。")
            return
        hint = (f"移除{p['records']}条关联记忆、{p['turns']}轮原文、{p['feedback']}条反馈；清理{p['assistant_messages']}条关联或来源未知助手正文。\n"
                f"移除{p['topics']}条话题、{p['drafts']}条草稿及其反馈，保留后续无关用户原话。\n"
                + ("管理中v1原件/备份会清空整个本场景。\n" if p["legacy_scene_copies"] else "")
                + "同源其他记忆一起清理；保留无原文的防重放ID。不代表删除服务商/系统备份。继续？")
        if messagebox.askyesno("关联删除影响预览", hint, parent=self.window):
            if self.run(lambda: self.core.forget_memory(self.scene.id, record_id)):
                self.quote.set("")
                self.content.set("")
                self.discard_edit()

    def mark(self):
        turn_id = self.turn_id
        current = self.core.snapshot(self.scene.id, compatibility=False)
        if (not turn_id or turn_id != self.mark_bound_id or self.turn_labels.get(self.turn.get()) != turn_id
                or not any(t['id'] == turn_id for t in current['turns']) or turn_id in hidden_sources(current)):
            self.message.set("原话未选择或已失效。输入保留，请明确重新选择来源；不会自动绑定最新条。")
            return
        person_label = self.scene.person(self.turns[turn_id]["speaker_id"]).display
        def prepare_view():
            self.person_filter.set(next(label for label, pid in self.people.items() if pid == self.turns[turn_id]['speaker_id']))
            self.status_filter.set("待确认")
            self.category_filter.set("全部类别")
            self.keyword.set("")
            self.tabs.select(self.library_tab)
        self.run(lambda: self.core.mark_turn(self.scene.id, turn_id, self.quote.get(), self.content.get(), CATEGORY_LABELS[self.category.get()]),
                 prepare_view=prepare_view, focus_result=True,
                 success_message="已加入待确认，切换到来源人物的候选列表。请选择后确认。")

    def preview(self):
        try:
            person_id = self.people.get(self.preview_person.get())
            if person_id is None:
                raise ValueError('请选择人物。')
            report = self.core.recall_preview(self.scene.id, person_id, self.query.get(),
                                              use_memory=self.app.use_memory.get(), use_topics=self.app.use_topics.get())
            self.preview_report = report
            self.display(self.preview_output, self.scene.label + " · " + self.scene.person(person_id).display + "\n\n" + preview_text(report))
            self.message.set("只查看当前快照；未联网、未提取、未保存，未更改上轮证据。")
        except (ValueError, RuntimeError, StopIteration):
            self.clear_preview()
            self.message.set("预览未完成，请核对人物和1—4000字问题；输入保留，没有生成或提交。")
