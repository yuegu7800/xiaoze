"""Conservative, replaceable Chinese self-statement extraction, no model calls.

Only complete short clauses are matched. Ambiguous messages are left for manual
marking; this is deliberately not a general natural-language fact extractor.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def local_now():
    return datetime.now(LOCAL_TZ)


@dataclass(frozen=True)
class Statement:
    category: str
    slot: str
    value: str
    quote: str
    start: int
    end: int
    expires_at: str | None = None
    change: str = "update"


def extract_statements(text, now=None):
    now = (now or local_now()).astimezone(LOCAL_TZ)
    if (re.search(r'不(?:要|用|必)?.{0,5}(?:长期记|记住|记录)|别.{0,4}(?:记住|记录|长期记)', text)
            or re.search(r'[“”‘’"\']|如果|假如|假设|要是|是假的|不是真的|不是我的|并不代表|并非|举个例子|据说|转述|引用', text)
            or re.search(r'(?:^|[，,。；;])(?:只是|我)?(?:在)?开玩笑', text)
            or re.search(r'(?:^|[，,。；;])[^，,。；;]{0,16}(?:说|表示)[：:，,]', text)):
        return []
    change = "correction" if re.search(r"更正|纠正|之前说错|以前说错", text) else "update"
    expires = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), LOCAL_TZ).isoformat()
    result = []
    for match in re.finditer(r"[^，,。！？!?；;\n]+", text):
        raw = match.group()
        quote = raw.strip()
        if not quote or len(quote) > 80:
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        clause = re.sub(r"^(?:小泽|阿澄)[：: ]*", "", quote)
        clause = re.sub(r"^(?:更正[：: ]*|纠正[：: ]*|现在开始|从现在起|现在)", "", clause)
        clause = re.sub(r"^我(?:其实|现在)", "我", clause)
        temporary = clause.startswith(("今天", "我今天", "仅今天", "只是今天"))
        if not temporary and "今天" in text[:start] and re.fullmatch(r"(?:只)?想安静(?:一点|点)?", clause):
            temporary = True
        if temporary:
            clause = re.sub(r"^(?:我今天|仅今天|只是今天|今天)", "", clause)
            clause = clause.removeprefix("我")
        category, slot, value = "preference", "", ""
        if re.fullmatch(r"(?:请)?(?:别(?:再)?|不要(?:再)?)(?:怼|损|骂|嘲笑)我(?:了)?|我不喜欢(?:被|你)?(?:怼|挖苦|嘲笑|开玩笑)", clause):
            category, slot, value = "boundary", "teasing", "不要对我挖苦、怼人或开冒犯玩笑"
        elif re.fullmatch(r"我(?:喜欢和你互怼|可以接受(?:友善的)?玩笑)", clause):
            slot, value = "teasing", "可接受友善玩笑，不能升级成攻击"
        elif re.fullmatch(r"(?:请)?(?:跟我|对我)(?:说话)?温柔(?:一点|点)|我喜欢(?:你)?温柔(?:一点|点)?", clause):
            slot, value = "tone", "说话温柔一点"
        elif re.fullmatch(r"(?:请)?(?:跟我|对我)(?:说话)?(?:简短|短)(?:一点|点)|我喜欢简短(?:的)?回复", clause):
            slot, value = "length", "回复简短，不要长篇说教"
        elif re.fullmatch(r"我(?:喜欢详细(?:的)?回复|希望(?:你)?回答详细一点)", clause):
            slot, value = "length", "回复可以详细展开"
        elif re.fullmatch(r"我(?:不喝|喝|喜欢喝|不喜欢喝)咖啡", clause) or (temporary and re.fullmatch(r"(?:不喝|喝|喜欢喝|不喜欢喝)咖啡", clause)):
            slot = "drink:coffee"
            value = "不喝咖啡" if "不" in clause else "喝咖啡"
        elif temporary and re.fullmatch(r"(?:很|有点)?累(?:了)?|(?:很|有点)?疲惫", clause):
            slot, value = "energy", "今天感到疲惫"
        elif temporary and re.fullmatch(r"(?:只)?想安静(?:一点|点)?|不想(?:聊天|互怼)", clause):
            slot, value = "quiet", "今天想安静一点，减少打扰和玩笑"
        else:
            liking = re.fullmatch(r"我(不喜欢|喜欢)([\u4e00-\u9fffA-Za-z0-9 ]{1,20})", clause)
            if liking and not re.search(r"(?:因为|但是|的话|可能|也许|是|不是|主人|身份|记住|转让|互怼)", liking[2]):
                slot = "like:" + liking[2].strip().casefold()
                value = liking[1] + liking[2].strip()
        if not slot:
            continue
        if temporary:
            category = "temporary"
        result.append(Statement(category, slot, value, quote, start, start + len(quote),
                                expires if temporary else None, "temporary" if temporary else change))
    # Conflicting clauses in one utterance are not independent reliable facts.
    values = {}
    for item in result:
        values.setdefault((item.category == "temporary", item.slot), set()).add(item.value)
    return [item for item in result if len(values[(item.category == "temporary", item.slot)]) == 1]
