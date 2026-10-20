"""Exercise the actual native reader with synthetic messages in a hidden form.

No THS window, source website, account profile or real message is accessed.
Run in a fresh process because WinForms text-rendering setup is process-wide.
"""

import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app import desktop_config
from native_board_app import run_native
from server import DOM_ROOM_ID, DOM_TEACHER_ID, utc_iso


def main():
    errors, evidence, owned = [], {}, {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        (base / "board_config.json").write_text("{}", encoding="utf-8")
        now = time.time()
        messages = [{"id": f"synthetic-{index}", "author_id": DOM_TEACHER_ID,
                     "role": "teacher", "public": True, "published_at": utc_iso(now - 100 + index),
                     "text": f"离线消息 {index}", "images": []} for index in range(61)]

        class HiddenBoard:
            def __init__(self, form, *_args):
                owned["form"] = form
            def start(self):
                assert not owned["form"].Visible
            def dispose(self):
                pass

        class SourceFixture:
            def __init__(self, _parent, _profile, _script, on_payload, *_callbacks):
                from System.Windows.Forms import Timer
                self.send_callback = on_payload
                self.step = 0
                self.timer = Timer()
                self.timer.Interval = 50
                self.timer.Tick += self.tick
            def send(self, items):
                self.send_callback({"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID,
                    "teacher_id": DOM_TEACHER_ID, "source": "yuen-native", "view_mode": "teacher",
                    "observed_at": utc_iso(time.time()), "source_ready": True, "messages": items})
            def status(self):
                return json.loads((base / "desktop_status.json").read_text(encoding="utf-8"))
            def navigate_to_source(self):
                self.send(messages[:60])
                self.before = self.status()
                self.timer.Start()
            def tick(self, *_args):
                try:
                    self.step += 1
                    if self.step == 1:
                        for _ in range(30):
                            self.send(copy.deepcopy(messages[:60]))
                        same = self.status()
                        assert same["reader_render_count"] == self.before["reader_render_count"]
                        assert same["reader_row_creations"] == self.before["reader_row_creations"]
                        evidence["unchanged_snapshots"] = 30
                    elif self.step == 2:
                        self.send(messages)
                        added = self.status()
                        assert added["message_count"] == 61
                        assert added["reader_row_creations"] == self.before["reader_row_creations"] + 1
                        assert added["reader_row_reuses"] == self.before["reader_row_reuses"] + 59
                        evidence["new_message"] = {"created": 1, "reused": 59}
                        self.before = added
                    elif self.step == 3:
                        changed = copy.deepcopy(messages)
                        changed[-1]["reply_context"] = {"text": "迟到的提问", "images": [],
                            "published_at": utc_iso(now - 60), "display_name": "离线学员"}
                        self.send(changed)
                        updated = self.status()
                        assert updated["reader_row_creations"] == self.before["reader_row_creations"] + 1
                        assert updated["reader_row_reuses"] == self.before["reader_row_reuses"] + 59
                        assert updated["reader_reply_contexts"] == 1
                        evidence["late_reply"] = {"created": 1, "reused": 59}
                    else:
                        assert not owned["form"].Visible
                        owned["form"].Close()
                except BaseException as error:
                    errors.append(error)
                    owned["form"].Close()
            def dispose(self):
                self.timer.Stop()
                self.timer.Dispose()

        args = SimpleNamespace(parent_hwnd=None, expected_root_hwnd=None, confirm_empty_cell=False)
        with patch("desktop_board.BoardAttachment", HiddenBoard), patch("desktop_webview.create_source_control", SourceFixture):
            run_native(args, Path(__file__).resolve().parent, base, desktop_config())
        if errors:
            raise errors[0]
        final = json.loads((base / "desktop_status.json").read_text(encoding="utf-8"))
        assert final["mode"] == "closed", final
        assert "new_message" in evidence and "late_reply" in evidence
        evidence["shutdown"] = "closed"
        print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
