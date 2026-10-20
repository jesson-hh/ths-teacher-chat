"""Create a portable ZIP from only a clean PyInstaller application directory."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import zipfile


GUIDE = """同花顺老师消息 · 解压安装版

1. 将压缩包全部解压到一个固定文件夹，例如 D:\\Tools\\THSTeacher。
2. 先启动同花顺，再双击“安装.exe”，确认识别的位置，点击“完成安装”。
3. 双击桌面“同花顺老师消息”。首次在“设置 → 约牛登录”完成登录。

同花顺需要有一个名为“老师消息”的空白看板；已有就不用重建。
安装不需要 Python，也不需要输入命令。
请保留整个解压文件夹（包括 _internal），不要只移动一个 EXE。
如果系统缺少 WebView2 Runtime，可从微软官方网站安装：
https://developer.microsoft.com/microsoft-edge/webview2/

顶部可切换“只看老师 / 全部聊天”。历史通过“加载更早”和翻页查看。
两种视图的缓存保留在本次运行中，关闭程序后重新读取。
升级前先在组件“设置 → 退出组件”，再解压新版本并运行安装.exe。
登录会话在当前用户自己的 AppData 中，不包含在本安装包里。
"""


def package(app_dir: Path, output_dir: Path, version: str) -> Path:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("expected a version such as v0.2.0")
    root = app_dir.resolve(strict=True)
    executable, internal = root / "THSTeacher.exe", root / "_internal"
    if not executable.is_file() or not internal.is_dir():
        raise ValueError("expected a clean PyInstaller THSTeacher application folder")
    forbidden = {"webview-profile", "board_config.json", "bridge_config.json", "desktop_status.json", "startup_error.txt"}
    files = [executable, *(path for path in internal.rglob("*") if path.is_file()
                          and path.relative_to(root).as_posix() != "_internal/desktop/SOURCE_CONTRACT.md")]
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root) or forbidden & set(path.relative_to(root).parts):
            raise ValueError("refusing to package a profile, runtime state or external file")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"THSTeacher-Windows-x64-{version}.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(files):
            zipped.write(path, Path("THSTeacher") / path.relative_to(root))
        # A second name uses the same bundled runtime; no shell script needed.
        zipped.write(executable, "THSTeacher/安装.exe")
        zipped.writestr("THSTeacher/使用说明.txt", "\ufeff" + GUIDE)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("archive integrity check failed")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(package(args.app_dir, args.output_dir, args.version))


if __name__ == "__main__":
    main()
