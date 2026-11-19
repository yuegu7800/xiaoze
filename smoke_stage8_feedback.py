"""Temporary fictional Tk actions; never reads existing stores or calls a model."""
from copy import deepcopy
from datetime import datetime, timedelta
import tempfile
import tkinter as tk
from unittest.mock import patch

from character import SCENE_BY_ID
from feedback_panel import FeedbackPanel
from local_chat import LocalChat, MODE_API, MODE_SIMULATION
from memory_rules import LOCAL_TZ
from memory_store import make_record


def main():
    with tempfile.TemporaryDirectory(prefix="xiaoze-stage8-") as folder, \
         patch("requests.post", side_effect=AssertionError("no HTTP")) as network:
        app = LocalChat(data_dir=folder)
        root, core = app.window, app.core
        now = [datetime(2026, 11, 11, 23, 50, tzinfo=LOCAL_TZ)]
        core.memory.clock = lambda: now[0]

        def say(text, person="owner", scene="local-owner"):
            core.exchange(scene, person, text, lambda _: "【虚构合成回复】", use_memory=False)
            return core.feedback_view(scene)[-1]["id"]

        def action_for(key):
            return next(tag for tag, target in app._feedback_actions.items() if target["turn_id"] == key)

        def click(key):
            # Dispatch a real mouse event at the original Text tag coordinates.
            tag = action_for(key)
            start, end = app.transcript.tag_ranges(tag)
            app.transcript.see(start)
            root.update()
            x, y, width, height = app.transcript.bbox(start)
            app.transcript.event_generate("<Motion>", x=x+2, y=y+height//2)
            app.transcript.event_generate("<Button-1>", x=x+2, y=y+height//2)
            app.transcript.event_generate("<ButtonRelease-1>", x=x+2, y=y+height//2)
            root.update()
            panel = app.feedback_panel
            if not panel or panel.labels.get(panel.chosen.get()) != key:
                print("Click diagnostic:", tag, app.transcript.index(f"@{x+2},{y+height//2}"),
                      app.transcript.tag_names(f"@{x+2},{y+height//2}"), app.status.get(),
                      panel.labels.get(panel.chosen.get()) if panel else None, key)
            assert panel and panel.labels.get(panel.chosen.get()) == key
            return panel

        def stable():
            return (core.memory.path.read_bytes(), deepcopy(core.memory.state), deepcopy(core._request_evidence),
                    core._epoch, core._paused, core._inflight, deepcopy(core.last_trace), core.last_notice)

        try:
            first, middle, latest = [say("虚构同秒问题" + str(i)) for i in range(3)]
            assert len({r["created_at"] for r in core.feedback_view("local-owner")}) == 1
            app.render()
            before = stable()
            for key in (first, middle, latest):
                panel = click(key)
                assert "所选：回应孤月大人" in panel.summary.get("1.0", "end")
                assert "本进程捕获的该轮实际取数" in panel.summary.get("1.0", "end")
                assert key in panel.diagnosis.get("1.0", "end")
                assert key not in panel.summary.get("1.0", "end")
                assert panel.evidence_tabs.index(panel.evidence_tabs.select()) == 0
                assert root.grab_current() is None  # Main transcript remains clickable.
            assert stable() == before

            panel = click(first)
            panel.tags["机械追问"].set(True)
            panel.note.insert("1.0", "对第一轮的虚构期望")
            panel = click(middle)
            assert str(panel.save_button.cget("state")) == "disabled"
            assert "另一轮" in panel.hint.get()
            panel.save()
            assert stable() == before and panel.editing_turn_id == first
            click(first)
            assert panel.note.get("1.0", "end").strip() == "对第一轮的虚构期望"
            assert str(panel.save_button.cget("state")) == "normal"

            # Do not silently replace a dirty panel with another Core/scene.
            app.scene_label.set(SCENE_BY_ID["local-group"].label)
            app.switch_scene()
            lin = say("群内小林", "friend-lin", "local-group")
            qiao = say("群内小乔", "friend-qiao", "local-group")
            owner = say("群内主人", "owner", "local-group")
            app.render()
            app._open_reply_feedback(action_for(qiao))
            assert app.feedback_panel is panel and panel.scene.id == "local-owner"
            assert "未保存" in panel.hint.get()
            panel.discard_button.invoke()
            for key, person in ((lin, "friend-lin"), (qiao, "friend-qiao"), (owner, "owner")):
                panel = click(key)
                assert panel.rows[key]["speaker_id"] == person
                assert SCENE_BY_ID["local-group"].person(person).display in panel.summary.get("1.0", "end")
            # Old callbacks and Tcl command registrations do not survive redraw.
            stale_tag = action_for(lin)
            stale_command = app._feedback_bindings[stale_tag]
            app.render()
            assert not root.tk.call("info", "commands", stale_command)
            chosen = panel.chosen.get()
            app._open_reply_feedback(stale_tag)
            assert panel.chosen.get() == chosen and "已刷新" in app.status.get()
            app.speaker.set(SCENE_BY_ID["local-group"].person("friend-qiao").display)
            app.invalidate_context()
            click(lin)
            assert panel.rows[panel.labels[panel.chosen.get()]]["speaker_id"] == "friend-lin"
            panel.tags["自然合适"].set(True)
            panel.note.insert("1.0", "不可跨模式搬走的虚构期望")
            app.mode.set(MODE_API)
            app.switch_mode()
            app.show_feedback()
            assert app.feedback_panel is panel and "未保存" in panel.hint.get()
            assert panel.note.get("1.0", "end").strip() == "不可跨模式搬走的虚构期望"
            panel.discard_button.invoke()
            app.show_feedback()
            assert app.feedback_panel.core is not core
            assert not app.feedback_panel.labels
            app.mode.set(MODE_SIMULATION)
            app.switch_mode()

            # A local adoption can be selected, but cannot claim exchange evidence.
            app.scene_label.set(SCENE_BY_ID["local-group"].label)
            app.switch_scene()
            core.enable_activity("local-group")
            source = say("虚构纸船结尾大家讨论", "owner", "local-group")
            core.mark_topic("local-group", source, "纸船结尾", "纸船结尾", "讨论方向", target="all", confirm=True)
            result = core.evaluate_candidate("local-group", "all", "group",
                lambda _: '{"decision":"draft","text":"【虚构群内开口】"}')
            adopted = core.adopt_draft("local-group", result["draft_id"], target="all", purpose="group")
            app.render()
            panel = click(adopted)
            assert "面向全群" in panel.summary.get("1.0", "end")
            assert "本地采用/候选没有本次直接取数清单" in panel.summary.get("1.0", "end")

            # Long metadata stays scrollable, with save/discard visible in both tabs.
            panel.window.destroy()
            app.scene_label.set(SCENE_BY_ID["local-owner"].label)
            app.switch_scene()
            data = core.snapshot("local-owner")
            data["memories"] = [make_record("local-owner", "owner", f"fiction-root-{i}", "preference",
                f"fixture-{i}", f"虚构内容{i}", "", 0, now[0], source="local_maintainer") for i in range(256)]
            core._save("local-owner", data)
            key = say("长证据的虚构问题")
            app.render()
            panel = click(key)
            for tab in (0, 1):
                panel.evidence_tabs.select(tab)
                root.update()
                widget = panel.summary if tab == 0 else panel.diagnosis
                widget.yview_moveto(1)
                assert panel.note.winfo_ismapped() and panel.save_button.winfo_ismapped()
                assert panel.discard_button.winfo_rooty() + panel.discard_button.winfo_height() <= panel.window.winfo_rooty() + panel.window.winfo_height()
            assert all(r["id"] in panel.diagnosis.get("1.0", "end") for r in data["memories"])

            # Neither tab survives a failed refresh with stale evidence on screen.
            with patch.object(core, "feedback_view", side_effect=RuntimeError("fiction read failure")):
                app.poll()
            assert "刷新失败" in panel.summary.get("1.0", "end")
            assert "刷新失败" in panel.diagnosis.get("1.0", "end")
            panel.refresh()
            app.poll()
            panel.tags["自然合适"].set(True)
            panel.note.insert("1.0", "虚构保存期望")
            with patch.object(core.memory, "save_scene", side_effect=OSError("fiction IO")):
                panel.save()
            assert not panel.submitted and panel.note.get("1.0", "end").strip() == "虚构保存期望"
            with patch.object(core, "feedback", wraps=core.feedback) as saved, \
                 patch.object(panel.window, "destroy", side_effect=tk.TclError("fiction paint failure")):
                panel.save()
                panel.save()
                assert saved.call_count == 1
            panel.window.destroy()

            # Deletion removes actions, both evidence tabs and unsaved feedback.
            panel = click(key)
            panel.tags["机械追问"].set(True)
            panel.note.insert("1.0", "DELETE_UNSAVED_STAGE8")
            old_tag = action_for(key)
            core.reset_scene("local-owner")
            app.poll()
            assert not app._feedback_actions and not panel.labels
            assert not panel.note.get("1.0", "end").strip()
            assert "所选轮次已淘汰" in panel.summary.get("1.0", "end")
            assert "所选轮次已淘汰" in panel.diagnosis.get("1.0", "end")
            app._open_reply_feedback(old_tag)
            assert not panel.chosen.get()
            panel.window.destroy()

            # Repeated repaint has bounded callbacks; a retained stale ID never rebinds.
            old = say("即将淘汰的虚构问题")
            app.render()
            old_tag = action_for(old)
            for i in range(13):
                say(f"虚构填充{i}")
            app.render()
            for _ in range(10):
                app.render()
            assert len(app._feedback_actions) == len(app._feedback_bindings) == 12
            assert len([x for x in app.transcript.tag_names() if x.startswith("reply-feedback-")]) == 12
            app._open_reply_feedback(old_tag)
            assert old not in {x["turn_id"] for x in app._feedback_actions.values()}
            # Clock expiry hides the original action on poll without a persistent write.
            temp = core.snapshot("local-owner")
            temp["memories"] = [make_record("local-owner", "owner", "fiction-temp", "temporary", "energy",
                "今天想安静", "", 0, now[0], source="local_maintainer", expires_at=(now[0]+timedelta(minutes=1)).isoformat())]
            core._save("local-owner", temp)
            core.exchange("local-owner", "owner", "虚构到期测试", lambda _: "【虚构到期回复】")
            expiry_turn = core.feedback_view("local-owner")[-1]["id"]
            app.render()
            panel = click(expiry_turn)
            now[0] += timedelta(minutes=2)
            before_expiry = stable()
            app.poll()
            assert expiry_turn not in {x["turn_id"] for x in app._feedback_actions.values()}
            assert "本进程捕获的该轮实际取数" not in panel.summary.get("1.0", "end")
            assert stable() == before_expiry
            network.assert_not_called()
            app.close()
            assert not app._feedback_actions and not app._feedback_bindings
            print("Stage 8 GUI OK: actual reply clicks, exact turn/speaker, drafts/scopes, bounded callbacks, summary/details, adoption, IO/paint recovery, expiry/deletion.")
            print("Fictional temporary data only; real model language remains unverified.")
        finally:
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
