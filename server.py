"""Loopback-only, read-only teacher feed for the optional THS desktop component.

This module never connects to the source website. A separately paired companion
extension may submit bounded public teacher messages. The native reader can
explicitly enable bounded attached public quotes and a separate public all-chat
view. Default HTTP feeds remain teacher-only; private messages and website
credentials are not accepted. Session-only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOST = "127.0.0.1"
COMPONENT = "ths-teacher-component"
API_SCHEMA = 1
NATIVE_IDENTITY_MODE = "native_id"
DOM_IDENTITY_MODE = "dom_teacher"
DOM_ROOM_ID = "yuen-route:VzPD.Y8fIGs_"
DOM_TEACHER_ID = "dom-teacher:VzPD.Y8fIGs_"
DEFAULT_PORT = 18765
TOKEN_HEADER = "X-Teacher-Bridge-Token"
MAX_REQUEST_BYTES = 256 * 1024
MAX_BATCH = 100
MAX_MESSAGES = 500
MAX_TEXT = 12_000
MAX_REPLY_TEXT = 4000
MAX_IMAGES = 6
MAX_URL = 2048
FRESH_SECONDS = 30
SOURCE_MAX_AGE = 300
FUTURE_TOLERANCE = 120
WEB_DIR = Path(__file__).resolve().parent / "web"
ID_PATTERN = re.compile(r"^[\w.\-:/+=@]{1,256}$", re.ASCII)
EXTENSION_PATTERN = re.compile(r"^[a-p]{32}$")


def default_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "THSTeacherComponent" / "bridge_config.json"


def utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ValidationError(ValueError):
    """A bounded public-message contract was not met."""


def parse_time(value: object, field: str) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValidationError(f"{field}: expected an ISO8601 time with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError) as exc:
        raise ValidationError(f"{field}: invalid time or missing timezone") from exc


def require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValidationError(f"{field}: a stable explicit identifier is required")
    return value


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise ValidationError("unsupported bridge configuration")
    if not isinstance(config.get("token"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", config["token"]):
        raise ValidationError("configuration needs a random pairing token")
    port = config.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValidationError("invalid bridge port")
    for name in ("room_id", "teacher_id"):
        if config.get(name):
            require_id(config[name], name)
    identity_mode = config.get("identity_mode", NATIVE_IDENTITY_MODE)
    if identity_mode not in (NATIVE_IDENTITY_MODE, DOM_IDENTITY_MODE):
        raise ValidationError("unsupported teacher identity mode")
    if identity_mode == DOM_IDENTITY_MODE:
        if config.get("room_id") not in (None, "", DOM_ROOM_ID) or config.get("teacher_id") not in (None, "", DOM_TEACHER_ID):
            raise ValidationError("DOM teacher mode is restricted to the explicitly named public route")
    elif config.get("room_id") == DOM_ROOM_ID or config.get("teacher_id") == DOM_TEACHER_ID:
        raise ValidationError("synthetic DOM scope cannot be represented as a native author identifier")
    teacher_name = config.get("teacher_name", "老师消息")
    if not isinstance(teacher_name, str) or not 1 <= len(teacher_name) <= 80:
        raise ValidationError("invalid teacher display name")
    try:
        teacher_name.encode("utf-8")
    except UnicodeError as exc:
        raise ValidationError("invalid teacher display name encoding") from exc
    origins = config.get("extension_origins", [])
    if not isinstance(origins, list) or len(origins) > 4:
        raise ValidationError("invalid extension origin list")
    for origin in origins:
        if not isinstance(origin, str) or not origin.startswith("chrome-extension://"):
            raise ValidationError("only explicitly named Chrome companion extensions are allowed")
        if not EXTENSION_PATTERN.fullmatch(origin.removeprefix("chrome-extension://")):
            raise ValidationError("invalid extension ID")
    hosts = config.get("image_hosts", [])
    if not isinstance(hosts, list) or len(hosts) > 12:
        raise ValidationError("invalid image hostname list")
    for hostname in hosts:
        if not isinstance(hostname, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", hostname):
            raise ValidationError("invalid exact image hostname")
        if "." not in hostname or ".." in hostname:
            raise ValidationError("image hosts must be exact public hostnames")
    return config


def new_config() -> dict:
    return {
        "schema": 1,
        "port": DEFAULT_PORT,
        "token": secrets.token_urlsafe(32),
        "room_id": "",
        "teacher_id": "",
        "teacher_name": "知行合一 · 交易逻辑专栏",
        "identity_mode": NATIVE_IDENTITY_MODE,
        "extension_origins": [],
        "image_hosts": [],
    }


def config_fingerprint(config: dict) -> str:
    """Identify a paired runtime without publishing its secret or raw scope IDs."""
    validate_config(config)
    effective = {
        "schema": API_SCHEMA,
        "port": config.get("port", DEFAULT_PORT),
        "token": config["token"],
        "room_id": config.get("room_id") or "",
        "teacher_id": config.get("teacher_id") or "",
        "teacher_name": config.get("teacher_name", "老师消息"),
        "identity_mode": config.get("identity_mode", NATIVE_IDENTITY_MODE),
        "extension_origins": sorted(set(config.get("extension_origins", []))),
        "image_hosts": sorted(set(config.get("image_hosts", []))),
    }
    canonical = json.dumps(effective, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_config(path: Path, config: dict) -> None:
    validate_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass  # Windows user-profile ACLs remain the access boundary.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_create_config(path: Path | None = None) -> dict:
    path = path or default_config_path()
    if not path.exists():
        config = new_config()
        save_config(path, config)
        return config
    if path.stat().st_size > 16_384:
        raise ValidationError("configuration exceeds size limit")
    with path.open("r", encoding="utf-8-sig") as handle:
        return validate_config(json.load(handle))


class TeacherFeed:
    def __init__(self, config: dict, clock=time.time, *, retention_limit: int = MAX_MESSAGES,
                 allow_reply_context: bool = False, allow_public_chat: bool = False):
        if type(retention_limit) is not int or not 1 <= retention_limit <= 5000:
            raise ValueError("retention_limit must be between 1 and 5000")
        self.retention_limit = retention_limit
        self.allow_reply_context = allow_reply_context
        self.allow_public_chat = allow_public_chat
        self.config = validate_config(dict(config))
        self.fingerprint = config_fingerprint(self.config)
        self.clock = clock
        self.started_at = clock()
        self.last_seen: float | None = None
        self.last_contact: float | None = None
        self.observed_at: float | None = None
        self.source_ready = False
        self.messages: dict[str, dict] = {}
        self.lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.config.get("room_id") and self.config.get("teacher_id"))

    def _image(self, url: object) -> str:
        if not isinstance(url, str) or not url or len(url) > MAX_URL:
            raise ValidationError("images: invalid URL")
        try:
            url.encode("utf-8")
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname in self.config.get("image_hosts", [])
                and parsed.port in (None, 443)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and not any(ord(character) <= 32 for character in url)
            )
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise ValidationError("images: HTTPS and an explicitly allowed image host are required")
        return url

    def ingest(self, payload: object) -> dict:
        if not self.configured:
            raise ValidationError("pairing required: choose an explicit room and teacher first")
        required = {"room_id", "teacher_id", "source", "observed_at", "source_ready", "messages"}
        optional = {"mode", "identity_mode"} | ({"view_mode"} if self.allow_public_chat else set())
        if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - optional:
            raise ValidationError("payload must contain only the public teacher-message contract")
        if payload["source"] != "yuen-native":
            raise ValidationError("unsupported message source")
        if payload["room_id"] != self.config["room_id"] or payload["teacher_id"] != self.config["teacher_id"]:
            raise ValidationError("room or teacher does not match the local pairing")
        if payload.get("identity_mode", NATIVE_IDENTITY_MODE) != self.config.get("identity_mode", NATIVE_IDENTITY_MODE):
            raise ValidationError("teacher identity mode does not match the local pairing")
        if not isinstance(payload["source_ready"], bool) or payload.get("mode", "snapshot") not in ("snapshot", "live"):
            raise ValidationError("source_ready must be explicit; mode must be snapshot or live")
        now = self.clock()
        observed = parse_time(payload["observed_at"], "observed_at")
        if observed > now + FUTURE_TOLERANCE or now - observed > SOURCE_MAX_AGE:
            raise ValidationError("source observation is expired or in the future")
        batch = payload["messages"]
        if not isinstance(batch, list) or len(batch) > MAX_BATCH:
            raise ValidationError("messages: expected at most 100 public teacher messages")
        if payload["source_ready"] is False and batch:
            raise ValidationError("an unavailable source must not submit messages")
        validated = []
        view_mode = payload.get("view_mode", "teacher")
        if view_mode not in ("teacher", "all"):
            raise ValidationError("invalid public chat view")
        metadata_fields = {"display_name", "source_id"} if self.allow_public_chat else set()

        def metadata(value):
            result = {}
            for name in metadata_fields & set(value):
                text = value[name]
                if (not isinstance(text, str) or not text or len(text) > (80 if name == "display_name" else 256)
                        or any(ord(character) < 32 or character in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for character in text)):
                    raise ValidationError("invalid public message metadata")
                try:
                    text.encode("utf-8")
                except UnicodeError as exc:
                    raise ValidationError("invalid public message metadata encoding") from exc
                if name == "source_id":
                    require_id(text, "source_id")
                result[name] = text
            return result

        for message in batch:
            fields = {
                "id", "author_id", "role", "public", "published_at", "text", "images"
            }
            if (not isinstance(message, dict) or not fields.issubset(message)
                    or set(message) - fields - ({"reply_context"} if self.allow_reply_context else set()) - metadata_fields):
                raise ValidationError("message: only explicitly public teacher fields are accepted")
            role = message["role"]
            roles = ("teacher", "student", "assistant") if self.allow_public_chat and view_mode == "all" else ("teacher",)
            if role not in roles or message["public"] is not True:
                raise ValidationError("only public teacher messages are accepted")
            expected_author = self.config["teacher_id"] if role == "teacher" else f"dom-{role}:VzPD.Y8fIGs_"
            if message["author_id"] != expected_author:
                raise ValidationError("message author must match the paired teacher identifier")
            message_id = require_id(message["id"], "id")
            published = parse_time(message["published_at"], "published_at")
            if published > observed + FUTURE_TOLERANCE:
                raise ValidationError("message publication cannot be after its source observation")
            body = message["text"]
            if not isinstance(body, str) or len(body) > MAX_TEXT:
                raise ValidationError("text: expected bounded plain text")
            try:
                body.encode("utf-8")
            except UnicodeError as exc:
                raise ValidationError("text: invalid Unicode") from exc
            if any(ord(character) < 32 and character not in "\n\r\t" for character in body):
                raise ValidationError("text: control characters are not accepted")
            images = message["images"]
            if not isinstance(images, list) or len(images) > MAX_IMAGES:
                raise ValidationError("images: expected at most 6 URLs")
            image_urls = list(dict.fromkeys(self._image(url) for url in images))
            if not body.strip() and not image_urls:
                raise ValidationError("message must contain text or an allowed image")
            item = {
                "id": message_id,
                "published_at": utc_iso(published),
                "text": body,
                "images": image_urls,
            }
            if self.allow_public_chat:
                item.update(role=role, **metadata(message))
            if "reply_context" in message:
                reply = message["reply_context"]
                if (not isinstance(reply, dict) or not {"text", "images", "published_at"}.issubset(reply)
                        or set(reply) - {"text", "images", "published_at"} - metadata_fields):
                    raise ValidationError("reply_context: expected bounded public quote fields")
                text = reply["text"]
                if (not isinstance(text, str) or len(text) > MAX_REPLY_TEXT
                        or any(ord(character) < 32 and character not in "\n\r\t" for character in text)):
                    raise ValidationError("reply_context: invalid plain text")
                try:
                    text.encode("utf-8")
                except UnicodeError as exc:
                    raise ValidationError("reply_context: invalid Unicode") from exc
                if not isinstance(reply["images"], list) or len(reply["images"]) > 1:
                    raise ValidationError("reply_context: expected at most one image")
                reply_images = [self._image(url) for url in reply["images"]]
                if not text.strip() and not reply_images:
                    raise ValidationError("reply_context: empty quote")
                reply_time = reply["published_at"]
                if reply_time is not None:
                    reply_time = parse_time(reply_time, "reply_context.published_at")
                    if reply_time > published + FUTURE_TOLERANCE:
                        raise ValidationError("reply_context: quote cannot follow the teacher reply")
                    reply_time = utc_iso(reply_time)
                item["reply_context"] = {"text": text, "images": reply_images, "published_at": reply_time}
                item["reply_context"].update(metadata(reply))
            validated.append(item)
        # Validate the entire batch before changing the feed or its freshness.
        with self.lock:
            added = 0
            for message in validated:
                if message["id"] not in self.messages:
                    self.messages[message["id"]] = message
                    added += 1
                elif self.allow_reply_context:
                    # A quote may resolve after the teacher's own text. Update
                    # the existing row without duplicating or reordering it.
                    self.messages[message["id"]] = message
            ordered = sorted(self.messages.values(), key=lambda item: (item["published_at"], item["id"]))
            self.messages = {message["id"]: message for message in ordered[-self.retention_limit:]}
            self.last_contact = now
            self.source_ready = payload["source_ready"]
            if self.source_ready:
                self.last_seen = now
                self.observed_at = observed
            return {"accepted": added, "duplicates": len(validated) - added, "retained": len(self.messages)}

    def _status_locked(self) -> dict:
        now = self.clock()
        age = None if self.last_seen is None else max(0, now - self.last_seen)
        source_age = None if self.observed_at is None else max(0, now - self.observed_at)
        fresh = self.source_ready and age is not None and age <= FRESH_SECONDS and source_age <= FRESH_SECONDS
        return {
            "component": COMPONENT,
            "schema": API_SCHEMA,
            "config_fingerprint": self.fingerprint,
            "identity_mode": self.config.get("identity_mode", NATIVE_IDENTITY_MODE),
            "configured": self.configured,
            "teacher_name": self.config["teacher_name"],
            "freshness": "waiting" if age is None else "fresh" if fresh else "stale",
            "source_ready": self.source_ready,
            "last_seen": None if self.last_seen is None else utc_iso(self.last_seen),
            "last_contact": None if self.last_contact is None else utc_iso(self.last_contact),
            "source_observed_at": None if self.observed_at is None else utc_iso(self.observed_at),
            "age_seconds": None if age is None else round(age, 1),
            "source_age_seconds": None if source_age is None else round(source_age, 1),
            "fresh_seconds": FRESH_SECONDS,
            "message_count": len(self.messages),
            "retention_limit": self.retention_limit,
            "server_time": utc_iso(now),
        }

    def status(self) -> dict:
        with self.lock:
            return self._status_locked()

    def snapshot(self) -> dict:
        with self.lock:
            return {"status": self._status_locked(), "messages": list(self.messages.values())}


class TeacherServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, config: dict, *, port: int | None = None, clock=time.time):
        self.feed = TeacherFeed(config, clock=clock)
        # A port override is used by isolated smoke tests; the bind address is fixed.
        super().__init__((HOST, config["port"] if port is None else port), TeacherHandler)


class TeacherHandler(BaseHTTPRequestHandler):
    server_version = "TeacherFeed/1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format, *args) -> None:
        # Do not log request payloads, query strings, pairing headers or source data.
        pass

    @property
    def own_origin(self) -> str:
        return f"http://{HOST}:{self.server.server_port}"

    def _check_host(self) -> bool:
        return self.headers.get_all("Host", []) == [f"{HOST}:{self.server.server_port}"]

    def _origin(self, *, write: bool) -> str | None:
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            raise ValidationError("multiple origins are not accepted")
        origin = origins[0] if origins else None
        if origin is None:
            return None  # Native companion clients still need the secret write header.
        if write:
            if origin not in self.server.feed.config.get("extension_origins", []):
                raise ValidationError("this companion extension is not paired")
        elif origin != self.own_origin:
            raise ValidationError("feed reads must use the local component origin")
        return origin

    def _reply(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", cors=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        image_sources = " ".join(f"https://{hostname}" for hostname in self.server.feed.config.get("image_hosts", []))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            f"img-src 'self' {image_sources}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Access-Control-Allow-Headers", f"Content-Type, {TOKEN_HEADER}")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict, cors=None):
        self._reply(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), cors=cors)

    def _reject(self, status: int, error: str):
        self._json(status, {"error": error})

    def do_GET(self):
        if not self._check_host():
            return self._reject(421, "unexpected local host")
        try:
            self._origin(write=False)
        except ValidationError as exc:
            return self._reject(403, str(exc))
        if "?" in self.path or "#" in self.path:
            return self._reject(404, "not found")
        if self.path == "/api/status":
            return self._json(200, self.server.feed.status())
        if self.path == "/api/messages":
            return self._json(200, self.server.feed.snapshot())
        static_files = {
            "/": ("teacher.html", "text/html; charset=utf-8"),
            "/teacher.html": ("teacher.html", "text/html; charset=utf-8"),
            "/teacher.css": ("teacher.css", "text/css; charset=utf-8"),
            "/teacher.js": ("teacher.js", "text/javascript; charset=utf-8"),
        }
        if self.path not in static_files:
            return self._reject(404, "not found")
        filename, mime_type = static_files[self.path]
        return self._reply(200, (WEB_DIR / filename).read_bytes(), mime_type)

    def do_OPTIONS(self):
        if not self._check_host():
            return self._reject(421, "unexpected local host")
        if self.path != "/api/teacher-messages":
            return self._reject(404, "not found")
        try:
            origin = self._origin(write=True)
            requested = {part.strip().lower() for part in self.headers.get("Access-Control-Request-Headers", "").split(",") if part.strip()}
            if origin is None or self.headers.get("Access-Control-Request-Method") != "POST":
                raise ValidationError("unsupported preflight")
            if not requested.issubset({"content-type", TOKEN_HEADER.lower()}):
                raise ValidationError("unsupported request headers")
        except ValidationError as exc:
            return self._reject(403, str(exc))
        return self._reply(204, b"", cors=origin)

    def do_POST(self):
        if not self._check_host():
            return self._reject(421, "unexpected local host")
        if self.path != "/api/teacher-messages":
            return self._reject(404, "not found")
        try:
            origin = self._origin(write=True)
        except ValidationError as exc:
            return self._reject(403, str(exc))
        tokens = self.headers.get_all(TOKEN_HEADER, [])
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode("utf-8"), self.server.feed.config["token"].encode("utf-8")):
            return self._reject(401, "pairing token required")
        if self.headers.get("Transfer-Encoding") is not None:
            return self._reject(400, "transfer encoding is not supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            return self._reject(415, "application/json required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            return self._reject(411, "one content length is required")
        length = int(lengths[0])
        if not 1 <= length <= MAX_REQUEST_BYTES:
            return self._reject(413, "payload exceeds the local bridge limit")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self._reject(400, "incomplete request")
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
            result = self.server.feed.ingest(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError) as exc:
            return self._json(422, {"error": str(exc)}, cors=origin)
        except (TimeoutError, OSError):
            return self._reject(408, "request timed out")
        return self._json(200, result, cors=origin)


def reject_duplicate_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValidationError("duplicate JSON fields are not accepted")
        output[key] = value
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="同花顺老师消息本机只读组件")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--configure", action="store_true", help="保存明确的配对配置后退出")
    parser.add_argument("--room-id")
    parser.add_argument("--teacher-id")
    parser.add_argument("--teacher-name")
    parser.add_argument("--identity-mode", choices=(NATIVE_IDENTITY_MODE, DOM_IDENTITY_MODE))
    parser.add_argument("--extension-id", action="append")
    parser.add_argument("--image-host", action="append")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        config = load_or_create_config(args.config)
        if args.configure:
            for key in ("room_id", "teacher_id", "teacher_name", "identity_mode", "port"):
                if getattr(args, key) is not None:
                    config[key] = getattr(args, key)
            if args.extension_id is not None:
                config["extension_origins"] = [f"chrome-extension://{identifier}" for identifier in args.extension_id]
            if args.image_host is not None:
                config["image_hosts"] = args.image_host
            save_config(args.config, config)
            print(f"已保存本机配对配置：{args.config}")
            return
        if any(getattr(args, key) is not None for key in ("room_id", "teacher_id", "teacher_name", "identity_mode", "port", "extension_id", "image_host")):
            parser.error("修改配对配置请加 --configure")
        server = TeacherServer(config)
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        parser.exit(1, f"老师消息组件未启动：{exc}\n")
    print(f"老师消息组件已就绪：http://{HOST}:{server.server_port}/")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
