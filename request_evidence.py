"""Ephemeral input-selection metadata. Never persist or copy prompt payloads."""
from copy import deepcopy
from collections import Counter
import hashlib
import json

EVIDENCE_VERSION = "request-selection-1"
RECALL_VERSION = "bounded-terms-stage6-review"
GROUP_RECALL_VERSION = "bounded-group-shared-1"


def material_stamp(data):
    """One digest, no retained material copy; expiry projections change it too."""
    activity = data.get("activity", {})
    material = [data["memories"], data["blocked"], activity.get("topics", []),
                activity.get("withdrawn", []), activity.get("public_source", "")]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def basis(reason):
    # Static codes only: not even a bounded excerpt of a private matched term.
    for marker, code in (("固定边界", "boundary"), ("词项/连续片段匹配", "literal"),
                         ("有限概念匹配", "concept"), ("明确饮品询问", "drink_question"),
                         ("明确泛问", "broad_query")):
        if marker in reason:
            return code
    return "state_or_insufficient"


def capture(report, provenance, turn_id, core_id, versions, *, use_memory, use_topics):
    """Whitelist projection of the diagnostics from the actual assembly only."""
    result = {"version": EVIDENCE_VERSION, "recall_version": RECALL_VERSION,
            "core_id": core_id, "scene_id": report["scene_id"], "person_id": report["person_id"],
            "turn_id": turn_id, "at": report["at"], "versions": dict(versions),
            "memory_enabled": bool(use_memory), "topics_enabled": bool(use_topics),
            "channels": deepcopy(report["channels"]), "dependencies": deepcopy(provenance),
            "records": [{"id": r["id"], "status": r["status"], "outcome": r["outcome"],
                         "basis": basis(r["reason"])}
                        | ({'person_id': r['person_id']} if 'person_id' in r else {}) for r in report["records"]],
            "topics": [{k: r[k] for k in ("id", "status", "outcome")}
                       | ({'target': r['target']} if 'target' in r else {}) for r in report["topics"]],
            "history": [{k: r[k] for k in ("id", "speaker_id", "kind", "user", "assistant")}
                        | {"user_budget": "预算" in r["user_reason"],
                           "assistant_budget": "预算" in r["assistant_reason"]}
                        for r in report["history"]]}
    if 'participant_scope' in report:
        scope = report['participant_scope']
        # IDs only, no display names, query strings, text or prompt copies.
        result['participant_scope'] = {k: deepcopy(scope[k]) for k in
            ('mode', 'last_speaker', 'participants', 'reply_audience', 'whole_group_retrieval')}
        if scope['mode'] == 'batch':
            result['recall_version'] = GROUP_RECALL_VERSION
    return result


def ids(values):
    if isinstance(values, dict):
        return "、".join(k + "@" + v for k, v in values.items()) or "无"
    return "、".join(values) or "无"


OUTCOME_LABELS = {
    "long_injected": "直接长期注入", "selected": "已选入", "recent_duplicate": "近期同源，长期不重复注入",
    "temporary_override": "被同人物同槽临时资料覆盖", "count_budget": "长期数量预算不足",
    "char_budget": "长期字符预算不足", "source_budget": "完整来源预算不足",
    "disabled": "相关开关关闭", "irrelevant": "有限规则依据不足，未从长期通道选入",
    "pending": "待确认", "expired": "已到期", "suppressed": "已停止召回",
    "corrected": "已被更正", "superseded": "已被新值替代", "active": "当前有效",
    "omitted": "未选入（清单未记录更细原因）",
}
BASIS_LABELS = {
    "boundary": "固定边界/语气或安静疲惫规则", "literal": "词项或连续片段匹配",
    "concept": "有限概念匹配", "drink_question": "明确饮品询问", "broad_query": "明确泛问",
    "state_or_insufficient": "状态过滤或依据不足（未细分）",
}


def evidence_summary(row):
    """Fixed Chinese projection of saved metadata, never another retrieval.

    Counts describe selections/dependencies only. No IDs, dynamic match terms,
    current record lookups or prompt payloads belong in this summary.
    """
    if row is None:
        return evidence_text(None)
    ev, trace = row.get("evidence"), row.get("trace")
    limits = ("这些清单不能证明助手复述了全部祖先，也不能证明模型注意到或理解资料。"
              "长期未选不等于近期/助手等其他通道没有提供信息。当前快照预览不是历史回放。")
    if not ev:
        reason = ("本地采用/候选没有本次直接取数清单。" if row["kind"] == "local_assistant" else
                  "本进程证据不可用；可能未捕获、窗口重启/关闭、资料维护/到期清理或属于旧记录，具体原因未记录。")
        lines = [reason, "不足以判断各通道当时直接提供了什么；不重新取数补造。"]
    else:
        c = ev["channels"]
        lines = ["本进程捕获的该轮实际取数（仅说明选择与通道，不保存当时正文）。",
                 "组装时刻：" + ev["at"],
                 "当时开关：长期记忆" + ("开启" if ev["memory_enabled"] else "关闭")
                 + "；话题延续" + ("开启" if ev["topics_enabled"] else "关闭") + "。",
                 f"直接长期资料：{len(c['long_memory'])}条；直接近期用户原话：{len(c['recent_users'])}条（不含本轮输入）。",
                 f"直接人工话题：{len(c['topics'])}个版本；纳入助手历史：{len(c['assistants'])}条。"]
        if 'participant_scope' in ev:
            scope = ev['participant_scope']
            actual = {r['person_id'] for r in ev['records'] if r.get('outcome') == 'long_injected' and 'person_id' in r}
            lines.append(f"本轮取数范围：{len(scope['participants'])}人；直接长期选入涉及{len(actual)}人。"
                         "范围清单不表示各人资料均被注入，也不是模型理解或全群取数证明。")
        inherited = c["assistant_only_dependencies"]
        lines.append("仅经助手历史继承的依赖："
                     f"删除祖先{len(inherited['source_ids'])}个、原话祖先{len(inherited['raw_source_ids'])}个、"
                     f"记忆依赖{len(inherited['record_ids'])}个、话题版本{len(inherited['topics'])}个。不是正文内容清单。")
        for label, records in (("长期资料选择状态", ev["records"]), ("人工话题选择状态", ev["topics"])):
            counts = Counter(OUTCOME_LABELS.get(r["outcome"], "旧选择码，摘要无法细分") for r in records)
            lines.append(label + "：" + ("；".join(f"{k}{v}条" for k, v in counts.items()) if counts else "本轮清单无条目") + "。")
        bases = Counter(BASIS_LABELS.get(r["basis"], "旧依据码，摘要无法细分") for r in ev["records"])
        if bases:
            lines.append("记录的判断依据：" + "；".join(f"{k}{v}条" for k, v in bases.items()) + "。不是模型思维。")
        lines.append("近期同源表示资料与已提供的近期或本轮输入同源，未重复占用长期名额；"
                     "本轮输入不计入上面的近期条数。有限规则依据不足不等于客观无关。")
    if trace:
        ancestry = (f"可能的原话祖先{len(trace.get('source_ids', []))}个（旧v1保守视为，未区分原话与维护资料）"
                    if trace.get("version") == 1 else f"原话祖先{len(trace.get('raw_source_ids', []))}个")
        lines.append(f"该轮保存的累计来源：记忆依赖{len(trace.get('record_ids', []))}个；{ancestry}。累计来源不等于本轮直接输入。")
    else:
        lines.append("该旧记录没有可用的累计来源；来源未知不等于确定没有提供资料。")
    lines.extend([limits, "可切到“技术详情”查看全部ID和状态码；记忆面板显示的是当前条目详情，不是当时正文。"])
    return "\n\n".join(lines)


def evidence_text(row):
    """No current lookup or recall; historical fallback stays explicitly limited."""
    if row is None:
        return "所选轮次已淘汰、来源失效或被删除。请选择仍存在的轮次；没有自动转绑。"
    ev = row.get("evidence")
    trace = row.get("trace") or {}
    ancestry = (f"可能的原话祖先 {len(trace.get('source_ids', []))} 个（旧v1保守视为，未区分原话与维护资料）"
                if trace.get("version") == 1 else f"原话祖先 {len(trace.get('raw_source_ids', []))} 个")
    common = (("\n该轮保存的累计依赖（含经助手历史传递的来源）：记忆依赖 " + ids(trace.get("record_ids", []))
               + "；" + ancestry + "。") if trace else "\n该旧记录没有可用的累计来源；来源未知不等于确定没有提供资料。")
    common += ("依赖ID不能证明本轮直接注入对应正文、助手复述全部祖先或模型注意到/理解资料。"
              + "长期未选不等于其他通道没有信息；当前快照预览不是历史回放。")
    if not ev:
        reason = ("本地采用/候选没有本次直接取数清单。" if row["kind"] == "local_assistant" else
                  "未捕获、窗口重启/关闭、资料维护/到期清理或旧记录：本进程证据不可用。")
        coverage = "仅有既有历史累计来源" if trace else "历史来源不足"
        return reason + coverage + "，不足以判断各通道当时直接提供了什么；不重新取数补造。" + common
    channel = ev["channels"]
    lines = [f"本进程捕获的该轮实际取数：{ev['scene_id']} / {ev['person_id']} / {ev['turn_id']}",
             f"Core {ev['core_id']}；组装时刻 {ev['at']}；{ev['version']} / {ev['recall_version']}",
             f"长期开关 {ev['memory_enabled']}；话题开关 {ev['topics_enabled']}；规则版本 {ev['versions']}",
             "本轮用户输入ID：" + ev["turn_id"],
             "直接长期资料ID：" + ids(channel["long_memory"]),
             "直接近期用户ID：" + ids(channel["recent_users"]),
             "直接人工话题版本：" + ids(channel["topics"]),
             "最终纳入助手ID：" + ids(channel["assistants"])]
    if 'participant_scope' in ev:
        scope = ev['participant_scope']
        lines.append('人物取数范围：' + ids(scope['participants']) + '；末位发言者：' + scope['last_speaker']
                     + '；回复受众：' + ids(scope['reply_audience'])
                     + '；模式：' + scope['mode'] + '。不代表全群取数或每人均有资料注入。')
    for key, value in channel["assistant_only_dependencies"].items():
        lines.append("仅经助手继承的依赖 " + key + "：" + ids(value))
    lines.append("资料选择状态（仅码，不含当时正文或匹配词）：")
    for r in ev["records"]:
        lines.append(f"{r['id']}：{r['status']} / {r['outcome']} / {r['basis']}" +
                     ('；所属人物：' + r['person_id'] if 'person_id' in r else ''))
    for r in ev["topics"]:
        lines.append(f"话题 {r['id']}：{r['status']} / {r['outcome']}" +
                     ('；适用目标：' + r['target'] if 'target' in r else ''))
    lines.append("literal=词项/片段；concept=有限概念；drink_question=饮品询问；broad_query=明确泛问；"
                 "boundary=固定边界；state_or_insufficient=状态过滤或依据不足。"
                 "本清单只证明选择与通道，不保存当时正文；详情只能在记忆面板查看当前条目。")
    return "\n".join(lines) + common
