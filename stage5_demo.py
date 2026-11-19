"""Optional existing-UI rehearsal seeded only with fictional, offline dialogue.

All files live in a fresh temporary folder and disappear when the window closes.
No user configuration or saved records are read; API use remains explicit.
"""
from pathlib import Path
import sys
import tempfile

from character import CharacterCore, LocalMemory

ROOT = Path(__file__).resolve().parent


def prepare_demo(folder):
    paths = [Path(folder) / (name + "-v2.json") for name in ("simulation", "state")]
    if any(path.exists() for path in paths):
        raise ValueError("虚构样例只写入全新的临时模式文件，不覆盖既有记录。")
    for path in paths:
        core = CharacterCore(ROOT / "persona.txt", LocalMemory(path))
        def simulated(_messages):
            return "【虚构样例填充，非模型回复】"
        core.exchange("local-owner", "owner", "我不喝咖啡。附带暗号WINDOW_ONLY_Z", simulated)
        for index in range(13):
            core.exchange("local-owner", "owner", f"无关虚构填充第{index+1}条", simulated)


def main():
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("Use --check or no arguments.")
    with tempfile.TemporaryDirectory(prefix="xiaoze-stage5-") as folder:
        prepare_demo(folder)
        if sys.argv[1:] == ["--check"]:
            for name in ("simulation", "state"):
                path = Path(folder) / (name + "-v2.json")
                restored = CharacterCore(ROOT / "persona.txt", LocalMemory(path))
                report = restored.recall_preview("local-owner", "owner", "喝点什么")
                assert report["records"][0]["outcome"] == "long_injected"
                assert restored.recall_preview("local-owner", "owner", "来杯拿铁怎么样")["channels"]["long_memory"]
                for query in ("不太想工作，陪我说说话", "奶茶怎么样", "记得昨晚球赛吗"):
                    assert not restored.recall_preview("local-owner", "owner", query)["channels"]["long_memory"]
                assert "WINDOW_ONLY_Z" not in path.read_text(encoding="utf-8")
            print("Stage 5 demo OK: temporary fictional seeds reloaded; no API, real storage or window opened.")
            return
        from local_chat import LocalChat
        app = LocalChat(data_dir=folder)
        app.window.title("小泽 · 第五至八阶段临时虚构体验（关闭即清除）")
        app.status.set("临时虚构样例已离线推窗并重载：旧轮无瞬时证据。记忆/证据可预览工作/拿铁/颜色对照；新发送后点回复旁“评价这条回复”，先看中文摘要，必要时切技术详情。切模型试聊仍手动配置、逐句发送。不要勾独立体验区（会切到另一空库）。关闭删除全部样例。")
        app.window.mainloop()


if __name__ == "__main__":
    main()
