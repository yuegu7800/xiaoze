"""Keep startup failures visible; --check never opens a window or connects WeChat."""
from datetime import datetime
import importlib
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main():
    sys.path.insert(0, str(ROOT))
    try:
        if sys.argv[1:] == ["--check"]:
            for name in ("tkinter", "win32crypt", "app"):
                importlib.import_module(name)
            print("Startup imports: OK. No window opened; no WeChat connection.")
        else:
            runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
        return 0
    except BaseException as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            return 0
        # Do not record config values, exception bodies, locals or chat text.
        frames = traceback.extract_tb(error.__traceback__)
        details = "\n".join(f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)
        report = f"{datetime.now().astimezone().isoformat()} {type(error).__name__}\n{details}\n"
        try:
            (ROOT / "data").mkdir(exist_ok=True)
            with (ROOT / "data" / "startup.log").open("a", encoding="utf-8") as stream:
                stream.write(report)
        except OSError:
            pass
        if isinstance(error, SystemExit):
            print("程序没有打开新窗口：可能已有小泽或旧入口窗口在运行，请先检查任务栏。")
        else:
            print("小泽启动失败：" + type(error).__name__)
        print(report)
        print("诊断位置：" + str(ROOT / "data" / "startup.log"))
        if sys.stdout is None:
            import ctypes
            hint = ("小泽或兼容旧入口窗口可能已在运行，请检查任务栏。" if isinstance(error, SystemExit)
                    else "启动失败：" + type(error).__name__ + "\n诊断已保存到 data/startup.log。")
            ctypes.windll.user32.MessageBoxW(None, hint, "小泽启动提示", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
