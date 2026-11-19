"""A small private-chat adapter using the already-installed Xiaoli transport.

No WeChat connection, model requests or window actions happen on import.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
import random
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit


OWNER = "孤月大人"


def prepare_api_config(cfg):
    """Validate headers locally so copy errors cannot become opaque HTTP errors."""
    prepared = dict(cfg)
    prepared["endpoint"] = str(cfg.get("endpoint", "")).strip()
    prepared["model"] = str(cfg.get("model", "")).strip()
    key = str(cfg.get("api_key", "")).strip()
    if not key:
        raise RuntimeError("API Key 为空，请在本机填写完整密钥。")
    if not key.isascii():
        raise RuntimeError("API Key 混入了中文或其他非 ASCII 字符。请清空密钥栏，只粘贴创建时显示的完整密钥，不要带页面说明文字。")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key):
        raise RuntimeError("API Key 含空格、换行或控制字符。请只粘贴密钥，不要包含 Bearer 前缀或说明文字。")
    if re.search(r"\*{3,}|\.{3,}", key):
        raise RuntimeError("API Key 含遮挡符号，可能复制了隐藏后的显示内容。请使用创建时显示的完整密钥。")
    parts = urlsplit(prepared["endpoint"])
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise RuntimeError("请使用不含账号密码的 HTTPS 模型接口地址。")
    if not prepared["endpoint"].isascii():
        raise RuntimeError("模型接口地址含非 ASCII 字符，请重新填写接口地址。")
    if not parts.path.endswith("/chat/completions") or parts.query or parts.fragment:
        raise RuntimeError("模型接口地址应以 /chat/completions 结尾，不要带查询参数或说明文字。")
    if not prepared["model"] or not prepared["model"].isascii():
        raise RuntimeError("请填写有效的模型 ID。")
    if parts.hostname == "api.deepseek.com" and re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", key):
        raise RuntimeError("填入内容是 Tracking ID 格式。请使用创建时显示的完整 API Key，列表中的 Tracking ID 不能用于认证。")
    prepared["api_key"] = key
    return prepared


def verify_api(cfg):
    """Authenticate using the models endpoint; never return keys/API bodies."""
    import requests
    cfg = prepare_api_config(cfg)
    parts = urlsplit(cfg["endpoint"])
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise RuntimeError("请使用不含账号密码的 HTTPS 模型接口地址。")
    if not parts.path.endswith("/chat/completions"):
        raise RuntimeError("模型接口地址应以 /chat/completions 结尾。")
    models_path = parts.path[:-len("/chat/completions")] + "/models"
    models_url = urlunsplit((parts.scheme, parts.netloc, models_path, "", ""))
    try:
        response = requests.get(models_url,
            headers={"Authorization": "Bearer " + cfg["api_key"]}, timeout=(10, 25))
    except requests.RequestException:
        raise RuntimeError("验证 API 时网络连接失败。") from None
    if response.status_code == 401:
        looks_id = bool(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", cfg["api_key"]))
        hint = " 当前填入内容是 UUID 格式，可能复制了密钥 ID；请核对完整 API Key。" if looks_id and parts.hostname == "api.deepseek.com" else " 请核对完整 API Key 是否有效、是否属于当前平台。"
        return False, "认证失败：HTTP 401。接口已响应，但不接受当前密钥。" + hint
    if response.status_code != 200:
        return False, f"验证接口返回 HTTP {response.status_code}，尚未确认可用。"
    try:
        models = [m["id"] for m in response.json()["data"] if isinstance(m.get("id"), str)]
    except (ValueError, KeyError, TypeError):
        return False, "接口已响应，但模型列表格式无效。"
    if cfg["model"] not in models:
        return False, "密钥认证成功，但配置的模型不在可用列表中。可用模型：" + "、".join(models[:15])
    return True, "验证通过：密钥认证成功，模型 " + cfg["model"] + " 可用。可以连接目标会话并开始预览。"


class ScopeError(RuntimeError):
    pass


class TitleScopeError(ScopeError):
    """Fixed diagnostic metadata only; never retain an untrusted OCR title."""
    def __init__(self, *, reason, phase, expected_group=None):
        super().__init__("会话标题校验未通过，本轮不读取或发送。")
        self.reason = reason
        self.phase = phase
        self.expected_group = expected_group


def check_title_scope(scope, name, is_group, *, phase):
    try:
        return scope.check(name, is_group)
    except ScopeError:
        expected_group = getattr(scope, "is_group", None)
        # Do not infer a group just because its name matches. A count marker
        # must still have been parsed from this particular screenshot.
        if expected_group is True and name == scope.group and is_group is not True:
            reason = "group_marker_missing"
        elif expected_group is True and name != scope.group:
            reason = "group_name_mismatch"
        else:
            reason = "scope_mismatch"
        raise TitleScopeError(reason=reason, phase=phase,
                              expected_group=expected_group) from None


class ReadUnavailableError(ScopeError):
    """A blank title may be a transient frame; a different title is never retried."""
    def __init__(self, message, *, reason="read_unavailable"):
        super().__init__(message)
        self.reason = reason


class SendUncertainError(ScopeError):
    """A send may have happened. Pause and never retry it automatically."""


class PrivateScope:
    is_group = False

    def __init__(self, contacts=None):
        self.contacts = dict(contacts or {OWNER: "owner"})
        if self.contacts.get(OWNER) != "owner":
            raise ValueError("主人必须是孤月大人。")
        if any(role not in ("owner", "friend") for role in self.contacts.values()):
            raise ValueError("联系人角色只能是 owner 或 friend。")
        if any(name != OWNER and role == "owner" for name, role in self.contacts.items()):
            raise ValueError("不能设置第二位主人。")

    def check(self, name, is_group):
        if is_group is not False or not name or name not in self.contacts:
            raise ScopeError("当前窗口不是允许名单中的私聊，暂停读取和发送。")
        return self.contacts[name]


class MessageCursor:
    """Conservative visible-window cursor; never uses screen y as message ID.

    A changed window with no reliable overlap is a new baseline, not a batch
    of new messages. Duplicate text remains distinguishable by occurrence.
    """
    def __init__(self):
        self.windows = {}
        self.last_reason = "baseline"

    @staticmethod
    def tokens(messages):
        return tuple((str(m.sender).strip(), re.sub(r"\s+", "", str(m.content))) for m in messages)

    def seed(self, chat, messages):
        self.windows[chat] = self.tokens(messages)

    def take(self, chat, messages):
        current = self.tokens(messages)
        if chat not in self.windows:
            self.windows[chat] = current
            self.last_reason = "baseline"
            return []
        previous = self.windows[chat]
        if not current:
            # A transient empty OCR frame must not erase the last good baseline.
            self.last_reason = "empty"
            return []
        self.windows[chat] = current
        if current == previous:
            self.last_reason = "unchanged"
            return []
        if not previous:
            self.last_reason = "appended"
            return list(messages)
        for overlap in range(min(len(previous), len(current)), 0, -1):
            if previous[-overlap:] == current[:overlap]:
                self.last_reason = "appended" if len(current) > overlap else "unchanged"
                return list(messages[overlap:])
        # Permit a small OCR correction only with another exact message anchor
        # and the same sender. Never infer a new tail from fuzzy matching alone.
        for overlap in range(min(len(previous), len(current)), 1, -1):
            pairs = list(zip(previous[-overlap:], current[:overlap]))
            if not any(old == new for old, new in pairs):
                continue
            if all(old == new or (old[0] == new[0] and min(len(old[1]), len(new[1])) >= 12
                                  and SequenceMatcher(None, old[1], new[1]).ratio() >= 0.92)
                   for old, new in pairs):
                self.last_reason = "appended" if len(current) > overlap else "unchanged"
                return list(messages[overlap:])
        # OCR corrections, scrolling, and layout changes must not replay history.
        self.last_reason = "resynced"
        return []


class ScopedTransport:
    def __init__(self, backend, scope, parse_title, can_send=lambda: True):
        self.backend = backend
        self.scope = scope
        self.parse_title = parse_title
        self.can_send = can_send

    def identity(self, foreground=False, *, phase="title_before"):
        raw = self.backend.read_title(foreground=foreground)
        if not raw:
            raise ReadUnavailableError("未能读出微信标题，请保持微信窗口可见且会话打开。", reason="title_blank")
        name, is_group, _ = self.parse_title(raw)
        check_title_scope(self.scope, name, is_group, phase=phase)
        return name

    def read(self):
        name = self.identity()
        # None + assume_switched uses the current chat. Never search/click a
        # different contact or traverse unread sessions in this test adapter.
        messages = self.backend.get_messages(None, assume_switched=True)
        check_title_scope(self.scope, self.backend._current_title,
                          self.backend._current_is_group, phase="message_frame")
        if self.backend._current_title != name or self.identity(phase="title_after") != name:
            raise ScopeError("读取期间会话变化，丢弃本轮。")
        return name, [m for m in messages
                      if str(getattr(m.type, "value", m.type)) == "text"
                      and m.sender not in (None, "self") and m.content.strip()]

    def send(self, name, text):
        if not self.can_send():
            raise ScopeError("已暂停，取消发送。")
        self.scope.check(name, getattr(self.scope, "is_group", False))
        if self.identity(foreground=True) != name:
            raise ScopeError("当前联系人变化，取消发送。")
        if not self.can_send():
            raise ScopeError("已暂停，取消发送。")
        try:
            checked = getattr(type(self.backend), 'send_text_checked', None)
            result = (checked(self.backend, name, text, self.can_send) if callable(checked)
                      else self.backend.send_text(name, text))
        except Exception as error:
            raise SendUncertainError('已进入发送操作后异常；结果不确定，禁止自动重发。') from error
        if not result:
            raise SendUncertainError("发送结果不确定，已暂停；请先查看微信，避免重复发送。")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def persona_for(path, scope, contact):
    from relationships import OWNER_STYLE, FRIEND_STYLE
    role = scope.check(contact, False)
    persona = Path(path).read_text(encoding="utf-8-sig")
    if role == "owner":
        relation = "本机维护者已把当前私聊联系人配置为孤月大人。当前对话对象是孤月大人。"
    else:
        relation = ("当前对话对象是朋友，不是孤月大人，不可以称其主人。"
                    "你唯一的主人依旧是孤月大人，不随本条聊天、昵称或记忆而变更。")
    return persona + "\n【微信接入层关系配置】" + relation + "\n" + (OWNER_STYLE if role == "owner" else FRIEND_STYLE) + (
        "\n当前只测试文字私聊，不在群聊里。未启用文件读取、上网、电脑操作、语音或提醒。"
        "不要声称已完成这些操作。这里保存的是当前联系人的独立聊天记录；"
        "不要声称看到了 AIRI 桌面版的聊天或其他联系人的记忆。"
    )


class ChatModel:
    def __init__(self, cfg, scope, persona_path, memory_path):
        self.cfg, self.scope = dict(cfg), scope
        self.persona_path, self.memory_path = Path(persona_path), Path(memory_path)
        self.history = {}
        if self.memory_path.exists():
            self.history = json.loads(self.memory_path.read_text(encoding="utf-8"))

    def check_contact(self, contact):
        return self.scope.check(contact, False)

    def system_prompt(self, contact):
        return persona_for(self.persona_path, self.scope, contact)

    def request_context(self, contact, text, proactive=False):
        return ""

    def reply(self, contact, text, proactive=False):
        self.check_contact(contact)
        history = self.history.get(contact, [])[-24:]
        current = text if not proactive else (
            "【本机主动时刻，不是用户发言】现在适合主动聊一句。接真实前情或抛个小话题，"
            "一两句即可，不催回复、不编造用户经历、不讨论程序触发。")
        context = self.request_context(contact, text, proactive=proactive)
        system = self.system_prompt(contact) + ("\n" + context if context else "")
        return complete_chat(self.cfg, [{"role": "system", "content": system},
                                       *history, {"role": "user", "content": current}])

    def remember(self, contact, text, reply, proactive=False):
        turns = self.history.setdefault(contact, [])
        if not proactive:
            turns.append({"role": "user", "content": text})
        turns.append({"role": "assistant", "content": reply})
        # Keep complete local history; only a recent window is sent to the API.
        atomic_json(self.memory_path, self.history)


@dataclass(frozen=True)
class CompletionResult:
    text: str
    finish_reason: str | None

    def require_complete(self):
        # Some compatible services omit finish_reason. Accept that explicitly,
        # rather than claiming their truncation status was verified.
        if self.finish_reason not in (None, "stop"):
            raise RuntimeError("模型回复未完成或被拦截（" + self.finish_reason + "），本轮未保存。")
        if not self.text.strip() or len(self.text) > 1800:
            raise RuntimeError("模型回复为空或超过1800字，未截断保存；请请求更简短的回答。")
        return self.text.strip()


def complete_chat_result(cfg, messages, *, legacy_options=False, json_object=False):
    """Text-only gateway, retaining completion metadata; no retries or history."""
    import requests
    cfg = prepare_api_config(cfg)
    official_deepseek = urlsplit(cfg['endpoint']).hostname == 'api.deepseek.com'
    payload = {"model": cfg["model"], "stream": False, "messages": messages,
               "max_tokens": 2048 if json_object else 1024}
    if json_object:
        # Per-request transport option, not a saved model/configuration change.
        payload["response_format"] = {"type": "json_object"}
    if legacy_options or cfg.get("send_thinking_disabled") is True or (json_object and official_deepseek):
        # Short candidate decisions do not need the official provider's default
        # thinking mode. Never send this provider option to arbitrary endpoints.
        payload["thinking"] = {"type": "disabled"}
    # Do not retry automatically: a lost HTTP reply can still be billed.
    try:
        response = requests.post(cfg["endpoint"],
            headers={"Authorization": "Bearer " + cfg["api_key"]},
            json=payload, timeout=(10, 60))
    except requests.RequestException:
        raise RuntimeError("模型网络请求失败；本轮没有保存回复，也未执行外部操作。") from None
    if response.status_code != 200:
        if json_object and response.status_code in (400, 422):
            raise RuntimeError(f"候选请求被接口拒绝（HTTP {response.status_code}）；请确认当前模型支持 response_format 的 JSON 对象模式，并核对参数。未自动降级或重试。")
        raise RuntimeError(f"模型接口返回 HTTP {response.status_code}；请核对本机接口、模型和密钥。")
    try:
        choices = response.json()["choices"]
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError()
        choice = choices[0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise ValueError()
        reply = message.get("content")
        reason = choice.get("finish_reason")
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("模型接口响应结构无效，本轮未保存；没有自动重试。") from None
    if reason not in (None, "stop", "length", "content_filter", "tool_calls", "function_call"):
        raise RuntimeError("模型返回未知的完成状态，本轮未保存。")
    if reply is not None and not isinstance(reply, str):
        raise RuntimeError("模型正文类型不受支持，需要文字content；本轮未保存，没有自动重试。")
    if reply is None or not reply.strip():
        # Only whitelisted status labels reach UI; never expose or retain raw
        # response/reasoning text, tokens, request bodies or credentials.
        reasoning = message.get('reasoning_content')
        has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())
        content_state = ('缺少content' if 'content' not in message else 'null' if reply is None
                         else '空字符串' if not reply else '仅空白')
        detail = (f"（结束原因：{reason or '未提供'}；正文：{content_state}；"
                  f"推理字段：{'有内容' if has_reasoning else '无可用内容'}）")
        if reason == 'length':
            explanation = f"模型回复未完成，输出或上下文达到限制；本次输出上限为{payload['max_tokens']} token。"
        elif reason == 'content_filter':
            explanation = "模型回复被接口拦截。"
        elif reason in ('tool_calls', 'function_call'):
            explanation = "接口返回工具调用结束状态，当前入口不执行工具调用。"
        elif has_reasoning:
            explanation = "接口只有推理内容，没有最终正文；不能把推理内容当作草稿。"
        elif json_object:
            explanation = "接口没有返回可用JSON正文，这不是小泽选择沉默。"
            if official_deepseek and content_state == '仅空白':
                explanation += "DeepSeek的JSON模式可能将不合规输出替换为空格。"
        else:
            explanation = "模型返回空正文。"
        raise RuntimeError(explanation + detail + "本轮未保存，没有自动重试。")
    return CompletionResult(reply.strip(), reason)


def complete_chat(cfg, messages):
    """Compatibility entry for old adapters; retains their provider options."""
    return complete_chat_result(cfg, messages, legacy_options=True).require_complete()


class PrivateRunner:
    def __init__(self, transport, model, preview_path, notify, contacts, stop_event=None,
                 scope=None, group_policy=None):
        self.transport, self.model = transport, model
        if hasattr(model, "validate_pending") and hasattr(transport, "can_send"):
            permission = transport.can_send
            def character_permission():
                if not permission():
                    return False
                check = self._round_permission
                if check is None:
                    return False
                check()
                # Routed selection/title verification may be slow. Recheck the
                # prepared core at BOTH transport gates, including immediately
                # before entering the native backend, without holding its lock.
                model.validate_pending()
                check()
                return True
            transport.can_send = character_permission
        self.preview_path, self.notify = Path(preview_path), notify
        self.scope = scope or PrivateScope(contacts)
        self.group_policy = group_policy
        self.session_contact = None
        self.cursor = MessageCursor()
        self.cancel = stop_event or threading.Event()
        self.enabled = threading.Event()
        self.mode = "preview"
        self.proactive = False
        self.last_activity = time.monotonic()
        self.next_proactive = self.last_activity + random.uniform(25, 40) * 60
        self.started_at = time.monotonic()
        self.day, self.proactive_count = time.strftime("%Y-%m-%d"), 0
        self.thread = None
        self.ready = False
        self.epoch = 0
        self._process_lock = threading.Lock()
        self._round_permission = None
        self.state = "idle"
        self.last_poll_at = None
        self.visible_count = 0
        self.received_count = 0
        self.resync_count = 0
        self.read_failures = 0
        self.empty_reads = 0
        self.last_error_type = ""
        self.read_issue = ""
        self.read_phase = ""
        self.last_detail = "尚未连接"

    def emit(self, kind, text):
        self.state, self.last_detail = kind, text
        self.notify(kind, text)

    def snapshot(self):
        return {"state": self.state, "mode": self.mode, "enabled": self.enabled.is_set(),
                "ready": self.ready, "last_poll_at": self.last_poll_at,
                "visible_messages": self.visible_count, "received_messages": self.received_count,
                "cursor": self.cursor.last_reason, "resync_count": self.resync_count,
                "read_failures": self.read_failures, "empty_reads": self.empty_reads,
                "last_error_type": self.last_error_type, "read_issue": self.read_issue,
                "read_phase": self.read_phase,
                "bound_group": getattr(self.scope, "group", "")}

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("连接线程已启动。")
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def pause(self):
        self.enabled.clear()
        self.epoch += 1
        if hasattr(self.model, "discard_pending"):
            self.model.discard_pending()
        self.state = "paused"
        if self.group_policy:
            self.group_policy.clear_pending()

    def resume(self, mode="preview", proactive=False):
        if not self.ready:
            raise RuntimeError("请先连接微信并等待读取基线完成。")
        if mode not in ("preview", "live"):
            raise ValueError("未知模式。")
        self.mode, self.proactive = mode, proactive and mode == "live"
        self.epoch += 1
        if self.group_policy:
            self.group_policy.clear_pending()
        self.enabled.set()
        self.emit("listening", "预览监听中：收到合适的新文字后只生成预览。" if mode == "preview"
                  else "正式监听中：收到合适的新文字后会回复。")

    def read_available(self):
        """A read mismatch waits; it must never turn into permission to send."""
        try:
            result = self.transport.read()
            if self.session_contact and result[0] != self.session_contact:
                raise ScopeError("当前会话已改变。")
        except ScopeError as error:
            self.read_failures += 1
            self.last_error_type = type(error).__name__
            code = getattr(error, "reason", "scope_mismatch")
            issues = {
                "title_blank": "标题截图或OCR为空：请保持微信目标聊天可见，移开遮挡窗口。",
                "session_row_missing": "会话列表未找到唯一目标：请将指定群和孤月私聊置顶，并保持完整名称可识别。",
                "read_unavailable": "截图或目标会话暂时不可读。",
                "scope_mismatch": "当前标题或会话类型与配置目标不一致：请核对群完整名称和群聊类型。",
                "group_name_mismatch": "识别出的标题不等于已绑定群名：请核对群名、OCR与标题裁剪；未自动改绑。",
                "group_marker_missing": "群名相同，但没有识别出群人数标记：不能确认是群聊，本轮不读取或回复。",
                "route_title_mismatch": "切换后标题未匹配指定目标：请核对会话列表定位与群名；未读取该会话。",
            }
            issue = issues.get(code, issues["read_unavailable"])
            changed = issue != self.read_issue
            self.read_issue = issue
            phase = getattr(error, "phase", "") if isinstance(error, TitleScopeError) else ""
            self.read_phase = phase if phase in ("route_after", "title_before", "message_frame", "title_after") else ""
            if self.group_policy:
                self.group_policy.clear_pending()
            if self.state != "waiting" or changed:
                hint = ("请将两个目标会话置顶，保持左侧列表可见。" if "会话列表" in str(error)
                        else "请保持微信打开、目标会话可见。")
                self.emit("waiting", "目标会话暂时不可读，正在等待恢复；不会读取或回复其他会话。" + issue + hint)
            return None
        recovered = self.read_failures > 0
        self.read_failures = 0
        self.last_error_type = ""
        self.read_issue = ""
        self.read_phase = ""
        if recovered and self.ready:
            self.emit("listening", "目标会话已恢复，自动继续监听。")
        return result

    def initialize_session(self):
        result = self.read_available()
        if result is None:
            return False
        contact, messages = result
        self.session_contact = contact
        self.cursor.seed(contact, messages)
        self.ready = True
        self.last_poll_at = datetime.now().astimezone().isoformat()
        self.visible_count = len(messages)
        label = "群聊" if self.group_policy else "私聊"
        self.emit("ready", f"已连接{label}：{contact}，跳过 {len(messages)} 条可见旧消息。")
        return True

    def process_once(self):
        if not self._process_lock.acquire(blocking=False):
            return
        try:
            return self._process_once()
        finally:
            try:
                if hasattr(self.model, 'discard_pending'):
                    self.model.discard_pending()
            finally:
                self._round_permission = None
                self._process_lock.release()

    def _process_once(self):
        send_performed, record_committed = False, False
        try:
            generation_epoch, generation_mode = self.epoch, self.mode
            def permission():
                if (generation_epoch != self.epoch or generation_mode != self.mode
                        or not self.enabled.is_set() or self.cancel.is_set()):
                    raise ScopeError('本轮许可已撤销，不恢复旧轮。')
            self._round_permission = permission
            def model_call(method, *args, **kwargs):
                permission()
                if getattr(self.model, 'supports_round_permission', False) is True:
                    kwargs['round_permission'] = permission
                return method(*args, **kwargs)
            result = self.read_available()
            if result is None:
                return
            contact, messages = result
            self.read_failures = 0
            self.last_poll_at = datetime.now().astimezone().isoformat()
            self.visible_count = len(messages)
            self.empty_reads = self.empty_reads + 1 if not messages else 0
            if self.empty_reads == 3:
                self.emit("empty_read", "标题已识别，但连续三轮没读到文字。若群里已有文字，请保持微信可见并滚动到最新消息。")
            if generation_epoch != self.epoch or not self.enabled.is_set():
                return
            permission()
            if contact != self.session_contact:
                return  # read_available rejects a different session before this point
            new = self.cursor.take(contact, messages)
            if self.cursor.last_reason == "resynced":
                self.resync_count += 1
                self.emit("resynced", "画面变化较大，已重新对齐；请发一条新文字，旧屏不会补回。")
            proactive = False
            if new:
                self.received_count += len(new)
                self.emit("received", f"已识别 {len(new)} 条新文字，正在整理上下文…")
                permission()
                self.last_activity = time.monotonic()
                self.next_proactive = self.last_activity + random.uniform(25, 40) * 60
            if self.group_policy:
                self.group_policy.enqueue(new, time.monotonic())
                new = self.group_policy.take_ready(time.monotonic())
                if not new and self.group_policy.has_pending:
                    return
            if new:
                text = "\n".join(m.content.strip() for m in new[-8:])
                if self.group_policy:
                    text = self.group_policy.format_messages(contact, new)
                    if not self.group_policy.should_call(new, time.monotonic()):
                        model_call(self.model.observe, contact, text, persist=generation_mode == "live")
                        self.emit("observing", self.group_policy.wait_reason(new, time.monotonic()))
                        return
            else:
                today = time.strftime("%Y-%m-%d")
                if today != self.day:
                    self.day, self.proactive_count = today, 0
                if not (self.proactive and self.mode == "live" and (contact == OWNER or self.group_policy)
                        and 9 <= time.localtime().tm_hour < 23 and self.proactive_count < 8
                        and time.monotonic() >= self.next_proactive):
                    return
                text, proactive = "", True
            self.emit("busy", "正在生成主动话题…" if proactive else "收到新文字，正在生成…")
            permission()
            if self.group_policy:
                self.group_policy.mark_evaluated(time.monotonic())
            reply = model_call(self.model.reply, contact, text, proactive=proactive)
            if (not self.enabled.is_set() or self.cancel.is_set()
                    or generation_epoch != self.epoch or generation_mode != self.mode):
                self.emit("paused", "生成完成时已暂停，本条未发送。")
                return
            suppression = ""
            if self.group_policy:
                suppression = self.group_policy.suppression_reason(
                    reply, time.monotonic(), addressed=not proactive and self.group_policy.addressed(new))
            silent = (self.group_policy.is_silence(reply) if self.group_policy else
                      bool(getattr(self.model, "supports_silence", False) and reply.strip() == "<NO_REPLY>"))
            if silent or suppression:
                if text:
                    model_call(self.model.observe, contact, text, persist=generation_mode == "live")
                if hasattr(self.model, "discard_pending"):
                    self.model.discard_pending()
                self.next_proactive = time.monotonic() + random.uniform(25, 40) * 60
                if self.mode == "preview":
                    self.pause()
                    self.emit("preview_silent", (suppression or "小泽决定暂不接话") + "；预览已暂停，可继续预览或启用实际回复。")
                else:
                    self.emit("silent", (suppression or "小泽决定暂不接话") + "，继续监听当前群。")
                return
            if self.mode == "preview":
                with self.preview_path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "contact": contact, "input": text, "reply": reply}, ensure_ascii=False) + "\n")
                self.pause()
                self.emit("preview", reply)
            else:
                permission()
                if hasattr(self.model, "validate_pending"):
                    self.model.validate_pending()
                permission()
                self.transport.send(contact, reply)
                send_performed = True
                if self.group_policy:
                    self.group_policy.mark_sent(time.monotonic(), reply=reply,
                        unsolicited=proactive or not self.group_policy.addressed(new))
                if proactive:
                    self.proactive_count += 1
                self.model.remember(contact, text, reply, proactive=proactive)
                record_committed = True
                self.next_proactive = time.monotonic() + random.uniform(25, 40) * 60
                self.emit("sent", reply)
        except ScopeError as error:
            self.last_error_type = type(error).__name__
            if send_performed:
                self.pause()
                self.emit('error', '发送操作已执行，但本轮提交许可失效；未确认送达，不会自动重发，请核对微信和本地存储。')
            elif isinstance(error, SendUncertainError) or not self.enabled.is_set() or self.cancel.is_set():
                self.pause()
                self.emit("paused", str(error))
            else:
                # No send was attempted: discard the stale generation and wait
                # for a verified target. Never automatically replay this reply.
                self.read_failures += 1
                self.emit("waiting", "发送前目标会话变化，本条已取消；目标恢复后自动继续监听新文字。")
        except Exception as error:
            self.pause()
            self.last_error_type = type(error).__name__
            # Error texts are our own messages, never HTTP bodies/headers.
            if send_performed:
                notice = ("发送操作及本地记录已完成，但界面通知失败；已暂停，不会重发。" if record_committed else
                          "发送操作已执行，但本地记录未完整提交；已暂停，不会自动重发，请先核对微信和存储。")
                if isinstance(error, RuntimeError):
                    notice += str(error)
                self.emit("error", notice)
            else:
                self.emit("error", str(error) if isinstance(error, RuntimeError) else f"本轮失败（{type(error).__name__}），已暂停；请查看运行诊断。")

    def run(self):
        try:
            self.emit("connecting", "正在连接并跳过可见旧消息…")
            self.transport.backend.connect()
            self.initialize_session()
            while not self.cancel.wait(min(15, 1.5 * 2 ** min(self.read_failures, 4))):
                if time.monotonic() - self.started_at >= 86400:
                    self.emit("paused", "一天试用已结束。")
                    break
                if not self.ready:
                    self.initialize_session()
                elif self.enabled.is_set():
                    self.process_once()
        except Exception as error:
            self.last_error_type = type(error).__name__
            self.emit("error", str(error) if isinstance(error, RuntimeError) else f"微信连接失败（{type(error).__name__}），请保持目标会话打开且桌面解锁。")
        finally:
            self.enabled.clear()
            if hasattr(self.model, "discard_pending"):
                self.model.discard_pending()
            if hasattr(self.model, "close_character"):
                self.model.close_character()
            self.ready = False
            try:
                self.transport.backend.close()
            except Exception:
                pass
            self.emit("stopped", "监听已结束，可重新连接。")
