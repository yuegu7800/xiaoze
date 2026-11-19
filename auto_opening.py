"""Process-local automatic-opening policy. No Tk, timers, I/O or network.

Lock order for transactions: Core -> controller -> shared budget. UI methods
release the controller lock before touching a Core. A run never reads widgets.
"""
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import threading
import time
from weakref import WeakKeyDictionary

from character_service import Cancelled
from memory_rules import LOCAL_TZ
from opening_state import WaitState

IDLE_SECONDS = 600
CHECK_SECONDS = 30
WAKE_GAP_SECONDS = 90
HOURLY_LIMIT = 2
SESSION_LIMIT = 6


class ResponseGates:
    """Finite scene gates per live Core, without retaining closed Core stores."""
    def __init__(self):
        self.cores = WeakKeyDictionary()

    def __contains__(self, key):
        core, scene, person = key
        return (scene, person) in self.cores.get(core, {})

    def __getitem__(self, key):
        core, scene, person = key
        return self.cores[core][scene, person]

    def __setitem__(self, key, value):
        core, scene, person = key
        self.cores.setdefault(core, {})[scene, person] = value

    def get(self, key, default=None):
        return self[key] if key in self else default


class SessionBudget:
    """Shared across automatic sessions/windows in this Python process."""
    def __init__(self):
        self.lock = threading.RLock()
        self.attempts = deque(maxlen=SESSION_LIMIT)
        self.adoptions = deque(maxlen=SESSION_LIMIT)
        self.attempt_total = self.adoption_total = 0
        self.waiting = ResponseGates()
        self.manual_receipts = deque(maxlen=128)

    def counts(self, now):
        with self.lock:
            return (self.attempt_total, sum(now - t < 3600 for t in self.attempts),
                    self.adoption_total, sum(now - t < 3600 for t in self.adoptions))

    def available(self, now, adoption=False):
        total, hourly, adopted, adopted_hourly = self.counts(now)
        if adoption:
            total, hourly = adopted, adopted_hourly
        return total < SESSION_LIMIT and hourly < HOURLY_LIMIT

    def reserve_attempt(self, now):
        with self.lock:
            if not self.available(now):
                raise Cancelled("自动请求尝试达到上限。")
            self.attempts.append(now)
            self.attempt_total += 1


PROCESS_BUDGET = SessionBudget()


@dataclass(frozen=True)
class Binding:
    core: object
    selection: tuple
    scene_id: str
    person_id: str
    api: bool
    use_memory: bool
    use_topics: bool

    @property
    def key(self):
        return self.core, self.scene_id, self.person_id


@dataclass(frozen=True)
class AutoRun:
    binding: Binding
    generation: int
    activity_version: int
    adoption_version: int
    auto_adopt: bool
    ticket: int


class AutoOpening:
    def __init__(self, *, monotonic=time.monotonic, wall_clock=None, budget=None):
        self.monotonic = monotonic
        self.wall_clock = wall_clock or (lambda: datetime.now(LOCAL_TZ))
        self.budget = budget if budget is not None else PROCESS_BUDGET
        self.lock = threading.RLock()
        self.enabled = self.auto_adopt = False
        self.binding = None
        self.active = None
        self.generation = self.activity_version = self.adoption_version = 0
        self.last_activity = self.last_check = self.monotonic()
        self.blocked = False
        self.blocked_reason = ""
        self.stopped_error = False
        self.silent_receipt = None
        self.note = "已关闭"

    def quiet(self):
        hour = self.wall_clock().astimezone(LOCAL_TZ).hour
        return hour >= 23 or hour < 8

    def start(self, binding, data):
        if binding.scene_id != "local-owner" or binding.person_id != "owner":
            raise ValueError("自动模式仅支持与孤月大人私聊。")
        scene = binding.core.scenes[binding.scene_id]
        if scene.kind != "private" or scene.person(binding.person_id).role != "owner":
            raise ValueError("自动模式的实际人物范围不符。")
        with self.lock, self.budget.lock:
            self.generation += 1
            self.enabled, self.auto_adopt = True, False
            self.binding, self.active = binding, None
            self.activity_version += 1
            self.adoption_version += 1
            self.last_activity = self.last_check = self.monotonic()
            self.blocked = False
            self.blocked_reason = ""
            self.stopped_error = False
            self.silent_receipt = None
            # Initialize from actual dialogue once; deletions/reopening cannot
            # reinterpret an old user turn as a new successful response.
            if binding.key not in self.budget.waiting:
                latest = next((t for t in reversed(data["turns"])
                               if t.get("target", t["speaker_id"]) == binding.person_id), None)
                self.budget.waiting[binding.key] = bool(latest and latest["kind"] == "local_assistant")
            self.note = "等待空闲（开启后重新等待10分钟）"

    def stop(self, reason="已关闭", *, error=False):
        with self.lock:
            self.enabled = self.auto_adopt = False
            self.generation += 1
            self.adoption_version += 1
            self.stopped_error = error
            self.silent_receipt = None
            self.note = reason

    def activity(self):
        with self.lock:
            self.last_activity = self.monotonic()
            self.activity_version += 1
            self.silent_receipt = None
            if self.enabled:
                self.note = "等待空闲（用户操作后重新计时）"

    def set_adoption(self, enabled):
        with self.lock:
            self.auto_adopt = bool(enabled and self.enabled)
            # A new checkmark never authorizes an older run retroactively.
            self.adoption_version += 1

    def environment(self, binding, blocked, *, reason="blocked"):
        with self.lock:
            if not self.enabled:
                return
            if binding != self.binding:
                self.stop("范围已改变，自动已关闭")
                return
            if blocked or self.blocked:
                self.activity()
            self.blocked = blocked
            self.blocked_reason = reason if blocked else ""
            if blocked:
                self.note = "暂缓：输入、子窗口、最小化或暂停；恢复后重新等待10分钟"

    def _condition(self, now, wall, environment_reason=None):
        """Pure decision in the original scheduler's priority order.

        Caller holds policy/budget locks. No Core calls or state writes here.
        """
        gap = now - self.last_check
        if gap > WAKE_GAP_SECONDS or gap < 0:
            return WaitState("resume", "恢复后重新等待空闲，不补跑。", "等待下一次检查重新计时。", IDLE_SECONDS)
        if not self.enabled:
            return WaitState("error_stopped" if self.stopped_error else "off", self.note,
                             "核对后由你重新开启；窗口关闭后不运行。" if self.stopped_error else "仅主人私聊可由你明确开启。")
        if self.active is not None:
            run = self.active
            cancelled = run.generation != self.generation or run.activity_version != self.activity_version
            return WaitState("evaluating", "正在评估（已作废，等待返回）" if cancelled else "正在评估。",
                             "输入保留，请等待；已发请求不能撤回，可能计费。" if run.binding.api else "输入保留，请等待；本地模拟不调用API。")
        blocked = self.blocked_reason or "blocked" if self.blocked else ""
        if environment_reason is not None:
            blocked = environment_reason
            if not blocked and self.blocked:
                # The scheduler's next environment() call resets idle when a
                # deferral clears. Preview that transition without doing it.
                return WaitState("environment_resume", "暂缓条件已解除，等待重新计时。", "环境核对后重新等待10分钟空闲。", IDLE_SECONDS)
        reasons = {"input": "输入框有未发送文字", "panel": "子窗口仍打开", "minimized": "主窗口已最小化或隐藏",
                   "paused": "试聊已暂停", "busy": "有手动生成在途", "data_error": "本地资料暂不可用",
                   "blocked": "输入、子窗口、最小化或暂停"}
        if blocked:
            return WaitState("deferred_" + blocked, "暂缓：" + reasons.get(blocked, reasons["blocked"]), "处理后重新等待10分钟空闲。")
        local = wall.astimezone(LOCAL_TZ)
        if local.hour >= 23 or local.hour < 8:
            morning = local.replace(hour=8, minute=0, second=0, microsecond=0)
            if local.hour >= 23:
                morning += timedelta(days=1)
            return WaitState("quiet_hours", "安静时段（北京时间23:00—08:00）", "到08:00后仍需满足其他条件。", (morning - local).total_seconds())
        idle = max(0, IDLE_SECONDS - (now - self.last_activity))
        if idle:
            return WaitState("idle", "等待空闲10分钟。", "满足空闲后还需有效话题；不保证到点开口。", idle)
        if self.budget.waiting.get(self.binding.key, False):
            return WaitState("waiting_user", "已加入本地聊天，等待用户回应。", "在该场景成功发送新聊天后才可再自动开口。")
        for adoption in (False, True):
            if (adoption and not self.auto_adopt) or (not adoption and not self.binding.api):
                continue
            total = self.budget.adoption_total if adoption else self.budget.attempt_total
            stamps = self.budget.adoptions if adoption else self.budget.attempts
            name = "自动本地开口" if adoption else "自动请求尝试"
            if total >= SESSION_LIMIT:
                return WaitState("adoption_session_limit" if adoption else "request_session_limit",
                                 name + "达到本次程序会话上限。", "本次会话不再自动执行；手动调用不在此配额内。")
            rolling = [stamp for stamp in stamps if now - stamp < 3600]
            if len(rolling) >= HOURLY_LIMIT:
                wait = max(0, sorted(rolling)[-HOURLY_LIMIT] + 3600 - now)
                return WaitState("adoption_hour_limit" if adoption else "request_hour_limit", name + "达到滚动小时上限。",
                                 "额度恢复后仍需其他条件；不用反复开关。", wait)
        return WaitState("ready", "空闲条件已满足，等待定时检查。", "有有效话题时才可能评估；不保证开口。")

    def projection(self, candidate=None, *, environment_reason=None):
        """Read-only current conditions, never a request history or a tick."""
        with self.lock, self.budget.lock:
            now, wall = self.monotonic(), self.wall_clock()
            state = self._condition(now, wall, environment_reason)
            # Off must remain off even after a long interval without scheduling.
            if not self.enabled:
                return WaitState("error_stopped" if self.stopped_error else "off", self.note,
                                 "核对后由你重新开启。" if self.stopped_error else "仅主人私聊可由你明确开启。")
            if state.ready and candidate is not None:
                state = candidate.state
                if state.remaining_seconds is not None:
                    state = replace(state, remaining_seconds=max(0, state.remaining_seconds - (wall - candidate.checked_at).total_seconds()))
                if (state.code == "cooldown" and self.silent_receipt is not None
                        and self.silent_receipt[1] == candidate.epoch):
                    mode = "模型" if self.silent_receipt[0] == "model_silent" else "模拟"
                    state = replace(state, message=mode + "选择沉默，成功评估后冷却。")
            return state

    def due(self):
        with self.lock, self.budget.lock:
            now = self.monotonic()
            state = self._condition(now, self.wall_clock())
            self.last_check = now
            if state.code == "resume":
                self.activity()
            if state.code not in ("off", "error_stopped", "evaluating") and not state.code.startswith("deferred_"):
                self.note = state.message
            return state.ready

    def begin(self, ticket):
        with self.lock:
            run = AutoRun(self.binding, self.generation, self.activity_version,
                          self.adoption_version, self.auto_adopt, ticket)
            self.active = run
            self.silent_receipt = None
            self.note = "正在评估（已发请求无法撤回）"
            return run

    def guard(self, run):
        with self.lock:
            if (not self.enabled or self.active is not run or run.generation != self.generation
                    or run.activity_version != self.activity_version or run.binding != self.binding
                    or self.blocked or self.quiet()):
                raise Cancelled("自动运行资格已取消；不展示或采用迟到结果，已发请求可能仍计费。")
            if self.monotonic() - self.last_check > WAKE_GAP_SECONDS:
                raise Cancelled("窗口调度中断，迟到结果不保存或采用。")
            with self.budget.lock:
                if self.budget.waiting.get(run.binding.key, False):
                    raise Cancelled("等待用户回应，本次不开口。")

    def reserve_attempt(self, run):
        # Called under Core lock immediately before actual completion, after
        # local gates and config validation; budget check+reservation is atomic.
        with self.lock:
            self.guard(run)
            self.budget.reserve_attempt(self.monotonic())

    def adopt(self, run, result):
        """One exact receipt, after evaluate_candidate released request_slot."""
        core, scene, person = run.binding.core, run.binding.scene_id, run.binding.person_id
        with core._lock, self.lock, self.budget.lock:
            self.guard(run)
            if (not run.auto_adopt or not self.auto_adopt or run.adoption_version != self.adoption_version
                    or result.get("decision") != "draft" or not result.get("draft_id")):
                return None
            if not self.budget.available(self.monotonic(), adoption=True):
                self.note = "自动加入本地聊天达到上限；已生成草稿保留"
                return None
            if "commit_ticket" not in result:
                raise Cancelled("缺少本次生成提交凭据，不能自动采用。")
            event_id = core.adopt_draft(scene, result["draft_id"], target=person, purpose="private",
                                        ticket=result["commit_ticket"])
            self.budget.adoptions.append(self.monotonic())
            self.budget.adoption_total += 1
            self.budget.waiting[run.binding.key] = True
            self.budget.manual_receipts.append(event_id)
            return event_id

    def finish(self, run, result=None, *, error=None, cancelled=False):
        with self.lock, self.budget.lock:
            if self.active is not run:
                return
            self.active = None
            if run.generation != self.generation or not self.enabled:
                return
            if error is not None:
                self.stop("错误后已停止：" + error, error=True)
            elif cancelled:
                self.activity()
                self.note = "本次自动运行已取消，重新等待空闲；已提交草稿可手动处理"
            elif result and result.get("decision") == "draft":
                self.note = "已加入本地聊天，等待用户回应" if self.budget.waiting.get(run.binding.key) else "留下待处理草稿（未发送）"
            else:
                # The transaction may have blocked locally after the UI
                # precheck (for example, topic expiry). Only it knows whether
                # a model ran and last_check was actually committed.
                self.note = result["reason"] if result else "本次自动评估未完成，等待核对"
                if result and result.get("commit_ticket") is not None and result.get("decision") == "silent":
                    self.silent_receipt = ("model_silent" if run.binding.api else "simulation_silent", result["commit_ticket"])

    def user_committed(self, core, scene_id, person_id):
        with self.budget.lock:
            self.budget.waiting[(core, scene_id, person_id)] = False
        self.activity()

    def manual_adopted(self, core, scene_id, person_id, event_id):
        with self.budget.lock:
            if event_id not in self.budget.manual_receipts:
                self.budget.manual_receipts.append(event_id)
                self.budget.waiting[(core, scene_id, person_id)] = True
        self.activity()

    def status(self, candidate=None, *, environment_reason=None):
        state = self.projection(candidate, environment_reason=environment_reason)
        total, hourly, adopted, adopted_hourly = self.budget.counts(self.monotonic())
        return (f"{state.text()} · 本次程序会话：自动模型请求尝试 {total}/6（滚动1小时{hourly}/2，余{max(0, min(6-total, 2-hourly))}）"
                f" · 自动本地开口 {adopted}/6（滚动1小时{adopted_hourly}/2）")
