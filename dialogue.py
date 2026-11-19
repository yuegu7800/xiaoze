"""Bounded request provenance for generated local messages, never user facts.

Roots are flattened only within a fixed budget. Older assistant context is omitted
BEFORE requesting a model when its complete provenance cannot fit. No hidden log.
"""
from copy import deepcopy
import hashlib
import json
import re

MAX_ROOTS, MAX_RECORD_DEPS, MAX_TOPIC_DEPS = 128, 32, 16
TRACE_VERSION = 2


def empty_trace(scene_id):
    return {"version": TRACE_VERSION, "scene_id": scene_id,
            "source_ids": [], "raw_source_ids": [], "record_ids": [], "topics": {}}


def validate_trace(trace, scene_id):
    if trace["version"] not in (1, TRACE_VERSION) or trace["scene_id"] != scene_id:
        raise ValueError("reply trace version/scope")
    for key, limit in (("source_ids", MAX_ROOTS), ("record_ids", MAX_RECORD_DEPS)):
        ids = trace[key]
        if (not isinstance(ids, list) or len(ids) > limit or len(set(ids)) != len(ids)
                or any(not isinstance(i, str) or not 0 < len(i) <= 160 for i in ids)):
            raise ValueError("reply trace capacity")
    if trace["version"] == 2:
        raw = trace["raw_source_ids"]
        if (not isinstance(raw, list) or len(raw) > MAX_ROOTS or len(set(raw)) != len(raw)
                or any(not isinstance(i, str) for i in raw) or not set(raw).issubset(trace["source_ids"])):
            raise ValueError("raw reply provenance")
    if not isinstance(trace["topics"], dict) or len(trace["topics"]) > MAX_TOPIC_DEPS:
        raise ValueError("reply topic capacity")
    for key, value in trace["topics"].items():
        if not isinstance(key, str) or not 0 < len(key) <= 160 or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{24}", value):
            raise ValueError("reply topic dependency")


def topic_signature(topic):
    """Content digest, not timestamp alone: two edits can share one clock tick."""
    content = {k: topic[k] for k in ("title", "direction", "quote", "source_ids", "record_ids",
                                    "status", "updated_at", "expires_at", "not_before")}
    if "revision_id" in topic:
        content["revision_id"] = topic["revision_id"]
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def merge_trace(base, extra):
    """Return complete union or refuse; callers may omit context before sending."""
    if base["scene_id"] != extra["scene_id"]:
        raise ValueError("不能合并不同场景的对话来源。")
    result = deepcopy(base)
    result["version"] = TRACE_VERSION
    result["raw_source_ids"] = sorted(set(raw_sources(base)) | set(raw_sources(extra)))
    for key in ("source_ids", "record_ids"):
        result[key] = sorted(set(base[key]) | set(extra[key]))
    result["topics"].update(extra["topics"])
    try:
        validate_trace(result, base["scene_id"])
    except ValueError:
        raise ValueError("本次上下文来源超过有界预算，请减少资料后再试；没有截断来源或调用模型。") from None
    return result


def raw_sources(trace):
    # v1 never distinguished raw text from a corrected record. Keep its former
    # conservative meaning; never infer missing historical input provenance.
    return trace["source_ids"] if trace["version"] == 1 else trace["raw_source_ids"]


def topic_raw_sources(topic):
    # A topic explicitly selected from a record contains the maintained record's
    # current content, not the obsolete original utterance. All roots still stay
    # in source_ids for destructive deletion.
    return [] if topic["record_ids"] else topic["source_ids"]


def hidden_sources(data):
    hidden = set(data["blocked"])
    temporary = {(r["person_id"], r["slot"]) for r in data["memories"]
                 if r["category"] == "temporary" and r["status"] == "active"}
    for record in data["memories"]:
        if record["status"] in ("corrected", "superseded", "expired", "suppressed") or (
                record["category"] != "temporary" and (record["person_id"], record["slot"]) in temporary):
            hidden.update(record["source_ids"])
    return hidden


def trace_valid(trace, data):
    if not trace or set(raw_sources(trace)) & hidden_sources(data):
        return False
    records = {r["id"]: r for r in data["memories"]}
    if any(i not in records or records[i]["status"] != "active" for i in trace["record_ids"]):
        return False
    topics = {t["id"]: t for t in data.get("activity", {}).get("topics", [])}
    return all(i in topics and topics[i]["status"] == "active" and topic_signature(topics[i]) == stamp
               for i, stamp in trace["topics"].items())


def reply_available(turn, data):
    return bool(turn["reply"]) and trace_valid(turn.get("reply_trace"), data)


def affected_replies(data, ids=(), record_ids=(), topic_ids=()):
    """Unknown legacy bodies are conservatively included in destructive previews."""
    roots, records, topics = set(ids), set(record_ids), set(topic_ids)
    return {t["id"] for t in data["turns"] if t["reply"] and (
        not t.get("reply_trace") or roots.intersection(t["reply_trace"]["source_ids"])
        or records.intersection(t["reply_trace"]["record_ids"])
        or topics.intersection(t["reply_trace"]["topics"]))}


def erase_replies(data, ids=(), record_ids=(), topic_ids=()):
    affected = affected_replies(data, ids, record_ids, topic_ids)
    for turn in data["turns"]:
        if turn["id"] in affected:
            turn["reply"] = ""
            turn["recall"] = []
    data["feedback"] = [f for f in data["feedback"] if f["turn_id"] not in affected]
    return affected


def trim_dialogue(data, limit):
    data["turns"] = data["turns"][-limit:]
    kept = {t["id"] for t in data["turns"]}
    data["feedback"] = [f for f in data["feedback"] if f["turn_id"] in kept]
