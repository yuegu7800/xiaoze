"""Actual stage-five Tk flow. Only temporary fictional records, no network."""
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import patch

from character import SCENE_BY_ID
from local_chat import LocalChat, MODE_API
from smoke_local_ui import pump


def main():
    with tempfile.TemporaryDirectory() as folder, patch("requests.post", side_effect=AssertionError("network forbidden")) as post:
        root = tk.Tk()
        app = LocalChat(root, folder)
        try:
            app.scene_label.set(SCENE_BY_ID["local-group"].label)
            app.switch_scene()
            for person, text in (("owner", "我喜欢简短回复"), ("friend-lin", "小泽，别怼我"), ("friend-qiao", "我可以接受友善的玩笑")):
                app.core.exchange("local-group", person, text, lambda _: "合成回复")
            app.input.insert("1.0", "面板维护时保留的草稿")
            panel = app.show_memory()
            root.update()
            assert "流程模拟" in panel.scope and "普通" in panel.scope and "三人测试群" in panel.scope
            assert len(panel.view.get_children()) == 1
            assert "唯一主人" in panel.profile.get("1.0", "end")
            panel.person_filter.set("全部人物")
            panel.refresh()
            assert len(panel.view.get_children()) == 3
            panel.keyword.set("别怼我")  # Match evidence rather than maintained content.
            panel.refresh()
            assert len(panel.view.get_children()) == 1
            original_id = panel.view.get_children()[0]
            assert panel.records[original_id]["person_id"] == "friend-lin"
            panel.view.selection_set(original_id)
            panel.selected()
            assert "小泽" not in panel.evidence.get("1.0", "end")  # Minimal quote only.
            assert "别怼我" in panel.evidence.get("1.0", "end")
            panel.edit("correction")
            panel.edit_value.set("请温和交流，不开冒犯玩笑")
            state = deepcopy(app.core.memory.state)
            path = app.core.memory.path
            before = path.read_bytes()
            with patch("memory_store.atomic_json", side_effect=OSError("synthetic IO failure")):
                panel.save_edit()
            assert app.core.memory.state == state and path.read_bytes() == before
            assert panel.edit_value.get() == "请温和交流，不开冒犯玩笑"
            assert "未完成" in panel.message.get()
            panel.person_filter.set(SCENE_BY_ID["local-group"].person("friend-qiao").display)
            panel.keyword.set("")
            panel.refresh()
            assert not panel.view.selection()
            qiao_id = panel.view.get_children()[0]
            panel.view.selection_set(qiao_id)
            panel.save_edit()
            assert app.core.memory.state == state and panel.edit_id == original_id
            assert panel.edit_value.get() == "请温和交流，不开冒犯玩笑"
            panel.person_filter.set(SCENE_BY_ID["local-group"].person("friend-lin").display)
            panel.refresh()
            panel.view.selection_set(original_id)
            panel.save_edit()
            root.update()
            new_id = panel.view.get_children()[0]
            assert new_id != original_id
            panel.view.selection_set(new_id)
            panel.selected()
            assert original_id in panel.evidence.get("1.0", "end")
            assert "请温和交流" in panel.profile.get("1.0", "end")
            panel.preview_person.set(SCENE_BY_ID["local-group"].person("friend-lin").display)
            panel.query.set("你记得我的偏好吗")
            state, trace = deepcopy(app.core.memory.state), deepcopy(app.core.last_trace)
            panel.tabs.select(panel.preview_tab)
            panel.preview()
            root.update()
            assert panel.preview_report["request_ready"]
            assert "长期注入" in panel.preview_output.get("1.0", "end")
            assert app.core.memory.state == state and app.core.last_trace == trace
            assert app.input.get("1.0", "end-1c") == "面板维护时保留的草稿"
            panel.tabs.select(panel.library_tab)
            panel.status_filter.set("历史 / 到期")
            panel.refresh()
            assert panel.view.get_children() == (original_id,)
            panel.status_filter.set("当前有效")
            panel.refresh()
            panel.view.selection_set(new_id)
            panel.quote.set("失败应保留的证据输入")
            with patch("memory_panel.messagebox.askyesno", return_value=True), patch("memory_store.atomic_json", side_effect=OSError("synthetic")):
                panel.forget()
            assert panel.quote.get() == "失败应保留的证据输入"
            assert new_id in panel.records
            with patch("memory_panel.messagebox.askyesno", return_value=True) as confirm:
                panel.forget()
            assert "保留后续无关用户原话" in confirm.call_args.args[1]
            assert not panel.view.get_children()
            assert not panel.preview_report
            assert "请温和交流" not in panel.profile.get("1.0", "end")
            panel.person_filter.set("全部人物")
            panel.keyword.set("没有这个结果")
            panel.refresh()
            assert "无匹配" in panel.counts.get() and "2/256" in panel.counts.get()
            panel.close()
            app.input.delete("1.0", "end")
            app.input.insert("1.0", "继续聊虚构小说")
            app.send()
            pump(root, lambda: not app.busy)
            assert "继续聊虚构小说" in app.transcript.get("1.0", "end")
            app.practice.set("第五阶段：主人连续交流")
            app.practice_isolated.set(True)
            app.switch_mode()
            app.load_case()
            assert not app.busy and not app.core.snapshot("local-owner")["turns"]
            panel = app.show_memory()
            assert "独立体验" in panel.scope and "尚无记忆" in panel.counts.get()
            panel.close()
            app.mode.set(MODE_API)
            app.switch_mode()
            panel = app.show_memory()
            panel.query.set("我喜欢银色月亮")
            panel.preview()
            assert "模型试聊" in panel.scope and panel.preview_report["records"] == []
            panel.close()
            app.close()
            from stage5_demo import prepare_demo
            demo_dir = Path(folder) / "demo"
            prepare_demo(demo_dir)
            unchanged = (demo_dir / "state-v2.json").read_bytes()
            try:
                prepare_demo(demo_dir)
                raise AssertionError("demo must refuse existing mode files")
            except ValueError:
                pass
            assert (demo_dir / "state-v2.json").read_bytes() == unchanged
            root = tk.Tk()
            app = LocalChat(root, demo_dir)
            app.mode.set(MODE_API)
            app.switch_mode()
            panel = app.show_memory()
            before = (app.core.memory.path.read_bytes(), deepcopy(app.core.memory.state),
                      app.core._epoch, deepcopy(app.core.last_trace), app.core.last_notice)
            for query, expected in (("不太想工作，陪我说说话", False), ("来杯拿铁怎么样", True), ("喝点什么", True),
                                    ("奶茶怎么样", False), ("咖啡色外套怎么搭", False), ("美式家具怎么选", False), ("来杯美式", True)):
                panel.query.set(query)
                assert panel.preview_report is None
                panel.preview()
                assert bool(panel.preview_report["channels"]["long_memory"]) == expected
                visible = panel.preview_output.get("1.0", "end")
                assert "本次组装的通道清单" in visible and "仅经助手历史继承" in visible
                assert "不代表其他通道没有提供" in visible
            assert (app.core.memory.path.read_bytes(), app.core.memory.state, app.core._epoch,
                    app.core.last_trace, app.core.last_notice) == before
            panel.close()
            app.input.insert("1.0", "来杯拿铁怎么样")
            captured = []
            def complete(_self, messages):
                captured.extend(messages)
                return "【虚构模拟】可以选不含咖啡的饮料"
            for key, value in {"endpoint": "https://example.test/v1/chat/completions", "model": "synthetic", "api_key": "synthetic-only"}.items():
                app.api_values[key].set(value)
            with patch("local_chat.ApiCompletion.__call__", complete):
                app.send()
                pump(root, lambda: not app.busy)
            assert "不喝咖啡" in captured[0]["content"]
            assert "WINDOW_ONLY_Z" not in str(captured)
            post.assert_not_called()
            print("Stage 5 GUI OK: scoped search, readable evidence, ID-bound edit/retry, pure preview, deletion and return to chat.")
            print("Temporary fictional simulation/API modes; no network, real records, clipboard or WeChat actions.")
            print("Stage 6 GUI OK: coffee contrasts and channel report; preview pure, actual mocked request includes the selected memory.")
        finally:
            if not app.closed:
                app.close()


if __name__ == "__main__":
    main()
