"""Small local evidence store. Scope filtering precedes ranking; no network.

Rules create self-reports, not verified facts. No biography/trait inference,
background jobs, vectors, automatic summaries or public sharing are implemented.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

from core import atomic_json
from memory_rules import LOCAL_TZ, extract_statements, local_now
from activity import validate_activity, reconcile, prune_sources
from dialogue import validate_trace as validate_reply_trace, erase_replies
from recall_terms import relevance

RECENT_TURNS = 12
MAX_RECORDS = 256
MAX_PENDING = 32
MAX_SOURCE_IDS = 10000
STATUSES = {"pending", "active", "superseded", "corrected", "expired", "suppressed"}
CATEGORIES = {"preference", "boundary", "temporary", "event"}
SOURCES = {"user_explicit", "local_maintainer", "local_dialogue", "legacy_review"}
FEEDBACK_TAGS = ("主人被怼", "机械追问", "无依据回忆", "串人", "重复安慰", "生硬播报记忆", "自然合适", "接得自然", "重复追问", "时机不合适", "其他")
MEMORY_FEEDBACK_TAGS = ("记错了", "记着但本次未选中", "已提供但表达生硬", "没有对应资料")
FEEDBACK_TAGS += MEMORY_FEEDBACK_TAGS


def empty_scene():
    return {"turns": [], "memories": [], "processed": [], "blocked": [], "feedback": []}


def instant(text):
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError("time zone required")
    return value


def validate_trace(trace):
    if not isinstance(trace, list) or len(trace) > 6:
        raise ValueError("trace")
    for entry in trace:
        if (not isinstance(entry["id"], str) or len(entry["id"]) > 160
                or not isinstance(entry["reason"], str) or len(entry["reason"]) > 300):
            raise ValueError("trace fields")


def expire(data, now):
    for record in data["memories"]:
        if record["status"] in ("active", "pending") and record["expires_at"] and instant(record["expires_at"]) <= now:
            record["status"] = "expired"


def source_ids(record):
    return set(record["source_ids"])


def omitted_sources(data):
    """Do not feed stale/withdrawn source turns back as current instructions."""
    hidden = set(data["blocked"])
    for record in data["memories"]:
        if record["status"] in ("superseded", "corrected", "expired", "suppressed"):
            hidden.update(record["source_ids"])
    return hidden


class CapacityError(RuntimeError):
    pass


def compact(data):
    removable = sorted((r for r in data["memories"] if r["status"] in ("expired", "superseded", "corrected")),
                        key=lambda r: r["updated_at"])
    while len(data["memories"]) >= MAX_RECORDS and removable:
        old = removable.pop(0)
        blocked = set(data["blocked"]) | set(old["source_ids"])
        if len(blocked) > MAX_SOURCE_IDS:
            raise CapacityError("来源标记达到上限，请整理场景；本轮不新增长期记忆。")
        data["blocked"] = sorted(blocked)
        data["memories"].remove(old)
    if len(data["memories"]) >= MAX_RECORDS:
        raise CapacityError("本场景记忆容量已满，请先整理记忆；本轮不新增长期记忆。")


def install_record(data, record, *, maintenance=False):
    # Blocking an utterance prevents automatic replay, not explicit maintenance
    # of another extant record with the same ancestor. Only the service's
    # validated edit/accept paths use maintenance=True; keep all deletion roots.
    if record["id"] in {r["id"] for r in data["memories"]} or (not maintenance and source_ids(record).intersection(data["blocked"])):
        return
    # A temporary exception shadows, rather than destroys, the stable preference.
    if record["status"] == "active":
        for old in data["memories"]:
            if (old["person_id"] == record["person_id"] and old["slot"] == record["slot"]
                    and old["status"] == "active"
                    and (old["category"] == "temporary") == (record["category"] == "temporary")):
                if old["content"] == record["content"] and record["change"] != "correction":
                    if len(set(old["source_ids"] + record["source_ids"])) > 64:
                        if maintenance:
                            raise CapacityError("合并后来源超过64个上限；候选及原资料保持不变，请先核对关联范围。")
                        return  # Keep provenance complete instead of dropping old source IDs.
                    old["evidence"] = (old["evidence"] + record["evidence"])[-3:]
                    old["source_ids"] = list(dict.fromkeys(old["source_ids"] + record["source_ids"]))[-64:]
                    old["updated_at"] = record["updated_at"]
                    # New explicit temporal statements may set a new deadline;
                    # retrieval never extends it.
                    old["expires_at"] = record["expires_at"]
                    return
                old["status"] = "corrected" if record["change"] == "correction" else "superseded"
                record["supersedes"] = old["id"]
    compact(data)
    data["memories"].append(record)


def make_record(scene_id, person_id, source_id, category, slot, content, quote, start, now,
                *, source="user_explicit", status="active", expires_at=None, change="update"):
    digest = hashlib.sha256(f"{scene_id}|{person_id}|{source_id}|{slot}".encode()).hexdigest()[:28]
    return {"id": digest, "scene_id": scene_id, "person_id": person_id, "category": category,
            "slot": slot, "content": content, "source": source, "status": status, "change": change,
            "created_at": now.isoformat(), "updated_at": now.isoformat(), "expires_at": expires_at,
            "supersedes": None, "source_ids": [source_id],
            "evidence": [{"source_id": source_id, "quote": quote, "start": start,
                          "end": start + len(quote), "observed_at": now.isoformat()}]}


def ingest_turn(data, scene_id, turn, role, now):
    """Mutates only a transaction snapshot; the caller commits dialogue and facts together."""
    expire(data, now)
    if turn["id"] in data["processed"] or turn["id"] in data["blocked"]:
        return []
    if len(data["processed"]) >= MAX_SOURCE_IDS:
        return ["源消息去重记录达到上限，请整理场景；本轮不新增长期记忆。"]
    notices = []
    for item in extract_statements(turn["text"], now):
        if role == "owner" and item.slot == "teasing":
            continue  # This slot cannot lower the fixed warmth toward the owner.
        record = make_record(scene_id, turn["speaker_id"], turn["id"], item.category, item.slot,
                             item.value, item.quote, item.start, now, expires_at=item.expires_at, change=item.change)
        before = deepcopy(data)
        try:
            install_record(data, record)
        except CapacityError as error:
            data.clear()
            data.update(before)
            notices.append(str(error))
    data["processed"].append(turn["id"])
    return notices


def recall(data, person_id, query, now, budget=1800, *, diagnostics=None, recent_source_ids=(), _candidates=False):
    """Select direct long-memory inputs; dedup BEFORE the 6/1800 budget.

    Trace is a <=6 decision summary: direct selections first, then any remaining
    space for recent duplicates (not an exhaustive input/channel manifest).
    Default calls without recent IDs retain one trace entry per selected record.
    """
    data = deepcopy(data)
    expire(data, now)
    # This function receives one authorized scene, never the whole database.
    scoped = [r for r in data["memories"] if r["person_id"] == person_id]
    decisions = {r["id"]: {"id": r["id"], "content": r["content"], "status": r["status"],
                           "outcome": r["status"], "reason": {
                               "pending": "待确认，不作为长期资料注入（近期原话另行判断）", "expired": "已到期",
                               "suppressed": "已停止召回", "corrected": "已被更正", "superseded": "已被新值替代",
                               "active": "当前有效"}[r["status"]]} for r in scoped}
    eligible = [r for r in scoped if r["status"] == "active"]
    temporary_slots = {r["slot"] for r in eligible if r["category"] == "temporary"}
    for r in eligible:
        if r["category"] != "temporary" and r["slot"] in temporary_slots:
            decisions[r["id"]].update(outcome="temporary_override", reason="同人物、同槽位的临时例外优先")
    eligible = [r for r in eligible if r["category"] == "temporary" or r["slot"] not in temporary_slots]
    ranked, duplicates = [], []
    recent_source_ids = set(recent_source_ids)
    for record in eligible:
        stable_boundary = record["slot"] in ("teasing", "tone", "length", "quiet", "energy") or record["category"] == "boundary"
        score, reason = (100, "固定边界/语气或当前安静/疲惫规则") if stable_boundary else relevance(record["content"], query)
        if not score:
            decisions[record["id"]].update(outcome="irrelevant", reason=reason)
            continue
        reason = "同场景、同人物、当前有效；" + reason
        if (record["source"] == "user_explicit" and not stable_boundary and record["category"] != "temporary"
                and record["source_ids"][-1] in recent_source_ids):
            reason += "；同源原文已在近期上下文，不重复注入"
            decisions[record["id"]].update(outcome="recent_duplicate", reason=reason)
            duplicates.append({"id": record["id"], "reason": reason})
            continue
        ranked.append((score, record["updated_at"], record, reason))
    if _candidates:
        # Internal pool only: no per-person count/character truncation.
        return ranked, duplicates, decisions
    selected, trace, used = [], [], 0
    for _, _, record, reason in sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True):
        if len(selected) >= 6:
            decisions[record["id"]].update(outcome="count_budget", reason="本次最多选择6条长期资料")
            continue
        if used + len(record["content"]) > budget:
            decisions[record["id"]].update(outcome="char_budget", reason=f"长期正文预算{budget}字符不足")
            continue
        selected.append(record)
        trace.append({"id": record["id"], "reason": reason})
        used += len(record["content"])
        decisions[record["id"]].update(outcome="selected", reason=trace[-1]["reason"])
    if diagnostics is not None:
        diagnostics.extend(decisions.values())
    return selected, trace + duplicates[:6-len(trace)]


def recall_many(data, queries, now, *, diagnostics=None, recent_source_ids=()):
    """One authorized scene, <=8 speakers, ONE 6-record/1800-char budget.

    Fixed boundaries first, then relevance; ties spread slots among speakers,
    then newest record, participant input order and ID. No coverage guarantee.
    """
    if not isinstance(queries, dict) or not 1 <= len(queries) <= 8:
        raise ValueError('共同取数范围限1至8个人物。')
    pool, duplicates, decisions = [], [], {}
    order = {pid: i for i, pid in enumerate(queries)}
    for pid, query in queries.items():
        ranked, repeated, detail = recall(data, pid, query, now,
            recent_source_ids=recent_source_ids, _candidates=True)
        for row in detail.values():
            row['person_id'] = pid
        pool.extend(ranked)
        duplicates.extend(repeated)
        decisions.update(detail)
    selected, trace, used, counts = [], [], 0, dict.fromkeys(queries, 0)
    while pool:
        def priority(item):
            score, updated, record, _ = item
            boundary = record['category'] == 'boundary' or record['slot'] in ('teasing', 'tone', 'length', 'quiet', 'energy')
            return (boundary, score, -counts[record['person_id']], updated,
                    -order[record['person_id']], record['id'])
        chosen = max(pool, key=priority)
        pool.remove(chosen)
        _, _, record, reason = chosen
        detail = decisions[record['id']]
        if len(selected) >= 6:
            detail.update(outcome='count_budget', reason='全请求共享最多6条长期资料')
        elif used + len(record['content']) > 1800:
            detail.update(outcome='char_budget', reason='全请求共享长期正文1800字符预算不足')
        else:
            selected.append(record)
            trace.append({'id': record['id'], 'reason': reason})
            counts[record['person_id']] += 1
            used += len(record['content'])
            detail.update(outcome='selected', reason=reason)
    if diagnostics is not None:
        diagnostics.extend(decisions.values())
    return selected, trace + duplicates[:6-len(trace)]


class MemoryStore:
    def __init__(self, path, identities, clock=None):
        self.path = Path(path)
        self.identities = identities
        self.clock = clock or local_now
        self.state = {"version": 3, "revision": 0, "legacy_imported": False, "scenes": {}}
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
                self.validate(state)
                self.state = state
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                raise RuntimeError("本地数据版本或格式无效，原文件保留；请检查当前模式的 *-v2.json。旧版需通过窗口升级。") from None

    def validate(self, state):
        if (state["version"] not in (2, 3) or type(state["revision"]) is not int or state["revision"] < 0
                or type(state["legacy_imported"]) is not bool or not isinstance(state["scenes"], dict)):
            raise ValueError("schema")
        for scene_id, data in state["scenes"].items():
            people = self.identities[scene_id]
            if "activity" in data:
                validate_activity(data["activity"], scene_id, people)
            if not all(isinstance(data[k], list) for k in ("turns", "memories", "processed", "blocked", "feedback")):
                raise ValueError("lists")
            if (len(data["turns"]) > RECENT_TURNS or len(data["memories"]) > MAX_RECORDS
                    or len(data["processed"]) > MAX_SOURCE_IDS or len(data["blocked"]) > MAX_SOURCE_IDS
                    or len(data["feedback"]) > 30):
                raise ValueError("capacity")
            for group in (data["processed"], data["blocked"]):
                if any(not isinstance(s, str) or len(s) > 160 for s in group) or len(group) != len(set(group)):
                    raise ValueError("ids")
            turn_ids, record_ids, draft_event_ids = set(), set(), set()
            for turn in data["turns"]:
                if turn["speaker_id"] not in people or turn["kind"] not in ({"local_exchange", "local_assistant"} if state["version"] == 3 else {"local_exchange"}) or turn["id"] in turn_ids:
                    raise ValueError("turn")
                for key, limit in (("id", 160), ("text", 4000), ("reply", 1800), ("backend", 40)):
                    minimum = 0 if state["version"] == 3 and (key == "reply" or (key == "text" and turn["kind"] == "local_assistant")) else 1
                    if not isinstance(turn[key], str) or not minimum <= len(turn[key]) <= limit:
                        raise ValueError("turn field")
                if turn["kind"] == "local_assistant" and (turn["text"] or turn.get("target") not in {*people, "all"}
                        or (turn["target"] == "all" and len(people) == 1) or not turn.get("draft_id")):
                    raise ValueError("local assistant scope")
                if turn["kind"] == "local_assistant":
                    if (not isinstance(turn["draft_id"], str) or len(turn["draft_id"]) > 160
                            or turn["draft_id"] in draft_event_ids or "reply_trace" not in turn):
                        raise ValueError("duplicate/untraced local message")
                    draft_event_ids.add(turn["draft_id"])
                if "reply_trace" in turn:
                    if state["version"] != 3 or not turn["reply_trace"].get("source_ids"):
                        raise ValueError("reply trace needs v3")
                    validate_reply_trace(turn["reply_trace"], scene_id)
                instant(turn["created_at"])
                for key in ("persona_version", "model_id", "case_id", "relation_version", "policy_version", "topic_id"):
                    if key in turn and (not isinstance(turn[key], str) or len(turn[key]) > 120):
                        raise ValueError("turn metadata")
                if "memory_enabled" in turn and type(turn["memory_enabled"]) is not bool:
                    raise ValueError("turn mode")
                validate_trace(turn.get("recall", []))
                if turn.get("finish_reason") not in (None, "stop"):
                    raise ValueError("incomplete turn")
                turn_ids.add(turn["id"])
            for record in data["memories"]:
                if (record["scene_id"] != scene_id or record["person_id"] not in people
                        or record["category"] not in CATEGORIES or record["status"] not in STATUSES
                        or record["source"] not in SOURCES or record["id"] in record_ids):
                    raise ValueError("record scope")
                for key, limit in (("id", 160), ("content", 300), ("slot", 160)):
                    if not isinstance(record[key], str) or not 0 < len(record[key]) <= limit:
                        raise ValueError("record field")
                instant(record["created_at"])
                instant(record["updated_at"])
                if record["expires_at"]:
                    instant(record["expires_at"])
                if record["category"] == "temporary" and not record["expires_at"]:
                    raise ValueError("temporary needs expiry")
                if (record["change"] not in ("update", "correction", "temporary")
                        or (record["supersedes"] is not None and (not isinstance(record["supersedes"], str) or len(record["supersedes"]) > 160))):
                    raise ValueError("record metadata")
                if (not 1 <= len(record["source_ids"]) <= 64 or not all(isinstance(s, str) and 0 < len(s) <= 160 for s in record["source_ids"])
                        or not 1 <= len(record["evidence"]) <= 3):
                    raise ValueError("sources")
                for evidence in record["evidence"]:
                    if (evidence["source_id"] not in record["source_ids"] or not isinstance(evidence["quote"], str)
                            or len(evidence["quote"]) > 160 or type(evidence["start"]) is not int
                            or evidence["start"] < 0 or evidence["end"] != evidence["start"] + len(evidence["quote"])):
                        raise ValueError("evidence")
                    instant(evidence["observed_at"])
                record_ids.add(record["id"])
            if len([r for r in data["memories"] if r["status"] == "pending"]) > MAX_PENDING:
                raise ValueError("pending capacity")
            for feedback in data["feedback"]:
                if (not isinstance(feedback["tags"], list) or not set(feedback["tags"]).issubset(FEEDBACK_TAGS)
                        or not isinstance(feedback["note"], str) or len(feedback["note"]) > 300
                        or feedback["turn_id"] not in turn_ids or not isinstance(feedback["model"], str)
                        or len(feedback["model"]) > 120):
                    raise ValueError("feedback")
                instant(feedback["created_at"])
                validate_trace(feedback.get("recall", []))

    def snapshot(self, scene_id, *, compatibility=True):
        if scene_id not in self.identities:
            raise ValueError("未知测试场景。")
        result = deepcopy(self.scene_data(scene_id))
        expire(result, self.clock())
        reconcile(result, self.clock())
        # A compatibility view only; primary records and all evidence are above.
        result["preferences"] = {}
        for person_id in self.identities[scene_id] if compatibility else ():
            records, _ = recall(result, person_id, "记得偏好", self.clock())
            for r in records:
                if r["slot"] in ("teasing", "tone", "length"):
                    e = r["evidence"][-1]
                    result["preferences"].setdefault(person_id, {})[r["slot"]] = {
                        "value": r["content"], "evidence": e["quote"], "recorded_at": e["observed_at"],
                        "turn_id": e["source_id"], "source": r["source"],
                        "uncertainty": "仅本人明确自述，未推断永久性格" if r["source"] == "user_explicit" else "本地维护资料，仍可修正"}
        return result

    def scene_data(self, scene_id):
        return self.state['scenes'].get(scene_id, empty_scene())

    def commit(self, next_state):
        self.require_writable()
        next_state = deepcopy(next_state)
        next_state["revision"] = self.state["revision"] + 1
        self.validate(next_state)
        atomic_json(self.path, next_state)
        self.state = next_state

    def require_writable(self):
        if self.state["version"] != 3:
            raise RuntimeError("当前为旧v2只读记录，请先点击“升级v2来源记录”；未改动原文件或请求模型。")

    def upgrade_v2(self):
        """Explicit atomic in-place schema upgrade; unknown replies stay isolated."""
        if self.state["version"] == 3:
            return
        upgraded = deepcopy(self.state)
        upgraded["version"] = 3
        upgraded["revision"] += 1
        self.validate(upgraded)
        atomic_json(self.path, upgraded)
        self.state = upgraded

    def save_scene(self, scene_id, data):
        if scene_id not in self.identities:
            raise ValueError("未知测试场景。")
        data = deepcopy(data)
        data.pop("preferences", None)
        expire(data, self.clock())
        reconcile(data, self.clock())
        next_state = deepcopy(self.state)
        next_state["scenes"][scene_id] = data
        self.commit(next_state)

    def legacy_paths(self):
        if not self.path.stem.endswith("-v2"):
            raise ValueError("升级仅用于正式的v2模式文件。")
        base = self.path.stem[:-3]
        return self.path.with_name(base + ".json"), self.path.with_name(base + ".v1-backup.json")

    def import_legacy(self):
        """Explicit UI operation only. Original + known backup remain managed copies."""
        self.require_writable()
        original, backup = self.legacy_paths()
        if self.state["legacy_imported"] or any(d["turns"] or d["memories"] or "activity" in d for d in self.state["scenes"].values()):
            raise ValueError("升级只能在本模式v2数据为空且未升级过时执行。")
        if backup.exists():
            raise ValueError("已有v1备份，请先核对，避免覆盖。")
        try:
            old = json.loads(original.read_text(encoding="utf-8"))
            if old["version"] != 1 or not isinstance(old["scenes"], dict):
                raise ValueError("version")
            next_state = deepcopy(self.state)
            for scene_id, old_data in old["scenes"].items():
                people = self.identities[scene_id]
                data = empty_scene()
                data["turns"] = deepcopy(old_data["turns"])
                data["processed"] = [t["id"] for t in data["turns"]]
                for person_id, prefs in old_data["preferences"].items():
                    if person_id not in people:
                        raise ValueError("person")
                    for slot, pref in prefs.items():
                        if slot not in ("teasing", "tone", "length") or not isinstance(pref["value"], str):
                            raise ValueError("preference")
                        source_id = pref.get("turn_id") or "legacy:" + person_id + ":" + slot
                        record = make_record(scene_id, person_id, source_id, "preference", slot,
                                             pref["value"], "", 0, self.clock(), source="legacy_review", status="pending")
                        install_record(data, record)
                next_state["scenes"][scene_id] = data
            next_state["legacy_imported"] = True
            self.validate(next_state)
        except (OSError, KeyError, TypeError, ValueError, AttributeError):
            raise RuntimeError("旧版文件不存在、格式无效或超出容量；未改动原文件。") from None
        atomic_json(backup, old)
        try:
            self.commit(next_state)
        except OSError:
            backup.unlink(missing_ok=True)
            raise

    def scrub_legacy_scene(self, scene_id):
        """Clear this scope in both known v1 copies before a destructive v2 commit.

        No arbitrary paths or glob deletion. Partial IO failure is reported; no
        success is claimed, and a retry is safe. Restore/import uses scrubbed copies.
        """
        self.require_writable()
        if not self.state["legacy_imported"]:
            return
        for path in self.legacy_paths():
            if not path.exists():
                continue
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                if old["version"] != 1 or not isinstance(old["scenes"], dict):
                    raise ValueError("legacy schema")
            except (OSError, KeyError, TypeError, ValueError):
                raise RuntimeError("管理中的v1副本无法清理，删除未完成；请检查原文件和备份。") from None
            old["scenes"].pop(scene_id, None)
            atomic_json(path, old)


def preview_forget(data, record_id):
    target = next(r for r in data["memories"] if r["id"] == record_id)
    ids = source_ids(target)
    while True:
        related = [r for r in data["memories"] if source_ids(r).intersection(ids)]
        expanded = ids.union(*(source_ids(r) for r in related))
        if expanded == ids:
            break
        ids = expanded
    return ids, {r["id"] for r in related}


def forget_sources(data, ids, record_ids):
    blocked = set(data["blocked"]) | set(ids)
    if len(blocked) > MAX_SOURCE_IDS:
        raise CapacityError("删除标记达到容量上限，请先导出必要资料并创建新的本地场景存储。")
    data["blocked"] = sorted(blocked)
    erase_replies(data, ids, record_ids)
    data["memories"] = [r for r in data["memories"] if r["id"] not in record_ids]
    data["turns"] = [t for t in data["turns"] if t["id"] not in ids]
    data["feedback"] = [f for f in data["feedback"] if f["turn_id"] not in ids]
    prune_sources(data, ids)
