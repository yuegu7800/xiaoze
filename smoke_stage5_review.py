"""Review UI checks with temporary fictional data, no live model or platform."""
from copy import deepcopy
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from character import SCENE_BY_ID
from dialogue import empty_trace
from local_chat import LocalChat
from memory_store import make_record


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    with tempfile.TemporaryDirectory() as folder, patch("requests.post", side_effect=AssertionError("network forbidden")) as post:
        root = tk.Tk()
        app = LocalChat(root, folder)
        try:
            app.scene_label.set(SCENE_BY_ID["local-group"].label)
            app.switch_scene()
            for person, text in (("owner", "我不喝咖啡"), ("friend-lin", "我喜欢简短回复")):
                app.core.exchange(app.scene.id, person, text, lambda _: "虚构回复")
            panel = app.show_memory()
            panel.query.set("你记得我的偏好吗")
            before = app.core.memory.path.read_bytes(), deepcopy(app.core.memory.state), deepcopy(app.core.last_trace), app.core._epoch
            panel.preview()
            assert panel.preview_report and "不喝咖啡" in panel.preview_output.get("1.0", "end")
            panel.preview_person.set(app.scene.person("friend-lin").display)
            assert panel.preview_report is None
            assert "不喝咖啡" not in panel.preview_output.get("1.0", "end")
            panel.preview()
            assert panel.preview_report["person_id"] == "friend-lin"
            panel.query.set("换一个虚构问题")
            assert panel.preview_report is None
            assert (app.core.memory.path.read_bytes(), app.core.memory.state, app.core.last_trace, app.core._epoch) == before
            # Commit once even when painting the confirmation fails.
            source = app.core.snapshot(app.scene.id)["turns"][0]
            candidate = app.core.mark_turn(app.scene.id, source["id"], "我不喝咖啡", "虚构饮料话题")
            panel.status_filter.set("待确认")
            panel.refresh()
            panel.view.selection_set(candidate)
            with patch.object(panel, "refresh", side_effect=tk.TclError("synthetic display failure")):
                panel.accept()
            assert "提交已完成" in panel.message.get()
            panel.refresh()
            assert panel.records[candidate]["status"] == "active"
            panel.close()
            # Max legal dependency count must not push feedback controls away.
            app.core.exchange(app.scene.id, "owner", "虚构反馈来源", lambda _: "虚构回复")
            data = app.core.snapshot(app.scene.id)
            turn = data["turns"][-1]
            records = [make_record(app.scene.id, "owner", turn["id"], "preference", f"fixture-{i}",
                                   f"虚构资料{i}", "虚构反馈来源", 0, app.core.memory.clock()) for i in range(32)]
            data["memories"].extend(records)
            trace = empty_trace(app.scene.id)
            trace.update(source_ids=[turn["id"]], raw_source_ids=[turn["id"]], record_ids=[r["id"] for r in records])
            turn["reply_trace"] = trace
            app.core._save(app.scene.id, data)
            app.show_feedback()
            root.update()
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            controls = list(descendants(dialog))
            evidence = next(w for w in controls if isinstance(w, tk.Text) and w.cget("state") == "disabled")
            note = next(w for w in controls if isinstance(w, tk.Text) and w.cget("state") == "normal")
            save = next(w for w in controls if isinstance(w, ttk.Button) and w.cget("text") == "保存本地反馈")
            assert all(record["id"] in evidence.get("1.0", "end") for record in records)
            assert "累计依赖" in evidence.get("1.0", "end")
            assert "不足以判断各通道" in evidence.get("1.0", "end")
            assert evidence.winfo_height() < 150 and note.winfo_ismapped() and save.winfo_ismapped()
            assert save.winfo_rooty() + save.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
            next(w for w in controls if isinstance(w, ttk.Checkbutton) and w.cget("text") == "已提供但表达生硬").invoke()
            note.insert("1.0", "虚构反馈：请接具体内容")
            save.invoke()
            assert app.core.snapshot(app.scene.id)["feedback"][-1]["note"] == "虚构反馈：请接具体内容"
            post.assert_not_called()
            print("Stage 5 review GUI OK: preview invalidation, committed paint failure and bounded feedback evidence.")
        finally:
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
