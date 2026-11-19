"""Small deterministic relevance features, not general semantic understanding.

Only current maintained content is searched: old evidence/slot names cannot
resurrect a corrected fact. English words and Chinese adjacent fragments avoid
single-character/function-word matches. Domain groups are intentionally finite.
"""
import re

CONCEPTS = {
    "咖啡": ("咖啡", "拿铁", "美式", "卡布奇诺", "coffee", "latte", "espresso", "cappuccino"),
    "音乐类别": ("音乐", "配乐", "歌曲", "爵士", "music", "jazz"),
    "游戏类别": ("游戏", "电玩", "game", "games"),
}
EN_STOP = set(("i you he she it we they me us my your our their the a an is are was were be been "
               "do does did not no like likes love prefer please what how today remember "
               "and or but of to for with about on in at from as this that these those").split())
ZH_FUNCTION = set("我你他她它的了是在这那个吗呢啊请有要不没也很太想点些还就都把和与但先再会能只为到过说对")
ZH_PHRASES = re.compile("本地对话提到|不喜欢|喜欢|偏好|喜好|记得|记忆|什么|怎么|今天|如何|可以|推荐|怎么样")


def literal_terms(text):
    """Protect one lexical color compound and qualify the ambiguous 美式 alias.

    This is local token disambiguation, not general intent inference. Keep the
    color as a whole searchable term; never turn a color preference into coffee.
    Unknown qualified uses of 美式 are conservative unless drink cues occur.
    """
    compounds = {"咖啡色"} if "咖啡色" in text else set()
    text = text.replace("咖啡色", " ")
    def qualify(match):
        left = re.split(r"[，,。！？!?；;\n]", text[:match.start()])[-1]
        right = re.split(r"[，,。！？!?；;\n]", text[match.end():])[0]
        drink_cue = bool(re.search(r"喝|饮|杯|咖啡", left[-6:] + right[:6]))
        standalone = bool(re.fullmatch(r"(?:怎么样|如何|可以|好不好|行不行|也行)?[吗呢吧啊呀么]*", right.strip()))
        return match.group() if drink_cue or standalone else " "
    return re.sub("美式", qualify, text), compounds


def features(text):
    text, compounds = literal_terms(text.casefold())
    words = {w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 2} - EN_STOP
    chunks = re.findall(r"[\u4e00-\u9fff]+", ZH_PHRASES.sub(" ", text))
    fragments = compounds | {chunk[i:i+2] for chunk in chunks for i in range(len(chunk)-1)
                             if not set(chunk[i:i+2]) & ZH_FUNCTION}
    concepts = {name for name, aliases in CONCEPTS.items()
                if any((alias in text if not alias.isascii() else alias in words) for alias in aliases)}
    return words, fragments, concepts


def relevance(content, query):
    """Return score and a bounded explanation of exactly the ranking feature."""
    qw, qf, qc = features(query)
    rw, rf, rc = features(content)
    words, fragments, concepts = sorted(qw & rw), sorted(qf & rf), sorted(qc & rc)
    # Unqualified requests may inspect this person's known material. Merely
    # containing 'remember/preference' is insufficient (e.g. an unrelated event).
    plain = re.sub(r"[\s，。！？?！,.!：:]", "", query)
    broad = bool(re.fullmatch(
        r"(?:你)?(?:还)?(?:记得|了解)(?:我|我的)?(?:(?:什么|哪些)(?:偏好|喜好|资料|事情)?|偏好|喜好|资料)(?:吗)?"
        r"|(?:你)?(?:还)?记得我喜欢什么(?:吗)?", plain))
    drink_question = bool(re.search(r"饮料|饮品|喝点|喝的|喝什么|喝些什么|喝点什么", query))
    known_drink = "咖啡" in rc or bool(re.search(r"奶茶|果汁|可乐|红茶|绿茶", content))
    if words or fragments:
        terms = (words + fragments)[:4]
        # Display excerpts only; matching still used complete terms. Keep the
        # existing <=300-character persisted reason contract, even at 300-character
        # record limits, without truncating content or deletion ancestry.
        shown = [term if len(term) <= 24 else term[:24] + "…" for term in terms]
        note = "（长词仅展示前24字，匹配用完整词）" if any(len(t) > 24 for t in terms) else ""
        return 20 + min(8, len(words) + len(fragments)), "词项/连续片段匹配" + note + "：" + "、".join(shown)
    if concepts:
        return 12, "有限概念匹配：" + "、".join(concepts)
    if drink_question and known_drink:
        return 8, "明确饮品询问；当前内容含已支持饮品"
    if broad:
        return 1, "明确泛问本人物已知资料（仍受范围/状态/预算限制）"
    return 0, "缺少内容词项、连续片段或已支持概念的关联；证据不足"
