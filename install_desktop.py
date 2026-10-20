"""Install only this component's user configuration and Windows shortcuts.

Never starts an executable, automates a window, edits THS files or reads a login
profile. All COM operations are limited to known-folder lookup and .lnk files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


SHORTCUT_NAME = "同花顺老师消息.lnk"

_KNOWN_FOLDERS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$shell = New-Object -ComObject WScript.Shell
try {
  @{desktop = [string]$shell.SpecialFolders.Item('Desktop'); programs = [string]$shell.SpecialFolders.Item('Programs')} | ConvertTo-Json -Compress
} finally {
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
}
"""

_SHORTCUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
$results = @()
try {
  foreach ($item in $request.items) {
    $shortcut = $shell.CreateShortcut([string]$item.path)
    try {
      $shortcut.TargetPath = [string]$request.exe
      $shortcut.Arguments = ''
      $shortcut.WorkingDirectory = [string]$request.working_directory
      $shortcut.IconLocation = [string]$request.exe + ',0'
      $shortcut.Description = '启动同花顺老师消息组件'
      $shortcut.WindowStyle = 1
      $shortcut.Save()
    } finally {
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
    }
    $saved = $shell.CreateShortcut([string]$item.path)
    try {
      $results += @{path = [string]$item.path; target = [string]$saved.TargetPath; arguments = [string]$saved.Arguments; working_directory = [string]$saved.WorkingDirectory}
    } finally {
      [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($saved)
    }
  }
  @{shortcuts = $results} | ConvertTo-Json -Compress -Depth 4
} finally {
  [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
}
"""


def _powershell(script: str, request: dict | None = None) -> dict:
    if os.name != "nt":
        raise RuntimeError("Windows shortcut installation requires Windows")
    executable = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_file():
        raise RuntimeError("Windows PowerShell is unavailable")
    # The command is constant. Every path is data in JSON stdin, never shell code.
    process = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(request or {}, ensure_ascii=True).encode("ascii"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if process.returncode:
        detail = process.stderr.decode("utf-8-sig", errors="replace").strip()
        raise RuntimeError("Windows shortcut operation failed: " + detail[:2000])
    try:
        return json.loads(process.stdout.decode("utf-8-sig").strip())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Windows shortcut operation returned invalid verification data") from exc


def _absolute(path: str | Path, *, existing_file: bool = False) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("an absolute path is required: " + str(candidate))
    resolved = candidate.resolve(strict=existing_file)
    if existing_file and not resolved.is_file():
        raise ValueError("an existing file is required: " + str(resolved))
    return resolved


def _executable(path: str | Path) -> Path:
    resolved = _absolute(path, existing_file=True)
    if resolved.suffix.lower() != ".exe":
        raise ValueError("the target must be an existing .exe file")
    return resolved


def _same_existing_path(left: str | Path, right: str | Path) -> bool:
    """Compare file identities across 8.3 names and MSIX redirected paths."""
    try:
        return os.path.samefile(_absolute(left), _absolute(right))
    except (OSError, ValueError):
        return False


def default_state_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData/Local"
    return _absolute(base / "THSTeacherDesktop")


def _replace_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(
    *, board_name: str, ths_exe: str | Path, exe_path: str | Path,
    canvas_class: str, control_id: int, top_inset: int,
    state_dir: str | Path | None = None, desktop_dir: str | Path | None = None,
    programs_dir: str | Path | None = None, dry_run: bool = False,
) -> dict:
    if not isinstance(board_name, str) or not board_name.strip() or len(board_name) > 80 or any(ord(c) < 32 for c in board_name):
        raise ValueError("board_name must be a nonempty plain title of at most 80 characters")
    if not isinstance(canvas_class, str) or not canvas_class.strip() or len(canvas_class) > 128 or any(ord(c) < 32 for c in canvas_class):
        raise ValueError("canvas_class must be a nonempty window class")
    if isinstance(control_id, bool) or not isinstance(control_id, int) or not 1 <= control_id <= 0x7FFFFFFF:
        raise ValueError("control_id is out of range")
    if isinstance(top_inset, bool) or not isinstance(top_inset, int) or not 0 <= top_inset <= 256:
        raise ValueError("top_inset must be a logical-pixel integer from 0 to 256")
    ths_target, component = _executable(ths_exe), _executable(exe_path)
    if ths_target == component:
        raise ValueError("the component executable must be separate from THS")
    state = _absolute(state_dir) if state_dir is not None else default_state_dir()
    folders = _powershell(_KNOWN_FOLDERS_SCRIPT) if desktop_dir is None or programs_dir is None else {}
    desktop = _absolute(desktop_dir if desktop_dir is not None else folders.get("desktop", ""))
    programs = _absolute(programs_dir if programs_dir is not None else folders.get("programs", ""))
    shortcut_paths = [desktop / SHORTCUT_NAME, programs / SHORTCUT_NAME]
    if shortcut_paths[0] == shortcut_paths[1]:
        raise ValueError("desktop and Programs shortcut locations must differ")
    config_path = state / "board_config.json"
    config = {"schema": 1, "enabled": True, "board_name": board_name.strip(), "ths_exe": ths_target.as_posix(),
              "canvas_class": canvas_class.strip(), "control_id": control_id, "top_inset": top_inset}
    result = {"config_path": str(config_path), "exe_path": str(component),
              "shortcut_paths": [str(path) for path in shortcut_paths], "config": config, "dry_run": dry_run}
    if dry_run:
        return result
    for directory in (state, desktop, programs):
        directory.mkdir(parents=True, exist_ok=True)
    targets = [config_path, *shortcut_paths]
    for target in targets:
        if target.exists() and not target.is_file():
            raise ValueError("installation target is not a regular file: " + str(target))
    backups = {target: target.read_bytes() if target.exists() else None for target in targets}
    try:
        _replace_bytes(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        verified = _powershell(_SHORTCUT_SCRIPT, {"exe": str(component), "working_directory": str(component.parent),
                                                 "items": [{"path": str(path)} for path in shortcut_paths]})
        shortcuts = verified.get("shortcuts")
        if not isinstance(shortcuts, list) or len(shortcuts) != 2:
            raise RuntimeError("shortcut creation did not return two verified links")
        for link, expected in zip(shortcuts, shortcut_paths):
            if (not _same_existing_path(link.get("path", ""), expected)
                    or not _same_existing_path(link.get("target", ""), component)
                    or link.get("arguments") != ""):
                raise RuntimeError("saved shortcut target does not match the requested absolute executable")
            if not _same_existing_path(link.get("working_directory", ""), component.parent) or not expected.is_file():
                raise RuntimeError("saved shortcut working directory or file is incorrect")
        result["verified"] = True
        return result
    except Exception:
        for target, original in backups.items():
            if original is None:
                if target.is_file():
                    target.unlink()
            else:
                _replace_bytes(target, original)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Install only user board settings and shortcuts; never edits or starts THS")
    parser.add_argument("--board-name", required=True)
    parser.add_argument("--ths-exe", required=True)
    parser.add_argument("--exe-path", required=True)
    parser.add_argument("--canvas-class", required=True)
    parser.add_argument("--control-id", type=int, required=True)
    parser.add_argument("--top-inset", type=int, default=28)
    parser.add_argument("--state-dir")
    parser.add_argument("--desktop-dir", help="override the current user's Desktop folder, primarily for isolated validation")
    parser.add_argument("--programs-dir", help="override the current user's Start Menu Programs folder, primarily for isolated validation")
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args())
    try:
        result = install(**args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Installation was not completed: " + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
