"""Give our persistent cursor a full visible incoming-message window.

Xiaoli's event reader intentionally cuts messages at the last outgoing bubble.
That contract is incompatible with a cursor polling across multiple replies.
Bind an isolated copy of get_messages to our block adapter, without changing
the installed module or other Xiaoli instances. All capture/OCR code is reused.
"""
from types import FunctionType
import re

from core import ReadUnavailableError, ScopeError, TitleScopeError, check_title_scope


def compatible_title_parser(original):
    """Normalize only a trailing, paired member-count marker, never the name.

    Missing/ambiguous markers remain unverified. No fuzzy name matching or
    group-name inference is used, and upstream module globals stay untouched.
    """
    marker = re.compile(r"^(.+?)\s*(?:\(\s*([0-9]{1,6})\s*\)|（\s*([0-9]{1,6})\s*）)\s*$")
    def parse(raw):
        match = marker.fullmatch(raw.strip()) if raw else None
        if match:
            raw = match[1].rstrip() + "(" + (match[2] or match[3]) + ")"
        return original(raw)
    return parse


def visible_blocks(analyze, img, colors, bot_tops, other_tops, skip_bot=0):
    # Include both sides as geometry anchors so right-hand bubbles still cap
    # incoming blocks. Analyze all candidates without an outgoing cutoff, then
    # discard right-hand candidates BEFORE OCR is assigned to message blocks.
    incoming = set(other_tops) - set(bot_tops)
    info = analyze(img, colors, [], sorted(set(bot_tops) | set(other_tops)))
    blocks = [block for block in info["blocks"] if block["avatar_top"] in incoming]
    return dict(info, blocks=blocks, bot_last_top=max(bot_tops, default=None),
                other_new_tops=sorted(incoming), region_top=min(incoming, default=None),
                has_text=any(b["kind"] in ("text", "quote") for b in blocks),
                has_media=any(b["kind"] in ("image", "file") for b in blocks))


def title_ocr_items(items):
    """Join a title line before the old reader's len>=2 item filter.

    OCR can split Chinese names and '(3)' into single-character boxes. Keep
    those literal characters; still exclude solitary Latin toolbar buttons
    and unrelated symbol-only boxes. This never guesses or repairs a name.
    """
    selected = []
    for item in sorted(items, key=lambda it: it["x"]):
        text = (item.get("text") or "").strip()
        if (len(text) >= 2 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text)
                or re.fullmatch(r"[\u4e00-\u9fff0-9]", text)
                or text in ("(", ")", "（", "）")):
            selected.append(dict(item, text=text))
    if not selected:
        return []
    left, top = min(it["x"] for it in selected), min(it["y"] for it in selected)
    return [dict(text="".join(it["text"] for it in selected), x=left, y=top,
                 w=max(it["x"] + it["w"] for it in selected) - left,
                 h=max(it["y"] + it["h"] for it in selected) - top)]


def read_title_items(ocr, image):
    """Retry a missed shallow title on a padded canvas, never a larger crop.

    RapidOCR's min-side detector resize can miss a real 678x40 title even
    though its pixels are intact. Padding limits that upscale and leaves the
    font untouched. Only retry an empty short title; message OCR is unchanged.
    Translate boxes back to the original crop for same-frame scope checks.
    """
    items = title_ocr_items(ocr(image))
    if items or image.height >= 112 or not image.width or not image.height:
        return items
    from PIL import ImageOps
    dx, dy = 16, max(16, (112 - image.height + 1) // 2)
    padded = ImageOps.expand(image, border=(dx, dy, dx, dy),
                             fill=image.getpixel((image.width - 1, image.height - 1)))
    restored = []
    for item in ocr(padded):
        x, y = item["x"] - dx, item["y"] - dy
        right, bottom = min(image.width, x + item["w"]), min(image.height, y + item["h"])
        x, y = max(0, x), max(0, y)
        if right > x and bottom > y:
            restored.append(dict(item, x=x, y=y, w=right - x, h=bottom - y))
    return title_ocr_items(restored)


def private_title_reader(reader):
    """Keep the native title capture, with the same cleanup as message frames."""
    namespace = getattr(reader, "__globals__", {})
    if "ocr_image" not in namespace:
        return reader
    original_ocr = namespace["ocr_image"]
    def ocr(image):
        return read_title_items(original_ocr, image)
    copied = FunctionType(reader.__code__, dict(namespace, ocr_image=ocr),
                          reader.__name__, reader.__defaults__, reader.__closure__)
    copied.__kwdefaults__ = reader.__kwdefaults__
    return copied


def message_frame_ocr(ocr, image, shot, title_region, message_region):
    """Read the title from its own crop of the SAME message frame.

    A large joint OCR crop recognizes short group titles differently, and its
    old y-only header filter also admits toolbar text outside the title box.
    Keep the original message coordinates and gap, but separate OCR contexts.
    Non-joint calls retain their original behavior. No title is cached/reused.
    """
    w, h = shot.size
    title = tuple(int(size * ratio) for size, ratio in zip((w, h, w, h), title_region))
    message = tuple(int(size * ratio) for size, ratio in zip((w, h, w, h), message_region))
    left, top = min(title[0], message[0]), min(title[1], message[1])
    right, bottom = max(title[2], message[2]), max(title[3], message[3])
    if image.size != (right - left, bottom - top):
        return ocr(image)
    if not (0 <= title[0] < title[2] <= w and 0 <= top < title[3] <= message[1] < bottom <= h):
        raise ReadUnavailableError("标题和消息区域无法分离。", reason="read_unavailable")
    header = []
    for item in read_title_items(ocr, shot.crop(title)):
        header.append(dict(item, x=item["x"] + title[0] - left,
                           y=item["y"] + title[1] - top))
    if not header:
        raise ReadUnavailableError("同帧标题为空，丢弃本轮。", reason="title_blank")
    offset = title[3] - top
    body = [dict(item, y=item["y"] + offset)
            for item in ocr(image.crop((0, offset, image.width, image.height)))]
    return header + body


def visible_message_reader(reader, parse_title=None, *, split_frame=False):
    """Return a class method with private globals; never monkeypatch upstream."""
    analyze = reader.__globals__["analyze_blocks"]
    def analyze_visible(img, colors, bot_tops, other_tops, skip_bot=0):
        return visible_blocks(analyze, img, colors, bot_tops, other_tops, skip_bot)
    namespace = dict(reader.__globals__, analyze_blocks=analyze_visible)
    if parse_title is not None:
        # The joint title/message OCR must use the very same parser as the
        # transport checks. Otherwise its cached group flag contradicts them.
        namespace["parse_title"] = parse_title
    copied = FunctionType(reader.__code__, namespace, reader.__name__,
                          reader.__defaults__, reader.__closure__)
    copied.__kwdefaults__ = reader.__kwdefaults__
    if split_frame and "ocr_image" in namespace:
        original_ocr = namespace["ocr_image"]
        def read_frame(self, *args, **kwargs):
            # A failed capture must not make the previous session's cache look
            # like verified identity for a new message read.
            self._current_title, self._current_is_group = None, False
            # Per-call globals keep this frame's OCR binding local; never patch
            # the upstream module or share an instance/shot across sessions.
            def ocr(image):
                shot = getattr(self, "_last_shot", None)
                if shot is None:
                    raise ReadUnavailableError("消息截图不可用。", reason="read_unavailable")
                return message_frame_ocr(original_ocr, image, shot,
                                         self._title_region, self._message_region)
            local = FunctionType(copied.__code__, dict(namespace, ocr_image=ocr),
                                 copied.__name__, copied.__defaults__, copied.__closure__)
            local.__kwdefaults__ = copied.__kwdefaults__
            result = local(self, *args, **kwargs)
            if self._current_title is None:
                raise ReadUnavailableError("本次消息帧未确认标题。", reason="read_unavailable")
            return result
        return read_frame
    return copied


def exact_session_hit(items, target, targets=()):
    """Accept a unique full label or an explicit, unambiguous ellipsis label.

    This only locates a row. Full title and conversation type are checked after
    entering it, before any message body is read or any reply is sent.
    """
    normalized = lambda text: re.sub(r"\s+", "", str(text))
    expected = normalized(target)
    configured = {normalized(name) for name in (*targets, target)}
    hits = []
    for item in items:
        label = normalized(item.get("text", ""))
        if label == expected:
            hits.append(item)
            continue
        truncated = re.fullmatch(r"(.{6,}?)(?:\.{2,}|…+|⋯+)", label)
        if truncated:
            prefix = truncated[1]
            candidates = {name for name in configured if name.startswith(prefix)}
            if candidates == {expected}:
                hits.append(item)
    return hits[0] if len(hits) == 1 else None


def current_chat_backend(base, parse_title, scopes):
    """Restrict automatic navigation to explicitly configured conversations."""
    read_native_title = private_title_reader(base.read_title)
    class CurrentChatBackend(base):
        get_messages = visible_message_reader(base.get_messages, parse_title, split_frame=True)

        # Native send_text ignores its chat argument and sends global keys.
        # Only desktop backends have this input geometry; test/event backends
        # retain their own destination-aware sender contract.
        if callable(getattr(base, '_input_box_rect', None)):
            def send_text_checked(self, chat, text, can_send, hold_after_paste=0.0):
                import pyautogui
                import pyperclip
                import time
                from desktop_sender import checked_desktop_send
                namespace = base.get_messages.__globals__
                return checked_desktop_send(self, chat, text, can_send,
                    pyautogui, pyperclip, namespace['u32'].GetForegroundWindow,
                    time.sleep, hold_after_paste)

            def send_text(self, chat, text, hold_after_paste=0.0):
                return self.send_text_checked(chat, text, lambda: True, hold_after_paste)

        def read_title(self, foreground=False):
            # Upstream caches the divider by window size. Dragging the chat-list
            # divider changes the layout without changing that size; refresh it
            # before title/message crops so a wider list cannot hide avatars.
            self._boundary_key = None
            return read_native_title(self, foreground=foreground)

        def check_title(self, requested=None):
            raw = self.read_title()
            if not raw:
                raise ReadUnavailableError("微信标题暂时无法识别。", reason="title_blank")
            name, is_group, _ = parse_title(raw)
            if name not in scopes or (requested is not None and requested != name):
                raise TitleScopeError(reason="route_title_mismatch", phase="route_after")
            check_title_scope(scopes[name], name, is_group, phase="route_after")
            # Empty-message retries use _current_chat to choose joint vs body-
            # only OCR. Update it only after this fresh exact scope validation.
            self._current_chat = name
            return name

        def _switch_chat(self, chat, force=False):
            # The reader's blank-frame fallback must never navigate somewhere
            # else. Only select_target may explicitly switch between targets.
            self.check_title(chat)
            # The original reader calls this between empty-frame attempts.
            # check_title proves the destination now, not the next screenshot.
            # Invalidate the preceding message frame so a failed second capture
            # cannot return [] with the first frame's identity still attached.
            self._current_title, self._current_is_group = None, False
            return True

        def resolve_chat_coord(self, chat):
            if chat not in scopes:
                raise ScopeError("目标不在本机监听名单。")
            if self._hwnd is None:
                return None
            shot = self._refresh(force=True, foreground=False)
            if shot is None:
                return None
            w, h = shot.size
            left, top, right, bottom = (int(size * ratio) for size, ratio in
                zip((w, h, w, h), self._session_region))
            namespace = base.get_messages.__globals__
            hit = exact_session_hit(namespace["ocr_image"](shot.crop((left, top, right, bottom))), chat, scopes)
            if hit is None:
                return None
            rect = namespace["wt"].RECT()
            namespace["u32"].GetWindowRect(self._hwnd, namespace["ctypes"].byref(rect))
            return (rect.left + left + hit["x"] + hit["w"] // 2,
                    rect.top + top + hit["y"] + hit["h"] // 2)

        def select_target(self, chat):
            if chat not in scopes:
                raise ScopeError("目标不在本机监听名单。")
            try:
                if self.check_title(chat) == chat:
                    return
            except ScopeError:
                pass
            # Rows move as messages arrive. Resolve the name on every switch;
            # never click a previously cached y coordinate.
            self._session_coords.pop(chat, None)
            if not base._switch_chat(self, chat):
                raise ReadUnavailableError("会话列表中未找到唯一目标；请将两个会话置顶并保持列表可见。", reason="session_row_missing")
            self.check_title(chat)

    return CurrentChatBackend
