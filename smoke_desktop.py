"""Offline checks for the owned desktop feed; no source page or login opened."""

import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from urllib.request import ProxyHandler, build_opener

from desktop_app import DesktopFeed, desktop_config
from desktop_board import read_board_config
from desktop_webview import parse_history_status, PayloadGate
from native_board_app import message_page_bounds
from server import DOM_ROOM_ID, DOM_TEACHER_ID, TeacherFeed, utc_iso, ValidationError


class DesktopFeedSmoke(unittest.TestCase):
    def test_installed_ths_path_is_explicit_and_portable(self):
        config = {"schema": 1, "enabled": True, "board_name": "老师消息",
                  "ths_exe": "C:/Programs/THS/hexin.exe", "canvas_class": "AfxFrameOrView140s",
                  "control_id": 59648, "top_inset": 28}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "board_config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(read_board_config(path)["ths_exe"], config["ths_exe"])
            for target in ("C:/Programs/THS/other.exe", "relative/hexin.exe"):
                path.write_text(json.dumps({**config, "ths_exe": target}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_board_config(path)

    def test_public_chat_metadata_is_explicit_and_isolated_by_view(self):
        now = utc_iso(time.time())
        message = {"id": "all-student-1", "author_id": "dom-student:VzPD.Y8fIGs_",
                   "role": "student", "public": True, "published_at": now, "text": "公开聊天",
                   "images": [], "display_name": "学员甲", "source_id": "1234567890123456789",
                   "reply_context": {"text": "对应提问", "images": [], "published_at": now,
                                     "display_name": "学员乙", "source_id": "question-42"}}
        payload = {"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID, "teacher_id": DOM_TEACHER_ID,
                   "source": "yuen-native", "observed_at": now, "source_ready": True,
                   "view_mode": "all", "messages": [message]}
        all_feed = TeacherFeed(desktop_config(), allow_reply_context=True, allow_public_chat=True)
        all_feed.ingest(PayloadGate().parse(json.dumps(payload)))
        stored = all_feed.snapshot()["messages"][0]
        self.assertEqual(stored["role"], "student")
        self.assertEqual(stored["source_id"], "1234567890123456789")
        self.assertEqual(stored["reply_context"]["display_name"], "学员乙")
        for invalid in ({**payload, "view_mode": "teacher"}, {**payload, "view_mode": "private"}):
            with self.assertRaises(ValidationError):
                all_feed.ingest(invalid)
        for changes in ({"role": "private"}, {"author_id": "actual-account-uid"}, {"public": False},
                        {"display_name": "x" * 81}, {"source_id": "has spaces"}, {"user_id": "never-accept"}):
            bad = copy.deepcopy(payload)
            bad["messages"][0].update(changes)
            with self.assertRaises(ValidationError):
                all_feed.ingest(bad)
        with self.assertRaises(ValidationError):
            TeacherFeed(desktop_config()).ingest(payload)

    def test_attached_question_updates_without_duplicate_and_stays_desktop_only(self):
        now = time.time()
        message = {"id": "reply-test", "author_id": DOM_TEACHER_ID, "role": "teacher", "public": True,
                   "published_at": utc_iso(now), "text": "老师回答", "images": []}
        payload = {"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID, "teacher_id": DOM_TEACHER_ID,
                   "source": "yuen-native", "observed_at": utc_iso(now), "source_ready": True,
                   "messages": [message]}
        feed = TeacherFeed(desktop_config(), allow_reply_context=True)
        feed.ingest(payload)
        question = {"text": "学员的提问", "images": [], "published_at": utc_iso(now - 10)}
        message["reply_context"] = question
        PayloadGate().parse(json.dumps(payload))
        feed.ingest(payload)
        self.assertEqual(feed.status()["message_count"], 1)
        self.assertEqual(feed.snapshot()["messages"][0]["reply_context"], question)
        with self.assertRaises(ValidationError):
            TeacherFeed(desktop_config()).ingest(payload)
        for invalid in ({**question, "user_id": "do-not-export"}, {**question, "text": "x" * 4001},
                        {**question, "text": "\ud800"}, {**question, "text": "\x00"},
                        {**question, "images": ["https://fileoss.zx093.com/x.png?token=secret"]},
                        {**question, "published_at": utc_iso(now + 130)}):
            bad = copy.deepcopy(payload)
            bad["messages"][0]["reply_context"] = invalid
            with self.assertRaises(ValidationError):
                feed.ingest(bad)
            self.assertEqual(feed.snapshot()["messages"][0]["reply_context"], question)
        del message["reply_context"]
        feed.ingest(payload)
        self.assertNotIn("reply_context", feed.snapshot()["messages"][0])

    def test_latest_page_is_full_and_history_has_no_gaps(self):
        for total in (0, 1, 59, 60, 61, 345, 361, 5000):
            start, end, current, pages = message_page_bounds(total, -1)
            self.assertEqual(end,total)
            self.assertEqual(end-start,min(total,60))
            self.assertEqual(current,pages-1)
            reconstructed=[]
            for page in range(pages):
                first,last,_,_=message_page_bounds(total,page)
                reconstructed.extend(range(first,last))
            self.assertEqual(reconstructed,list(range(total)))

    def test_history_retention_and_strict_progress_contract(self):
        now = time.time()
        feeds = [TeacherFeed(desktop_config()), TeacherFeed(desktop_config(), retention_limit=5000)]
        for start in range(0, 600, 100):
            payload = {"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID,
                       "teacher_id": DOM_TEACHER_ID, "source": "yuen-native",
                       "observed_at": utc_iso(now), "mode": "snapshot", "source_ready": True,
                       "messages": [{"id": f"history-{index}", "author_id": DOM_TEACHER_ID,
                                     "role": "teacher", "public": True, "published_at": utc_iso(now - index),
                                     "text": "离线历史验证", "images": []}
                                    for index in range(start, start + 100)]}
            for feed in feeds:
                feed.ingest(payload)
                feed.ingest(payload)
        self.assertEqual(feeds[0].status()["message_count"], 500)
        self.assertEqual(feeds[1].status()["message_count"], 600)
        self.assertEqual(feeds[1].status()["retention_limit"], 5000)
        status = {"kind": "history_status", "state": "idle", "pages": 6,
                  "loaded": 600, "teachers": 600, "has_more": True, "oldest": utc_iso(now - 600)}
        self.assertEqual(parse_history_status(status), status)
        for invalid in ({**status, "token": "never-accept"}, {**status, "loaded": True},
                        {**status, "state": "run-code"}, {**status, "teachers": 5001}):
            with self.assertRaises(ValueError):
                parse_history_status(invalid)

    def test_own_feed_has_no_extension_or_persisted_secret_and_clears_on_logout(self):
        source = DesktopFeed().start()
        try:
            config = source.server.feed.config
            self.assertEqual(config["extension_origins"], [])
            with build_opener(ProxyHandler({})).open(source.url) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("老师消息".encode(), response.read())
            now = utc_iso(time.time())
            message = {"id": "desktop-offline-test", "author_id": DOM_TEACHER_ID,
                       "role": "teacher", "public": True, "published_at": now,
                       "text": "离线测试", "images": []}
            payload = {"identity_mode": "dom_teacher", "room_id": DOM_ROOM_ID,
                       "teacher_id": DOM_TEACHER_ID, "source": "yuen-native",
                       "observed_at": now, "mode": "snapshot", "source_ready": True,
                       "messages": [message]}
            source.ingest(payload)
            self.assertEqual(source.server.feed.status()["message_count"], 1)
            with build_opener(ProxyHandler({})).open(source.url.split("#")[0] + "api/messages") as response:
                snapshot = json.load(response)
            self.assertEqual(snapshot["messages"][0]["text"], "离线测试")
            self.assertNotIn(config["token"], json.dumps(snapshot))
            source.unavailable()
            self.assertEqual(source.server.feed.status()["message_count"], 0)
            self.assertFalse(source.server.feed.status()["source_ready"])
            source.ingest(payload)
            self.assertEqual(source.server.feed.status()["message_count"], 1)
            with self.assertRaises(ValidationError):
                source.ingest({**payload, "messages": [{**message, "role": "student"}]})
        finally:
            source.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
