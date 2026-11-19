"""Actual temporary Tk callbacks, fictional data and completions only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import Mock, patch

from local_chat import LocalChat
from smoke_local_ui import pump
from core import atomic_json


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = tk.Tk()
        app = LocalChat(root, folder)
        try:
            app.input.insert("1.0", "虚构小说纸船的结尾还没想好")
            app.send()
            pump(root, lambda: not app.busy)
            panel = app.show_activity()
            root.update()
            panel.enable()
            panel.select_source()
            raw = panel.details.get("1.0", "end-1c")
            self_quote = "小说纸船的结尾还没想好"
            start = raw.index(self_quote)
            panel.details.tag_add("sel", f"1.0+{start}c", f"1.0+{start + len(self_quote)}c")
            panel.use_selected_evidence()
            assert panel.quote.get() == self_quote
            assert panel.title.get() == self_quote
            panel.mark(confirm=True)
            assert next(iter(panel.topics.values()))["status"] == "active"
            panel.evaluate()
            pump(root, lambda: not app.busy)
            draft_id = next(d["id"] for d in panel.drafts.values() if d["status"] == "pending")
            panel.draft_view.selection_set(draft_id)
            app.input.insert("1.0", "采用期间保留的草稿")
            original = deepcopy(app.core.memory.state)
            with patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
                panel.adopt()
            assert app.core.memory.state == original
            assert app.input.get("1.0", "end-1c") == "采用期间保留的草稿"
            # Commit succeeds but painting fails. Recover from the committed
            # event, not another generation or a second event.
            with patch.object(app, "render", side_effect=tk.TclError("synthetic paint failure")):
                panel.adopt()
            assert "已提交" in panel.message.get()
            assert len(app.core.snapshot(app.scene.id)["turns"]) == 2
            panel.refresh()
            panel.draft_view.selection_set(draft_id)
            panel.adopt()
            assert len(app.core.snapshot(app.scene.id)["turns"]) == 2
            assert "本地开口" in app.transcript.get("1.0", "end")
            assert app.input.get("1.0", "end-1c") == "采用期间保留的草稿"
            panel.close()
            app.input.delete("1.0", "end")
            app.input.insert("1.0", "我想试试这个方向")
            from character_service import simulation_completion
            complete = Mock(side_effect=simulation_completion)
            with patch("local_chat.simulation_completion", complete):
                app.send()
                pump(root, lambda: not app.busy)
            messages = complete.call_args.args[0]
            assert messages[-2]["role"] == "assistant"
            assert "本地草稿" in messages[-2]["content"]
            app.show_feedback()
            root.update()
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            from tkinter import ttk
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            controls = list(descendants(dialog))
            selected = next(w for w in controls if isinstance(w, ttk.Combobox))
            labels = list(selected.cget("values"))
            selected.set(next(label for label in labels if "本地开口" in label))
            next(w for w in controls if isinstance(w, ttk.Checkbutton) and w.cget("text") == "接得自然").invoke()
            next(w for w in controls if isinstance(w, ttk.Button) and w.cget("text") == "保存本地反馈").invoke()
            assert app.core.snapshot(app.scene.id)["feedback"][0]["case_id"] == "local-opening"
            # Source deletion removes adopted and ordinary derived text while
            # retaining the later independent user's answer.
            source = app.core.snapshot(app.scene.id)["turns"][0]
            app.core.mark_turn(app.scene.id, source["id"], self_quote, "虚构小说话题")
            record = app.core.snapshot(app.scene.id)["memories"][0]
            app.core.forget_memory(app.scene.id, record["id"])
            app.render()
            assert "我想试试这个方向" in app.transcript.get("1.0", "end")
            assert "正文已删除" in app.transcript.get("1.0", "end")
            app.close()

            # Recreate a fictional v2 file, verify read-only loading and the
            # explicit UI upgrade. No user's existing records are opened.
            path = Path(folder) / "simulation-v2.json"
            old = json.loads(path.read_text(encoding="utf-8"))
            old["version"] = 2
            for data in old["scenes"].values():
                data.pop("activity", None)
                data["turns"] = [t for t in data["turns"] if t["kind"] == "local_exchange"]
                data["feedback"] = []
                for turn in data["turns"]:
                    turn.pop("reply_trace")
                    turn["reply"] = "旧版虚构回复"
            atomic_json(path, old)
            before = path.read_bytes()
            root = tk.Tk()
            app = LocalChat(root, folder)
            root.update()
            assert path.read_bytes() == before
            assert "仅供查看" in app.status.get()
            with patch("local_chat.messagebox.askyesno", return_value=True):
                app.upgrade_v2()
            assert app.core.memory.state["version"] == 3
            assert "旧版虚构回复" in app.transcript.get("1.0", "end")
            print("Stage 4 GUI OK: selected evidence, confirmed topic, adoption, reply, feedback, deletion, paint recovery and explicit v2 upgrade.")
            print("Temporary fictional data only; no real API, clipboard or WeChat actions.")
        finally:
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
