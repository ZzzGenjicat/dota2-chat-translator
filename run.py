from pathlib import Path
import sys
import traceback


ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
SRC = ROOT / "src"
DEPENDENCIES = ROOT / 'vendor' / 'offline'
if DEPENDENCIES.is_dir():
    sys.path.insert(0, str(DEPENDENCIES))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


if __name__ == "__main__":
    try:
        if '--self-test' in sys.argv:
            from dota2_map_assistant.release_smoke import main as smoke_main
            sys.exit(smoke_main(sys.argv[1:]))
        from dota2_map_assistant.translator_app import main

        main()
    except Exception as exc:
        try:
            from dota2_map_assistant.app_paths import user_data_dir
            error_log = user_data_dir() / 'logs' / 'gui_error.log'
            error_log.parent.mkdir(parents=True, exist_ok=True)
            error_log.write_text(traceback.format_exc(), encoding="utf-8")
            detail = f'详细日志：{error_log}'
        except (OSError, ImportError, RuntimeError):
            detail = '无法写入错误日志，请检查文件夹权限。'
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f'启动失败：{exc}\n\n{detail}',
                'Dota 2 本地翻译器', 0x10,
            )
        elif sys.platform == 'darwin':
            try:
                import tkinter as tk
                from tkinter import messagebox
                error_root = tk.Tk()
                error_root.withdraw()
                messagebox.showerror('Dota 2 本地翻译器', f'启动失败：{exc}\n\n{detail}', parent=error_root)
                error_root.destroy()
            except Exception:
                pass  # The per-user log remains available if Tk itself cannot start.
        raise
