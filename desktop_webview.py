"""A raw, app-owned WebView2 login/source tab; no extension or pywebview API.

All methods and callbacks run on the owning WinForms UI thread. ``on_payload``
must be a short local consumer such as TeacherFeed.ingest, never network I/O.
The browser owns its normal website session in a dedicated WebView2 profile;
this module neither reads cookies/tokens nor stores website passwords.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from server import DOM_IDENTITY_MODE, DOM_ROOM_ID, DOM_TEACHER_ID, TeacherFeed, new_config, utc_iso


SOURCE_BASE = "https://tougu.zx093.cn/touguapp/webChatRoom/index.html"
ROUTE_TEACHER_ID = "VzPD.Y8fIGs_"
SOURCE_URL = SOURCE_BASE + "#/chatRoom?id=&teacherId=" + ROUTE_TEACHER_ID
LOGIN_URL = SOURCE_BASE + "#/?teacherId=" + ROUTE_TEACHER_ID
IMAGE_HOSTS = ("fileoss.zx093.com", "tgpub.zx093.com", "ynimg.zx093.cn")
MAX_MESSAGE_BYTES = 256 * 1024
MAX_SCRIPT_BYTES = 512 * 1024
NATIVE_TICK_INTERVAL_MS = 5000
NATIVE_TICK_EVENT = "ths-desktop-source-tick"
_SOURCE_EXECUTION_GATE = """const u=new URL(location.href);const q=new URLSearchParams(u.hash.split('?')[1]||'');
if(window.top!==window || u.origin!=='https://tougu.zx093.cn' || u.username || u.password ||
u.pathname!=='/touguapp/webChatRoom/index.html' || u.search ||
u.hash.split('?')[0]!=='#/chatRoom' || q.getAll('teacherId').length!==1 ||
q.get('teacherId')!=='VzPD.Y8fIGs_' || q.getAll('id').length>1 ||
[...q.keys()].some(k=>k!=='id'&&k!=='teacherId') ||
!(/^[A-Za-z0-9_.:-]{0,128}$/.test(q.get('id')||'')))return 'blocked';"""


def is_document_url(value: object) -> bool:
    """Permit only this official SPA document for top-level navigation."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    if any(ord(character) <= 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and parsed.hostname == "tougu.zx093.cn"
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                and parsed.path == "/touguapp/webChatRoom/index.html" and not parsed.query)
    except (ValueError, UnicodeError):
        return False


def is_source_url(value: object) -> bool:
    """Capture only the explicit teacher route, with no ambiguous parameters."""
    if not is_document_url(value):
        return False
    try:
        fragment = urlsplit(value).fragment
        route, separator, query = fragment.partition("?")
        if route != "/chatRoom" or not separator:
            return False
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
        if any(key not in ("id", "teacherId") for key, _ in pairs):
            return False
        if len({key for key, _ in pairs}) != len(pairs):
            return False
        parameters = dict(pairs)
        room = parameters.get("id", "")
        return parameters.get("teacherId") == ROUTE_TEACHER_ID and (
            not room or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", room) is not None
        )
    except (ValueError, UnicodeError):
        return False


def not_ready_payload() -> dict[str, Any]:
    return {"identity_mode": DOM_IDENTITY_MODE, "room_id": DOM_ROOM_ID, "teacher_id": DOM_TEACHER_ID,
            "source": "yuen-native", "observed_at": utc_iso(time.time()), "mode": "snapshot",
            "source_ready": False, "messages": []}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class PayloadGate:
    """Reuse the canonical feed validator; no page value becomes host code."""

    def __init__(self) -> None:
        config = new_config()
        config.update(identity_mode=DOM_IDENTITY_MODE, room_id=DOM_ROOM_ID, teacher_id=DOM_TEACHER_ID,
                      image_hosts=list(IMAGE_HOSTS))
        self._validator = TeacherFeed(config, allow_reply_context=True, allow_public_chat=True)

    def parse(self, raw: object) -> dict[str, Any]:
        if not isinstance(raw, str) or len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message size or type")
        try:
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise ValueError("message size")
            payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (UnicodeError, RecursionError) as error:
            raise ValueError("invalid message encoding or nesting") from error
        self._validator.ingest(payload)
        return payload


def parse_history_status(value: object) -> dict:
    fields = {"kind", "state", "pages", "loaded", "teachers", "has_more", "oldest"}
    states = {"loading", "idle", "exhausted", "unavailable", "error", "limit", "cancelled"}
    if not isinstance(value, dict) or set(value) - {"audit", "latest_state", "latest_sync", "view_mode"} != fields or value["kind"] != "history_status":
        raise ValueError("unsupported history status")
    if value["state"] not in states or type(value["has_more"]) is not bool:
        raise ValueError("invalid history state")
    if value.get("view_mode", "teacher") not in ("teacher", "all"):
        raise ValueError("invalid history view")
    for key, maximum in (("pages", 10000), ("loaded", 50000), ("teachers", 5000)):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise ValueError("invalid history count")
    oldest = value["oldest"]
    if oldest is not None and (not isinstance(oldest, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", oldest)):
        raise ValueError("invalid history boundary")
    if "latest_state" in value and value["latest_state"] not in ("idle", "loading", "error"):
        raise ValueError("invalid latest reconciliation state")
    latest = value.get("latest_sync")
    if latest is not None and (not isinstance(latest,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",latest)):
        raise ValueError("invalid latest reconciliation time")
    if "audit" in value:
        audit=value["audit"]
        counters={"total", "teacher", "projected", "duplicate", "non_teacher", "withdrawn", "private_reply",
                  "room_mismatch", "other_biz", "locked", "unsupported", "bad_date", "empty"}
        if not isinstance(audit, dict) or set(audit) != counters | {"first", "last"}:
            raise ValueError("invalid source audit")
        for name in counters:
            if type(audit[name]) is not int or not 0 <= audit[name] <= 50000:
                raise ValueError("invalid source audit count")
        for name in ("first", "last"):
            if audit[name] is not None and (not isinstance(audit[name], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", audit[name])):
                raise ValueError("invalid source audit date")
    return value


def persistent_webview_options(profile_path: str | Path) -> dict[str, Any]:
    """For callers that also need a persistent pywebview profile (not required here)."""
    path = Path(profile_path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return {"private_mode": False, "storage_path": str(path)}


def get_own_core(window: Any) -> Any:
    """Get the CoreWebView2 from an app-owned pywebview 6.2.1 WinForms window."""
    native = getattr(window, "native", None)
    if native is None or native.InvokeRequired:
        raise RuntimeError("必须在组件自己的 WinForms UI 线程获取浏览器。")
    renderer = getattr(native, "browser", None)
    control = getattr(renderer, "webview", None)
    core = getattr(control, "CoreWebView2", None)
    if core is None:
        raise RuntimeError("组件自己的 WebView2 尚未初始化。")
    return core


def _load_dotnet() -> dict[str, Any]:
    # pywebview has already selected the CLR and loaded assemblies in the app.
    # Importing this module alone creates neither a UI nor a browser process.
    import clr
    from webview.util import interop_dll_path

    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.WinForms.dll"))
    for architecture in ("win-arm64", "win-x64", "win-x86"):
        candidate = interop_dll_path(architecture)
        if candidate not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + candidate
    from System import Action, String
    from System.Drawing import Color
    from System.Threading.Tasks import Task
    from System.Windows.Forms import DockStyle, Timer
    from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

    return {"Action": Action, "String": String, "Task": Task, "Color": Color, "DockStyle": DockStyle,
            "CreationProperties": CoreWebView2CreationProperties, "WebView2": WebView2, "Timer": Timer}


class DesktopSourceBinding:
    def __init__(self, parent_control: Any, profile_path: str | Path, script: str,
                 on_payload: Callable[[dict], Any], on_status: Callable[[dict], Any] | None = None,
                 on_history: Callable[[dict], Any] | None = None) -> None:
        if parent_control is None or parent_control.IsDisposed or parent_control.InvokeRequired:
            raise RuntimeError("请在组件自己的 WinForms UI 线程创建约牛登录页。")
        if not isinstance(script, str) or not script.strip() or len(script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise ValueError("desktop observer script is missing or exceeds its size limit")
        if not callable(on_payload):
            raise TypeError("on_payload must be a local callable")
        self._dotnet = _load_dotnet()
        self._on_payload = on_payload
        self._on_status = on_status
        self._on_history = on_history
        self._script = script
        self._gate = PayloadGate()
        self._subscriptions: list[tuple[Any, str, Any]] = []
        self._disposed = False
        self._initialized = False
        self._document_ready = False
        self._navigation_pending = False
        self._epoch = 0
        self._injection_epoch: int | None = None
        self._payload_epoch: int | None = None
        self._pending_url: str | None = None
        self._last_status: dict[str, Any] | None = None
        self._core = None
        self._native_tick_timer = None
        self._native_tick_task = None
        self._view_mode = "teacher"
        self.profile_path = Path(profile_path).expanduser().resolve()
        self.profile_path.mkdir(parents=True, exist_ok=True)
        self.control = self._dotnet["WebView2"]()
        properties = self._dotnet["CreationProperties"]()
        properties.UserDataFolder = str(self.profile_path)
        properties.IsInPrivateModeEnabled = False
        properties.AdditionalBrowserArguments = "--disable-features=ElasticOverscroll"
        self.control.CreationProperties = properties
        self.control.Dock = self._dotnet["DockStyle"].Fill
        self.control.DefaultBackgroundColor = self._dotnet["Color"].FromArgb(255, 241, 234, 217)
        parent_control.Controls.Add(self.control)
        self._subscribe(self.control, "CoreWebView2InitializationCompleted", self._initialized_event)
        self._status("loading", "webview_initializing")
        self._initialization_task = self.control.EnsureCoreWebView2Async(None)

    @property
    def core(self) -> Any:
        """Own raw CoreWebView2, or None before initialization; use on UI thread."""
        return self._core

    def _assert_ui(self) -> None:
        if self._disposed or self.control.IsDisposed:
            raise RuntimeError("约牛登录页已经关闭。")
        if self.control.InvokeRequired:
            raise RuntimeError("约牛浏览器操作必须在组件自己的 UI 线程执行。")

    def _subscribe(self, owner: Any, event_name: str, handler: Any) -> None:
        event = getattr(owner, event_name)
        event += handler
        self._subscriptions.append((owner, event_name, handler))

    def _status(self, state: str, reason: str, **metadata: Any) -> None:
        status = {"state": state, "reason": reason, **metadata}
        if status == self._last_status:
            return
        self._last_status = status
        if self._on_status is not None:
            try:
                self._on_status(status)
            except Exception:
                pass  # A host status label must not interrupt browser event delivery.

    def _deliver(self, payload: dict) -> bool:
        try:
            self._on_payload(payload)
            return True
        except Exception as error:
            self._status("error", "local_feed_rejected", error_type=type(error).__name__)
            return False

    def _not_ready(self, reason: str) -> None:
        self._deliver(not_ready_payload())
        self._status("not_ready", reason, source_ready=False)

    def _current_source(self) -> str:
        return str(self._core.Source or "") if self._core is not None else ""

    def navigate_to_source(self, url: str = SOURCE_URL) -> None:
        self._assert_ui()
        if not is_document_url(url):
            raise ValueError("只允许在程序内打开指定约牛官方聊天登录页面。")
        self._pending_url = url
        self._not_ready("opening_source")
        if self._initialized:
            self._core.Navigate(url)

    def request_history(self, action: str = "older") -> None:
        self._assert_ui()
        if action not in ("older", "cancel"):
            raise ValueError("unsupported history action")
        if not self._initialized or not self._document_ready or not is_source_url(self._current_source()):
            return
        script = ("(() => {" + _SOURCE_EXECUTION_GATE
                  + "\nwindow.__THS_TEACHER_SOURCE__?.requestHistory(" + json.dumps(action) + ");})()")
        self._history_task = self._core.ExecuteScriptAsync(script)

    def set_view_mode(self, mode: str) -> None:
        self._assert_ui()
        if mode not in ("teacher", "all"):
            raise ValueError("unsupported public chat view")
        self._view_mode = mode
        if not self._initialized or not self._document_ready or not is_source_url(self._current_source()):
            return
        script = ("(() => {" + _SOURCE_EXECUTION_GATE
                  + "\nwindow.__THS_TEACHER_SOURCE__?.setViewMode(" + json.dumps(mode) + ");})()")
        self._view_task = self._core.ExecuteScriptAsync(script)

    def _initialized_event(self, _sender: Any, args: Any) -> None:
        if self._disposed:
            return
        if not args.IsSuccess:
            self._not_ready("webview_initialization_failed")
            self._status("error", "webview_initialization_failed")
            return
        try:
            self._core = self.control.CoreWebView2
            settings = self._core.Settings
            settings.IsWebMessageEnabled = True
            settings.AreHostObjectsAllowed = False
            settings.AreDevToolsEnabled = False
            settings.AreDefaultContextMenusEnabled = False
            settings.IsStatusBarEnabled = False
            for name in ("IsPasswordAutosaveEnabled", "IsGeneralAutofillEnabled"):
                if hasattr(settings, name):
                    setattr(settings, name, False)
            self._subscribe(self._core, "NavigationStarting", self._navigation_starting)
            self._subscribe(self._core, "NavigationCompleted", self._navigation_completed)
            self._subscribe(self._core, "SourceChanged", self._source_changed)
            self._subscribe(self._core, "WebMessageReceived", self._message_received)
            self._subscribe(self._core, "NewWindowRequested", self._new_window_requested)
            self._subscribe(self._core, "DownloadStarting", self._download_starting)
            # Chromium may throttle a hidden tab's JavaScript timers. This own
            # UI-thread timer requests a fresh DOM scan even while the source
            # tab is hidden; the observer still verifies login and teacher DOM.
            self._native_tick_timer = self._dotnet["Timer"]()
            self._native_tick_timer.Interval = NATIVE_TICK_INTERVAL_MS
            self._subscribe(self._native_tick_timer, "Tick", self._native_tick)
            self._native_tick_timer.Start()
            self._initialized = True
            self._status("ready", "webview_initialized", persistent_profile=True)
            if self._pending_url:
                self._core.Navigate(self._pending_url)
        except Exception as error:
            self._not_ready("source_initialization_failed")
            self._status("error", "source_initialization_failed", error_type=type(error).__name__)

    def _navigation_starting(self, _sender: Any, args: Any) -> None:
        if self._disposed:
            return
        self._epoch += 1
        self._injection_epoch = None
        self._document_ready = False
        self._navigation_pending = True
        if not is_document_url(str(args.Uri)):
            args.Cancel = True
            self._navigation_pending = False
            self._not_ready("top_level_navigation_blocked")
            return
        self._not_ready("navigation_started")
        self._status("loading", "source_loading")

    def _navigation_completed(self, _sender: Any, args: Any) -> None:
        if self._disposed:
            return
        self._navigation_pending = False
        self._document_ready = bool(args.IsSuccess)
        if not args.IsSuccess:
            self._not_ready("source_navigation_failed")
        elif is_source_url(self._current_source()):
            self._inject_if_allowed()
        else:
            self._not_ready("login_or_other_route")

    def _source_changed(self, _sender: Any, args: Any) -> None:
        if self._disposed:
            return
        if not is_source_url(self._current_source()):
            self._injection_epoch = None
            self._not_ready("left_teacher_route")
            return
        # Login is a hash route in this SPA: returning to chatRoom does not have
        # to produce NavigationCompleted. Same-document SourceChanged is enough.
        if not args.IsNewDocument:
            self._navigation_pending = False
            self._document_ready = True
            self._epoch += 1
            self._injection_epoch = None
            self._inject_if_allowed()

    def _inject_if_allowed(self) -> None:
        if (self._disposed or not self._document_ready or self._navigation_pending
                or not is_source_url(self._current_source()) or self._injection_epoch == self._epoch):
            return
        epoch = self._epoch
        self._injection_epoch = epoch
        # A second gate runs inside the top-level document at execution time.
        # There is no AddHostObjectToScript or generic Python/pywebview JS API.
        script = ("(() => {" + _SOURCE_EXECUTION_GATE + "\nwindow.__THS_CHAT_VIEW_MODE__="
                  + json.dumps(self._view_mode) + ";\n" + self._script + "\nreturn 'injected';})()")
        self._status("loading", "binding_teacher_observer")
        try:
            task = self._core.ExecuteScriptAsync(script)
            action, task_type, string_type = (self._dotnet[name] for name in ("Action", "Task", "String"))

            def completed(finished: Any) -> None:
                if self._disposed:
                    return

                def on_ui() -> None:
                    if (self._disposed or epoch != self._epoch or not is_source_url(self._current_source())):
                        return
                    if finished.IsFaulted or finished.IsCanceled:
                        self._injection_epoch = None
                        self._not_ready("observer_injection_failed")
                        return
                    try:
                        injected = json.loads(str(finished.Result)) == "injected"
                    except (ValueError, TypeError):
                        injected = False
                    if injected and self._payload_epoch != epoch:
                        self._status("ready", "observer_bound", source_ready=False)
                    elif not injected:
                        self._not_ready("observer_source_changed")

                try:
                    self.control.BeginInvoke(action(on_ui))
                except Exception:
                    pass  # The owner may have disposed the control while executing.

            self._script_task = task.ContinueWith(action[task_type[string_type]](completed))
        except Exception as error:
            self._injection_epoch = None
            self._not_ready("observer_injection_failed")
            self._status("error", "observer_injection_failed", error_type=type(error).__name__)

    def _native_tick(self, _sender: Any = None, _args: Any = None) -> None:
        """Nonblocking periodic scan of this owned control, never browser state."""
        if self._disposed:
            return
        try:
            if (not self._initialized or not self._document_ready or self._navigation_pending
                    or self._injection_epoch != self._epoch or not is_source_url(self._current_source())):
                return
            previous = self._native_tick_task
            if previous is not None:
                if not previous.IsCompleted:
                    return  # Never queue overlapping renderer requests.
                if previous.IsFaulted:
                    _observed_exception = previous.Exception
                    self._not_ready("native_source_tick_failed")
                self._native_tick_task = None
            script = ("(() => {" + _SOURCE_EXECUTION_GATE + "\nwindow.dispatchEvent(new Event("
                      + json.dumps(NATIVE_TICK_EVENT) + "));return 'ticked';})()")
            self._native_tick_task = self._core.ExecuteScriptAsync(script)
        except Exception:
            self._native_tick_task = None
            self._not_ready("native_source_tick_failed")

    def _message_received(self, _sender: Any, args: Any) -> None:
        if self._disposed:
            return
        if (not self._document_ready or self._navigation_pending or self._injection_epoch != self._epoch
                or not is_source_url(str(args.Source)) or not is_source_url(self._current_source())):
            return
        try:
            # The observer deliberately posts a JSON string, never host objects.
            raw = str(args.TryGetWebMessageAsString())
            if len(raw) > MAX_MESSAGE_BYTES:
                raise ValueError("oversized source status")
            value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if isinstance(value, dict) and value.get("kind") == "history_status":
                history = parse_history_status(value)
                if self._native_tick_timer is not None:
                    self._native_tick_timer.Interval = 1000 if history["state"] == "loading" or history.get("latest_state") == "loading" else NATIVE_TICK_INTERVAL_MS
                if self._on_history is not None:
                    self._on_history(history)
                return
            payload = self._gate.parse(raw)
        except Exception:
            self._not_ready("invalid_source_message")
            return
        if self._deliver(payload):
            self._payload_epoch = self._epoch
            ready = payload["source_ready"]
            self._status("receiving" if ready else "not_ready", "teacher_feed" if ready else "login_required",
                         source_ready=ready, message_count=len(payload["messages"]))

    def _new_window_requested(self, _sender: Any, args: Any) -> None:
        args.Handled = True
        self._status("not_ready", "external_window_blocked")

    @staticmethod
    def _download_starting(_sender: Any, args: Any) -> None:
        args.Cancel = True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._assert_ui()
        if self._native_tick_timer is not None:
            self._native_tick_timer.Stop()
        self._not_ready("source_closed")
        self._disposed = True
        for owner, name, handler in reversed(self._subscriptions):
            try:
                event = getattr(owner, name)
                event -= handler
            except Exception:
                pass
        self._subscriptions.clear()
        if self._native_tick_timer is not None:
            self._native_tick_timer.Dispose()
            self._native_tick_timer = None
        self.control.Dispose()
        self._core = None


def create_source_control(parent_control: Any, profile_path: str | Path, script: str,
                          on_payload: Callable[[dict], Any],
                          on_status: Callable[[dict], Any] | None = None,
                          on_history: Callable[[dict], Any] | None = None) -> DesktopSourceBinding:
    """Create one raw WebView2 tab; call binding.navigate_to_source() explicitly."""
    return DesktopSourceBinding(parent_control, profile_path, script, on_payload, on_status, on_history)
