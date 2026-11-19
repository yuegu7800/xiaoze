"""Transient, scene-scoped memory views. No model, files or persisted profile."""
from memory_store import MAX_RECORDS, MAX_PENDING, MAX_SOURCE_IDS
from relationships import OWNER_STYLE, FRIEND_STYLE

CATEGORY_NAMES = {"preference": "明确偏好", "boundary": "边界", "temporary": "临时状态", "event": "保留经历"}
STATUS_NAMES = {"active": "当前有效", "pending": "待确认", "expired": "已到期", "superseded": "已改变", "corrected": "已更正", "suppressed": "停止召回"}
SOURCE_NAMES = {"user_explicit": "本人明确自述（有限规则）", "local_maintainer": "本地维护者提供", "local_dialogue": "本地对话中提到", "legacy_review": "旧版待审核资料"}


def library_view(data, *, person_id=None, category=None, status="active", keyword=""):
    """Input must already be one authorized scene, never a whole database."""
    records = data["memories"]
    matches = []
    needle = keyword.strip().casefold()
    for record in reversed(records):
        if person_id and record["person_id"] != person_id:
            continue
        if category and record["category"] != category:
            continue
        if status == "history":
            if record["status"] in ("active", "pending"):
                continue
        elif status and record["status"] != status:
            continue
        searchable = record["content"] + "\n" + "\n".join(e["quote"] for e in record["evidence"])
        if needle and needle not in searchable.casefold():
            continue
        matches.append(record)
    return {"records": matches, "matched": len(matches), "total": len(records),
            "pending": sum(r["status"] == "pending" for r in records),
            "capacity": MAX_RECORDS, "pending_capacity": MAX_PENDING,
            "processed": len(data["processed"]), "blocked": len(data["blocked"]), "source_capacity": MAX_SOURCE_IDS,
            "empty": "本场景尚无记忆" if not records else "当前筛选无匹配；可切换人物、待确认或历史状态" if not matches else ""}


def overview(scene, data, person_id):
    person = scene.person(person_id)
    records = [r for r in data["memories"] if r["person_id"] == person_id and r["status"] == "active"]
    temporary = {r["slot"] for r in records if r["category"] == "temporary"}
    lines = [person.display + " · 当前场景人物概览（临时视图，不是性格推断）",
             "固定关系（不由记忆编辑）：" + ("唯一主人；" + OWNER_STYLE if person.role == "owner" else "朋友；" + FRIEND_STYLE)]
    if not records:
        lines.append("暂无已确认资料。不会据此补全性格或共同经历。")
    for record in records:
        shadow = "〔当前被同槽位临时状态覆盖〕" if record["category"] != "temporary" and record["slot"] in temporary else ""
        lines.append(f"{CATEGORY_NAMES[record['category']]}：{record['content']}{shadow}（{SOURCE_NAMES[record['source']]}）")
    lines.append("自述/本地确认不等于外部核实；同名人物在其他场景的资料不自动共享。")
    return "\n\n".join(lines)


def record_detail(scene, records, record):
    by_id = {r["id"]: r for r in records}
    lines = [scene.person(record["person_id"]).display + " · " + CATEGORY_NAMES[record["category"]],
             record["content"], "状态：" + STATUS_NAMES[record["status"]], "来源：" + SOURCE_NAMES[record["source"]],
             "创建：" + record["created_at"], "更新：" + record["updated_at"], "期限：" + (record["expires_at"] or "无固定到期时间"),
             "ID：" + record["id"], "槽位：" + record["slot"], "证据（最小片段；不是客观核实）："]
    for evidence in record["evidence"]:
        lines.append(f"“{evidence['quote']}” · {evidence['observed_at']}\n来源 {evidence['source_id']}，位置 {evidence['start']}—{evidence['end']}")
    lines.append("全部删除祖先ID：" + "、".join(record["source_ids"]))
    previous, seen = record.get("supersedes"), set()
    while previous and previous not in seen:
        seen.add(previous)
        old = by_id.get(previous)
        lines.append("更正/改变前：" + previous + (" · " + old["content"] if old else "（历史条目已不在库中，不补链）"))
        previous = old.get("supersedes") if old else None
    following = [r for r in records if r.get("supersedes") == record["id"]]
    for child in following:
        lines.append("后续版本：" + child["id"] + " · " + child["content"])
    return "\n".join(lines)


def preview_text(report):
    lines = [report["note"], "范围：" + report["scene_id"] + " / " + report["person_id"] + "；快照时间：" + report["at"],
             "请求取数：" + ("可组装（此处没有调用模型）" if report["request_ready"] else report["error"]),
             "长期资料（仅当前人物；“未选”仅指未从此通道直接注入，不代表其他通道没有提供）："]
    labels = {"long_injected": "长期注入", "recent_duplicate": "近期同源，不重复注入"}
    for entry in report["records"]:
        lines.append(f"• {labels.get(entry['outcome'], '未选')}：{entry['content']}\n  状态：{STATUS_NAMES[entry['status']]}；{entry['reason']} · ID {entry['id']}")
    if not report["records"]:
        lines.append("当前人物暂无任何长期条目。未检查其他场景。")
    lines.append("人工待续话题（不同于人物事实）：")
    for entry in report["topics"]:
        lines.append(f"• {entry['title']}：{entry['reason']} · ID {entry['id']}")
    if not report["topics"]:
        lines.append("当前人物/全群暂无话题。")
    lines.append("近期上下文（同场景；群内原话可包含其他成员，不是跨私聊召回）：")
    decisions = {"selected": "纳入", "omitted": "省略"}
    for entry in reversed(report["history"]):
        lines.append(f"• {entry['id']} / {entry['speaker_id']}：原话 {decisions[entry['user']]}（{entry['user_reason']}）；助手 {decisions[entry['assistant']]}（{entry['assistant_reason']}）")
    channels = report.get("channels")
    if channels is not None:
        lines.append("本次组装的通道清单（只列ID，不保存请求副本）：")
        for label, key in (("直接长期资料", "long_memory"), ("直接近期用户原话", "recent_users"),
                           ("直接人工话题版本", "topics"), ("可用助手历史", "assistants")):
            entries = channels[key]
            value = "、".join(f"{k}@{v}" for k, v in entries.items()) if isinstance(entries, dict) else "、".join(entries)
            lines.append(label + "：" + (value or "无"))
        inherited = channels["assistant_only_dependencies"]
        lines.append("仅经助手历史继承的依赖（分别扣除本轮直接通道的同类依赖；不代表正文复述了这些资料）：")
        for label, key in (("删除祖先", "source_ids"), ("原话祖先", "raw_source_ids"), ("记忆依赖", "record_ids"), ("话题版本", "topics")):
            entries = inherited[key]
            value = "、".join(f"{k}@{v}" for k, v in entries.items()) if isinstance(entries, dict) else "、".join(entries)
            lines.append(label + "：" + (value or "无"))
    else:
        lines.append("请求未能组装，以上是候选/拒绝原因，没有已提供通道。")
    lines.append("这些是本地取数依据，不表示模型实际注意到或正确表达了每条资料。实际发送还会处理新输入中的明确自述/停止表达。")
    return "\n\n".join(lines)
