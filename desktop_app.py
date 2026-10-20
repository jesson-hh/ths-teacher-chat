"""A standalone Windows program: official sign-in + teacher-only reader.

No Chrome, extension, imported browser profile, or reconstructed authentication
protocol. The source is an owned WebView2 control inside the same native form.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import threading
import time
import sys

from server import (TeacherServer, DOM_ROOM_ID, DOM_TEACHER_ID, new_config,
                    utc_iso, ValidationError)


APP_DIR = Path(__file__).resolve().parent
IMAGE_HOSTS = ["fileoss.zx093.com", "tgpub.zx093.com", "ynimg.zx093.cn"]


def user_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "THSTeacherDesktop"


def desktop_config() -> dict:
    config = new_config()
    config.update(identity_mode="dom_teacher", room_id=DOM_ROOM_ID,
                  teacher_id=DOM_TEACHER_ID, image_hosts=IMAGE_HOSTS,
                  extension_origins=[])
    return config


class DesktopFeed:
    """Own one in-process source and one random-port, read-only local view."""

    def __init__(self):
        self.server = TeacherServer(desktop_config(), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True, name="desktop-teacher-view")
        self.url = f"http://127.0.0.1:{self.server.server_port}/#desktop=1"

    def start(self):
        self.thread.start()
        return self

    def ingest(self, payload: dict):
        result = self.server.feed.ingest(payload)
        if not payload["source_ready"]:
            # A logged-out/changed source must not leave an earlier account's
            # messages visible when the user signs in again.
            with self.server.feed.lock:
                self.server.feed.messages.clear()
        return result

    def unavailable(self):
        return self.ingest({"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID,
                            "teacher_id": DOM_TEACHER_ID, "source": "yuen-native",
                            "observed_at": utc_iso(time.time()), "mode": "snapshot",
                            "source_ready": False, "messages": []})

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def save_diagnostics(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    safe = {"component": "ths-teacher-desktop", "schema": 1, "pid": os.getpid(),
            "updated_at": time.time(), **state}
    temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def run(args) -> None:
    from native_board_app import run_native
    run_native(args, APP_DIR, user_directory(), desktop_config())


def main():
    parser = argparse.ArgumentParser(description="独立老师消息组件，在程序内登录约牛")
    parser.add_argument("--parent-hwnd", type=lambda value: int(value, 0))
    parser.add_argument("--expected-root-hwnd", type=lambda value: int(value, 0))
    parser.add_argument("--confirm-empty-cell", action="store_true")
    parser.add_argument("--setup", action="store_true", help="打开无需命令行的安装窗口")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("此程序需要 Windows 和 WebView2")
    if args.parent_hwnd is not None and not args.confirm_empty_cell:
        parser.error("须先明确确认所选同花顺空白格")
    if args.parent_hwnd is None and (args.confirm_empty_cell or args.expected_root_hwnd):
        parser.error("嵌入参数须提供明确的父窗口句柄")
    installer = getattr(sys, "frozen", False) and Path(sys.executable).stem == "安装"
    first_start = (getattr(sys, "frozen", False) and args.parent_hwnd is None
                   and not (user_directory() / "board_config.json").is_file())
    if args.setup or installer or first_start:
        from desktop_setup import run_setup
        run_setup()
        return
    run(args)


if __name__ == "__main__":
    main()
