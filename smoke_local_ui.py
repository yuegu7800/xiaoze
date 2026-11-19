"""Opt-in GUI smoke, synthetic API responses only, all records in a temp folder.

Run manually with the configured Python. This never imports or operates WeChat.
"""
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import Mock, patch

from local_chat import LocalChat, MODE_API
from character import SCENES


def pump(root, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        root.update()
        if time.monotonic() > deadline:
            raise AssertionError("GUI event timed out")
        time.sleep(0.01)
    root.update()


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = tk.Tk()
        app = LocalChat(root, folder)
        try:
            root.update()
            assert app.send_button.winfo_ismapped()
            assert app.input.winfo_height() > 30
            assert app.transcript.winfo_height() > 100
            app.scene_label.set(SCENES[1].label)
            app.switch_scene()
            app.input.insert("1.0", "小泽，别怼我")
            app.send()
            pump(root, lambda: not app.busy)
            assert "流程模拟" in app.transcript.get("1.0", "end")
            assert app.core.snapshot("local-lin")["preferences"]["friend-lin"]
            app.show_memory()
            root.update()
            dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            assert len(dialogs) == 1
            dialogs[0].destroy()
            app.mode.set(MODE_API)
            app.switch_mode()
            assert not app.core.snapshot("local-lin")["turns"]
            app.input.insert("1.0", "虚构模型测试")
            app.send()
            assert not app.busy
            assert "API Key" in app.status.get()
            for key, value in {"endpoint": "https://example.test/v1/chat/completions",
                               "model": "synthetic", "api_key": "synthetic-ui-only-key"}.items():
                app.api_values[key].set(value)
            response = Mock(status_code=200)
            response.json.return_value = {"choices": [{"message": {"content": "合成模型回复，仅测试界面。"}}]}
            with patch("requests.post", return_value=response) as post:
                app.send()
                pump(root, lambda: not app.busy)
                app.input.insert("1.0", "接着刚才的虚构话题")
                app.send()
                pump(root, lambda: not app.busy)
                payload = post.call_args.kwargs["json"]
                assert len(payload["messages"]) == 4
                assert "流程模拟" not in json.dumps(payload, ensure_ascii=False)
            before = app.core.snapshot("local-lin")
            app.input.insert("1.0", "保留这条未完成的虚构输入")
            with patch("requests.post", return_value=Mock(status_code=503)):
                app.send()
                pump(root, lambda: not app.busy)
            assert app.last_error == "error"
            assert "503" in app.status.get()
            assert app.core.snapshot("local-lin") == before
            started, release = threading.Event(), threading.Event()
            def delayed(_self, _messages):
                started.set()
                release.wait(4)
                return "迟到的合成模型回复"
            with patch("local_chat.ApiCompletion.__call__", delayed):
                app.send()
                pump(root, started.is_set)
                app.toggle_pause()
                app.toggle_pause()
                release.set()
                pump(root, lambda: not app.busy)
            assert app.last_error == "cancelled"
            assert app.core.snapshot("local-lin") == before
            assert "未完成" in app.input.get("1.0", "end")
            assert "synthetic-ui-only-key" not in (Path(folder) / "state-v2.json").read_text(encoding="utf-8")

            # Success after a new draft was entered must not erase it. The real
            # Tk modified-event path is exercised, not just a mock Text widget.
            app.input.delete("1.0", "end")
            app.input.insert("1.0", "我不喝咖啡")
            started.clear()
            release.clear()
            with patch("local_chat.ApiCompletion.__call__", delayed):
                app.send()
                pump(root, started.is_set)
                app.input.delete("1.0", "end")
                app.input.insert("1.0", "这是后来写的新草稿")
                root.update()
                release.set()
                pump(root, lambda: not app.busy)
            assert app.input.get("1.0", "end-1c") == "这是后来写的新草稿"
            assert app.core.snapshot("local-lin")["memories"]

            panel = app.show_memory()
            root.update()
            turn = app.core.snapshot("local-lin")["turns"][-1]
            label = next(label for label, turn_id in panel.turn_labels.items() if turn_id == turn["id"])
            panel.turn.set(label)
            panel.quote.set("我不喝咖啡")
            panel.content.set("谈到了饮料口味")
            panel.mark()
            pending = next(r for r in app.core.snapshot("local-lin")["memories"] if r["status"] == "pending")
            panel.view.selection_set(pending["id"])
            panel.accept()
            assert any(r["id"] == pending["id"] and r["status"] == "active" for r in app.core.snapshot("local-lin")["memories"])
            panel.view.selection_set(pending["id"])
            with patch("memory_panel.messagebox.askyesno", return_value=True):
                panel.forget()
            assert all(t["id"] != turn["id"] for t in app.core.snapshot("local-lin")["turns"])
            panel.close()
            app.show_feedback()
            root.update()
            feedback_dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            widgets = list(descendants(feedback_dialog))
            from tkinter import ttk
            tag = next(w for w in widgets if isinstance(w, ttk.Checkbutton))
            tag.invoke()
            note = next(w for w in widgets if isinstance(w, tk.Text) and w.cget("state") == "normal")
            note.insert("1.0", "合成反馈：希望更自然地接住话题")
            next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "保存本地反馈").invoke()
            root.update()
            assert app.core.snapshot(app.scene.id)["feedback"]

            # A failed delayed request also leaves subsequent draft edits alone.
            app.input.delete("1.0", "end")
            app.input.insert("1.0", "虚构待失败输入")
            started.clear()
            release.clear()
            def delayed_failure(_self, _messages):
                started.set()
                release.wait(4)
                raise RuntimeError("合成失败")
            with patch("local_chat.ApiCompletion.__call__", delayed_failure):
                app.send()
                pump(root, started.is_set)
                app.input.delete("1.0", "end")
                app.input.insert("1.0", "失败期间的新草稿")
                release.set()
                pump(root, lambda: not app.busy)
            assert app.input.get("1.0", "end-1c") == "失败期间的新草稿"
            assert app.last_error == "error"
            # Stage 3 uses actual Tk panel callbacks, synthetic candidates and
            # a mock clipboard. The main chat draft must survive throughout.
            app.input.delete("1.0", "end")
            app.input.insert("1.0", "虚构小说纸船结尾还没想好")
            with patch("local_chat.ApiCompletion.__call__", return_value="合成回复"):
                app.send()
                pump(root, lambda: not app.busy)
            panel = app.show_activity()
            root.update()
            same_prefix = "合成重复前缀" * 12
            for suffix in ("甲", "乙"):
                app.core.exchange(app.scene.id, app.scene.people[0].id, same_prefix + suffix, lambda _: "合成回复")
            panel.refresh()
            duplicates = [label for label in panel.sources if "合成重复前缀" in label]
            assert len(duplicates) == 2  # Equal first 50 characters no longer overwrite sources.
            panel.source.set(duplicates[1])
            panel.select_source()
            assert same_prefix + "乙" in panel.details.get("1.0", "end")
            panel.enable()
            topic_source = next(label for label in panel.sources if "小说纸船" in label)
            panel.source.set(topic_source)
            panel.quote.set("小说纸船结尾还没想好")
            panel.title.set("小说纸船")
            panel.direction.set("继续讨论结尾")
            panel.mark()
            topic_id = next(iter(panel.topics))
            panel.topic_view.selection_set(topic_id)
            panel.change("activate")
            app.input.insert("1.0", "主动候选期间保留的聊天草稿")
            payload = '{"decision":"draft","text":"纸船的结尾要不要试试留白？"}'
            with patch("activity_panel.ApiCompletion.__call__", return_value=payload):
                panel.evaluate()
                pump(root, lambda: not app.busy)
            assert app.input.get("1.0", "end-1c") == "主动候选期间保留的聊天草稿"
            draft_id = next(iter(panel.drafts))
            panel.draft_view.selection_set(draft_id)
            before_turns = app.core.snapshot(app.scene.id)["turns"]
            with patch.object(panel.window, "clipboard_clear") as clear, patch.object(panel.window, "clipboard_append") as append:
                panel.copy()
                clear.assert_called_once()
                append.assert_called_once_with("纸船的结尾要不要试试留白？")
            assert app.core.snapshot(app.scene.id)["turns"] == before_turns
            panel.draft_view.selection_set(draft_id)
            panel.note.insert(0, "合成候选反馈")
            panel.feedback()
            assert app.core.snapshot(app.scene.id)["activity"]["drafts"][0]["feedback"]
            panel.draft_view.selection_set(draft_id)
            panel.discard()
            with patch("activity_panel.ApiCompletion.__call__") as complete:
                panel.evaluate()
                pump(root, lambda: not app.busy)
                complete.assert_not_called()
            assert "先不说" in panel.message.get()
            panel.topic_view.selection_set(topic_id)
            panel.title.set("删除失败时保留这个输入")
            with patch("activity_panel.messagebox.askyesno", return_value=True), patch.object(app.core, "change_topic", side_effect=OSError("synthetic")):
                panel.delete()
            assert panel.title.get() == "删除失败时保留这个输入"
            with patch("activity_panel.messagebox.askyesno", return_value=True):
                panel.delete()
            assert not app.core.snapshot(app.scene.id)["activity"]["topics"]
            assert not panel.draft_view.get_children()
            # Confirmed public material is cleared on discard; unsaved editing
            # remains a separate explicit input, never silently published.
            panel.public.insert("1.0", "一条合成公开素材")
            panel.public_material()
            assert not panel.public.edit_modified()
            from datetime import timedelta
            old_clock = app.core.memory.clock
            advanced_now = old_clock() + timedelta(minutes=11)
            with patch.object(app.core.memory, "clock", return_value=advanced_now), patch("activity_panel.ApiCompletion.__call__", return_value=payload):
                panel.purpose.set("朋友圈草稿")
                panel.evaluate()
                pump(root, lambda: not app.busy)
                public_draft = next(d["id"] for d in panel.drafts.values() if d["purpose"] == "public" and d["status"] == "pending")
                panel.draft_view.selection_set(public_draft)
                panel.discard()
            assert not panel.public.get("1.0", "end-1c")
            panel.close()
            app.practice.set("主人：自然关心")
            app.practice_isolated.set(True)
            app.switch_mode()
            with patch("local_chat.messagebox.askyesno", return_value=True):
                app.load_case()
            assert app.scene.id == "local-owner"
            assert "今天有点累" in app.input.get("1.0", "end")
            assert not app.core.snapshot("local-owner")["turns"]
            assert "尚未发送" in app.status.get()
            # A damaged mode does not blank its file or kill the window.
            app.close()
            (Path(folder) / "simulation-v2.json").write_text('{"version":999}', encoding="utf-8")
            root = tk.Tk()
            app = LocalChat(root, folder)
            root.update()
            assert "原文件保留" in app.status.get()
            assert str(app.send_button.cget("state")) == "disabled"
            assert "999" in (Path(folder) / "simulation-v2.json").read_text(encoding="utf-8")
            print("GUI smoke OK: two turns, draft preservation, candidate confirmation, source deletion, feedback and isolated practice.")
            print("Synthetic responses only; no API request, real data or WeChat used.")
            print("Stage 3 GUI OK: topic activation, candidate/silence, mocked copy, feedback, discard/delete and chat draft preservation.")
        finally:
            app.close()


if __name__ == "__main__":
    main()
