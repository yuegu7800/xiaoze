"""Original local feedback flow, with read-only, turn-bound transient evidence."""
import tkinter as tk
from tkinter import ttk

from memory_store import FEEDBACK_TAGS, MEMORY_FEEDBACK_TAGS
from request_evidence import evidence_text, evidence_summary
from character_service import Cancelled


class FeedbackPanel:
    def __init__(self, app, *, core=None, scene=None, mode_label=None, turn_id=None):
        self.core = core if core is not None else app.core
        self.scene = scene if scene is not None else app.scene
        self.mode_label = mode_label if mode_label is not None else app.feedback_mode_label()
        self.window = tk.Toplevel(app.window)
        self.window.title("本地体验反馈 · 不自动上传")
        self.window.geometry("760x650")
        self.window.transient(app.window)
        # Modeless: a user can select another reply in the original transcript.
        # This window retains its original Core/scene, including while typing.
        self.window.bind("<Destroy>", self._destroyed)
        self.submitted = False
        self.editing_turn_id = None
        self._selected_turn_id = None
        self._refreshing = self._editing_guard = False
        self.rows, self.labels = {}, {}
        self._last_view = None
        self.chosen = tk.StringVar()
        self.choice = ttk.Combobox(self.window, textvariable=self.chosen, state="readonly", width=80)
        self.choice.pack(fill="x", padx=12, pady=12)
        self.choice.bind("<<ComboboxSelected>>", self.show_evidence)
        ttk.Label(self.window, text="先看中文摘要；技术详情保留原通道ID。可回聊天点击另一条回复，未保存反馈不会跟着转绑。",
                  wraplength=710).pack(anchor="w", padx=12, pady=(0, 5))
        self.tags = {}
        row = ttk.Frame(self.window)
        row.pack(fill="x", padx=12)
        for i, tag in enumerate(t for t in FEEDBACK_TAGS if t not in MEMORY_FEEDBACK_TAGS):
            self.tags[tag] = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text=tag, variable=self.tags[tag]).grid(row=i//4, column=i%4, sticky="w", padx=5, pady=4)
        row = ttk.LabelFrame(self.window, text="记忆问题判断（你的评价，不是程序自动诊断）", padding=5)
        row.pack(fill="x", padx=12, pady=5)
        for i, tag in enumerate(MEMORY_FEEDBACK_TAGS):
            self.tags[tag] = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text=tag, variable=self.tags[tag]).grid(row=i//2, column=i%2, sticky="w", padx=5)
        self.evidence_tabs = ttk.Notebook(self.window)
        self.evidence_tabs.pack(fill="x", padx=12)
        # Keep the original technical Text first in the widget hierarchy for
        # existing integrations; the summary is the first visible tab.
        for name, title in (("diagnosis", "技术详情"), ("summary", "中文摘要")):
            frame = ttk.Frame(self.evidence_tabs)
            self.evidence_tabs.insert(0 if self.evidence_tabs.tabs() else "end", frame, text=title)
            widget = tk.Text(frame, height=4, wrap="word", state="disabled")
            scroll = ttk.Scrollbar(frame, command=widget.yview)
            scroll.pack(side="right", fill="y")
            widget.configure(yscrollcommand=scroll.set)
            widget.pack(side="left", fill="x", expand=True)
            setattr(self, name, widget)
        self.evidence_tabs.select(0)
        ttk.Label(self.window, text="哪里不合适，期望怎样（最多300字；只在本地保留）").pack(anchor="w", padx=12, pady=(10, 0))
        self.note = tk.Text(self.window, height=5, wrap="word")
        self.note.pack(fill="x", padx=12, pady=8)
        self.hint = tk.StringVar(value="绑定打开时的模式/场景和所选轮次。只保存原有标签与一句期望，不保存瞬时取数证据。")
        ttk.Label(self.window, textvariable=self.hint, wraplength=670).pack(anchor="w", padx=12)
        self.save_button = ttk.Button(self.window, text="保存本地反馈", command=self.save)
        self.save_button.pack(anchor="e", padx=12, pady=12)
        self.discard_button = ttk.Button(self.window, text="放弃未保存反馈", command=self.discard_edit)
        self.discard_button.pack(anchor="e", padx=12)
        self.chosen.trace_add("write", self._selection_changed)
        self.note.bind("<<Modified>>", self._note_changed)
        for variable in self.tags.values():
            variable.trace_add("write", self._draft_changed)
        self.refresh(initial=True)
        if turn_id is not None:
            self.select_turn(turn_id)

    def _destroyed(self, event):
        if event.widget is self.window:
            self.rows, self.labels, self._last_view = {}, {}, None
            self.editing_turn_id = None
            self._selected_turn_id = None

    def selected_turn_id(self):
        # Keep identity independently of labels even during a partial repaint.
        # The fallback supports older lightweight panel integrations.
        return getattr(self, "_selected_turn_id", self.labels.get(self.chosen.get()))

    def refresh(self, *, initial=False):
        if not self.window.winfo_exists():
            return
        view = self.core.feedback_view(self.scene.id)
        if view == self._last_view:
            return
        previous = self.selected_turn_id()
        self.rows = {r["id"]: r for r in view}
        # Ordinals are display only; all selection/draft ownership uses full IDs.
        self.labels = {f"第{i+1}轮 · {r['created_at']} · {self.row_audience(r)} · "
                       f"{'本地开口' if r['kind'] == 'local_assistant' else '普通对话'}": r["id"]
                       for i, r in enumerate(view)}
        self._selected_turn_id = previous if previous in self.rows else None
        if initial and self.rows:
            self._selected_turn_id = next(reversed(self.rows))
        if self.editing_turn_id is not None and self.editing_turn_id not in self.rows:
            self.discard_edit(source_removed=True)
            self.hint.set("原反馈轮次已失效或移除，窗口中的标签与期望已清理；没有转绑另一轮。")
        self.choice.configure(values=list(self.labels), state="disabled" if self.submitted else "readonly")
        label = next((label for label, key in self.labels.items() if key == self._selected_turn_id), "")
        self._refreshing = True
        try:
            self.chosen.set(label)
        finally:
            self._refreshing = False
        self._sync_draft_binding()
        self._render_evidence()
        # Only mark painted after success; a paint failure can retry this view.
        self._last_view = view

    def show_evidence(self, _event=None):
        # Selection is a read of the bound Core, not a stale snapshot retained
        # when the window opened. Never use the main window's current speaker.
        try:
            self.refresh()
            self._sync_draft_binding()
            self._render_evidence()
        except (RuntimeError, ValueError, tk.TclError):
            self.clear_evidence()
            self.hint.set("证据暂时无法刷新；反馈仍绑定原轮，请等待恢复后再保存。")

    def select_turn(self, turn_id):
        """Explicit target, never the current person or a displayed ordinal."""
        if self.submitted:
            return False
        self.refresh()
        label = next((label for label, key in self.labels.items() if key == turn_id), None)
        self.chosen.set(label or "")
        self.show_evidence()
        return label is not None

    def has_unsaved(self):
        return not self.submitted and (bool(self.note.get("1.0", "end").strip())
                                       or any(v.get() for v in self.tags.values()))

    def row_audience(self, row):
        if row["kind"] == "local_assistant":
            target = row.get("target", row["speaker_id"])
            return "面向全群" if target == "all" else "面向" + self.scene.person(target).display
        return "回应" + self.scene.person(row["speaker_id"]).display

    def _selection_changed(self, *_args):
        if not self._refreshing:
            if self._last_view is None:
                # After failed painting, a queued old label is not an identity.
                # Recover the stable selection before accepting new UI actions.
                self.show_evidence()
                return
            if self.submitted:
                self._refreshing = True
                try:
                    self.chosen.set(next((label for label, key in self.labels.items()
                                          if key == self.selected_turn_id()), ""))
                finally:
                    self._refreshing = False
                return
            self._selected_turn_id = self.labels.get(self.chosen.get())
            self.show_evidence()

    def _note_changed(self, _event=None):
        if self.note.edit_modified():
            self.note.edit_modified(False)
            self._draft_changed()

    def _draft_changed(self, *_args):
        if not self._editing_guard:
            self._sync_draft_binding()

    def _sync_draft_binding(self):
        selected = self.selected_turn_id()
        dirty = bool(self.note.get("1.0", "end").strip()) or any(v.get() for v in self.tags.values())
        if not dirty:
            self.editing_turn_id = selected
        matches = selected is not None and selected == self.editing_turn_id
        self.save_button.configure(state="normal" if matches and not self.submitted else "disabled")
        if dirty and not matches and not self.submitted:
            self.hint.set("未保存反馈仍属于另一轮（" + (self.editing_turn_id or "尚未选定")
                          + "）。请切回原轮，或先放弃未保存反馈再评价当前轮；没有自动转绑。")
        elif matches and getattr(self, "_draft_mismatch", False) and not self.submitted:
            self.hint.set("反馈已回到所绑定的轮次，原输入仍保留；可继续编辑或保存。")
        self._draft_mismatch = dirty and not matches
        return matches

    def discard_edit(self, *, source_removed=False):
        if self.submitted and not source_removed:
            return
        self._editing_guard = True
        try:
            self.note.delete("1.0", "end")
            self.note.edit_modified(False)
            for variable in self.tags.values():
                variable.set(False)
        finally:
            self._editing_guard = False
        self.editing_turn_id = self.selected_turn_id()
        self.hint.set("未保存反馈已清空；可填写当前所选轮次的期望。")
        self._sync_draft_binding()

    def _render_evidence(self):
        row = self.rows.get(self.selected_turn_id())
        scope = f"固定范围：{self.mode_label} / {self.scene.label}\n"
        context = (f"所选：{self.row_audience(row)} · {row['created_at']} · "
                   + ("本地开口（未外发）" if row["kind"] == "local_assistant" else "普通对话") + "\n") if row else ""
        self.display(self.summary, scope + context + evidence_summary(row))
        self.display(self.diagnosis, scope + f"Core {self.core._evidence_core_id}\n" + evidence_text(row))

    @staticmethod
    def display(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def clear_evidence(self):
        # Clear both tabs on refresh failure; never leave a stale summary.
        for widget in (self.summary, self.diagnosis):
            self.display(widget, "证据显示刷新失败；没有重新生成或提交。")
        self._last_view = None
        self.choice.configure(state="disabled")
        self.save_button.configure(state="disabled")

    def save(self):
        if self.submitted:
            self.hint.set("反馈已提交；显示未关闭不会再次提交。")
            return
        try:
            self.refresh()
            if not self._sync_draft_binding():
                return
            key = self.selected_turn_id()
            if key is None:
                raise ValueError("轮次已失效")
            self.core.feedback(self.scene.id, key, [k for k, v in self.tags.items() if v.get()], self.note.get("1.0", "end").strip())
        except Cancelled as error:
            self.hint.set(str(error) + " 输入仍保留，请稍后手动保存。")
            return
        except (ValueError, RuntimeError, OSError, StopIteration, tk.TclError):
            self.hint.set("未保存，输入仍保留；请检查标签、长度或所选轮次是否仍有效。")
            return
        self.submitted = True
        try:
            self.choice.configure(state="disabled")
            self.save_button.configure(state="disabled")
            self.discard_button.configure(state="disabled")
            self.window.destroy()
        except tk.TclError:
            self.hint.set("反馈已提交；显示刷新失败，请关闭窗口，不要重复保存。")
