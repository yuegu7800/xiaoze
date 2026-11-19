"""Fictional Tk review: unsaved feedback never follows a different turn."""
import tempfile
import threading
from unittest.mock import patch

from local_chat import LocalChat


def main():
    with tempfile.TemporaryDirectory(prefix="xiaoze-review7-") as folder, patch("requests.post", side_effect=AssertionError("no network")) as network:
        app = LocalChat(data_dir=folder)
        core, scene = app.core, "local-owner"
        root = app.window
        release = threading.Event()
        worker = None
        try:
            for text in ("虚构第一轮", "虚构第二轮"):
                core.exchange(scene, "owner", text, lambda _: "合成回复")
            first, second = [r["id"] for r in core.feedback_view(scene)]
            panel = app.show_feedback()
            def select(key):
                panel.chosen.set(next(label for label, value in panel.labels.items() if value == key))
                root.update()
            select(first)
            panel.tags["机械追问"].set(True)
            panel.note.insert("1.0", "针对第一轮的虚构期望")
            select(second)
            before = core.memory.path.read_bytes()
            assert str(panel.save_button.cget("state")) == "disabled"
            assert "另一轮" in panel.hint.get()
            panel.save()  # Even an accidental/direct callback cannot bypass binding.
            assert before == core.memory.path.read_bytes()
            assert panel.editing_turn_id == first
            select(first)
            assert str(panel.save_button.cget("state")) == "normal"
            assert panel.note.get("1.0", "end").strip() == "针对第一轮的虚构期望"
            assert "另一轮" not in panel.hint.get()
            select(second)
            panel.discard_button.invoke()
            assert not panel.note.get("1.0", "end").strip() and not any(v.get() for v in panel.tags.values())
            assert panel.editing_turn_id == second
            panel.tags["自然合适"].set(True)
            panel.note.insert("1.0", "针对第二轮的虚构期望")
            root.update()
            assert panel.discard_button.winfo_rooty() + panel.discard_button.winfo_height() <= panel.window.winfo_rooty() + panel.window.winfo_height()
            entered, errors = threading.Event(), []
            def complete(_):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test timeout")
                return "在途的合成回复"
            def work():
                try:
                    core.exchange(scene, "owner", "虚构第三轮", complete)
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=work)
            worker.start()
            assert entered.wait(5)
            epoch = core._epoch
            panel.save()
            assert not panel.submitted and "正在生成" in panel.hint.get()
            assert core._epoch == epoch and core._inflight
            assert before == core.memory.path.read_bytes()
            assert panel.note.get("1.0", "end").strip() == "针对第二轮的虚构期望"
            release.set()
            worker.join(5)
            assert not worker.is_alive() and not errors
            panel.refresh()
            assert panel.labels[panel.chosen.get()] == second
            panel.save()
            feedback = core.snapshot(scene)["feedback"]
            assert len(feedback) == 1 and feedback[0]["turn_id"] == second
            assert feedback[0]["note"] == "针对第二轮的虚构期望"
            assert len(core.feedback_view(scene)) == 3
            assert core.feedback_view(scene)[-1]["evidence"] is not None
            # Removing the source must clear even unsaved feedback text, not
            # merely its evidence display or the dropdown.
            panel = app.show_feedback()
            panel.tags["机械追问"].set(True)
            panel.note.insert("1.0", "DELETE_UNSAVED_FICTIONAL_NOTE")
            core.reset_scene(scene)
            app.poll()
            root.update()
            assert panel.chosen.get() == "" and panel.editing_turn_id is None
            assert not panel.note.get("1.0", "end").strip()
            assert not any(v.get() for v in panel.tags.values())
            assert "没有转绑" in panel.hint.get()
            network.assert_not_called()
            print("Stage 7 review GUI OK: feedback draft stays on its turn, explicit discard, removal clears unsaved text, in-flight save waits without cancellation.")
        finally:
            release.set()
            if worker:
                worker.join(5)
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
