"""Temporary fictional Tk paths; all completions/network are mocked or offline."""
from copy import deepcopy
from datetime import datetime, timedelta
import tempfile
import tkinter as tk
from unittest.mock import patch

from character import SCENE_BY_ID
from local_chat import LocalChat, MODE_API, MODE_SIMULATION
from memory_rules import LOCAL_TZ
from memory_store import make_record
from smoke_local_ui import pump


def main():
    with tempfile.TemporaryDirectory(prefix="xiaoze-stage7-") as folder, patch("requests.post", side_effect=AssertionError("no network")) as post:
        app = LocalChat(data_dir=folder)
        root = app.window
        core = app.core
        now = [datetime(2026, 11, 11, 23, 50, tzinfo=LOCAL_TZ)]
        core.memory.clock = lambda: now[0]
        scene = "local-owner"
        try:
            data = core.snapshot(scene)
            records = [make_record(scene, "owner", f"fiction-root-{i}", "preference", f"fixture-{i}",
                                   "不喝咖啡" if i == 0 else f"虚构非饮品资料编号{i}", "", 0, now[0], source="local_maintainer")
                       for i in range(256)]
            data["memories"] = records
            core._save(scene, data)
            core.exchange(scene, "owner", "拿铁怎么样", lambda _: "合成回复")
            first = core.feedback_view(scene)[-1]["id"]
            core.exchange(scene, "owner", "工作有点烦", lambda _: "第二段合成回复")
            second = core.feedback_view(scene)[-1]["id"]
            panel = app.show_feedback()
            root.update()
            assert panel.core is core and panel.scene.id == scene
            evidence = panel.diagnosis.get("1.0", "end")
            assert second in evidence and "本进程捕获的该轮实际取数" in evidence
            assert "不喝咖啡" not in evidence and "第二段合成回复" not in evidence
            assert all(r["id"] in evidence for r in records)
            assert panel.diagnosis.winfo_height() < 150
            assert panel.note.winfo_ismapped() and panel.save_button.winfo_ismapped()
            assert panel.save_button.winfo_rooty() + panel.save_button.winfo_height() <= panel.window.winfo_rooty() + panel.window.winfo_height()
            panel.chosen.set(next(label for label, key in panel.labels.items() if key == first))
            panel.choice.event_generate("<<ComboboxSelected>>")
            root.update()
            assert " / " + first in panel.diagnosis.get("1.0", "end")
            # Main context changes cannot reinterpret the selected old turn.
            app.scene_label.set(SCENE_BY_ID["local-group"].label)
            app.switch_scene()
            app.speaker.set(SCENE_BY_ID["local-group"].person("friend-qiao").display)
            app.mode.set(MODE_API)
            app.switch_mode()
            panel.refresh()
            assert panel.core is core and panel.scene.id == scene
            assert panel.labels[panel.chosen.get()] == first
            # Explicit maintenance clears old evidence even in an open window.
            core.change_memory(scene, records[0]["id"], "edit", "喜欢奶茶")
            app.poll()
            assert "本进程捕获的该轮实际取数" not in panel.diagnosis.get("1.0", "end")
            assert "不喝咖啡" not in panel.diagnosis.get("1.0", "end")
            assert "不足以判断各通道" in panel.diagnosis.get("1.0", "end") or not panel.chosen.get()
            core.reset_scene(scene)
            app.poll()
            assert panel.chosen.get() == "" and not panel.labels
            assert "所选轮次已淘汰" in panel.diagnosis.get("1.0", "end")
            panel.window.destroy()
            app.mode.set(MODE_SIMULATION)
            app.switch_mode()
            app.scene_label.set(SCENE_BY_ID[scene].label)
            app.switch_scene()
            # A committed response remains inspectable after transcript painting fails.
            app.input.insert("1.0", "新的虚构问题")
            with patch.object(app, "render", side_effect=tk.TclError("synthetic paint failure")):
                app.send()
                pump(root, lambda: not app.busy)
            assert "回复已保存" in app.status.get()
            assert len(core.feedback_view(scene)) == 1
            assert core.feedback_view(scene)[-1]["evidence"] is not None
            committed = deepcopy(core._request_evidence)
            # Repeated completion notification may repaint, never re-commit or
            # assign the earlier evidence to another turn.
            app.events.put((app.request_number, "done", "重复通知", core, app.draft_version, "新的虚构问题"))
            app.poll()
            assert len(core.feedback_view(scene)) == 1 and core._request_evidence == committed
            app.render()
            panel = app.show_feedback()
            root.update()
            panel.tags["已提供但表达生硬"].set(True)
            panel.note.insert("1.0", "虚构期望：接住具体细节")
            before = deepcopy(core._request_evidence)
            with patch.object(core.memory, "save_scene", side_effect=OSError("synthetic feedback IO")):
                panel.save()
            assert not panel.submitted and "输入仍保留" in panel.hint.get()
            assert panel.note.get("1.0", "end").strip() == "虚构期望：接住具体细节"
            assert core._request_evidence == before
            with patch.object(core, "feedback", wraps=core.feedback) as saved, patch.object(panel.window, "destroy", side_effect=tk.TclError("synthetic close failure")):
                panel.save()
                panel.save()
                assert saved.call_count == 1
            assert panel.submitted and "已提交" in panel.hint.get()
            assert len(core.snapshot(scene)["feedback"]) == 1
            assert core._request_evidence == before
            panel.window.destroy()
            # Reopening/refresh is read-only, expiry hides evidence without mutation.
            core.exchange(scene, "owner", "今天想安静一点", lambda _: "合成回复")
            panel = app.show_feedback()
            root.update()
            now[0] += timedelta(days=1)
            state = (deepcopy(core.memory.state), deepcopy(core._request_evidence), core._epoch, core.memory.path.read_bytes())
            panel.refresh()
            assert "本进程捕获的该轮实际取数" not in panel.diagnosis.get("1.0", "end")
            assert state == (core.memory.state, core._request_evidence, core._epoch, core.memory.path.read_bytes())
            post.assert_not_called()
            app.close()
            assert all(not c._request_evidence for c in app.cores.values())
            print("Stage 7 GUI OK: exact-turn selection, fixed scope, live invalidation/expiry, scrollable evidence, committed paint recovery and feedback saved once.")
            print("Temporary fictional data only; no real model, user store or platform action.")
        finally:
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
