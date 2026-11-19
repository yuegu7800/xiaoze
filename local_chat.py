"""Tkinter local character playground. Does not import the WeChat application."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from character import (SCENES, CharacterCore, LocalMemory,
                       ApiCompletion, Cancelled, simulation_completion)
from core import prepare_api_config
from experience import CASES
from memory_panel import MemoryPanel
from dialogue import reply_available
from local_api_profile import ApiProfile
from auto_opening import AutoOpening, Binding, CHECK_SECONDS
from activity import simulation_candidate

ROOT = Path(__file__).resolve().parent
MODE_SIMULATION = "流程模拟（无联网，不评价人格）"
MODE_API = "模型试聊（本次手动填写 API）"


class LocalChat:
    def __init__(self, window=None, data_dir=None, *, auto_policy=None):
        self.window = window or tk.Tk()
        self.window.title("小泽 · 本地文字试聊")
        height = min(800, max(640, self.window.winfo_screenheight() - 100))
        self.window.geometry(f"980x{height}")
        self.window.minsize(820, min(680, height))
        self.window.option_add("*Font", "{Microsoft YaHei UI} 10")
        self.data_dir = Path(data_dir) if data_dir else ROOT / ".local-xiaoze"
        self.api_profile = ApiProfile(self.data_dir)
        self.cores = {}
        self.events = queue.Queue()
        self.busy = False
        self.paused = False
        self.closed = False
        self.last_error = None
        self.request_number = 0
        self.draft_version = 0
        self.case_cursor = {}
        self.case_id = "manual"
        self.loaded_case_text = None
        self.memory_error = False
        self.poll_after = None
        self.auto_after = None
        self.auto_policy = auto_policy if auto_policy is not None else AutoOpening()
        self._auto_candidate_cache = None
        self._auto_status_painted_at = None
        self.activity_panel = None
        self.feedback_panel = None
        self._feedback_actions = {}
        self._feedback_bindings = {}
        self._feedback_render_number = 0
        self._feedback_actions_refresh_needed = False
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        box = ttk.Frame(self.window, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="小泽，来聊两句", font=("Microsoft YaHei UI", 19, "bold")).pack(anchor="w")
        ttk.Label(box, text="独立本地试聊 · 所有场景身份由你在这里选择 · 请使用虚构素材", foreground="#555555").pack(anchor="w", pady=(4, 10))
        choices = ttk.Frame(box)
        choices.pack(fill="x")
        self.mode = tk.StringVar(value=MODE_SIMULATION)
        self.mode_box = ttk.Combobox(choices, textvariable=self.mode, values=(MODE_SIMULATION, MODE_API), state="readonly", width=33)
        self.mode_box.pack(side="left", padx=(0, 8))
        self.mode_box.bind("<<ComboboxSelected>>", self.switch_mode)
        self.scene_label = tk.StringVar(value=SCENES[0].label)
        self.scene_box = ttk.Combobox(choices, textvariable=self.scene_label, values=[s.label for s in SCENES], state="readonly", width=32)
        self.scene_box.pack(side="left", fill="x", expand=True)
        self.scene_box.bind("<<ComboboxSelected>>", self.switch_scene)
        self.speaker = tk.StringVar()
        self.speaker_box = ttk.Combobox(choices, textvariable=self.speaker, state="readonly", width=21)
        self.speaker_box.pack(side="left", padx=(8, 0))
        self.speaker_box.bind("<<ComboboxSelected>>", self.invalidate_context)

        practice = ttk.Frame(box)
        practice.pack(fill="x", pady=(7, 0))
        self.practice = tk.StringVar(value="日常试聊")
        self.practice_box = ttk.Combobox(practice, textvariable=self.practice, values=list(CASES), state="readonly", width=28)
        self.practice_box.pack(side="left")
        self.case_button = ttk.Button(practice, text="载入下一句（不发送）", command=self.load_case)
        self.case_button.pack(side="left", padx=5)
        self.practice_isolated = tk.BooleanVar(value=False)
        self.isolated_check = ttk.Checkbutton(practice, text="独立体验区", variable=self.practice_isolated, command=self.switch_mode)
        self.isolated_check.pack(side="left")
        self.use_memory = tk.BooleanVar(value=True)
        self.memory_check = ttk.Checkbutton(practice, text="召回长期记忆", variable=self.use_memory, command=self.switch_mode)
        self.memory_check.pack(side="left", padx=6)
        self.use_topics = tk.BooleanVar(value=True)
        self.topics_check = ttk.Checkbutton(practice, text="话题延续", variable=self.use_topics, command=self.switch_mode)
        self.topics_check.pack(side="left")
        self.practice_box.bind("<<ComboboxSelected>>", self.switch_mode)

        api = ttk.LabelFrame(box, text="模型试聊配置 · 可由你明确保存到本机（Windows用户加密）", padding=8)
        api.pack(fill="x", pady=10)
        self.api_values = {k: tk.StringVar() for k in ("endpoint", "model", "api_key")}
        self.api_entries = []
        for row, (key, label) in enumerate((("endpoint", "完整 HTTPS 接口"), ("model", "模型 ID"), ("api_key", "本次 API Key"))):
            ttk.Label(api, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entry = ttk.Entry(api, textvariable=self.api_values[key], show="*" if key == "api_key" else "")
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self.api_entries.append(entry)
        api.columnconfigure(1, weight=1)
        ttk.Label(api, text="接口以 /chat/completions 结尾。发送时：角色设定、本场景近期对话和当前人物偏好会发给你填写的模型服务。", wraplength=880, foreground="#555555").grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        profile_actions = ttk.Frame(api)
        profile_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.save_api_button = ttk.Button(profile_actions, text="保存本机API配置", command=self.save_api_profile)
        self.save_api_button.pack(side="left")
        self.forget_api_button = ttk.Button(profile_actions, text="忘记已保存配置", command=self.forget_api_profile)
        self.forget_api_button.pack(side="left", padx=6)
        self.api_profile_notice = tk.StringVar(value="尚未保存；点击保存后下次自动填入，不会自动发起请求。")
        ttk.Label(api, textvariable=self.api_profile_notice, wraplength=880).grid(row=5, column=0, columnspan=2, sticky="w")
        try:
            saved_api = self.api_profile.load()
            if saved_api:
                for key, value in self.api_values.items():
                    value.set(saved_api[key])
                self.api_profile_notice.set("已填入本机加密配置；选模型试聊后可发送，不会自动联网。")
        except RuntimeError as error:
            self.api_profile_notice.set(str(error))

        automatic = ttk.Frame(box)
        automatic.pack(fill="x", pady=(0, 5))
        self.auto_enabled = tk.BooleanVar(value=False)
        self.auto_adopt = tk.BooleanVar(value=False)
        self.auto_check = ttk.Checkbutton(automatic, text="自动考虑开口（仅主人私聊）", variable=self.auto_enabled, command=self.toggle_auto)
        self.auto_check.grid(row=0, column=0, sticky="w")
        self.auto_adopt_check = ttk.Checkbutton(automatic, text="自动加入本地聊天", variable=self.auto_adopt, command=self.toggle_auto_adopt, state="disabled")
        self.auto_adopt_check.grid(row=0, column=1, sticky="w", padx=8)
        self.auto_stop_button = ttk.Button(automatic, text="停止自动", command=self.stop_auto)
        self.auto_stop_button.grid(row=0, column=2, sticky="e")
        automatic.columnconfigure(2, weight=1)
        self.auto_help = ttk.Label(automatic, text="需人工确认话题；空闲10分钟才考虑，23—08时安静。模型可能计费；本地加入不发微信。", wraplength=880, foreground="#555555")
        self.auto_help.grid(row=1, column=0, columnspan=3, sticky="w")
        self.auto_status = tk.StringVar(value=self.auto_policy.status())
        self.auto_status_label = ttk.Label(automatic, textvariable=self.auto_status, wraplength=880)
        self.auto_status_label.grid(row=2, column=0, columnspan=3, sticky="w")
        automatic.bind("<Configure>", lambda e: (self.auto_help.configure(wraplength=max(300, e.width)),
                                                self.auto_status_label.configure(wraplength=max(300, e.width))))

        transcript_frame = ttk.Frame(box)
        transcript_frame.pack(fill="both", expand=True)
        # Keep a small requested height so pack can reserve space for input and
        # stop/send controls even at the minimum window size; expand fills the rest.
        self.transcript = tk.Text(transcript_frame, wrap="word", state="disabled", height=4, padx=10, pady=10)
        scroll = ttk.Scrollbar(transcript_frame, command=self.transcript.yview)
        scroll.pack(side="right", fill="y")
        self.transcript.configure(yscrollcommand=scroll.set)
        self.transcript.pack(side="left", fill="both", expand=True)
        self.input = tk.Text(box, wrap="word", height=3, padx=8, pady=6)
        self.input.pack(fill="x", pady=(10, 6))
        self.input.bind("<Control-Return>", self.send)
        self.input.bind("<<Modified>>", self.draft_changed)
        self.input.edit_modified(False)
        actions = ttk.Frame(box)
        actions.pack(fill="x")
        self.send_button = ttk.Button(actions, text="发送到本地试聊（Ctrl+Enter）", command=self.send)
        self.send_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="暂停 / 作废本轮", command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=8)
        ttk.Button(actions, text="记忆 / 证据", command=self.show_memory).pack(side="left")
        ttk.Button(actions, text="话题 / 主动草稿", command=self.show_activity).pack(side="left", padx=4)
        ttk.Button(actions, text="记录体验", command=self.show_feedback).pack(side="left", padx=5)
        ttk.Button(actions, text="升级v1记录", command=self.import_legacy).pack(side="left")
        ttk.Button(actions, text="升级v2来源记录", command=self.upgrade_v2).pack(side="left", padx=4)
        ttk.Button(actions, text="清空当前场景", command=self.clear_scene).pack(side="right")
        self.status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.status, wraplength=910, foreground="#555555").pack(anchor="w", pady=(8, 0))
        self.switch_mode()
        self.switch_scene()
        # Only physical input events count; render/poll/feedback refresh do not.
        self.window.bind("<KeyPress>", self.auto_user_activity, add="+")
        self.window.bind("<ButtonPress>", self.auto_user_activity, add="+")
        self.window.bind("<Unmap>", self.auto_window_hidden, add="+")
        for variable in (self.mode, self.scene_label, self.speaker, self.practice,
                         self.practice_isolated, self.use_memory, self.use_topics):
            variable.trace_add("write", self.auto_scope_changed)
        for variable in self.api_values.values():
            variable.trace_add("write", self.auto_scope_changed)
        self.poll_after = self.window.after(100, self.poll)

    def auto_binding(self):
        scene = self.scene
        person = next(p for p in scene.people if p.display == self.speaker.get())
        selection = (self.mode.get(), self.practice_isolated.get(), self.practice.get(),
                     self.use_memory.get(), self.use_topics.get())
        return Binding(self.core, selection, scene.id, person.id, self.mode.get() == MODE_API,
                       self.use_memory.get(), self.use_topics.get())

    def auto_environment_reason(self):
        """Read widgets only; never acknowledge/reset the activity clock here."""
        if self.paused:
            return "paused"
        if self.memory_error:
            return "data_error"
        if self.input.get("1.0", "end-1c"):
            return "input"
        if self.window.state() in ("iconic", "withdrawn"):
            return "minimized"
        if any(isinstance(w, tk.Toplevel) and w.winfo_exists() for w in self.window.winfo_children()):
            return "panel"
        if self.busy and self.auto_policy.active is None:
            return "busy"
        return ""

    def auto_candidate_view(self):
        """One metadata-only view; invalidate on data/flags/time boundaries.

        This UI cache contains no source IDs, text or configuration. Actual
        generation always uses the uncached transaction checks.
        """
        binding = self.auto_policy.binding
        core = binding.core
        with core._lock:
            key = (binding, core._epoch, core._paused, core._inflight, core.memory.state["version"])
            now = core.memory.clock()
            cached = self._auto_candidate_cache
            if cached is not None and cached[0] == key and cached[1].checked_at <= now < cached[1].valid_until:
                return cached[1]
            view = core.candidate_state(binding.scene_id, binding.person_id, "private",
                use_memory=binding.use_memory, use_topics=binding.use_topics)
            self._auto_candidate_cache = (key, view)
            return view

    def refresh_auto_controls(self, *, force=True):
        self.auto_enabled.set(self.auto_policy.enabled)
        self.auto_adopt.set(self.auto_policy.auto_adopt)
        self.auto_adopt_check.configure(state="normal" if self.auto_policy.enabled else "disabled")
        now = self.auto_policy.monotonic()
        if not force and self._auto_status_painted_at is not None and 0 <= now - self._auto_status_painted_at < 1:
            return
        reason = self.auto_environment_reason() if self.auto_policy.enabled else None
        condition = self.auto_policy.projection(environment_reason=reason)
        view = self.auto_candidate_view() if condition.ready else None
        condition = self.auto_policy.projection(view, environment_reason=reason)
        total, hourly, adopted, adopted_hourly = self.auto_policy.budget.counts(now)
        self.auto_status.set(condition.text() + "\n" +
            f"本次会话 自动请求尝试 {total}/6（小时{hourly}/2，余{max(0, min(6-total, 2-hourly))}）"
            f" · 自动本地开口 {adopted}/6（小时{adopted_hourly}/2）")
        self._auto_status_painted_at = now

    def stop_auto(self, reason="已关闭；已发出的请求无法撤回", *, render=True, error=False):
        # Cancellation/cleanup must succeed independently of widget painting.
        self.auto_policy.stop(reason, error=error)
        self._auto_candidate_cache = None
        callback, self.auto_after = self.auto_after, None
        if callback is not None:
            try:
                self.window.after_cancel(callback)
            except tk.TclError:
                pass
        if render:
            try:
                self.refresh_auto_controls()
            except tk.TclError:
                self.last_error = "display"
                try:
                    self.status.set("自动已停止；状态显示未完成，请刷新或重新打开窗口。已发请求可能仍计费。")
                except tk.TclError:
                    pass  # Both views can fail; cancellation is already done.

    def auto_scope_changed(self, *_args):
        if self.auto_policy.enabled:
            self.stop_auto("范围或配置改变，自动已关闭；返回后需重新开启")

    def auto_user_activity(self, _event=None):
        self.auto_policy.activity()

    def auto_window_hidden(self, event):
        if event.widget is self.window:
            self.auto_policy.activity()

    def sync_auto_environment(self):
        if not self.auto_policy.enabled:
            return
        reason = self.auto_environment_reason()
        self.auto_policy.environment(self.auto_binding(), bool(reason), reason=reason)

    def toggle_auto(self):
        if not self.auto_enabled.get():
            self.stop_auto()
            return
        try:
            if self.busy or self.paused or self.memory_error:
                raise ValueError("请先完成生成、继续试聊并处理数据错误。")
            binding = self.auto_binding()
            binding.core.memory.require_writable()
            self.auto_policy.start(binding, binding.core.snapshot(binding.scene_id, compatibility=False))
            self._auto_candidate_cache = None
            self.sync_auto_environment()
            self.schedule_auto()
            self.refresh_auto_controls()
        except (tk.TclError, ValueError, RuntimeError, StopIteration):
            self.stop_auto("未开启：请使用主人私聊，并核对生成、暂停、数据及窗口状态", error=True)

    def toggle_auto_adopt(self):
        self.auto_policy.set_adoption(self.auto_adopt.get())
        self.refresh_auto_controls()

    def schedule_auto(self):
        if not self.closed and self.auto_policy.enabled and self.auto_after is None:
            self.auto_after = self.window.after(int(CHECK_SECONDS * 1000), self.auto_tick)

    def auto_tick(self):
        self.auto_after = None
        if self.closed or not self.auto_policy.enabled:
            return
        try:
            self.sync_auto_environment()
            due = self.auto_policy.due()
            if due and not self.busy:
                binding = self.auto_policy.binding
                ready, reason = binding.core.candidate_precheck(binding.scene_id, binding.person_id, "private",
                    use_memory=binding.use_memory, use_topics=binding.use_topics)
                if ready:
                    self.start_auto_request(binding)
                else:
                    self.auto_policy.note = reason
            self.refresh_auto_controls()
            self.schedule_auto()
        except (tk.TclError, RuntimeError, ValueError, OSError, StopIteration):
            self.stop_auto("错误后已停止：自动检查或状态显示未完成，请核对后主动开启", error=True)

    def start_auto_request(self, binding):
        core = binding.core
        run = self.auto_policy.begin(core.ticket())
        values = {k: v.get() for k, v in self.api_values.items()} if binding.api else None

        def complete(messages):
            # Validate before reservation. No model attempt for missing config.
            cfg = prepare_api_config(values) if binding.api else None
            with core._lock:
                self.auto_policy.guard(run)
                if core._paused or core._epoch != run.ticket:
                    raise Cancelled("上下文改变，自动请求未发出。")
                if binding.api:
                    self.auto_policy.reserve_attempt(run)
            return ApiCompletion(cfg, json_object=True)(messages) if binding.api else simulation_candidate(messages)

        complete.model_id = values["model"][:120] if binding.api else "simulation"
        self.request_number += 1
        request_id = self.request_number
        self.busy = True
        self.last_error = None

        def work():
            try:
                result = core.evaluate_candidate(binding.scene_id, binding.person_id, "private", complete,
                    "api" if binding.api else "simulation", ticket=run.ticket,
                    use_memory=binding.use_memory, use_topics=binding.use_topics,
                    guard=lambda: self.auto_policy.guard(run))
                kind, payload = "auto_done", {"run": run, "result": result}
            except Cancelled:
                kind, payload = "auto_cancelled", {"run": run}
            except RuntimeError as error:
                kind, payload = "auto_error", {"run": run, "message": str(error)}
            except Exception:
                kind, payload = "auto_error", {"run": run, "message": "候选格式或本地存储失败，请检查后主动恢复"}
            self.events.put((request_id, kind, payload, core, 0, ""))
        try:
            self.update_controls()
            threading.Thread(target=work, daemon=True).start()
        except (RuntimeError, tk.TclError):
            # No worker owns the request: release UI state without pretending
            # an HTTP attempt or a generation took place. Do not retry.
            self.busy = False
            self.auto_policy.finish(run, error="自动工作线程未能启动，本次未请求模型")
            self.stop_auto(self.auto_policy.note, error=self.auto_policy.stopped_error)
            try:
                self.update_controls()
            except tk.TclError:
                self.last_error = "display"  # Recover painting from existing state.

    def finish_auto_request(self, kind, payload):
        run = payload["run"]
        if self.auto_policy.active is not run:
            return  # Duplicate/old queue event can never adopt again.
        try:
            self.sync_auto_environment()
            if kind == "auto_error":
                self.auto_policy.finish(run, error=payload["message"])
                self.last_error = "error"
            elif kind == "auto_cancelled":
                self.auto_policy.finish(run, cancelled=True)
            else:
                result = payload["result"]
                self.auto_policy.adopt(run, result)
                self.auto_policy.finish(run, result)
        except Cancelled:
            self.auto_policy.finish(run, cancelled=True)
        except (tk.TclError, RuntimeError, ValueError, OSError, StopIteration):
            # The worker has finished, so an environment/widget read failure
            # cannot leave a permanent active run with no completion pending.
            self.auto_policy.finish(run, error="自动完成核对或本地采用失败；已有草稿可手动核对，不自动重试")
            self.last_error = "error"
        if not self.auto_policy.enabled:
            self.stop_auto(self.auto_policy.note, error=self.auto_policy.stopped_error)
        self.refresh_auto_controls()
        # Actual completion stays in the original notice area. Current status
        # above may already have a new blocking condition; it is not history.
        self.status.set(self.auto_policy.note + "。只影响本地，输入保留。")

    @property
    def scene(self):
        return next(scene for scene in SCENES if scene.label == self.scene_label.get())

    def save_api_profile(self):
        if self.busy:
            self.api_profile_notice.set("请等本轮生成结束再保存配置。")
            return
        try:
            self.api_profile.save({key: value.get() for key, value in self.api_values.items()})
        except (OSError, RuntimeError, ValueError):
            self.api_profile_notice.set("保存失败，原配置保留；请检查三栏完整性和本机权限。")
            return
        self.api_profile_notice.set("已加密保存到本机；以后启动自动填入。修改后需再次点击保存。")

    def forget_api_profile(self):
        if self.busy:
            self.api_profile_notice.set("请等本轮生成结束再忘记配置。")
            return
        try:
            self.api_profile.forget()
        except OSError:
            self.api_profile_notice.set("删除未完成，已保存配置仍可能存在，请检查本机权限。")
            return
        for value in self.api_values.values():
            value.set("")
        self.api_profile_notice.set("已删除本程序保存的配置并清空三栏；不影响聊天记录或服务商密钥。")

    @property
    def core(self):
        name = "simulation" if self.mode.get() == MODE_SIMULATION else "state"
        if self.practice_isolated.get():
            import hashlib
            suffix = hashlib.sha256(self.practice.get().encode()).hexdigest()[:8]
            name = "experience-" + name + "-" + suffix + ("-with" if self.use_memory.get() else "-without")
            name += "-topics" if self.use_topics.get() else "-no-topics"
        if name not in self.cores:
            self.cores[name] = CharacterCore(ROOT / "persona.txt", LocalMemory(self.data_dir / (name + "-v2.json")))
        return self.cores[name]

    def switch_mode(self, _event=None):
        self.invalidate_context()
        if self.busy:
            return
        self.paused = False
        try:
            self.core.resume()
        except RuntimeError as error:
            self.memory_error = True
            self.status.set(str(error))
            self.send_button.configure(state="disabled")
            return
        self.memory_error = False
        self.pause_button.configure(text="暂停 / 作废本轮")
        self.update_controls()
        self.render()
        self.status.set("流程模拟使用独立记录，不会混入模型试聊。" if self.mode.get() == MODE_SIMULATION else
                        "请核对接口、模型与密钥；可点击“保存本机API配置”，以后自动填入。不会读取旧微信配置。")
        if self.core.memory.state["version"] == 2:
            self.status.set("当前旧v2记录仅供查看；请点击“升级v2来源记录”后继续。旧助手文本不会推测补链。")

    def switch_scene(self, _event=None):
        self.invalidate_context()
        people = self.scene.people
        self.speaker_box.configure(values=[p.display for p in people])
        self.speaker.set(people[0].display)
        self.render()

    def invalidate_context(self, _event=None):
        if self.auto_policy.enabled:
            self.stop_auto("模式或场景改变，自动已关闭")
        for core in self.cores.values():
            core.invalidate()

    def update_controls(self):
        for control in (self.mode_box, self.scene_box, self.speaker_box):
            control.configure(state="disabled" if self.busy else "readonly")
        for entry in self.api_entries:
            entry.configure(state="normal" if not self.busy and self.mode.get() == MODE_API else "disabled")
        self.send_button.configure(state="disabled" if self.busy or self.paused or self.memory_error else "normal")
        self.practice_box.configure(state="disabled" if self.busy else "readonly")
        self.case_button.configure(state="disabled" if self.busy else "normal")
        self.isolated_check.configure(state="disabled" if self.busy else "normal")
        self.memory_check.configure(state="disabled" if self.busy else "normal")
        self.topics_check.configure(state="disabled" if self.busy else "normal")

    def render(self):
        self._clear_feedback_actions()
        self._feedback_render_number += 1
        mode_label = self.feedback_mode_label()
        try:
            core, scene = self.core, self.scene
            data = core.snapshot(scene.id, compatibility=False)
        except RuntimeError as error:
            self.memory_error = True
            self.status.set(str(error))
            self.send_button.configure(state="disabled")
            return
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        if not data["turns"]:
            self.transcript.insert("end", "还没有本地记录。可从“今天有点累，陪我聊两句”开始。\n")
        for turn in data["turns"]:
            person = scene.person(turn["speaker_id"])
            if turn["text"]:
                self.transcript.insert("end", person.display + "：" + turn["text"] + "\n\n")
            label = "小泽"
            if turn["kind"] == "local_assistant":
                audience = "全群" if turn["target"] == "all" else scene.person(turn["target"]).display
                label += "（本地开口，面向" + audience + "）"
            if reply_available(turn, data):
                body = turn["reply"]
            elif not turn["reply"]:
                body = "〔正文已删除；仅保留本地消息位置〕"
            elif not turn.get("reply_trace"):
                body = "〔旧助手记录无来源链，仅供维护查看，不参与生成〕\n" + turn["reply"]
            else:
                body = "〔所依赖资料已失效，历史正文保留但不参与生成〕"
            self.transcript.insert("end", label + "：" + body + "\n")
            if turn["reply"] and (not turn.get("reply_trace") or reply_available(turn, data)):
                tag = f"reply-feedback-{self._feedback_render_number}-{len(self._feedback_actions)}"
                self._feedback_actions[tag] = dict(core=core, scene=scene, turn_id=turn["id"],
                    speaker_id=turn["speaker_id"], mode_label=mode_label)
                self.transcript.insert("end", "评价这条回复", (tag,))
                self.transcript.tag_configure(tag, foreground="#245a81", underline=True)
                func_id = self.transcript.tag_bind(tag, "<Button-1>",
                    lambda _event, action=tag: self._open_reply_feedback(action))
                self._feedback_bindings[tag] = func_id
                self.transcript.insert("end", "\n")
            self.transcript.insert("end", "\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")
        self._feedback_actions_refresh_needed = False

    def feedback_mode_label(self):
        return self.mode.get() + (" / 独立体验区：" + self.practice.get() if self.practice_isolated.get() else "")

    def _clear_feedback_actions(self):
        # tag_delete alone does not release Python callbacks registered in Tcl.
        for tag, func_id in self._feedback_bindings.items():
            self.transcript.tag_unbind(tag, "<Button-1>", func_id)
            self.transcript.tag_delete(tag)
        self._feedback_actions.clear()
        self._feedback_bindings.clear()

    def _open_reply_feedback(self, tag):
        target = self._feedback_actions.get(tag)
        if target is None:
            self.status.set("这条回复入口已刷新或失效，请使用当前聊天中的入口。")
            return "break"
        if target["core"] is not self.core or target["scene"].id != self.scene.id:
            self.status.set("原入口不属于当前模式/场景，请在相应聊天中重新选择。")
            return "break"
        try:
            rows = target["core"].feedback_view(target["scene"].id)
        except (RuntimeError, ValueError):
            self.status.set("该回复暂时无法核对，请等待刷新后再选择；没有改评另一轮。")
            return "break"
        if not any(r["id"] == target["turn_id"] and r["speaker_id"] == target["speaker_id"] for r in rows):
            self.status.set("该回复已淘汰、删除或失效，没有改评另一轮。")
            return "break"
        self.show_feedback(**target)
        return "break"

    def _refresh_feedback_actions(self):
        if getattr(self, "_feedback_actions_refresh_needed", False):
            self.render()
            return
        if not getattr(self, "_feedback_actions", None):
            return
        target = next(iter(self._feedback_actions.values()))
        core, scene = target["core"], target["scene"]
        available = {r["id"]: r["speaker_id"] for r in core.feedback_view(scene.id)}
        if core is not self.core or scene.id != self.scene.id or any(
                available.get(t["turn_id"]) != t["speaker_id"] for t in self._feedback_actions.values()):
            # Only redraw on actual loss of a reply action, not on each poll.
            self.render()

    def send(self, _event=None):
        self.auto_policy.activity()
        if self.busy or self.paused:
            return "break"
        self.draft_changed()
        raw_input = self.input.get("1.0", "end-1c")
        text = raw_input.strip()
        if not text or len(text) > 4000:
            self.status.set("请输入1至4000字。")
            return "break"
        scene = self.scene
        person = next(p for p in scene.people if p.display == self.speaker.get())
        if self.mode.get() == MODE_API:
            try:
                cfg = prepare_api_config({k: v.get() for k, v in self.api_values.items()})
            except RuntimeError as error:
                self.status.set(str(error))
                return "break"
            completion = ApiCompletion(cfg)
            backend = "api"
        else:
            completion, backend = simulation_completion, "simulation"
        core = self.core
        ticket = core.ticket()
        self.request_number += 1
        request_id = self.request_number
        draft_version = self.draft_version
        use_memory = self.use_memory.get()
        use_topics = self.use_topics.get()
        case_id = self.case_id if text == self.loaded_case_text else "manual"
        self.busy = True
        self.last_error = None

        def work():
            try:
                reply = core.exchange(scene.id, person.id, text, completion, backend, ticket=ticket, use_memory=use_memory, use_topics=use_topics, case_id=case_id)
                self.auto_policy.user_committed(core, scene.id, person.id)
                self.events.put((request_id, "done", reply, core, draft_version, raw_input))
            except Cancelled as error:
                self.events.put((request_id, "cancelled", str(error), core, draft_version, raw_input))
            except RuntimeError as error:
                # Gateway and store errors contain only locally authored diagnostics.
                self.events.put((request_id, "error", str(error), core, draft_version, raw_input))
            except Exception as error:
                self.events.put((request_id, "error", "本轮未保存（" + type(error).__name__ + "），请检查输入或本地数据目录。", core, draft_version, raw_input))

        try:
            self.update_controls()
            self.status.set("正在生成本地回复……暂停会丢弃迟到结果；已发出的模型请求可能仍计费。")
            threading.Thread(target=work, daemon=True).start()
        except (RuntimeError, tk.TclError):
            # No worker owns this request yet. Preserve the draft and do not
            # leave automatic scheduling running behind a failed manual action.
            self.busy = False
            self.last_error = "error"
            self.stop_auto("手动发送未能启动，自动已停止；请核对后主动开启", error=True)
            for update in (lambda: self.status.set("发送未能启动，本次未请求模型、未保存；输入保留，可稍后手动重试。"),
                           self.update_controls):
                try:
                    update()
                except tk.TclError:
                    self.last_error = "display"
        return "break"

    def poll(self):
        try:
            while True:
                request_id, kind, text, core, version, sent = self.events.get_nowait()
                if request_id != self.request_number:
                    continue
                self.busy = False
                try:
                    if kind.startswith("auto_"):
                        self.finish_auto_request(kind, text)
                    elif kind.startswith("candidate_"):
                        if kind == "candidate_error":
                            self.last_error = "error"
                        self.status.set(text + " 本地草稿未发送，聊天输入保留。")
                        panel = self.activity_panel
                        if panel and panel.window.winfo_exists() and panel.core is core:
                            panel.refresh()
                            panel.message.set(self.status.get())
                    elif kind == "done":
                        self.draft_changed()
                        if version == self.draft_version and self.input.get("1.0", "end-1c") == sent:
                            self.input.delete("1.0", "end")
                        self.status.set("本轮仅保存在本地；新写的草稿会保留。" + core.last_notice)
                    else:
                        self.last_error = kind
                        self.status.set(text + " 输入仍保留，可手动重试。")
                    self.render()
                except (tk.TclError, RuntimeError, ValueError, KeyError, StopIteration):
                    self.last_error = "display"
                    result = ("回复已保存。" if kind == "done" else
                              "自动评估/本地提交已结束，以原记录为准；不要重复提交。" if kind == "auto_done" else
                              "评估已完成，仍未发送。" if kind == "candidate_done" else "本次请求未完成保存。")
                    self.status.set(result + "显示刷新失败，切换场景或重新打开窗口可恢复；不要因显示问题重复发送。")
                finally:
                    if not self.closed:
                        self.update_controls()
        except queue.Empty:
            pass
        finally:
            if not self.closed:
                try:
                    self._refresh_feedback_actions()
                except (tk.TclError, RuntimeError, ValueError):
                    self._clear_feedback_actions()
                    self._feedback_actions_refresh_needed = True
            panel = getattr(self, "feedback_panel", None)
            if panel and not self.closed:
                try:
                    panel.refresh()
                except (tk.TclError, RuntimeError, ValueError):
                    # Clear any previously rendered evidence on a refresh error.
                    # Never retry a generation/feedback commit to repair painting.
                    try:
                        panel.clear_evidence()
                    except tk.TclError:
                        pass
            if not self.closed and hasattr(self, "auto_policy"):
                try:
                    self.sync_auto_environment()
                    self.refresh_auto_controls(force=False)
                except (tk.TclError, RuntimeError, ValueError, StopIteration):
                    self.stop_auto("错误后已停止：自动范围核对失败", error=True)
            if not self.closed:
                self.poll_after = self.window.after(100, self.poll)

    def toggle_pause(self):
        if self.memory_error:
            return
        self.paused = not self.paused
        if self.paused:
            self.stop_auto("暂停后自动已关闭，继续聊天不会自动重开")
            self.core.pause()
            self.status.set("已暂停；生成中的结果将作废。暂停不能撤销已经提交给模型服务的请求。")
        else:
            self.core.resume()
            self.status.set("已继续；上一轮作废结果仍不会保存。")
        self.pause_button.configure(text="继续试聊" if self.paused else "暂停 / 作废本轮")
        self.update_controls()

    def draft_changed(self, _event=None):
        if self.input.edit_modified():
            self.draft_version += 1
            self.auto_policy.activity()
            self.input.edit_modified(False)

    def clear_scene(self):
        if messagebox.askyesno("清空本地场景", "删除当前场景的近期原文、记忆、候选与体验反馈？如已升级v1，同步清空管理中v1原件和备份的本场景。不影响其他场景或微信data目录。", parent=self.window):
            try:
                self.core.reset_scene(self.scene.id)
            except (OSError, RuntimeError):
                self.status.set("清空未完成，请检查本地数据目录或管理中的v1副本；可安全重试，未宣称全部删除。")
                return
            self.render()
            self.status.set("当前场景记录已从本地 JSON 移除；生成中的旧结果已作废。不代表模型服务端数据也被删除。")

    def show_memory(self):
        self.auto_policy.activity()
        try:
            return MemoryPanel(self)
        except RuntimeError as error:
            self.status.set(str(error))

    def show_activity(self):
        self.auto_policy.activity()
        try:
            from activity_panel import ActivityPanel
            if self.activity_panel and self.activity_panel.window.winfo_exists():
                self.activity_panel.window.lift()
                return self.activity_panel
            self.activity_panel = ActivityPanel(self)
            return self.activity_panel
        except RuntimeError as error:
            self.status.set(str(error))

    def import_legacy(self):
        if self.busy or self.practice_isolated.get():
            self.status.set("请在普通试聊模式、没有生成任务时进行升级。")
            return
        if messagebox.askyesno("升级v1试聊记录", "读取本模式旧版试聊文件并保留原件与v1备份？旧偏好只进入待审核候选，旧证据不直接导入；删除时管理副本会同步清理本场景。不会导入微信data目录。", parent=self.window):
            try:
                self.core.import_legacy()
                self.render()
                self.status.set("升级完成。请在记忆面板审核旧偏好候选；未自动激活。")
            except (ValueError, RuntimeError, OSError) as error:
                self.status.set(str(error) if isinstance(error, (ValueError, RuntimeError)) else "升级写入失败，原件保留；请检查目录权限。")

    def upgrade_v2(self):
        if self.busy:
            self.status.set("请等待当前生成结束再升级。")
            return
        if self.core.memory.state["version"] == 3:
            self.status.set("当前已使用v3来源结构；文件名保留原路径，无需再次升级。")
            return
        count = sum(len(d["turns"]) for d in self.core.memory.state["scenes"].values())
        if messagebox.askyesno("明确升级本模式v2记录", f"将本模式{count}条近期记录所在文件升级为v3？保留原路径、用户原话和记忆，不新增完整备份。旧助手正文不猜测补链，也不再进入模型请求。后续关联删除将保守清理本场景所有未知来源的旧助手正文；旧程序不能再写此文件。", parent=self.window):
            try:
                self.core.upgrade_v2()
                self.render()
                self.status.set("来源结构已升级为v3；旧正文保留供查看，没有补造来源。")
            except (ValueError, RuntimeError, OSError):
                self.status.set("升级未完成，正式文件和内存保持原状态；可检查权限后重试。")

    def load_case(self):
        if self.busy:
            return
        case_id, lines = CASES[self.practice.get()]
        if not lines:
            self.status.set("日常试聊没有预置脚本，可直接输入。")
            return
        if self.input.get("1.0", "end-1c").strip():
            if not messagebox.askyesno("保留草稿", "载入下一句将替换输入框里的草稿，继续？", parent=self.window):
                return
        mode_key = (self.practice.get(), self.mode.get(), self.use_memory.get(), self.use_topics.get())
        index = self.case_cursor.get(mode_key, 0) % len(lines)
        scene_id, person_id, text, focus = lines[index]
        self.case_cursor[mode_key] = index + 1
        from character import SCENE_BY_ID
        scene = SCENE_BY_ID[scene_id]
        self.scene_label.set(scene.label)
        self.switch_scene()
        self.speaker.set(scene.person(person_id).display)
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.case_id = case_id
        self.loaded_case_text = text
        self.status.set(f"体验 {index + 1}/{len(lines)}：{focus}。尚未发送，核对场景后点击发送。")

    def show_feedback(self, *, core=None, scene=None, turn_id=None, speaker_id=None, mode_label=None):
        if getattr(self, "auto_policy", None) is not None:
            self.auto_policy.activity()
        core = core if core is not None else self.core
        scene = scene if scene is not None else self.scene
        try:
            if turn_id is not None and not any(r["id"] == turn_id and r["speaker_id"] == speaker_id
                    for r in core.feedback_view(scene.id)):
                self.status.set("该回复已淘汰、删除或失效，没有改评另一轮。")
                return
            panel = self.feedback_panel
            if panel and panel.window.winfo_exists() and getattr(panel, "submitted", False):
                # A committed window is terminal even if its close failed.
                # Never attach its submitted flag or old draft to a new turn.
                try:
                    panel.window.destroy()
                except tk.TclError:
                    notice = "原反馈已提交；窗口关闭失败，请先关闭原窗口再评价另一轮，没有重复提交。"
                    panel.hint.set(notice)
                    self.status.set(notice)
                    return panel
                panel = None
            if panel and panel.window.winfo_exists():
                if panel.core is core and panel.scene.id == scene.id:
                    if turn_id is not None:
                        panel.select_turn(turn_id)
                    panel.window.lift()
                    return panel
                if panel.has_unsaved():
                    notice = "另一模式/场景仍有未保存反馈。请先在原窗口保存，或点击“放弃未保存反馈”，再回原聊天入口选择；没有转绑。"
                    panel.hint.set(notice)
                    self.status.set(notice)
                    panel.window.lift()
                    return panel
                panel.window.destroy()
            from feedback_panel import FeedbackPanel
            self.feedback_panel = FeedbackPanel(self, core=core, scene=scene, turn_id=turn_id,
                mode_label=mode_label if mode_label is not None else self.feedback_mode_label())
            return self.feedback_panel
        except (RuntimeError, ValueError, tk.TclError):
            panel = self.feedback_panel
            if panel and panel.window.winfo_exists():
                try:
                    panel.clear_evidence()
                except tk.TclError:
                    pass
            self.status.set("反馈窗口显示未完成；未生成或提交，请从原回复入口重新打开。")

    def close(self):
        self.stop_auto("窗口关闭，自动已停止", render=False)
        self._clear_feedback_actions()
        for core in self.cores.values():
            core.pause()
            core.clear_request_evidence()
        self.api_values["api_key"].set("")
        self.closed = True
        if self.poll_after is not None:
            try:
                self.window.after_cancel(self.poll_after)
            except tk.TclError:
                pass
        self.window.destroy()


def json_display(value):
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    LocalChat().window.mainloop()
