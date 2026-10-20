"""Invisible, self-owned native smoke test; never attaches to a THS window.

Run directly with Python. The parent lives in a separate Python process and
never calls ShowWindow. A successful run emits one bounded JSON evidence line.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from win32_embed import EmbeddingError, Win32Host


class NativeWindows:
    def __init__(self) -> None:
        self.host = Win32Host()
        self.u = self.host.user32
        self.k = self.host.kernel32
        self.wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                             wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT), ("lpfnWndProc", self.wndproc_type),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
            ]

        self.WNDCLASSW = WNDCLASSW
        self.k.GetModuleHandleW.argtypes, self.k.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
        self.u.RegisterClassW.argtypes, self.u.RegisterClassW.restype = [ctypes.POINTER(WNDCLASSW)], wintypes.WORD
        self.u.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.u.UnregisterClassW.restype = wintypes.BOOL
        self.u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        self.u.CreateWindowExW.restype = wintypes.HWND
        self.u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.u.DefWindowProcW.restype = ctypes.c_ssize_t
        self.u.DestroyWindow.argtypes, self.u.DestroyWindow.restype = [wintypes.HWND], wintypes.BOOL
        self.u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.u.PostMessageW.restype = wintypes.BOOL
        self.u.PostQuitMessage.argtypes, self.u.PostQuitMessage.restype = [ctypes.c_int], None
        self.u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                      wintypes.UINT, wintypes.UINT]
        self.u.PeekMessageW.restype = wintypes.BOOL
        self.u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.u.GetMessageW.restype = ctypes.c_int
        self.u.TranslateMessage.argtypes, self.u.TranslateMessage.restype = [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL
        self.u.DispatchMessageW.argtypes, self.u.DispatchMessageW.restype = [ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t
        self.instance = self.k.GetModuleHandleW(None)
        self.class_name = f"CodexHiddenEmbedSmoke_{os.getpid()}"
        self.callback = None
        self.windows: list[int] = []

    def register(self, handler=None) -> None:
        @self.wndproc_type
        def procedure(hwnd, message, wparam, lparam):
            if handler:
                result = handler(hwnd, message, wparam, lparam)
                if result is not None:
                    return result
            return self.u.DefWindowProcW(hwnd, message, wparam, lparam)

        self.callback = procedure
        window_class = self.WNDCLASSW()
        window_class.lpfnWndProc = procedure
        window_class.hInstance = self.instance
        window_class.lpszClassName = self.class_name
        if not self.u.RegisterClassW(ctypes.byref(window_class)):
            raise self.host._failure("创建不可见测试窗口类")

    def create(self, parent=0, child=False) -> int:
        style = Win32Host.WS_CHILD if child else (
            Win32Host.WS_POPUP | Win32Host.WS_CAPTION | Win32Host.WS_THICKFRAME | Win32Host.WS_SYSMENU
        )
        hwnd = self.u.CreateWindowExW(Win32Host.WS_EX_APPWINDOW if not child else 0,
                                     self.class_name, "Codex invisible embedding self-test", style,
                                     0, 0, 480, 320, parent or None, None, self.instance, None)
        if not hwnd:
            raise self.host._failure("创建不可见测试窗口")
        self.windows.append(int(hwnd))
        assert not self.u.IsWindowVisible(hwnd), "Self-test must never show a window"
        return int(hwnd)

    def pump(self) -> None:
        message = wintypes.MSG()
        while self.u.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            self.u.TranslateMessage(ctypes.byref(message))
            self.u.DispatchMessageW(ctypes.byref(message))

    def close(self) -> None:
        for hwnd in reversed(self.windows):
            if self.host.is_window(hwnd):
                self.u.DestroyWindow(hwnd)
        self.pump()
        self.u.UnregisterClassW(self.class_name, self.instance)


def parent_process() -> None:
    native = NativeWindows()
    pending: queue.Queue[dict] = queue.Queue()
    root = 0
    slot = 0

    def handler(hwnd, message, _wparam, _lparam):
        if message == 0x8001:
            command = pending.get_nowait()
            if command["cmd"] == "resize":
                assert native.u.SetWindowPos(slot, None, 0, 0, command["width"], command["height"],
                                             Win32Host.SWP_NOZORDER | Win32Host.SWP_NOACTIVATE)
                print(json.dumps({"event": "resized", "client_size": native.host.get_client_size(slot)}), flush=True)
            elif command["cmd"] == "destroy":
                assert native.u.DestroyWindow(root)
                print(json.dumps({"event": "destroyed"}), flush=True)
                native.u.PostQuitMessage(0)
            return 0
        return None

    native.register(handler)
    root = native.create()
    slot = native.create(parent=root, child=True)
    print(json.dumps({"event": "ready", "root_hwnd": root, "slot_hwnd": slot,
                      "pid": os.getpid(), "client_size": native.host.get_client_size(slot)}), flush=True)

    def receive() -> None:
        for line in sys.stdin:
            pending.put(json.loads(line))
            native.u.PostMessageW(root, 0x8001, 0, 0)

    threading.Thread(target=receive, daemon=True).start()
    message = wintypes.MSG()
    while native.u.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        native.u.TranslateMessage(ctypes.byref(message))
        native.u.DispatchMessageW(ctypes.byref(message))
    native.close()


class Parent:
    def __init__(self, native: NativeWindows) -> None:
        self.native = native
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--parent"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        self.lines: queue.Queue[str] = queue.Queue()

        def consume() -> None:
            for line in self.process.stdout:
                self.lines.put(line)

        threading.Thread(target=consume, daemon=True).start()

    def read(self) -> dict:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.native.pump()
            try:
                return json.loads(self.lines.get(timeout=0.02))
            except queue.Empty:
                if self.process.poll() is not None:
                    raise AssertionError(self.process.stderr.read() or "Hidden parent exited without a response")
        raise TimeoutError("Hidden native parent did not respond")

    def send(self, command: dict) -> dict:
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        return self.read()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)


def assert_rejected(action) -> None:
    try:
        action()
    except EmbeddingError:
        return
    raise AssertionError("Expected target/ownership validation to reject this operation")


def smoke_test() -> dict:
    native = NativeWindows()
    native.register()
    parent = Parent(native)
    host = native.host
    try:
        ready = parent.read()
        assert ready["event"] == "ready" and ready["pid"] != os.getpid()
        target, target_root = ready["slot_hwnd"], ready["root_hwnd"]
        child = native.create()
        original = (host.get_parent(child), host.get_style(child), host.get_style(child, True))
        original_rect = host.get_window_rect(child)
        assert_rejected(lambda: host.validate_ths_target(target_root))
        assert_rejected(lambda: host.validate_ths_target(target))
        assert_rejected(lambda: host.attach(child, target, {"hwnd": target, "kind": "ths"}))
        assert_rejected(lambda: host._attach_owned(target, child))
        assert (host.get_parent(child), host.get_style(child), host.get_style(child, True)) == original

        # Native styles must be restored if SetParent fails after style changes.
        actual_set_parent = host._set_parent
        failures = [True]

        def fail_once(owned_child, new_parent):
            if failures and failures.pop():
                raise EmbeddingError("Deliberate own-window fault injection")
            return actual_set_parent(owned_child, new_parent)

        host._set_parent = fail_once
        try:
            assert_rejected(lambda: host._attach_owned(child, target))
        finally:
            host._set_parent = actual_set_parent
        assert (host.get_parent(child), host.get_style(child), host.get_style(child, True)) == original
        assert host.get_window_rect(child) == original_rect

        # A failure after the actual cross-process SetParent must undo that too.
        actual_resize = host.resize_to_parent

        def fail_resize(_child, _parent):
            raise EmbeddingError("Deliberate resize fault after native SetParent")

        host.resize_to_parent = fail_resize
        try:
            assert_rejected(lambda: host._attach_owned(child, target))
        finally:
            host.resize_to_parent = actual_resize
        assert (host.get_parent(child), host.get_style(child), host.get_style(child, True)) == original
        assert host.get_window_rect(child) == original_rect

        state = host._attach_owned(child, target)
        assert host.get_parent(child) == target and host.is_child(target, child)
        assert host.get_style(child) & host.WS_CHILD and not host.get_style(child) & host.WS_POPUP
        assert host.get_client_size(child) == tuple(ready["client_size"])
        assert not native.u.IsWindowVisible(child)
        generic_fingerprint = host._fingerprint(target, require_ths=False)
        # An existing target can temporarily collapse without changing identity.
        collapsed = parent.send({"cmd": "resize", "width": 0, "height": 0})
        assert collapsed["client_size"] == [0, 0]
        assert host._is_fingerprint_current(generic_fingerprint, require_ths=False)
        assert_rejected(lambda: host._fingerprint(target, require_ths=False))
        assert host.resize_to_parent(child, target) == (0, 0)
        assert host.get_client_size(child) == (0, 0) and host.is_window(child)
        resized = parent.send({"cmd": "resize", "width": 640, "height": 360})
        new_size = host.resize_to_parent(child, target)
        assert new_size == tuple(resized["client_size"]) == host.get_client_size(child)
        host.detach(child, state["original_parent"], state["original_style"], state["original_exstyle"],
                    state["original_rect"])
        assert (host.get_parent(child), host.get_style(child), host.get_style(child, True)) == original
        assert host.get_window_rect(child) == original_rect
        assert not host.is_child(target, child)

        generic_fingerprint = host._fingerprint(target, require_ths=False)
        assert host._is_fingerprint_current(generic_fingerprint, require_ths=False)
        host._attach_owned(child, target)
        destroyed = parent.send({"cmd": "destroy"})
        assert destroyed["event"] == "destroyed"
        deadline = time.monotonic() + 3
        while host.is_window(target) and time.monotonic() < deadline:
            native.pump()
            time.sleep(0.01)
        assert not host.is_window(target)
        assert not host._is_fingerprint_current(generic_fingerprint, require_ths=False)
        auto_destroyed = not host.is_window(child)
        # Cross-process native children can survive parent destruction. Our UI
        # owner must explicitly close its own orphan on target invalidation.
        if not auto_destroyed:
            assert native.u.DestroyWindow(child)
        assert not host.is_window(child)
        return {
            "ok": True,
            "test_scope": "own_invisible_windows_only",
            "cross_process": True,
            "parent_pid": ready["pid"],
            "child_pid": os.getpid(),
            "get_parent_verified": True,
            "is_child_verified": True,
            "child_style_verified": True,
            "resized_client_size": list(new_size),
            "zero_size_keeps_existing_target_valid": True,
            "zero_size_selection_rejected": True,
            "detach_restores_parent_and_styles": True,
            "failure_rolls_back_styles": True,
            "post_parent_failure_rolls_back_parent_and_rectangle": True,
            "foreign_child_rejected": True,
            "non_ths_and_root_rejected": True,
            "target_destroy_detected": True,
            "child_destroyed_with_parent": auto_destroyed,
            "own_orphan_cleanup_verified": True,
            "real_ths_mutations": 0,
        }
    finally:
        # The hidden parent was created by this test, never discovered from THS.
        parent.close()
        native.close()


def managed_form_smoke_test() -> dict:
    """Exercise the host's WinForms handle/Show lifecycle, still never visible."""
    import clr

    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Drawing import Size
    from System.Windows.Forms import Form, FormBorderStyle

    native = NativeWindows()
    native.register()
    parent = Parent(native)
    form = None
    try:
        ready = parent.read()
        target = ready["slot_hwnd"]
        assert not native.u.IsWindowVisible(target)
        form = Form()
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.ShowInTaskbar = False
        form.MinimumSize = Size(240, 200)
        hwnd = int(form.Handle.ToInt64())
        state = native.host._attach_owned(hwnd, target)
        # All ancestors are hidden before Show; this never displays the form.
        form.Show()
        native.pump()
        assert not native.u.IsWindowVisible(hwnd)
        assert native.host.get_parent(hwnd) == target and native.host.is_child(target, hwnd)
        parent.send({"cmd": "resize", "width": 120, "height": 80})
        assert native.host.resize_to_parent(hwnd, target) == (120, 80)
        assert native.host.get_client_size(hwnd) == (120, 80)
        fingerprint = native.host._fingerprint(target, require_ths=False)
        parent.send({"cmd": "resize", "width": 0, "height": 0})
        assert native.host._is_fingerprint_current(fingerprint, require_ths=False)
        native.host.resize_to_parent(hwnd, target)
        assert native.host.get_client_size(hwnd) == (0, 0)
        parent.send({"cmd": "resize", "width": 640, "height": 360})
        native.host.resize_to_parent(hwnd, target)
        assert native.host.get_client_size(hwnd) == (640, 360)
        form.Hide()
        native.host.detach(hwnd, state["original_parent"], state["original_style"],
                           state["original_exstyle"], state["original_rect"])
        assert native.host.get_parent(hwnd) == state["original_parent"]
        form.Close()
        form.Dispose()
        form = None
        assert not native.host.is_window(hwnd)
        return {"ok": True, "form_show_keeps_native_parent": True,
                "small_and_zero_client_size_verified": True,
                "recover_client_size": [640, 360], "own_form_closed": True,
                "all_windows_invisible": True, "real_ths_mutations": 0}
    finally:
        if form is not None:
            form.Hide()
            form.Close()
            form.Dispose()
        parent.close()
        native.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--parent"]:
        parent_process()
    elif sys.argv[1:] == ["--winforms"]:
        print(json.dumps({"native": smoke_test(), "winforms": managed_form_smoke_test()},
                         ensure_ascii=False, separators=(",", ":")))
    elif sys.argv[1:]:
        raise SystemExit("Usage: python test_win32_embed.py [--winforms]")
    else:
        print(json.dumps(smoke_test(), ensure_ascii=False, separators=(",", ":")))
