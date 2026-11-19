"""Actual Tk stage-ten controls; fake clocks, temporary data, mock HTTP only."""
from contextlib import contextmanager
import tempfile
import threading
import tkinter as tk
from unittest.mock import Mock, patch
import requests

from auto_opening import AutoOpening, SessionBudget
from local_chat import LocalChat, MODE_API
from smoke_local_ui import pump
from test_activity import CFG, DRAFT, SILENT
from test_auto_opening import Clock


def response(content=DRAFT, reason="stop"):
    value = Mock(status_code=200)
    value.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": reason}]}
    return value


@contextmanager
def playground(api=True):
    with tempfile.TemporaryDirectory(prefix="xiaoze-auto-fiction-") as folder:
        clock = Clock()
        policy = AutoOpening(monotonic=lambda: clock.mono, wall_clock=lambda: clock.wall, budget=SessionBudget())
        app = LocalChat(data_dir=folder, auto_policy=policy)
        try:
            if api:
                app.mode.set(MODE_API)
                app.switch_mode()
                for key, value in CFG.items():
                    app.api_values[key].set(value)
            app.core.memory.clock = lambda: clock.wall
            app.window.update()
            yield app, clock
        finally:
            if not app.closed:
                app.close()


def topic(app):
    core, scene = app.core, app.scene.id
    core.exchange(scene, "owner", "虚构小说纸船结尾没想好", lambda _: "合成回复")
    core.enable_activity(scene)
    source = core.snapshot(scene)["turns"][-1]["id"]
    core.mark_topic(scene, source, "纸船结尾没想好", "虚构纸船", "讨论结尾", confirm=True)
    app.render()
    app.window.update()


def tick(app, clock, seconds=30):
    if app.auto_after is not None:
        app.window.after_cancel(app.auto_after)
        app.auto_after = None
    clock.advance(seconds)
    app.auto_tick()
    app.window.update()


def idle(app, clock):
    for _ in range(20):
        tick(app, clock)


def check_defaults_and_blocks():
    with playground() as (app, clock), patch("requests.post", side_effect=AssertionError("unexpected network")) as post:
        assert not app.auto_enabled.get() and not app.auto_adopt.get()
        assert app.auto_after is None
        app.auto_check.invoke()
        assert app.auto_enabled.get()
        for _ in range(19):
            tick(app, clock)
        assert not app.busy
        tick(app, clock)  # No topic -> still no network or write.
        assert not app.core.memory.path.exists()
        assert "话题" in app.auto_status.get()
        topic(app)
        app.input.insert("1.0", "未发送的输入")
        app.window.update()
        idle(app, clock)
        assert not app.busy and "暂缓" in app.auto_status.get()
        app.input.delete("1.0", "end")
        app.window.update()
        panel = app.show_activity()
        app.window.update()
        idle(app, clock)
        assert not app.busy
        panel.close()
        app.window.update()
        tick(app, clock)
        assert not app.busy  # Closing panel gives a fresh idle interval.
        app.auto_stop_button.invoke()
        assert not app.auto_enabled.get() and app.auto_after is None
        post.assert_not_called()
        # Minimum-window controls stay visible and useful.
        app.window.geometry("820x680")
        app.window.update()
        sizes = {"input": app.input.winfo_height(), "chat": app.transcript.winfo_height()}
        assert app.send_button.winfo_ismapped() and app.auto_stop_button.winfo_ismapped(), sizes
        assert sizes["input"] >= 40 and sizes["chat"] >= 75, sizes
        assert app.send_button.winfo_rooty() + app.send_button.winfo_height() <= app.window.winfo_rooty() + app.window.winfo_height()
        print("PASS: default off, idle/topic/input/panel gates, stopped timer, minimum 820x680 layout", sizes)


def check_draft_and_adoption():
    for adopt in (False, True):
        with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
            topic(app)
            app.auto_check.invoke()
            if adopt:
                app.auto_adopt_check.invoke()
            with patch.object(app, "finish_auto_request", wraps=app.finish_auto_request) as finish:
                idle(app, clock)
                pump(app.window, lambda: not app.busy)
                kind, payload = finish.call_args.args
            # A repeated queue completion is a no-op even after painting.
            app.finish_auto_request(kind, payload)
            post.assert_called_once()
            assert post.call_args.kwargs["json"]["response_format"] == {"type": "json_object"}
            data = app.core.snapshot(app.scene.id)
            assert len(data["turns"]) == (2 if adopt else 1)
            assert app.auto_policy.budget.attempt_total == 1
            assert app.auto_policy.budget.adoption_total == int(adopt)
            assert ("已加入" if adopt else "留下待处理草稿") in app.auto_status.get(), app.auto_status.get()
            idle(app, clock)
            assert post.call_count == 1
            assert ("等待用户回应" if adopt else "待处理") in app.auto_status.get()
            # A mode/scope/config change switches off, not just pauses.
            app.practice_isolated.set(True)
            assert not app.auto_enabled.get() and not app.auto_adopt.get()
            assert app.auto_policy.budget.attempt_total == 1
    print("PASS: exact generated draft, optional adoption, response/pending gates, quota retained across scope changes")


def check_input_cancel_and_close():
    for action in ("input", "pause", "stop", "close"):
        with playground() as (app, clock):
            topic(app)
            core = app.core
            before = core.memory.path.read_bytes()
            entered, release = threading.Event(), threading.Event()
            def provider(*_args, **_kwargs):
                entered.set()
                assert release.wait(4), "mock provider release timeout"
                return response()
            with patch("requests.post", side_effect=provider) as post:
                app.auto_check.invoke()
                app.auto_adopt_check.invoke()
                idle(app, clock)
                pump(app.window, entered.is_set)
                if action == "input":
                    app.input.insert("1.0", "请求期间新输入必须保留")
                    app.window.update()
                elif action == "pause":
                    app.toggle_pause()
                elif action == "stop":
                    app.auto_stop_button.invoke()
                else:
                    afters = (app.poll_after, app.auto_after)
                    app.close()
                    remaining = app.window.tk.call("after", "info")
                    assert all(item not in remaining for item in afters if item)
                release.set()
                if action != "close":
                    pump(app.window, lambda: not app.busy)
                    if action == "input":
                        assert app.input.get("1.0", "end-1c") == "请求期间新输入必须保留"
                else:
                    # Worker uses only the queue/Core after window destruction.
                    pump_worker = threading.Event()
                    # A bounded thread join is unnecessary: request_slot becomes
                    # available before the final event is enqueued.
                    import time
                    deadline = time.monotonic() + 4
                    while core._inflight and time.monotonic() < deadline:
                        pump_worker.wait(.01)
                    assert not core._inflight
                post.assert_called_once()
                assert core.memory.path.read_bytes() == before
                assert app.auto_policy.budget.attempt_total == 1
                assert app.auto_policy.budget.adoption_total == 0
    print("PASS: in-flight input/pause/stop/window-close cancel late commit; requests counted, input preserved, timers cleared")


def check_errors_silent_and_paint():
    for content, reason in (("   ", "stop"), ("", "length"), ("not JSON", "stop"), (None, "stop")):
        with playground() as (app, clock), patch("requests.post", return_value=response(content, reason)) as post:
            topic(app)
            before = app.core.memory.path.read_bytes()
            app.auto_check.invoke()
            idle(app, clock)
            pump(app.window, lambda: not app.busy)
            assert not app.auto_policy.enabled and not app.paused and not app.core._paused
            assert "错误后已停止" in app.auto_status.get()
            assert app.auto_policy.budget.attempt_total == 1
            assert app.core.memory.path.read_bytes() == before
            idle(app, clock)
            post.assert_called_once()
    with playground() as (app, clock), patch("requests.post", return_value=response(SILENT)) as post:
        topic(app)
        app.auto_check.invoke()
        idle(app, clock)
        pump(app.window, lambda: not app.busy)
        assert app.auto_policy.enabled
        assert "沉默" in app.auto_status.get()
        tick(app, clock)
        assert "冷却" in app.auto_status.get()
        post.assert_called_once()
    with playground() as (app, clock), patch("requests.post", return_value=response()) as post:
        topic(app)
        app.auto_check.invoke()
        app.auto_adopt_check.invoke()
        with patch.object(app, "render", side_effect=tk.TclError("fictional painting error")):
            idle(app, clock)
            pump(app.window, lambda: not app.busy)
        assert app.auto_policy.budget.adoption_total == 1
        assert len(app.core.snapshot(app.scene.id)["turns"]) == 2
        app.render()
        assert "本地开口" in app.transcript.get("1.0", "end")
        idle(app, clock)
        post.assert_called_once()
        assert app.auto_policy.budget.adoption_total == 1
    # Invalid local config is rejected before attempt reservation.
    with playground() as (app, clock), patch("requests.post", side_effect=AssertionError("network")) as post:
        topic(app)
        app.api_values["api_key"].set("")
        app.auto_check.invoke()
        idle(app, clock)
        pump(app.window, lambda: not app.busy)
        assert not app.auto_policy.enabled and app.auto_policy.budget.attempt_total == 0
        post.assert_not_called()
    print("PASS: blank/truncation/format failures stop without retry, silent cools down, paint failure does not duplicate, config error costs no attempt")


def check_network_storage_and_profile():
    for failure in ("network", "generation-io", "adoption-io"):
        with playground() as (app, clock):
            topic(app)
            app.auto_check.invoke()
            app.auto_adopt_check.invoke()
            save = app.core.memory.save_scene
            saves = []
            def save_scene(scene, data):
                saves.append(data)
                if failure == "generation-io" or (failure == "adoption-io" and len(saves) == 2):
                    raise OSError("fictional storage failure")
                return save(scene, data)
            with patch("requests.post", side_effect=requests.Timeout() if failure == "network" else None,
                       return_value=response()) as post, patch.object(app.core.memory, "save_scene", side_effect=save_scene):
                idle(app, clock)
                pump(app.window, lambda: not app.busy)
                assert not app.auto_policy.enabled
                assert "错误后已停止" in app.auto_status.get()
                assert app.auto_policy.budget.attempt_total == 1
                assert app.auto_policy.budget.adoption_total == 0
                assert len(app.core.snapshot(app.scene.id)["turns"]) == 1
                if failure == "adoption-io":
                    assert app.core.snapshot(app.scene.id)["activity"]["drafts"][0]["status"] == "pending"
                idle(app, clock)
                post.assert_called_once()
    # Mock an already-saved fictional profile at startup; loading is not consent.
    with tempfile.TemporaryDirectory(prefix="xiaoze-auto-profile-") as folder, \
            patch("local_chat.ApiProfile.load", return_value=CFG), \
            patch("requests.post", side_effect=AssertionError("network forbidden")) as post:
        app = LocalChat(data_dir=folder, auto_policy=AutoOpening(budget=SessionBudget()))
        try:
            app.window.update()
            assert {k: v.get() for k, v in app.api_values.items()} == CFG
            assert not app.auto_enabled.get() and not app.auto_adopt.get() and app.auto_after is None
            post.assert_not_called()
        finally:
            app.close()
    # Simulation uses the same real controls and transactions without HTTP.
    with playground(api=False) as (app, clock), patch("requests.post", side_effect=AssertionError("network")) as post:
        topic(app)
        app.auto_check.invoke()
        app.auto_adopt_check.invoke()
        idle(app, clock)
        pump(app.window, lambda: not app.busy)
        assert app.auto_policy.budget.adoption_total == 1
        assert app.auto_policy.budget.attempt_total == 0
        post.assert_not_called()
    print("PASS: network/generation IO/adoption IO stop once, saved profile stays off, simulation uses no HTTP")


def main():
    # Prevent accidental unmocked HTTP throughout the entire script.
    with patch("requests.post", side_effect=AssertionError("unmocked network forbidden")):
        check_defaults_and_blocks()
        check_draft_and_adoption()
        check_input_cancel_and_close()
        check_errors_silent_and_paint()
        check_network_storage_and_profile()
    print("Stage ten Tk mock verification OK; no real credentials, API or WeChat.")


if __name__ == "__main__":
    main()
