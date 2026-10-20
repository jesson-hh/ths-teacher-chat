"""Own-window-only Win32 embedding helpers for the THS teacher component.

No DLL is loaded into THS, no THS window procedure is replaced, and no
synchronous window message is sent to THS. A caller must explicitly select a
non-root THS window and retain its validated fingerprint before attaching.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import ntpath
import os
from typing import Any


class EmbeddingError(RuntimeError):
    """The selected window cannot safely be used, or a Win32 call failed."""


class Win32Host:
    WS_CHILD = 0x40000000
    WS_POPUP = 0x80000000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    WS_CLIPSIBLINGS = 0x04000000
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_TOPMOST = 0x00000008
    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    GA_ROOT = 2
    GW_CHILD = 5
    SWP_NOZORDER = 0x0004
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        if os.name != "nt":
            raise EmbeddingError("此组件仅支持 Windows。")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

    def _bind(self) -> None:
        """Declare pointer-sized signatures explicitly, including on x64."""
        u, k = self.user32, self.kernel32
        self._enum_callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowEnabled": ([wintypes.HWND], wintypes.BOOL),
            "IsChild": ([wintypes.HWND, wintypes.HWND], wintypes.BOOL),
            "GetParent": ([wintypes.HWND], wintypes.HWND),
            "GetDesktopWindow": ([], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetClientRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ScreenToClient": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetDlgCtrlID": ([wintypes.HWND], ctypes.c_int),
            "EnumWindows": ([self._enum_callback_type, wintypes.LPARAM], wintypes.BOOL),
            "EnumChildWindows": ([wintypes.HWND, self._enum_callback_type, wintypes.LPARAM], wintypes.BOOL),
            "SetParent": ([wintypes.HWND, wintypes.HWND], wintypes.HWND),
            "SetWindowPos": (
                [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT],
                wintypes.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(u, name)
            fn.argtypes, fn.restype = args, result
        long_get = "GetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "GetWindowLongW"
        long_set = "SetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "SetWindowLongW"
        self._get_window_long = getattr(u, long_get)
        self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get_window_long.restype = ctypes.c_ssize_t
        self._set_window_long = getattr(u, long_set)
        self._set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self._set_window_long.restype = ctypes.c_ssize_t
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.GetProcessTimes.restype = wintypes.BOOL

    @staticmethod
    def _handle(value: Any) -> int:
        return int(value or 0)

    @staticmethod
    def _failure(operation: str) -> EmbeddingError:
        error = ctypes.get_last_error()
        description = ctypes.FormatError(error).strip() if error else "调用未成功"
        return EmbeddingError(f"{operation} 失败（{error}）：{description}")

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd and self.user32.IsWindow(int(hwnd)))

    def get_parent(self, hwnd: int) -> int:
        return self._handle(self.user32.GetParent(int(hwnd)))

    def is_child(self, parent_hwnd: int, child_hwnd: int) -> bool:
        return bool(self.user32.IsChild(int(parent_hwnd), int(child_hwnd)))

    def get_client_size(self, hwnd: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
            raise self._failure("读取窗口客户区")
        return rect.right - rect.left, rect.bottom - rect.top

    def get_window_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            raise self._failure("读取窗口范围")
        return rect.left, rect.top, rect.right, rect.bottom

    def _pid_tid(self, hwnd: int) -> tuple[int, int]:
        pid = wintypes.DWORD()
        tid = int(self.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid)))
        if not tid:
            raise self._failure("读取窗口所属进程")
        return int(pid.value), tid

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(512)
        if not self.user32.GetClassNameW(int(hwnd), buffer, len(buffer)):
            raise self._failure("读取窗口类名")
        return buffer.value

    def _process_identity(self, pid: int) -> tuple[str, str]:
        process = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            raise self._failure("读取目标进程")
        try:
            path_buffer = ctypes.create_unicode_buffer(32768)
            path_size = wintypes.DWORD(len(path_buffer))
            if not self.kernel32.QueryFullProcessImageNameW(process, 0, path_buffer, ctypes.byref(path_size)):
                raise self._failure("读取目标进程路径")
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.kernel32.GetProcessTimes(
                process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
            ):
                raise self._failure("读取进程创建时间")
            # A decimal string preserves the exact 64-bit FILETIME in JSON readers.
            creation_time = str((int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime))
            return path_buffer.value, creation_time
        finally:
            self.kernel32.CloseHandle(process)

    def _metadata(self, hwnd: int) -> dict[str, Any]:
        if not self.is_window(hwnd):
            raise EmbeddingError("窗口已关闭，需重新选择。")
        pid, tid = self._pid_tid(hwnd)
        width, height = self.get_client_size(hwnd)
        return {
            "hwnd": int(hwnd),
            "parent_hwnd": self.get_parent(hwnd),
            "pid": pid,
            "thread_id": tid,
            "class_name": self._class_name(hwnd),
            "control_id": int(self.user32.GetDlgCtrlID(hwnd)),
            "client_size": [width, height],
            "window_rect": list(self.get_window_rect(hwnd)),
            "visible": bool(self.user32.IsWindowVisible(hwnd)),
            "enabled": bool(self.user32.IsWindowEnabled(hwnd)),
            "is_leaf": not bool(self.user32.GetWindow(hwnd, self.GW_CHILD)),
        }

    def _fingerprint(self, hwnd: int, require_ths: bool = True,
                     require_usable_size: bool = True) -> dict[str, Any]:
        metadata = self._metadata(hwnd)
        root_hwnd = self._handle(self.user32.GetAncestor(hwnd, self.GA_ROOT))
        if not root_hwnd:
            raise EmbeddingError("无法确认目标所属主窗口。")
        if require_ths and root_hwnd == hwnd:
            raise EmbeddingError("请选择同花顺内的空白单元，不能嵌入整个主窗口。")
        root_pid, _ = self._pid_tid(root_hwnd)
        if root_pid != metadata["pid"]:
            raise EmbeddingError("目标单元与主窗口不属于同一进程。")
        path, creation_time = self._process_identity(root_pid)
        if require_ths and ntpath.basename(path).lower() != "hexin.exe":
            raise EmbeddingError("目标不是同花顺 hexin.exe 窗口。")
        if require_usable_size and min(metadata["client_size"]) <= 0:
            raise EmbeddingError("目标单元没有可用显示范围。")
        chain = []
        current = hwnd
        for _ in range(64):
            chain.append([current, self._class_name(current)])
            if current == root_hwnd:
                break
            current = self.get_parent(current)
            if not current:
                raise EmbeddingError("目标窗口结构已变化，请重新选择。")
        else:
            raise EmbeddingError("目标窗口层级异常。")
        return {
            **metadata,
            "kind": "ths" if require_ths else "test_window",
            "root_hwnd": root_hwnd,
            "root_class_name": self._class_name(root_hwnd),
            "path": path,
            "creation_time": creation_time,
            "ancestor_chain": chain,
        }

    def inspect_ths(self) -> list[dict[str, Any]]:
        """Read metadata only; candidates are not inferred to be blank or safe."""
        roots: list[dict[str, Any]] = []

        @self._enum_callback_type
        def on_root(hwnd: int, _lparam: int) -> bool:
            try:
                pid, _ = self._pid_tid(hwnd)
                path, creation_time = self._process_identity(pid)
                if ntpath.basename(path).lower() != "hexin.exe":
                    return True
                root = {
                    **self._metadata(hwnd),
                    "root_hwnd": self._handle(hwnd),
                    "path": path,
                    "creation_time": creation_time,
                    "candidates": [],
                }

                @self._enum_callback_type
                def on_child(child: int, _data: int) -> bool:
                    try:
                        info = self._metadata(child)
                        if info["pid"] == pid and min(info["client_size"]) > 0:
                            root["candidates"].append(info)
                    except (EmbeddingError, OSError):
                        pass  # A disappearing window is normal during enumeration.
                    return True

                self.user32.EnumChildWindows(hwnd, on_child, 0)
                roots.append(root)
            except (EmbeddingError, OSError):
                pass
            return True

        ctypes.set_last_error(0)
        if not self.user32.EnumWindows(on_root, 0) and ctypes.get_last_error():
            raise self._failure("枚举同花顺窗口")
        return roots

    def validate_ths_target(self, parent_hwnd: int, expected_root: int | None = None) -> dict[str, Any]:
        fingerprint = self._fingerprint(int(parent_hwnd), require_ths=True)
        if expected_root is not None and fingerprint["root_hwnd"] != int(expected_root):
            raise EmbeddingError("所选单元不属于指定同花顺主窗口。")
        return fingerprint

    def _is_fingerprint_current(self, fingerprint: dict[str, Any], require_ths: bool) -> bool:
        try:
            if fingerprint.get("kind") != ("ths" if require_ths else "test_window"):
                return False
            current = self._fingerprint(int(fingerprint["hwnd"]), require_ths=require_ths,
                                        require_usable_size=False)
            stable = (
                "hwnd", "pid", "thread_id", "class_name", "control_id", "root_hwnd",
                "root_class_name", "creation_time", "ancestor_chain",
            )
            return all(current[key] == fingerprint[key] for key in stable) and (
                ntpath.normcase(current["path"]) == ntpath.normcase(fingerprint["path"])
            )
        except (KeyError, TypeError, ValueError, EmbeddingError, OSError):
            return False

    def is_target_valid(self, fingerprint: dict[str, Any]) -> bool:
        return self._is_fingerprint_current(fingerprint, require_ths=True)

    def _require_owned_window(self, hwnd: int) -> None:
        if not self.is_window(hwnd):
            raise EmbeddingError("组件窗口已关闭。")
        if self._pid_tid(hwnd)[0] != os.getpid():
            raise EmbeddingError("拒绝修改其他进程的窗口；只能嵌入本组件自己创建的窗口。")

    def get_style(self, hwnd: int, extended: bool = False) -> int:
        ctypes.set_last_error(0)
        result = self._get_window_long(hwnd, self.GWL_EXSTYLE if extended else self.GWL_STYLE)
        if not result and ctypes.get_last_error():
            raise self._failure("读取窗口样式")
        return int(result) & 0xFFFFFFFF

    def _set_style(self, hwnd: int, value: int, extended: bool = False) -> None:
        self._require_owned_window(hwnd)
        ctypes.set_last_error(0)
        result = self._set_window_long(
            hwnd, self.GWL_EXSTYLE if extended else self.GWL_STYLE, ctypes.c_ssize_t(value).value
        )
        if not result and ctypes.get_last_error():
            raise self._failure("设置组件窗口样式")

    def _set_parent(self, child_hwnd: int, parent_hwnd: int) -> None:
        self._require_owned_window(child_hwnd)
        ctypes.set_last_error(0)
        previous = self.user32.SetParent(child_hwnd, parent_hwnd or None)
        if not previous and ctypes.get_last_error():
            raise self._failure("嵌入组件窗口")
        actual_parent = self.get_parent(child_hwnd)
        # SetParent(NULL) temporarily reports the desktop for a WS_CHILD window.
        # Its top-level style is restored immediately afterwards by detach().
        expected = {parent_hwnd}
        if not parent_hwnd:
            expected.add(self._handle(self.user32.GetDesktopWindow()))
        if actual_parent not in expected:
            raise EmbeddingError("系统未建立预期的父子窗口关系。")

    def attach(self, child_hwnd: int, parent_hwnd: int, fingerprint: dict[str, Any]) -> dict[str, Any]:
        """Attach only our window to an explicitly validated THS child window."""
        if int(fingerprint.get("hwnd", 0)) != int(parent_hwnd) or not self.is_target_valid(fingerprint):
            raise EmbeddingError("目标校验已失效，请重新选择同花顺空白单元。")
        state = self._attach_owned(child_hwnd, parent_hwnd)
        if not self.is_target_valid(fingerprint):
            self.detach(child_hwnd, state["original_parent"], state["original_style"], state["original_exstyle"],
                        state["original_rect"])
            raise EmbeddingError("嵌入时目标窗口发生变化，已撤回组件。")
        state["fingerprint"] = fingerprint
        return state

    def _attach_owned(self, child_hwnd: int, parent_hwnd: int) -> dict[str, Any]:
        """Generic native primitive; hidden self-tests use only their own parent."""
        child_hwnd, parent_hwnd = int(child_hwnd), int(parent_hwnd)
        self._require_owned_window(child_hwnd)
        if not self.is_window(parent_hwnd):
            raise EmbeddingError("目标单元已关闭。")
        if child_hwnd == parent_hwnd or self.is_child(child_hwnd, parent_hwnd):
            raise EmbeddingError("不能建立循环窗口关系。")
        width, height = self.get_client_size(parent_hwnd)
        if width <= 0 or height <= 0:
            raise EmbeddingError("目标单元没有可用显示范围。")
        state = {
            "child_hwnd": child_hwnd,
            "parent_hwnd": parent_hwnd,
            "original_parent": self.get_parent(child_hwnd),
            "original_style": self.get_style(child_hwnd),
            "original_exstyle": self.get_style(child_hwnd, extended=True),
            "original_rect": list(self.get_window_rect(child_hwnd)),
        }
        remove = self.WS_POPUP | self.WS_CAPTION | self.WS_THICKFRAME | self.WS_SYSMENU
        remove |= self.WS_MINIMIZEBOX | self.WS_MAXIMIZEBOX
        style = (state["original_style"] & ~remove) | self.WS_CHILD | self.WS_CLIPSIBLINGS
        exstyle = state["original_exstyle"] & ~(self.WS_EX_APPWINDOW | self.WS_EX_TOPMOST)
        try:
            self._set_style(child_hwnd, style)
            self._set_style(child_hwnd, exstyle, extended=True)
            self._set_parent(child_hwnd, parent_hwnd)
            if not self.is_child(parent_hwnd, child_hwnd):
                raise EmbeddingError("嵌入验证失败：组件没有成为目标单元的子窗口。")
            self.resize_to_parent(child_hwnd, parent_hwnd)
        except Exception as error:
            try:
                self.detach(child_hwnd, state["original_parent"], state["original_style"], state["original_exstyle"],
                            state["original_rect"])
            except Exception as rollback_error:
                raise EmbeddingError(f"嵌入失败，恢复组件窗口也失败：{rollback_error}") from error
            raise
        return state

    def resize_to_parent(self, child_hwnd: int, parent_hwnd: int) -> tuple[int, int]:
        self._require_owned_window(child_hwnd)
        if not self.is_window(parent_hwnd) or self.get_parent(child_hwnd) != int(parent_hwnd):
            raise EmbeddingError("目标单元已变化，组件不再嵌入该单元。")
        if not self.is_child(parent_hwnd, child_hwnd):
            raise EmbeddingError("组件父子关系校验失败。")
        width, height = self.get_client_size(parent_hwnd)
        if not self.user32.SetWindowPos(
            child_hwnd, None, 0, 0, max(0, width), max(0, height),
            self.SWP_NOZORDER | self.SWP_NOACTIVATE | self.SWP_FRAMECHANGED,
        ):
            raise self._failure("调整组件显示范围")
        return width, height

    def detach(self, child_hwnd: int, original_parent: int, original_style: int, original_exstyle: int,
               original_rect: list[int] | tuple[int, int, int, int] | None = None) -> None:
        """Restore our window only; an already destroyed child needs no changes."""
        if not self.is_window(child_hwnd):
            return
        self._require_owned_window(child_hwnd)
        if original_parent and not self.is_window(original_parent):
            raise EmbeddingError("组件原父窗口已关闭，无法恢复其原父子关系。")
        self._set_parent(child_hwnd, int(original_parent))
        self._set_style(child_hwnd, int(original_style))
        self._set_style(child_hwnd, int(original_exstyle), extended=True)
        flags = self.SWP_NOZORDER | self.SWP_NOACTIVATE | self.SWP_FRAMECHANGED
        if original_rect is None:
            flags |= self.SWP_NOMOVE | self.SWP_NOSIZE
            x, y, width, height = 0, 0, 0, 0
        else:
            x, y, right, bottom = (int(value) for value in original_rect)
            width, height = max(1, right - x), max(1, bottom - y)
            if original_parent:
                point = wintypes.POINT(x, y)
                if not self.user32.ScreenToClient(original_parent, ctypes.byref(point)):
                    raise self._failure("恢复组件原位置")
                x, y = point.x, point.y
        if not self.user32.SetWindowPos(
            child_hwnd, None, x, y, width, height, flags,
        ):
            raise self._failure("恢复组件窗口边框")
        if self.get_parent(child_hwnd) != int(original_parent):
            raise EmbeddingError("恢复组件父窗口后的校验失败。")

