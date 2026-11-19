"""Independent local launcher; never imports app.py or reads legacy data."""
import importlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main():
    for module in ("tkinter", "requests", "character", "local_chat"):
        importlib.import_module(module)
    if sys.argv[1:] == ["--check"]:
        print("Local imports OK; no window, stored configuration or WeChat opened.")
        return 0
    if sys.argv[1:]:
        print("Use --check or no arguments.")
        return 2
    # Keep the lock handle alive for the entire GUI lifetime. Import checks do
    # not create storage. This lock prevents two windows overwriting one store.
    import msvcrt
    data_dir = ROOT / ".local-xiaoze"
    data_dir.mkdir(exist_ok=True)
    with (data_dir / "instance.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "小泽本地试聊窗口已在运行，请检查任务栏。", "小泽", 0x40)
            return 0
        from local_chat import LocalChat
        LocalChat(data_dir=data_dir).window.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # No exception bodies, inputs, credentials or raw API responses in logs.
        import traceback
        frames = traceback.extract_tb(error.__traceback__)
        print("Local chat startup failed: " + type(error).__name__)
        for frame in frames:
            print(f"  {frame.filename}:{frame.lineno} in {frame.name}")
        print("Existing WeChat configuration and chat data were not imported.")
        raise SystemExit(1) from None
