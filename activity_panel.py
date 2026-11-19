"""Explicit local topics and unsent candidate UI. No platform execution."""
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from activity import ACTIVITY_TAGS, simulation_candidate
from character import ApiCompletion, Cancelled
from core import prepare_api_config

LABELS = {"pending": "待确认/待处理", "active": "可继续", "paused": "暂停", "closed": "结束",
          "expired": "到期", "invalid": "失效", "discarded": "已丢弃", "adopted": "已加入本地对话"}
PURPOSE_LABELS = {"private": "私聊草稿", "group": "群聊草稿", "public": "朋友圈草稿"}


class ActivityPanel:
    def __init__(self, app):
        self.app, self.scene, self.core = app, app.scene, app.core
        self.window = tk.Toplevel(app.window)
        self.window.title("小泽 · 待续话题与本地草稿（未发送）")
        self.window.geometry("1020x740")
        self.window.minsize(850, 640)
        self.window.transient(app.window)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.message = tk.StringVar(value="先显式启用本场景扩展。所有候选仅在本地，复制也不表示发送。")
        top = ttk.Frame(self.window, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text=self.scene.label).pack(side="left")
        ttk.Button(top, text="暂停 / 继续", command=app.toggle_pause).pack(side="left", padx=5)
        ttk.Button(top, text="启用本场景话题与草稿", command=self.enable).pack(side="right")
        tabs = ttk.Notebook(self.window)
        tabs.pack(fill="both", expand=True, padx=10)
        topics, drafts = ttk.Frame(tabs, padding=8), ttk.Frame(tabs, padding=8)
        tabs.add(topics, text="以后可以接着聊")
        tabs.add(drafts, text="看看小泽现在想说什么")
        self.topic_view = ttk.Treeview(topics, columns=("target", "status", "title"), show="headings", height=6)
        for key, label, width in (("target", "对象", 140), ("status", "状态", 100), ("title", "话题", 650)):
            self.topic_view.heading(key, text=label)
            self.topic_view.column(key, width=width)
        self.topic_view.pack(fill="both", expand=True)
        self.topic_view.bind("<<TreeviewSelect>>", self.select_topic)
        self.details = tk.Text(topics, height=6, wrap="word", state="disabled")
        self.details.pack(fill="x", pady=5)
        ttk.Button(topics, text="用上方选中片段作证据（可调整主题和方向）", command=self.use_selected_evidence).pack(anchor="w")
        form = ttk.Frame(topics)
        form.pack(fill="x")
        self.source = tk.StringVar()
        self.source_box = ttk.Combobox(form, textvariable=self.source, state="readonly")
        self.source_box.grid(row=0, column=1, sticky="ew", columnspan=3)
        self.source_box.bind("<<ComboboxSelected>>", self.select_source)
        ttk.Label(form, text="近期发言 / 有效记忆来源").grid(row=0, column=0, sticky="w")
        self.quote, self.title, self.direction = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for row, (label, var) in enumerate((("连续证据（最多160字）", self.quote), ("主题（最多100字）", self.title), ("待续方向（最多200字）", self.direction)), 1):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=var).grid(row=row, column=1, columnspan=3, sticky="ew")
        self.days = tk.StringVar(value="7")
        self.wait = tk.StringVar(value="0")
        self.whole_group = tk.BooleanVar(value=False)
        ttk.Label(form, text="有效天数 / 最早再考虑分钟").grid(row=4, column=0, sticky="w")
        ttk.Combobox(form, textvariable=self.days, values=("1", "3", "7"), state="readonly", width=5).grid(row=4, column=1, sticky="w")
        ttk.Combobox(form, textvariable=self.wait, values=("0", "10", "60"), state="readonly", width=5).grid(row=4, column=2, sticky="w")
        if self.scene.kind == "group":
            ttk.Checkbutton(form, text="本群话题面向全群", variable=self.whole_group).grid(row=4, column=3)
        form.columnconfigure(1, weight=1)
        row = ttk.Frame(topics)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="标记并确认", command=lambda: self.mark(confirm=True)).pack(side="left", padx=2)
        for label, fn in (("加入待确认话题", self.mark), ("确认 / 明确重新开启", lambda: self.change("activate")),
                          ("暂停", lambda: self.change("pause")), ("结束", lambda: self.change("close")),
                          ("保存主题修改", lambda: self.change("edit")), ("删除话题与草稿", self.delete)):
            ttk.Button(row, text=label, command=fn).pack(side="left", padx=2)

        choice = ttk.Frame(drafts)
        choice.pack(fill="x")
        self.purpose = tk.StringVar(value=PURPOSE_LABELS[self.scene.kind])
        purpose_box = ttk.Combobox(choice, textvariable=self.purpose, values=(PURPOSE_LABELS[self.scene.kind], PURPOSE_LABELS["public"]), state="readonly", width=12)
        purpose_box.pack(side="left")
        purpose_box.bind("<<ComboboxSelected>>", lambda _event: self.core.invalidate())
        targets = {p.display: p.id for p in self.scene.people}
        if self.scene.kind == "group":
            targets["全群"] = "all"
        self.targets = targets
        self.target = tk.StringVar(value=next(iter(targets)))
        target_box = ttk.Combobox(choice, textvariable=self.target, values=list(targets), state="readonly", width=24)
        target_box.pack(side="left", padx=5)
        target_box.bind("<<ComboboxSelected>>", lambda _event: self.core.invalidate())
        self.evaluate_button = ttk.Button(choice, text="评估一次（可能调用一次模型）", command=self.evaluate)
        self.evaluate_button.pack(side="left")
        ttk.Label(drafts, text="每次评估最多一次模型请求（输出上限2048 token）；可能选择沉默。朋友圈草稿只用明确确认的公开素材。").pack(anchor="w", pady=6)
        self.public = tk.Text(drafts, height=3, wrap="word")
        self.public.pack(fill="x")
        ttk.Button(drafts, text="确认以上为公开素材（空白则撤回旧素材）", command=self.public_material).pack(anchor="w", pady=5)
        self.draft_view = ttk.Treeview(drafts, columns=("purpose", "status", "text"), show="headings", height=5)
        for key, label, width in (("purpose", "用途", 90), ("status", "状态", 100), ("text", "本地草稿，未发送", 690)):
            self.draft_view.heading(key, text=label)
            self.draft_view.column(key, width=width)
        self.draft_view.pack(fill="both", expand=True)
        self.draft_view.bind("<<TreeviewSelect>>", self.select_draft)
        self.draft_details = tk.Text(drafts, height=6, wrap="word", state="disabled")
        self.draft_details.pack(fill="x", pady=5)
        ttk.Button(drafts, text="在本地对话中说出（仅私聊/群聊）", command=self.adopt).pack(anchor="w", pady=(0, 5))
        actions = ttk.Frame(drafts)
        actions.pack(fill="x")
        ttk.Button(actions, text="复制所选有效草稿（不发送）", command=self.copy).pack(side="left")
        ttk.Button(actions, text="丢弃并暂停同源话题", command=self.discard).pack(side="left", padx=5)
        self.tag = tk.StringVar(value=ACTIVITY_TAGS[0])
        ttk.Combobox(actions, textvariable=self.tag, values=ACTIVITY_TAGS, state="readonly", width=18).pack(side="left")
        ttk.Button(actions, text="记录体验", command=self.feedback).pack(side="left", padx=5)
        self.note = ttk.Entry(drafts)
        self.note.pack(fill="x", pady=6)
        ttk.Label(drafts, text="上方填写一句具体期望（最多300字）。反馈随草稿/来源删除，不另存聊天副本。").pack(anchor="w")
        ttk.Label(self.window, textvariable=self.message, wraplength=980, padding=8).pack(anchor="w")
        ttk.Button(self.window, text="刷新状态 / 到期检查", command=self.refresh).pack(anchor="e", padx=10, pady=5)
        self.refresh()

    def close(self):
        self.core.invalidate()
        self.window.grab_release()
        self.window.destroy()
        self.app.render()

    def run(self, operation, message="已更新。已有旧草稿会按来源和状态重新核查。"):
        try:
            operation()
            self.refresh()
            self.message.set(message)
            return True
        except (ValueError, RuntimeError, OSError, StopIteration) as error:
            self.message.set(str(error) if isinstance(error, (ValueError, RuntimeError)) else "操作未完成，请检查选择和本地文件权限。")
            return False

    def enable(self):
        self.run(lambda: self.core.enable_activity(self.scene.id), "本场景已启用兼容v2扩展；旧记录仍保留。没有联网。")

    @staticmethod
    def display(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", json.dumps(value, ensure_ascii=False, indent=2))
        widget.configure(state="disabled")

    def refresh(self):
        data = self.core.snapshot(self.scene.id)
        try:
            self.view_ticket = self.core.ticket()
        except Cancelled:
            self.view_ticket = None
        self.topics = {t["id"]: t for t in data.get("activity", {}).get("topics", [])}
        self.drafts = {d["id"]: d for d in data.get("activity", {}).get("drafts", [])}
        for view in (self.topic_view, self.draft_view):
            for i in view.get_children():
                view.delete(i)
        for t in reversed(list(self.topics.values())):
            target_label = "全群" if t["target"] == "all" else self.scene.person(t["target"]).display
            self.topic_view.insert("", "end", iid=t["id"], values=(target_label, LABELS[t["status"]], t["title"]))
        for d in reversed(list(self.drafts.values())):
            self.draft_view.insert("", "end", iid=d["id"], values=(PURPOSE_LABELS[d["purpose"]], LABELS[d["status"]], d["text"]))
        self.sources = {f"发言{i+1}：{self.scene.person(t['speaker_id']).display} {t['text'][:50]}": ("turn", t["id"]) for i, t in enumerate(data["turns"]) if t["kind"] == "local_exchange"}
        self.sources.update({f"记忆：{r['id'][:6]} {r['content'][:50]}": ("memory", r["id"]) for r in data["memories"] if r["status"] == "active"})
        self.source_texts = {("turn", t["id"]): t["text"] for t in data["turns"] if t["kind"] == "local_exchange"}
        self.source_texts.update({("memory", r["id"]): r["content"] for r in data["memories"] if r["status"] == "active"})
        self.source_box.configure(values=list(self.sources))
        if self.source.get() not in self.sources:
            self.source.set(next(reversed(self.sources), ""))
        # Refresh clears displayed derived text as well as the list, after deletion.
        self.display(self.details, {"说明": "选择话题查看最小证据、对象、来源与期限。"})
        self.display(self.draft_details, {"说明": "草稿只在本地，选择查看依据。失效正文不可复制。"})
        if not self.public.edit_modified():
            self.public.delete("1.0", "end")
            self.public.insert("1.0", data.get("activity", {}).get("public_material", ""))
            self.public.edit_modified(False)

    def select_source(self, _event=None):
        key = self.sources.get(self.source.get())
        if key:
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", self.source_texts[key])
            self.details.configure(state="disabled")

    def use_selected_evidence(self):
        try:
            quote = self.details.get("sel.first", "sel.last")
            original = self.source_texts[self.sources[self.source.get()]]
            if not 0 < len(quote) <= 160 or quote not in original:
                raise ValueError()
        except (tk.TclError, KeyError, ValueError):
            self.message.set("请先选择来源，在原文预览中选中1—160字的连续片段。")
            return
        self.quote.set(quote)
        if not self.title.get().strip():
            self.title.set(quote[:100])
        if not self.direction.get().strip():
            self.direction.set("从这段话自然接续，不预设后续结果")
        self.message.set("已填入选中证据和可编辑的主题/方向；核对后点击标记并确认，不会自动生成或联网。")

    def select_topic(self, _event=None):
        items = self.topic_view.selection()
        if items:
            topic = self.topics[items[0]]
            self.title.set(topic["title"])
            self.direction.set(topic["direction"])
            self.display(self.details, topic)

    def select_draft(self, _event=None):
        items = self.draft_view.selection()
        if items:
            self.display(self.draft_details, self.drafts[items[0]])

    def mark(self, confirm=False):
        if self.source.get() not in self.sources:
            self.message.set("请先完成一轮本地对话或选择有效记忆。")
            return
        kind, source_id = self.sources[self.source.get()]
        self.run(lambda: self.core.mark_topic(self.scene.id, source_id, self.quote.get(), self.title.get(), self.direction.get(),
            source_kind=kind, target="all" if self.whole_group.get() else None, days=int(self.days.get()), wait_minutes=int(self.wait.get()), confirm=confirm),
            "话题已明确确认，可用于延续和候选评估。" if confirm else "话题已成为待确认；选择它再确认，才会用于延续和主动候选。")

    def change(self, action):
        items = self.topic_view.selection()
        if items:
            return self.run(lambda: self.core.change_topic(self.scene.id, items[0], action, title=self.title.get(), direction=self.direction.get()))
        return False

    def delete(self):
        if messagebox.askyesno("删除本地话题", "删除所选话题、其草稿和反馈，并清理依赖它的助手正文及其反馈？来源未知的旧助手正文也将保守清理。保留无关用户原话和来源ID，阻止同源重新标记。", parent=self.window):
            if self.change("delete"):
                self.quote.set("")
                self.title.set("")
                self.direction.set("")
                self.note.delete(0, "end")

    def public_material(self):
        if self.run(lambda: self.core.set_public_material(self.scene.id, self.public.get("1.0", "end")), "已确认公开素材；替换或撤回会删除旧公开草稿。"):
            self.public.edit_modified(False)
            self.refresh()

    def evaluate(self):
        app = self.app
        app.auto_policy.activity()
        if app.busy or app.paused or app.memory_error:
            self.message.set("请先完成当前生成或继续本地试聊。")
            return
        core, scene_id = self.core, self.scene.id
        purpose = next(key for key, label in PURPOSE_LABELS.items() if label == self.purpose.get())
        target = self.targets[self.target.get()] if purpose != "public" else self.scene.people[0].id
        use_memory, use_topics = app.use_memory.get(), app.use_topics.get()
        api = app.mode.get().startswith("模型试聊")
        values = {k: v.get() for k, v in app.api_values.items()} if api else None
        # Configuration validation is lazy: local silence never requires a key.
        def complete(messages):
            return ApiCompletion(prepare_api_config(values), json_object=True)(messages) if api else simulation_candidate(messages)
        complete.model_id = values["model"][:120] if api else "simulation"
        ticket = core.ticket()
        app.request_number += 1
        request_id = app.request_number
        app.busy = True
        app.last_error = None
        def work():
            try:
                result = core.evaluate_candidate(scene_id, target, purpose, complete, "api" if api else "simulation", ticket=ticket,
                                                 use_memory=use_memory, use_topics=use_topics)
                app.events.put((request_id, "candidate_done", result["reason"], core, 0, ""))
            except (RuntimeError, ValueError) as error:
                app.events.put((request_id, "candidate_error", str(error), core, 0, ""))
            except Exception:
                app.events.put((request_id, "candidate_error", "候选未保存，请检查本地存储或刷新来源。", core, 0, ""))
        try:
            app.update_controls()
            self.message.set("正在评估；可能选择沉默。暂停会作废迟到结果。")
            threading.Thread(target=work, daemon=True).start()
        except (RuntimeError, tk.TclError):
            app.busy = False
            app.last_error = "error"
            app.stop_auto("手动评估未能启动，自动已停止；请核对后主动开启", error=True)
            notice = "评估未能启动，本次未请求模型、未保存草稿；输入保留，可稍后手动重试。"
            # Updating one view must not prevent restoring the other controls.
            for update in (lambda: self.message.set(notice), lambda: app.status.set(notice), app.update_controls):
                try:
                    update()
                except tk.TclError:
                    app.last_error = "display"

    def copy(self):
        items = self.draft_view.selection()
        if items:
            def clipboard(text):
                self.window.clipboard_clear()
                self.window.clipboard_append(text)
            self.run(lambda: self.core.copy_draft(self.scene.id, items[0], clipboard), "只复制到剪贴板，没有发送，也未记成经历。")

    def adopt(self):
        app = self.app
        if app.busy or app.paused or app.core is not self.core or app.scene.id != self.scene.id:
            self.message.set("请先结束当前生成、继续试聊并返回对应场景。")
            return
        items = self.draft_view.selection()
        if not items:
            self.message.set("请先选择有效的私聊或群聊草稿。")
            return
        purpose = next(key for key, label in PURPOSE_LABELS.items() if label == self.purpose.get())
        target = self.targets[self.target.get()]
        try:
            with self.core._lock:
                before_epoch = self.core._epoch
                event_id = self.core.adopt_draft(self.scene.id, items[0], target=target, purpose=purpose, ticket=self.view_ticket)
                if self.core._epoch != before_epoch:
                    app.auto_policy.manual_adopted(self.core, self.scene.id, target, event_id)
        except (ValueError, RuntimeError, OSError, StopIteration) as error:
            self.message.set(str(error) if isinstance(error, (ValueError, RuntimeError)) else "提交失败，未加入本地对话，输入保留。")
            return
        # Persistence is already committed. A paint failure must never resubmit.
        try:
            app.render()
            self.refresh()
            self.message.set("已加入本地对话，尚不代表阅读或外部发送。关闭面板后可以接着回复；输入保留。")
            app.status.set("小泽已在本地开口；关闭话题面板后可接话。")
        except Exception:
            self.message.set("已提交本地消息，但显示刷新失败；请刷新或重新打开，勿另建消息。再次采用同一草稿不会重复。")

    def discard(self):
        items = self.draft_view.selection()
        if items:
            if self.run(lambda: self.core.discard_draft(self.scene.id, items[0]), "已丢弃；同源话题暂停。公开素材被拒绝则撤回，需明确重新提供。"):
                self.note.delete(0, "end")

    def feedback(self):
        items = self.draft_view.selection()
        if items:
            self.run(lambda: self.core.draft_feedback(self.scene.id, items[0], [self.tag.get()], self.note.get()), "草稿体验已记录，只在本地保留。")
