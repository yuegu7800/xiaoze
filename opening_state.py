"""Immutable current-condition metadata; no dialogue, configuration or I/O."""
from dataclasses import dataclass
from datetime import datetime
import math


def duration(seconds):
    value = max(0, math.ceil(seconds))
    hours, rest = divmod(value, 3600)
    minutes, seconds = divmod(rest, 60)
    return ((f"{hours}小时" if hours else "") + (f"{minutes}分" if minutes else "")
            + (f"{seconds}秒" if seconds or not (hours or minutes) else ""))


@dataclass(frozen=True)
class WaitState:
    code: str
    message: str
    action: str
    remaining_seconds: float | None = None

    @property
    def ready(self):
        return self.code == "ready"

    def text(self):
        wait = f"还约{duration(self.remaining_seconds)}；" if self.remaining_seconds is not None else ""
        return f"{self.message} {wait}{self.action}"


@dataclass(frozen=True)
class CandidateState:
    state: WaitState
    epoch: int
    checked_at: datetime
    valid_until: datetime
