"""Platform-independent character service. No WeChat, scheduler or tool execution.

Only the local scene selector supplies identity. User/model text never assigns it.
All persistence in this module is for the isolated local rehearsal environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core import OWNER
from memory_store import MemoryStore, RECENT_TURNS


@dataclass(frozen=True)
class Person:
    id: str
    display: str
    role: Literal["owner", "friend"]


@dataclass(frozen=True)
class Scene:
    id: str
    label: str
    kind: Literal["private", "group"]
    people: tuple[Person, ...]

    def person(self, person_id: str) -> Person:
        for person in self.people:
            if person.id == person_id:
                return person
        raise ValueError("当前场景中没有这个测试人物。")


OWNER_PERSON = Person("owner", OWNER, "owner")
LIN = Person("friend-lin", "小林（虚构）", "friend")
QIAO = Person("friend-qiao", "小乔（虚构）", "friend")
SCENES = (
    Scene("local-owner", "与孤月大人私聊（本地扮演）", "private", (OWNER_PERSON,)),
    Scene("local-lin", "与小林私聊（虚构朋友）", "private", (LIN,)),
    Scene("local-qiao", "与小乔私聊（虚构朋友）", "private", (QIAO,)),
    Scene("local-group", "三人测试群（完全虚构）", "group", (OWNER_PERSON, LIN, QIAO)),
)
SCENE_BY_ID = {scene.id: scene for scene in SCENES}
PREFERENCE_KEYS = {"teasing", "tone", "length"}
HISTORY_CHAR_BUDGET = 16000


@dataclass(frozen=True)
class DraftCandidate:
    """Legacy lightweight shape; persisted unsent drafts use activity/version=1."""
    scene_id: str
    text: str
    based_on_turns: tuple[str, ...]
    status: Literal["draft"] = "draft"


class LocalMemory(MemoryStore):
    def __init__(self, path, clock=None):
        identities = {scene.id: {person.id: person.role for person in scene.people} for scene in SCENES}
        super().__init__(path, identities, clock)


from character_service import CharacterCoreBase, ApiCompletion, Cancelled, simulation_completion


class CharacterCore(CharacterCoreBase):
    def __init__(self, persona_path, memory):
        super().__init__(persona_path, memory, SCENE_BY_ID)
