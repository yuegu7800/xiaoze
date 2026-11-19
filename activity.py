"""Bounded local topics and unsent drafts. No scheduler, platform or network."""
from datetime import datetime, timedelta
import hashlib
import json
import re
import uuid
from memory_rules import LOCAL_TZ
from dialogue import trace_valid, validate_trace, hidden_sources, topic_raw_sources
from opening_state import WaitState

MAX_TOPICS, MAX_DRAFTS = 32, 24
COOLDOWN_SECONDS = 600
POLICY_VERSION = "local-candidates-4"
TOPIC_STATES = {"pending", "active", "paused", "closed", "expired", "invalid"}
DRAFT_STATES = {"pending", "discarded", "expired", "invalid", "adopted"}
ACTIVITY_TAGS = ("时机合适", "打扰", "话题硬转", "重复追问", "草稿冒充已发生", "其他")
PUBLIC_PERSONA = ("你是小泽，自然正常交流，有温柔女仆气质，偶尔有荒诞联想。音乐和游戏是可调整的角色兴趣，"
                  "不是现实经历。公开草稿只根据明确公开素材或一般小想法，不编造外出、身体体验或共同经历，"
                  "不声称已经发布。普通中文一小段，不写客服开场、标题或动作旁白。")


def at(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("时间需要时区。")
    return result


def new_activity():
    return {"version": 1, "topics": [], "drafts": [], "withdrawn": [], "last_check": None,
            "public_material": "", "public_source": ""}


def validate_activity(value, scene_id, people):
    if value["version"] != 1:
        raise ValueError("activity version")
    if not all(isinstance(value[k], list) for k in ("topics", "drafts", "withdrawn")):
        raise ValueError("activity lists")
    if len(value["topics"]) > MAX_TOPICS or len(value["drafts"]) > MAX_DRAFTS or len(value["withdrawn"]) > 512:
        raise ValueError("activity capacity")
    for key, limit in (("public_material", 600), ("public_source", 160)):
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise ValueError("public input")
    if bool(value["public_material"]) != bool(value["public_source"]):
        raise ValueError("public source")
    if value["last_check"] is not None:
        at(value["last_check"])
    if len(set(value["withdrawn"])) != len(value["withdrawn"]) or any(not isinstance(s, str) or len(s) > 160 for s in value["withdrawn"]):
        raise ValueError("withdrawn")
    ids = set()
    for kind, records, states in (("topic", value["topics"], TOPIC_STATES), ("draft", value["drafts"], DRAFT_STATES)):
        for record in records:
            if record["scene_id"] != scene_id or record["target"] not in {*people, "all"} or record["status"] not in states:
                raise ValueError("activity scope")
            if record["target"] == "all" and len(people) == 1:
                raise ValueError("private audience")
            for key, limit in (("id", 160), ("source", 40)):
                if not isinstance(record[key], str) or not 0 < len(record[key]) <= limit:
                    raise ValueError("activity identity")
            if record["id"] in ids:
                raise ValueError("duplicate activity id")
            ids.add(record["id"])
            for key in ("created_at", "updated_at", "expires_at"):
                at(record[key])
            for key in ("source_ids", "record_ids"):
                limit = 512 if kind == "draft" and key == "source_ids" else 64
                if not isinstance(record[key], list) or len(record[key]) > limit or any(not isinstance(s, str) or not 0 < len(s) <= 160 for s in record[key]):
                    raise ValueError("activity provenance")
            if not record["source_ids"]:
                raise ValueError("missing activity provenance")
            fields = (("title", 100), ("direction", 200), ("quote", 160)) if kind == "topic" else (("text", 800), ("reason", 200), ("model_id", 120))
            for key, limit in fields:
                if not isinstance(record[key], str) or len(record[key]) > limit:
                    raise ValueError("activity text")
            if kind == "topic":
                if "revision_id" in record and (not isinstance(record["revision_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["revision_id"])):
                    raise ValueError("topic revision")
                if record["source"] != "local_maintainer" or type(record["start"]) is not int or record["start"] < 0:
                    raise ValueError("topic source")
                if not record["title"].strip() or not record["direction"].strip() or not record["quote"]:
                    raise ValueError("empty topic")
                at(record["not_before"])
            else:
                if "reply_trace" in record:
                    validate_trace(record["reply_trace"], scene_id)
                if record["status"] == "adopted" and (record["text"] or not isinstance(record.get("adopted_message_id"), str)):
                    raise ValueError("adopted draft receipt")
                if record["purpose"] not in ("private", "group", "public") or record["source"] != "local_generation":
                    raise ValueError("draft type")
                if record["purpose"] != "public" and record["purpose"] != ("private" if len(people) == 1 else "group"):
                    raise ValueError("draft scene kind")
                if record["status"] == "pending" and not record["text"].strip():
                    raise ValueError("empty draft")
                if record["copied_at"] is not None:
                    at(record["copied_at"])
                feedback = record["feedback"]
                if feedback is not None:
                    if not isinstance(feedback["note"], str) or len(feedback["note"]) > 300 or not isinstance(feedback["tags"], list) or not set(feedback["tags"]).issubset(ACTIVITY_TAGS):
                        raise ValueError("draft feedback")
                if not isinstance(record["versions"], dict) or any(not isinstance(v, str) or len(v) > 120 for v in record["versions"].values()):
                    raise ValueError("draft versions")
                if record["topic_id"] is not None and (not isinstance(record["topic_id"], str) or len(record["topic_id"]) > 160):
                    raise ValueError("draft topic")
                if record["purpose"] == "public" and record["topic_id"] is not None:
                    raise ValueError("public topic")
                if record["purpose"] != "public" and record["topic_id"] is None:
                    raise ValueError("missing draft topic")
                if record["status"] == "pending":
                    if record["purpose"] == "public":
                        if record["source_ids"] != [value["public_source"]]:
                            raise ValueError("public provenance")
                    else:
                        topic = next((t for t in value["topics"] if t["id"] == record["topic_id"]), None)
                        if not topic or not set(topic["source_ids"]).issubset(record["source_ids"]) or not set(topic["record_ids"]).issubset(record["record_ids"]) or record["target"] != topic["target"]:
                            raise ValueError("draft provenance")
    if sum(d["status"] == "pending" for d in value["drafts"]) > 1:
        raise ValueError("pending draft capacity")


def reconcile(data, now):
    """Lazy expiry/invalidation, also called before every write and copy."""
    activity = data.get("activity")
    if activity is None:
        return
    hidden = hidden_sources(data)
    memories = {r["id"]: r for r in data["memories"]}
    for topic in activity["topics"]:
        invalid = (bool(set(topic_raw_sources(topic)) & hidden) or any(i not in memories or memories[i]["status"] != "active" for i in topic["record_ids"]))
        if invalid:
            topic["status"] = "invalid"
        elif topic["status"] in ("pending", "active", "paused") and at(topic["expires_at"]) <= now:
            topic["status"] = "expired"
    topics = {t["id"]: t for t in activity["topics"]}
    for draft in activity["drafts"]:
        if draft["status"] != "pending":
            continue
        if draft["purpose"] == "public":
            invalid = draft["source_ids"] != [activity["public_source"]]
        else:
            topic = topics.get(draft["topic_id"])
            invalid = (not topic or topic["status"] != "active"
                       or any(i not in memories or memories[i]["status"] != "active" for i in draft["record_ids"]))
            # Old drafts did not track the full prompt's sources. Never pretend
            # to reconstruct provenance from their text or silently reuse them.
            invalid = invalid or draft["versions"].get("policy_version") != POLICY_VERSION
            invalid = invalid or not trace_valid(draft.get("reply_trace"), data)
        if invalid or at(draft["expires_at"]) <= now:
            draft["status"] = "invalid" if invalid else "expired"
            draft["text"], draft["feedback"] = "", None


def prune_sources(data, source_ids):
    """Destructive forget removes all owned derived text, including feedback."""
    activity = data.get("activity")
    if activity is None:
        return
    ids = set(source_ids)
    activity["topics"] = [t for t in activity["topics"] if not ids.intersection(t["source_ids"])]
    activity["drafts"] = [d for d in activity["drafts"] if not ids.intersection(d["source_ids"])]


def relevant_topic(data, person_id, query, now, *, diagnostics=None):
    activity = data.get("activity", new_activity())
    chars = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", query.casefold())) - set("我你他她的了是在这个吗呢什么怎么继续刚才")
    eligible = [t for t in activity["topics"] if t["status"] == "active" and t["target"] in (person_id, "all") and at(t["not_before"]) <= now]
    ranked = [(len(chars & set((t["title"] + t["direction"]).casefold())), t["updated_at"], t) for t in eligible]
    ranked = [item for item in ranked if item[0] >= 2]
    selected = max(ranked, key=lambda item: item[:2])[2] if ranked else None
    if diagnostics is not None:
        ranked_ids = {t["id"] for _, _, t in ranked}
        for topic in activity["topics"]:
            if topic["target"] not in (person_id, "all"):
                continue
            reason = ("相关性最高的人工话题；不是人物事实" if topic is selected else
                      "话题状态：" + topic["status"] if topic["status"] != "active" else
                      "尚未到最早接续时间" if at(topic["not_before"]) > now else
                      "只选一个相关性最高的话题" if topic["id"] in ranked_ids else "与查询无足够字符关联")
            diagnostics.append({"id": topic["id"], "title": topic["title"], "status": topic["status"],
                                "outcome": "selected" if topic is selected else "omitted", "reason": reason})
    return selected


def relevant_topic_many(data, queries, now, *, diagnostics=None):
    """At most one topic, from a bounded speaker range; never widen its target."""
    if not isinstance(queries, dict) or not 1 <= len(queries) <= 8:
        raise ValueError('话题取数范围限1至8个人物。')
    details, candidates = {}, {}
    for pid, query in queries.items():
        rows = []
        topic = relevant_topic(data, pid, query, now, diagnostics=rows)
        for row in rows:
            details.setdefault(row['id'], row)
        if topic:
            chars = set(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', query.casefold())) - set('我你他她的了是在这个吗呢什么怎么继续刚才')
            score = len(chars & set((topic['title'] + topic['direction']).casefold()))
            item = (score, topic['updated_at'], topic['id'], topic)
            if topic['id'] not in candidates or item[:3] > candidates[topic['id']][:3]:
                candidates[topic['id']] = item
    selected = max(candidates.values(), key=lambda item: item[:3])[3] if candidates else None
    targets = {t['id']: t['target'] for t in data.get('activity', {}).get('topics', [])}
    for row in details.values():
        row['target'] = targets[row['id']]
        if selected is not None and row['id'] == selected['id']:
            row.update(outcome='selected', reason='全请求唯一最佳相关话题；保留原目标')
        else:
            row['outcome'] = 'omitted'
            if row['id'] in candidates:
                row['reason'] = '全请求仅选择一个最佳相关话题'
    if diagnostics is not None:
        diagnostics.extend(details.values())
    return selected


def apply_topic_boundary(data, person_id, text, now):
    """Limited explicit stop phrases only; quotations/conditions do not close topics."""
    activity = data.get("activity")
    if not activity or re.search(r'[“”"\']|如果|假如|假设|转述|开玩笑', text):
        return
    clauses = [part.strip() for part in re.split(r"[，,。！？!?；;\n]", text)]
    for topic in activity["topics"]:
        if topic["target"] not in (person_id, "all") or topic["status"] not in ("active", "pending"):
            continue
        stop = any(re.fullmatch(r"(?:这个话题)?(?:我)?(?:不想继续聊(?:了)?|不要再聊(?:了)?|别再聊(?:了)?|不聊了|先到这里|结束了)", clause)
                   or (topic["title"] in clause and re.fullmatch(r"(?:这个话题|" + re.escape(topic["title"]) + r")(?:不要再聊|别再聊|不聊了|结束了)", clause)) for clause in clauses)
        if stop:
            topic.update(status="closed", updated_at=now.isoformat())
            topic["revision_id"] = uuid.uuid4().hex
    reconcile(data, now)


def explicit_silence(text, recorded_at, now):
    """Match complete direct clauses; keywords inside negations/quotes are not boundaries."""
    if re.search(r'[“”"\']|如果|假如|假设|转述|开玩笑', text):
        return False
    pattern = (r"(?:我)?(?:今天)?(?:暂时|现在)?(?:不想(?:聊天|聊|继续(?:聊)?)|想安静(?:一点|点)?|不想被打扰)(?:了)?"
               r"|(?:今天)?(?:先)?(?:不聊了|不聊|到这里|晚安)"
               r"|(?:请)?(?:别|不要)(?:再)?(?:问我|问|打扰我)(?:了)?")
    for match in re.finditer(r"[^，,。！？!?；;\n]+", text):
        clause = match.group().strip()
        if not re.fullmatch(pattern, clause):
            continue
        prefix = re.split(r"[。！？!?；;\n]", text[:match.start()])[-1]
        today_only = "今天" in clause or ("想安静" in clause and "今天" in prefix)
        if today_only and at(recorded_at).astimezone(LOCAL_TZ).date() < now.astimezone(LOCAL_TZ).date():
            continue
        return True
    return False


def actual_user_turns(data):
    """Accepted visible speech in stored order; import time is not speech time."""
    hidden = hidden_sources(data)
    return [t for t in data['turns'] if t['kind'] == 'local_exchange' and t['text']
            and t.get('backend') != 'wechat-legacy' and t['id'] not in hidden]


def audience_quiet(data, audience, now):
    """Shared boundary gate, independent of candidate UI/scheduler prerequisites.

    Caller expires/reconciles its snapshot. Unknown-time imported utterances
    cannot establish today's quiet/ending; maintained active boundaries can.
    """
    if any(r['person_id'] in audience and r['status'] == 'active' and r['slot'] == 'quiet'
           for r in data['memories']):
        return True
    turns = actual_user_turns(data)
    for pid in audience:
        latest = next((t for t in reversed(turns) if t['speaker_id'] == pid), None)
        if latest and explicit_silence(latest['text'], latest['created_at'], now):
            return True
    return False


def wechat_opening_context(data, people, group, now):
    """Pure bounded opening basis; no candidate activation/quota/clock mutation.

    Boundaries check the entire intended chat audience, while retrieval only
    uses at most the last eight reliable visible utterances / 4000 body chars.
    No reliable actual speech means silence, even if a manifest/profile exists.
    """
    audience = set(people)
    if not audience or audience_quiet(data, audience, now):
        return None
    chosen, size = [], 0
    for turn in reversed(actual_user_turns(data)):
        if turn['speaker_id'] not in audience:
            continue
        if len(chosen) == 8 or size + len(turn['text']) > 4000:
            break
        chosen.append(turn)
        size += len(turn['text'])
    if not chosen:
        return None
    chosen.reverse()
    own = {}
    for turn in chosen:
        own.setdefault(turn['speaker_id'], []).append(turn['text'])
    topics = [t for t in data.get('activity', {}).get('topics', [])
              if t['status'] == 'active' and at(t['not_before']) <= now
              and (t['target'] in own or (group and t['target'] == 'all'))]
    topic = max(topics, key=lambda t: (t['updated_at'], t['id'])) if topics else None
    common = '\n'.join(t['text'] for t in chosen)
    if topic:
        common += '\n' + topic['title'] + '\n' + topic['direction']
    last = chosen[-1]['speaker_id']
    target = topic['target'] if topic else ('all' if group and len(audience) > 1 else last)
    return {'last_speaker': last, 'participants': list(own), 'target': target,
            'boundary_audience': sorted(audience), 'topic_id': topic['id'] if topic else None,
            'queries': {pid: '\n'.join(bodies) + '\n' + common for pid, bodies in own.items()},
            'source_ids': [t['id'] for t in chosen]}


def check_candidate(data, scene, target, purpose, now, use_memory, use_topics):
    basis, state = candidate_condition(data, scene, target, purpose, now, use_memory, use_topics)
    return basis, state.message


def candidate_condition(data, scene, target, purpose, now, use_memory, use_topics):
    """One pure gate for both actual transactions and current status display."""
    activity = data.get("activity")
    if not activity:
        return None, WaitState("activity_disabled", "请先明确启用本场景的话题与草稿。", "打开“话题 / 主动草稿”启用并确认话题。")
    if any(d["status"] == "pending" for d in activity["drafts"]):
        return None, WaitState("pending_draft", "已有待处理草稿，先查看或丢弃。", "留下待处理草稿时，请到“话题 / 主动草稿”处理。")
    if activity["last_check"] and (now - at(activity["last_check"])).total_seconds() < COOLDOWN_SECONDS:
        return None, WaitState("cooldown", "候选评估冷却中（10分钟），这次先不说。", "等冷却结束后再按其他条件检查。",
                               max(0, COOLDOWN_SECONDS - (now - at(activity["last_check"])).total_seconds()))
    if purpose == "public":
        if not activity["public_material"]:
            return None, WaitState("public_missing", "尚无明确公开素材，这次先不说。", "手动确认公开素材。")
        return {"source_ids": [activity["public_source"]], "record_ids": [], "material": activity["public_material"]}, WaitState("ready", "用户明确提供公开素材。", "手动评估。")
    if not use_memory or not use_topics:
        return None, WaitState("switches_off", "长期记忆召回或话题延续已关闭，这次先不说。", "核对这两个开关；改变开关后需重新开启自动。")
    # Current quiet boundaries remain decisive for the intended audience.
    audience = {p.id for p in scene.people} if target == "all" else {target}
    if audience_quiet(data, audience, now):
        return None, WaitState("explicit_quiet", "当前对象中有人明确结束或想安静，先保持沉默。", "尊重结束或安静边界。")
    topics = [t for t in activity["topics"] if t["status"] == "active" and t["target"] == target and at(t["not_before"]) <= now]
    if not topics:
        future = [t for t in activity["topics"] if t["status"] == "active" and t["target"] == target and at(t["not_before"]) > now]
        if future:
            code, action, wait = "topic_wait", "已确认话题尚未到接续时间；到时还需满足其他条件。", min((at(t["not_before"]) - now).total_seconds() for t in future)
        elif any(t["status"] == "pending" and t["target"] == target for t in activity["topics"]):
            code, action, wait = "topic_unconfirmed", "到“话题 / 主动草稿”确认待续话题。", None
        else:
            code, action, wait = "topic_missing", "到“话题 / 主动草稿”从已有资料人工确认话题。", None
        return None, WaitState(code, "没有适合当前对象的有效待续话题，这次先不说。", action, wait)
    topic = max(topics, key=lambda t: t["updated_at"])
    return topic, WaitState("ready", "同场景、指定对象、人工保留的待续话题；边界与冷却允许再次考虑。", "等待定时检查；不保证生成或开口。")


def decode_candidate(result):
    # Only content is model supplied. All scope, IDs and deadlines are local.
    def invalid(reason):
        # Static reasons only; never display provider text or store it elsewhere.
        raise RuntimeError("候选格式无效（" + reason + "），本次未保存；没有自动重试或外部发送。") from None

    class DuplicateField(ValueError):
        pass

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise DuplicateField()
            value[key] = item
        return value

    if not isinstance(result, str) or not result.strip():
        invalid("正文为空或不是文字")
    raw = result.strip()
    if len(raw) > 12000:
        invalid("返回内容过长")
    # Accept only a single full JSON fence, never scrape JSON out of prose.
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", raw, re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except DuplicateField:
        invalid("JSON含重复字段")
    except (ValueError, TypeError):
        invalid("不是JSON对象；接口可能没有遵守JSON输出模式")
    if not isinstance(value, dict) or set(value) != {"decision", "text"}:
        invalid("字段不符合约定")
    if value["decision"] == "silent" and value["text"] == "":
        return None
    if value["decision"] != "draft":
        invalid("决定值不符合约定，沉默时正文必须为空")
    if not isinstance(value["text"], str) or not 0 < len(value["text"].strip()) <= 800:
        invalid("草稿正文为空、类型不符或超过800字")
    return value["text"].strip()


def candidate_protocol():
    return ('这是本地主动表达评估，不是用户发言。允许沉默，不催回复，不编造后续进展；不复述规则。'
            '仅返回JSON：{"decision":"silent","text":""} 或 {"decision":"draft","text":"一小段自然中文"}。'
            '沉默也必须返回完整的silent对象，不能用空白、不作答或省略正文来表示。'
            '这是本次返回的传输格式：角色自然口吻只写在text字符串里，外层必须是JSON对象。'
            '历史助手消息只是前情，不是本次输出格式的示例；角色设定中的普通聊天排版只约束text字符串。'
            '不得添加其他字段或Markdown，不要在JSON之外直接聊天。正文最多800字。生成结果只是尚未发送的草稿，不能写成已说过或已发布。')


def simulation_candidate(_messages):
    return json.dumps({"decision": "draft", "text": "【流程模拟，非模型回复】这是一条尚未发送的本地草稿。"}, ensure_ascii=False)
