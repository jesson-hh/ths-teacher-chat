"""Keep our own form inside the explicitly named THS board while it is active.

The installation configuration contains selectors, never live HWNDs. This
module only reads that configuration and THS metadata; every window mutation
is restricted to the component's own WinForms form.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import ntpath
import os
from pathlib import Path
from typing import Any, Callable

from win32_embed import EmbeddingError, Win32Host


CONFIG_FIELDS = {"schema", "enabled", "board_name", "ths_exe", "canvas_class", "control_id", "top_inset"}
WS_VISIBLE = 0x10000000


def _path_key(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration field")
        result[key] = value
    return result


def read_board_config(path: Path) -> dict[str, Any]:
    """Read only the installer's selector contract, with no handle fallback."""
    if path.stat().st_size > 16_384:
        raise ValueError("board configuration exceeds its size limit")
    config = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    if not isinstance(config, dict) or set(config) != CONFIG_FIELDS or config.get("schema") != 1:
        raise ValueError("unsupported board configuration")
    if not isinstance(config["enabled"], bool):
        raise ValueError("enabled must be a boolean")
    for key, maximum in (("board_name", 80), ("canvas_class", 128)):
        if (not isinstance(config[key], str) or not 1 <= len(config[key]) <= maximum
                or any(ord(character) < 32 for character in config[key])):
            raise ValueError("invalid board selector")
    if (not isinstance(config["ths_exe"], str) or not ntpath.isabs(config["ths_exe"])
            or ntpath.basename(config["ths_exe"]).lower() != "hexin.exe"):
        raise ValueError("unsupported THS executable")
    control_id = config["control_id"]
    if not isinstance(control_id, int) or isinstance(control_id, bool) or not 1 <= control_id <= 0x7FFFFFFF:
        raise ValueError("invalid canvas control ID")
    inset = config["top_inset"]
    if (not isinstance(inset, (int, float)) or isinstance(inset, bool)
            or not math.isfinite(inset) or not 0 <= inset <= 500):
        raise ValueError("invalid board top inset")
    return config


class BoardAttachment:
    """UI-thread controller. No UI is touched until the caller invokes start()."""

    def __init__(self, form: Any, config_path: str | Path,
                 on_status: Callable[[dict], Any] | None = None) -> None:
        if form is None or form.IsDisposed or form.InvokeRequired:
            raise RuntimeError("请在组件自己的 UI 线程绑定老师消息看板。")
        self.form = form
        self.config_path = Path(config_path).expanduser().resolve()
        self.on_status = on_status
        self._host = Win32Host()
        self._timer = None
        self._disposed = False
        self._running = False
        self._attachment = None
        self._fingerprint = None
        self._selected_config = None
        self._last_geometry = None
        self._last_status = None
        self._tick_busy = False
        self._bind_metadata_api()

    def _bind_metadata_api(self) -> None:
        user32 = self._host.user32
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsIconic.argtypes, user32.IsIconic.restype = [wintypes.HWND], wintypes.BOOL
        self._get_dpi = getattr(user32, "GetDpiForWindow", None)
        if self._get_dpi is not None:
            self._get_dpi.argtypes, self._get_dpi.restype = [wintypes.HWND], wintypes.UINT

    def _assert_ui(self) -> None:
        if self._disposed or self.form.IsDisposed:
            raise RuntimeError("老师消息组件已经关闭。")
        if self.form.InvokeRequired:
            raise RuntimeError("看板窗口操作必须在组件自己的 UI 线程执行。")

    def _child(self) -> int:
        return int(self.form.Handle.ToInt64())

    def _caption(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(2048)
        self._host.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _board_active(self, root_hwnd: int, config: dict) -> bool:
        return (self._host.is_window(root_hwnd)
                and bool(self._host.user32.IsWindowVisible(root_hwnd))
                and not bool(self._host.user32.IsIconic(root_hwnd))
                and self._caption(root_hwnd).endswith(" - " + config["board_name"]))

    def _hide_own(self) -> None:
        if not self.form.IsDisposed and self.form.Visible:
            self.form.Hide()

    def _report(self, state: str, reason: str, *, board_visible: bool = False,
                verified_parent: bool = False, width: int = 0, height: int = 0, **extra: Any) -> None:
        parent = self._fingerprint["hwnd"] if self._fingerprint else None
        root = self._fingerprint["root_hwnd"] if self._fingerprint else None
        child = self._child() if not self.form.IsDisposed else None
        status = {"state": state, "reason": reason, "parent": parent, "child": child,
                  "parent_hwnd": parent, "child_hwnd": child, "root_hwnd": root,
                  "width": width, "height": height, "verified_parent": verified_parent,
                  "board_visible": board_visible, **extra}
        if status == self._last_status:
            return
        self._last_status = status
        if self.on_status:
            try:
                self.on_status(status)
            except Exception:
                pass

    def _detach_hidden(self) -> None:
        self._hide_own()
        state = self._attachment
        if state is not None:
            child = state["child_hwnd"]
            if self._host.is_window(child):
                original_parent = state["original_parent"]
                # Never restore a stale foreign parent on board invalidation.
                if original_parent and (not self._host.is_window(original_parent)
                                        or self._host._pid_tid(original_parent)[0] != os.getpid()):
                    original_parent = 0
                style = state["original_style"] & ~WS_VISIBLE
                if not original_parent and style & self._host.WS_CHILD:
                    style = (style & ~self._host.WS_CHILD) | self._host.WS_POPUP
                self._host.detach(child, original_parent, style, state["original_exstyle"],
                                  state["original_rect"])
            self._hide_own()
        self._attachment = None
        self._fingerprint = None
        self._selected_config = None
        self._last_geometry = None

    def _find_active_canvas(self, config: dict) -> tuple[dict | None, str]:
        matches = []
        found_root = False
        for root in self._host.inspect_ths():
            if _path_key(root.get("path", "")) != _path_key(config["ths_exe"]):
                continue
            found_root = True
            root_hwnd = int(root["root_hwnd"])
            if not self._board_active(root_hwnd, config):
                continue
            for candidate in root.get("candidates", []):
                if (candidate.get("class_name") != config["canvas_class"]
                        or candidate.get("control_id") != config["control_id"]):
                    continue
                try:
                    fingerprint = self._host.validate_ths_target(candidate["hwnd"], expected_root=root_hwnd)
                except (EmbeddingError, OSError):
                    continue
                if (_path_key(fingerprint["path"]) == _path_key(config["ths_exe"])
                        and self._board_active(root_hwnd, config)):
                    matches.append(fingerprint)
        if len(matches) == 1:
            return matches[0], "canvas_found"
        if matches:
            return None, "ambiguous_teacher_boards"
        return None, "teacher_board_not_active" if found_root else "ths_not_running"

    def _place_own(self, config: dict) -> tuple[int, int, int]:
        child = self._child()
        parent = self._fingerprint["hwnd"]
        self._host._require_owned_window(child)
        if self._host.get_parent(child) != parent or not self._host.is_child(parent, child):
            raise EmbeddingError("老师消息组件的父子关系已经变化。")
        width, full_height = self._host.get_client_size(parent)
        dpi = int(self._get_dpi(parent)) if self._get_dpi is not None else 96
        dpi = dpi if dpi > 0 else 96
        inset = min(max(0, int(round(config["top_inset"] * dpi / 96))), max(0, full_height))
        height = max(0, full_height - inset)
        geometry = (child, parent, width, height, inset)
        if geometry != self._last_geometry:
            if not self._host.user32.SetWindowPos(
                child, None, 0, inset, max(0, width), height,
                self._host.SWP_NOZORDER | self._host.SWP_NOACTIVATE,
            ):
                raise self._host._failure("调整老师消息看板范围")
            self._last_geometry = geometry
        return width, height, inset

    def _reconcile(self) -> None:
        config = read_board_config(self.config_path)
        if not config["enabled"]:
            self._detach_hidden()
            self._report("disabled", "board_disabled")
            return
        if self._attachment is not None:
            child = self._child()
            if (config != self._selected_config or not self._host.is_target_valid(self._fingerprint)
                    or self._attachment["child_hwnd"] != child
                    or self._host.get_parent(child) != self._fingerprint["hwnd"]
                    or not self._host.is_child(self._fingerprint["hwnd"], child)):
                self._detach_hidden()
                self._report("waiting", "canvas_identity_changed")
                return  # Rediscover fresh handles on the next tick.
            if not self._board_active(self._fingerprint["root_hwnd"], config):
                self._hide_own()
                self._report("hidden", "other_board_active", verified_parent=True)
                return
        else:
            self._hide_own()
            fingerprint, reason = self._find_active_canvas(config)
            if fingerprint is None:
                self._report("waiting", reason)
                return
            self._fingerprint = fingerprint
            # Capture the original style while hidden, so a later detach cannot
            # accidentally restore a visible desktop window before Hide runs.
            self._attachment = self._host.attach(self._child(), fingerprint["hwnd"], fingerprint)
            self._selected_config = config
            self._last_geometry = None
        width, height, inset = self._place_own(config)
        if width <= 0 or height <= 0:
            self._hide_own()
            self._report("hidden", "canvas_collapsed", verified_parent=True, width=width, height=height)
            return
        # Recheck after layout: never intentionally show over another board.
        if not self._board_active(self._fingerprint["root_hwnd"], config):
            self._hide_own()
            self._report("hidden", "other_board_active", verified_parent=True)
            return
        if not self.form.Visible:
            self.form.Show()
        if (self._host.get_parent(self._child()) != self._fingerprint["hwnd"]
                or not self._host.is_child(self._fingerprint["hwnd"], self._child())):
            raise EmbeddingError("显示老师组件后父子关系校验失败。")
        self._report("attached", "teacher_board_active", board_visible=True, verified_parent=True,
                     width=width, height=height, top_inset_pixels=inset)

    def _tick(self, *_args: Any) -> None:
        if self._disposed or not self._running or self._tick_busy:
            return
        self._tick_busy = True
        try:
            self._reconcile()
        except Exception as error:
            self._hide_own()
            try:
                self._detach_hidden()
            except Exception:
                pass  # Keep the hidden attachment state so a later tick retries.
            self._report("waiting", "board_unavailable", error_type=type(error).__name__)
        finally:
            self._tick_busy = False

    def start(self) -> None:
        self._assert_ui()
        if self._running:
            return
        from System.Windows.Forms import Timer

        self._hide_own()
        self._running = True
        self._timer = Timer()
        self._timer.Interval = 500
        self._timer.Tick += self._tick
        self._timer.Start()
        self._tick()

    def dispose(self) -> None:
        if self._disposed:
            return
        if self.form.InvokeRequired:
            raise RuntimeError("请在组件自己的 UI 线程解除看板绑定。")
        self._running = False
        if self._timer is not None:
            self._timer.Stop()
            self._timer.Tick -= self._tick
            self._timer.Dispose()
            self._timer = None
        try:
            self._detach_hidden()
            self._report("closed", "board_attachment_closed")
        finally:
            self._disposed = True
