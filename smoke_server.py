"""Independent smoke checks; only binds an ephemeral loopback test port.

All synthetic data stays in memory and is discarded when this script exits.
The real component configuration and its normal port are never used.
"""

from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import (
    API_SCHEMA,
    COMPONENT,
    DOM_IDENTITY_MODE,
    DOM_ROOM_ID,
    DOM_TEACHER_ID,
    FRESH_SECONDS,
    MAX_MESSAGES,
    SOURCE_MAX_AGE,
    TOKEN_HEADER,
    TeacherFeed,
    TeacherServer,
    ValidationError,
    config_fingerprint,
    load_or_create_config,
    new_config,
    save_config,
    utc_iso,
)


class Clock:
    def __init__(self):
        self.value = 1_792_468_800.0

    def __call__(self):
        return self.value


class BridgeSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = new_config()
        cls.config.update({
            "room_id": "public-room-test",
            "teacher_id": "verified-teacher-test",
            "teacher_name": "烟测老师（合成数据）",
            "extension_origins": ["chrome-extension://" + "a" * 32],
            "image_hosts": ["fileoss.zx093.com"],
        })
        cls.server = TeacherServer(cls.config, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.clock = Clock()
        self.server.feed = TeacherFeed(self.config, clock=self.clock)

    def payload(self, identifier="teacher-message-test"):
        return {
            "source": "yuen-native",
            "mode": "snapshot",
            "room_id": self.config["room_id"],
            "teacher_id": self.config["teacher_id"],
            "observed_at": utc_iso(self.clock()),
            "source_ready": True,
            "messages": [{
                "id": identifier,
                "author_id": self.config["teacher_id"],
                "role": "teacher",
                "public": True,
                "published_at": utc_iso(self.clock() - 2),
                "text": "合成烟测消息，非真实老师内容。<script>never executed</script>",
                "images": [],
            }],
        }

    def request(self, method, path="/api/teacher-messages", payload=None, headers=None, auth=True, raw=None):
        defaults = {"Content-Type": "application/json"}
        if auth:
            defaults[TOKEN_HEADER] = self.config["token"]
        defaults.update(headers or {})
        body = raw if raw is not None else None if payload is None else json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=defaults)
            response = connection.getresponse()
            result = response.read()
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def test_loopback_read_only_and_no_secret_in_status(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("GET", "/api/status", auth=False)
        self.assertEqual(status, 200)
        parsed = json.loads(body)
        self.assertEqual(parsed["freshness"], "waiting")
        self.assertEqual(parsed["component"], COMPONENT)
        self.assertEqual(parsed["schema"], API_SCHEMA)
        self.assertEqual(parsed["config_fingerprint"], config_fingerprint(self.config))
        self.assertNotIn(self.config["token"].encode(), body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/bridge_config.json")[0], 404)
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)

    def test_runtime_identity_and_configuration_mismatch_are_detectable(self):
        status, _, body = self.request("GET", "/api/messages", auth=False)
        self.assertEqual(status, 200)
        running = json.loads(body)["status"]
        self.assertEqual(running["component"], "ths-teacher-component")
        self.assertEqual(running["schema"], 1)
        for field, value in (
            ("token", "b" * 43), ("teacher_id", "another-teacher"),
            ("room_id", "another-room"), ("extension_origins", ["chrome-extension://" + "b" * 32]),
            ("image_hosts", ["tgpub.zx093.com"]), ("port", 18766),
        ):
            with self.subTest(field=field):
                different = dict(self.config)
                different[field] = value
                self.assertNotEqual(running["config_fingerprint"], config_fingerprint(different))
        same = dict(self.config)
        same["image_hosts"] = ["fileoss.zx093.com", "fileoss.zx093.com"]
        self.assertEqual(running["config_fingerprint"], config_fingerprint(same))
        # Starting a differently paired server on the occupied port must fail.
        different = dict(self.config)
        different["teacher_id"] = "another-teacher"
        with self.assertRaises(OSError):
            TeacherServer(different, port=self.server.server_port)

    def test_pairing_token_and_origin_required(self):
        payload = self.payload()
        self.assertEqual(self.request("POST", payload=payload, auth=False)[0], 401)
        self.assertEqual(self.request("POST", payload=payload, headers={TOKEN_HEADER: "wrong"})[0], 401)
        self.assertEqual(self.request("POST", payload=payload, headers={TOKEN_HEADER: "é"})[0], 401)
        for origin in ("https://evil.example", "null", "http://127.0.0.1:18765", "chrome-extension://" + "b" * 32):
            with self.subTest(origin=origin):
                self.assertEqual(self.request("POST", payload=payload, headers={"Origin": origin})[0], 403)
        self.assertEqual(len(self.server.feed.snapshot()["messages"]), 0)

    def test_host_and_read_origin_reject_dns_rebinding(self):
        self.assertEqual(self.request("GET", "/api/messages", headers={"Host": "evil.example"})[0], 421)
        self.assertEqual(self.request("POST", payload=self.payload(), headers={"Host": "127.0.0.1.evil.example"})[0], 421)
        self.assertEqual(self.request("GET", "/api/messages", headers={"Origin": "https://evil.example"})[0], 403)
        own = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.request("GET", "/api/messages", headers={"Origin": own})[0], 200)

    def test_named_extension_preflight_and_write(self):
        origin = self.config["extension_origins"][0]
        headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-teacher-bridge-token",
        }
        status, reply_headers, _ = self.request("OPTIONS", headers=headers, auth=False)
        self.assertEqual(status, 204)
        self.assertEqual(reply_headers["Access-Control-Allow-Origin"], origin)
        status, reply_headers, body = self.request("POST", payload=self.payload(), headers={"Origin": origin})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["accepted"], 1)
        self.assertEqual(reply_headers["Access-Control-Allow-Origin"], origin)

    def test_student_private_and_wrong_author_rejected_atomically(self):
        for field, value in (("role", "student"), ("public", False), ("author_id", "another-person")):
            with self.subTest(field=field):
                payload = self.payload()
                bad = copy.deepcopy(payload["messages"][0])
                bad["id"] = "rejected-message"
                bad[field] = value
                payload["messages"].append(bad)
                self.assertEqual(self.request("POST", payload=payload)[0], 422)
                self.assertEqual(self.server.feed.status()["freshness"], "waiting")
                self.assertEqual(self.server.feed.snapshot()["messages"], [])
        payload = self.payload()
        payload["messages"][0]["private_text"] = "must never be accepted"
        self.assertEqual(self.request("POST", payload=payload)[0], 422)

    def test_exact_room_teacher_and_explicit_ready(self):
        for field in ("room_id", "teacher_id"):
            payload = self.payload()
            payload[field] = "another-room-or-person"
            self.assertEqual(self.request("POST", payload=payload)[0], 422)
        payload = self.payload()
        del payload["source_ready"]
        self.assertEqual(self.request("POST", payload=payload)[0], 422)
        unpaired = new_config()
        self.server.feed = TeacherFeed(unpaired, clock=self.clock)
        self.assertFalse(self.server.feed.status()["configured"])
        self.assertEqual(self.request("POST", payload=self.payload(), headers={TOKEN_HEADER: unpaired["token"]})[0], 422)

    def test_dom_scope_is_explicit_and_never_a_native_identity(self):
        config = dict(self.config)
        config.update({"identity_mode": DOM_IDENTITY_MODE, "room_id": DOM_ROOM_ID, "teacher_id": DOM_TEACHER_ID})
        self.server.feed = TeacherFeed(config, clock=self.clock)
        payload = self.payload()
        payload.update({"room_id": DOM_ROOM_ID, "teacher_id": DOM_TEACHER_ID, "identity_mode": DOM_IDENTITY_MODE})
        payload["messages"][0]["author_id"] = DOM_TEACHER_ID
        self.assertEqual(self.request("POST", payload=payload)[0], 200)
        self.assertEqual(self.server.feed.status()["identity_mode"], DOM_IDENTITY_MODE)
        without_mode = dict(payload)
        del without_mode["identity_mode"]
        self.assertEqual(self.request("POST", payload=without_mode)[0], 422)
        with self.assertRaises(ValidationError):
            TeacherFeed({**config, "identity_mode": "native_id"})
        with self.assertRaises(ValidationError):
            TeacherFeed({**config, "room_id": "another-public-room"})
        with self.assertRaises(ValidationError):
            TeacherFeed({**config, "teacher_id": "another-synthetic-author"})
        native_payload = self.payload("native-source")
        native_payload["identity_mode"] = "dom_teacher"
        self.server.feed = TeacherFeed(self.config, clock=self.clock)
        self.assertEqual(self.request("POST", payload=native_payload)[0], 422)

    def test_deduplication_and_bounded_retention(self):
        self.assertEqual(self.request("POST", payload=self.payload())[0], 200)
        status, _, body = self.request("POST", payload=self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"accepted": 0, "duplicates": 1, "retained": 1})
        for index in range(MAX_MESSAGES + 10):
            self.server.feed.ingest(self.payload(f"smoke-message-{index:04}"))
        snapshot = self.server.feed.snapshot()
        self.assertEqual(len(snapshot["messages"]), MAX_MESSAGES)
        self.assertNotIn("author_id", snapshot["messages"][0])
        self.assertNotIn("role", snapshot["messages"][0])
        self.assertNotIn("token", json.dumps(snapshot))

    def test_freshness_expires_and_unready_heartbeat_does_not_refresh(self):
        self.server.feed.ingest(self.payload())
        self.assertEqual(self.server.feed.status()["freshness"], "fresh")
        original_seen = self.server.feed.status()["last_seen"]
        self.clock.value += FRESH_SECONDS + 1
        self.assertEqual(self.server.feed.status()["freshness"], "stale")
        payload = self.payload()
        payload.update({"messages": [], "source_ready": False})
        self.server.feed.ingest(payload)
        status = self.server.feed.status()
        self.assertEqual(status["last_seen"], original_seen)
        self.assertFalse(status["source_ready"])
        self.assertEqual(status["freshness"], "stale")
        payload["source_ready"] = True
        self.server.feed.ingest(payload)
        self.assertEqual(self.server.feed.status()["freshness"], "fresh")

    def test_expired_or_future_observation_rejected(self):
        for offset in (-SOURCE_MAX_AGE - 1, 121):
            payload = self.payload()
            payload["observed_at"] = utc_iso(self.clock() + offset)
            self.assertEqual(self.request("POST", payload=payload)[0], 422)
        self.assertEqual(self.server.feed.status()["freshness"], "waiting")

    def test_safe_images_and_plain_text(self):
        payload = self.payload()
        for url in (
            "javascript:alert(1)", "data:image/png;base64,abcd", "http://fileoss.zx093.com/a.png",
            "https://fileoss.zx093.com.evil.example/a.png", "https://fileoss.zx093.com@evil.example/a.png",
            "https://fileoss.zx093.com/a.png?token=secret", "https://fileoss.zx093.com/a.png#fragment",
            "https://fileoss.zx093.com:8443/a.png",
        ):
            with self.subTest(url=url):
                payload["messages"][0]["images"] = [url]
                self.assertEqual(self.request("POST", payload=payload)[0], 422)
        payload["messages"][0]["images"] = ["https://fileoss.zx093.com/public-test.png"]
        self.assertEqual(self.request("POST", payload=payload)[0], 200)
        snapshot = self.server.feed.snapshot()
        self.assertIn("<script>", snapshot["messages"][0]["text"])
        source = (Path(__file__).resolve().parent / "web" / "teacher.js").read_text(encoding="utf-8")
        self.assertIn("text.textContent = message.text", source)
        self.assertNotIn("innerHTML", source)

    def test_limits_duplicate_json_and_static_csp(self):
        payload = self.payload()
        payload["messages"][0]["text"] = "x" * 12001
        self.assertEqual(self.request("POST", payload=payload)[0], 422)
        self.assertEqual(self.request("POST", raw=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", raw=b'{"source":"a","source":"b"}')[0], 422)
        # Reject the declared size before reading. Sending an oversized body races
        # Windows TCP's reset-on-close behavior and can obscure the actual 413.
        self.assertEqual(self.request("POST", raw=b"", headers={"Content-Length": str(256 * 1024 + 1)})[0], 413)
        status, headers, body = self.request("GET", "/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("老师消息".encode(), body)

    def test_unpaired_unicode_surrogates_cannot_break_reads(self):
        payload = self.payload()
        payload["messages"][0]["text"] = "\ud800"
        self.assertEqual(self.request("POST", payload=payload)[0], 422)
        payload["messages"][0]["text"] = "safe synthetic text"
        payload["messages"][0]["images"] = ["https://fileoss.zx093.com/\ud800.png"]
        self.assertEqual(self.request("POST", payload=payload)[0], 422)
        self.assertEqual(self.request("GET", "/api/messages")[0], 200)
        self.assertEqual(self.server.feed.snapshot()["messages"], [])

    def test_local_config_token_is_stable_and_not_replaced(self):
        with tempfile.TemporaryDirectory(prefix="ths-bridge-smoke-") as temporary:
            path = Path(temporary) / "bridge.json"
            first = load_or_create_config(path)
            second = load_or_create_config(path)
            self.assertEqual(first["token"], second["token"])
            self.assertFalse(first["extension_origins"])
            self.assertFalse(first["image_hosts"])
            self.assertEqual(first["teacher_id"], "")
            first["room_id"] = "explicit-room"
            save_config(path, first)
            self.assertEqual(load_or_create_config(path)["token"], first["token"])
            first["extension_origins"] = ["https://arbitrary-web-origin.example"]
            with self.assertRaises(ValidationError):
                save_config(path, first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
