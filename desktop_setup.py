"""Small local setup window for the prebuilt portable Windows package."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from install_desktop import default_state_dir, install


def component_executable(executable: str | Path | None = None) -> Path:
    """Both installer and reader share the same onedir runtime."""
    path = Path(executable or sys.executable).resolve()
    target = path.with_name("THSTeacher.exe")
    if not target.is_file():
        raise ValueError("请先完整解压文件夹，再打开安装程序。")
    return target


def valid_ths_path(value: str) -> Path | None:
    try:
        path = Path(value.strip().strip('"')).expanduser()
        return path.resolve() if path.is_absolute() and path.name.lower() == "hexin.exe" and path.is_file() else None
    except (OSError, ValueError):
        return None


def find_ths_path() -> str:
    candidates = []
    try:
        config = json.loads((default_state_dir() / "board_config.json").read_text(encoding="utf-8-sig"))
        candidates.append(config.get("ths_exe", ""))
    except (OSError, ValueError, TypeError):
        pass
    try:
        from win32_embed import Win32Host
        candidates.extend(item["path"] for item in Win32Host().inspect_ths())
    except Exception:
        pass
    candidates.extend(("D:/app/同花顺/同花顺/hexin.exe", "C:/同花顺/hexin.exe"))
    for candidate in candidates:
        if isinstance(candidate, str) and (path := valid_ths_path(candidate)):
            return str(path)
    return ""


def configure_portable(executable: str | Path, ths_exe: str, **locations) -> dict:
    component = component_executable(executable)
    target = valid_ths_path(ths_exe)
    if target is None:
        raise ValueError("请选择同花顺文件夹里的 hexin.exe。先启动同花顺，再重新打开安装程序，也可自动识别。")
    return install(board_name="老师消息", ths_exe=target, exe_path=component,
                   canvas_class="AfxFrameOrView140s", control_id=59648, top_inset=28, **locations)


def run_setup() -> bool:
    """Configure only local files and shortcuts. Never starts/changes THS."""
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Drawing import Color, Font, FontStyle, Size, Point
    from System.Threading import Thread, ThreadStart, ApartmentState
    from System.Windows.Forms import (Application, Form, Label, TextBox, Button,
        OpenFileDialog, DialogResult, FormBorderStyle, FormStartPosition, MessageBox,
        MessageBoxButtons, MessageBoxIcon)

    result = {"ok": False}

    def gui():
        Application.EnableVisualStyles()
        form = Form()
        form.Text = "安装同花顺老师消息"
        form.ClientSize = Size(600, 360)
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.MaximizeBox = False
        form.StartPosition = FormStartPosition.CenterScreen
        form.Font = Font("Microsoft YaHei UI", 10)
        form.BackColor = Color.FromArgb(247, 247, 247)

        def label(text, x, y, width, height, bold=False):
            item = Label()
            item.Text = text
            item.SetBounds(x, y, width, height)
            if bold:
                item.Font = Font(form.Font, FontStyle.Bold)
            form.Controls.Add(item)
            return item

        label("解压后，一次配置即可使用", 22, 20, 550, 30, True)
        label("先启动同花顺，程序会自动识别位置。", 22, 57, 550, 25)
        label("同花顺程序", 22, 100, 480, 24)
        path_box = TextBox()
        path_box.SetBounds(22, 128, 450, 28)
        path_box.Text = find_ths_path()
        form.Controls.Add(path_box)
        browse = Button()
        browse.Text = "选择…"
        browse.SetBounds(482, 126, 92, 31)
        form.Controls.Add(browse)

        def choose(*_):
            dialog = OpenFileDialog()
            dialog.Title = "选择同花顺 hexin.exe"
            dialog.Filter = "同花顺程序 (hexin.exe)|hexin.exe"
            dialog.CheckFileExists = True
            if dialog.ShowDialog(form) == DialogResult.OK:
                path_box.Text = dialog.FileName
            dialog.Dispose()

        browse.Click += choose
        label("同花顺里需要有一个名为“老师消息”的空白看板。\n已创建过的不用重建。安装完成后，使用桌面快捷方式启动。", 22, 180, 550, 65)
        label("程序留在当前解压文件夹，请保留整个文件夹。", 22, 255, 550, 25)
        finish = Button()
        finish.Text = "创建快捷方式，完成安装"
        finish.SetBounds(310, 303, 264, 36)
        form.Controls.Add(finish)

        def complete(*_):
            try:
                configure_portable(sys.executable, str(path_box.Text))
            except Exception as error:
                MessageBox.Show(form, str(error), "尚未安装", MessageBoxButtons.OK, MessageBoxIcon.Information)
                return
            result["ok"] = True
            MessageBox.Show(form, "安装完成。\n请双击桌面“同花顺老师消息”，然后打开同花顺的“老师消息”看板。",
                            "安装完成", MessageBoxButtons.OK, MessageBoxIcon.Information)
            form.Close()

        finish.Click += complete
        try:
            Application.Run(form)
        finally:
            form.Dispose()

    thread = Thread(ThreadStart(gui))
    thread.SetApartmentState(ApartmentState.STA)
    thread.Start()
    thread.Join()
    return result["ok"]
