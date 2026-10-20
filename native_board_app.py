"""Native Windows teacher board. Only the official sign-in uses WebView2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time

from server import TeacherFeed, ValidationError


def reconcile_message_rows(rows, messages, width, create, dispose):
    """Keep unchanged row objects (including loaded images) across snapshots.

    Build replacements before discarding anything, so a failed build leaves
    the last usable page intact. A None key represents the empty-state row.
    """
    prepared, created = {}, []
    try:
        for message in messages or [None]:
            key = message["id"] if message is not None else None
            signature = (width, json.dumps(message, ensure_ascii=False, sort_keys=True))
            existing = rows.get(key)
            if existing is not None and existing["signature"] == signature:
                prepared[key] = existing
            else:
                row = create(message, width)
                row["signature"] = signature
                created.append(row)
                prepared[key] = row
    except Exception:
        for row in created:
            dispose(row)
        raise
    removed = [row for key, row in rows.items() if prepared.get(key) is not row]
    for row in removed:
        dispose(row)
    rows.clear()
    rows.update(prepared)
    return len(created), len(prepared) - len(created), len(removed)


def enable_double_buffer(control):
    from System.Reflection import BindingFlags
    prop = control.GetType().GetProperty("DoubleBuffered", BindingFlags.Instance | BindingFlags.NonPublic)
    prop.SetValue(control, True, None)
    return control


@contextmanager
def suspend_redraw(control):
    """Commit native rows and scroll position together, only on our control.

    Do not send WM_SETREDRAW to hidden controls: enabling it can otherwise
    make the inactive view or the reader behind the login page visible.
    """
    import ctypes
    from ctypes import wintypes
    if control.InvokeRequired:
        raise RuntimeError("message rendering must run on its own UI thread")
    handle = int(control.Handle.ToInt64()) if control.Visible and control.IsHandleCreated else None
    send = ctypes.WinDLL("user32", use_last_error=True).SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    if handle is not None:
        send(handle, 0x000B, 0, 0)  # WM_SETREDRAW(FALSE)
    try:
        yield
    finally:
        if handle is not None and not control.IsDisposed:
            send(handle, 0x000B, 1, 0)
            control.Invalidate(True)


def message_page_bounds(total: int, page: int, page_size: int = 60) -> tuple[int, int, int, int]:
    """Oldest page holds the remainder; the latest page is always full."""
    pages = max(1, (total + page_size - 1) // page_size)
    current = pages - 1 if page < 0 else max(0, min(page, pages - 1))
    end = max(0, total - (pages - 1 - current) * page_size)
    return max(0, end - page_size), end, current, pages


def run_native(args, resources: Path, base: Path, config: dict) -> None:
    from desktop_webview import _load_dotnet
    _load_dotnet()
    from System.Threading import Thread, ThreadStart, ApartmentState

    base.mkdir(parents=True, exist_ok=True)
    feeds = {mode: TeacherFeed(config, retention_limit=5000, allow_reply_context=True, allow_public_chat=True)
             for mode in ("teacher", "all")}
    output = {"component": "ths-teacher-desktop", "schema": 1, "mode": "starting",
              "source_state": "waiting", "error": None, "message_count": 0}

    def record(**values):
        import os
        output.update(values)
        output.update(pid=os.getpid(), updated_at=time.time())
        temporary = base / "desktop_status.tmp"
        temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(base / "desktop_status.json")

    def gui():
        from System import Action
        from System.Drawing import Color, Font, FontStyle, GraphicsUnit, Size, Point
        from System.Windows.Forms import (Application, Form, Panel, Label, Button,
            FlatStyle, FlowLayoutPanel, FlowDirection, Padding, AnchorStyles,
            ContextMenuStrip, AutoScaleMode, FormBorderStyle, Timer, PictureBox,
            PictureBoxSizeMode, DockStyle, ToolStripDropDownDirection, SystemInformation)
        from desktop_webview import create_source_control

        form = None
        life = {"closing": False, "source": None, "board": None, "timer": None,
                "ids": None, "first_ready": False, "content_width": None,
                "panels": [], "rows": {}, "source_visible": False, "rendering": False,
                "page": -1, "page_count": 1, "scroll_top": False, "scroll_latest": False,
                "view": "teacher", "restore_scroll": None, "render_count": 0,
                "row_creations": 0, "row_reuses": 0, "row_disposals": 0,
                "views": {mode: {"page": -1, "scroll": 0,
                           "panels": [], "rows": {}, "ids": None, "page_count": 1,
                           "history": {"state": "idle", "pages": 0, "has_more": True, "oldest": None}}
                          for mode in ("teacher", "all")},
                "picture_pool": ThreadPoolExecutor(max_workers=3, thread_name_prefix="teacher-image"),
                "history": {"state": "idle", "pages": 0, "has_more": True, "oldest": None}}
        life["panels"] = life["views"]["teacher"]["panels"]
        life["rows"] = life["views"]["teacher"]["rows"]
        try:
            Application.EnableVisualStyles()
            Application.SetCompatibleTextRenderingDefault(False)
            board_path = base / "board_config.json"
            board_mode = board_path.exists() and args.parent_hwnd is None
            form = enable_double_buffer(Form())
            form.Text = "老师消息组件"
            form.ClientSize = Size(900, 720)
            form.AutoScaleMode = getattr(AutoScaleMode, "None")
            form.BackColor = Color.FromArgb(13, 13, 13)
            if board_mode or args.parent_hwnd:
                form.FormBorderStyle = getattr(FormBorderStyle, "None")
            # THS is DPI-unaware. Pixel fonts keep their native metrics when
            # Windows resets a cross-process child's DPI context on SetParent.
            font = Font("Microsoft YaHei UI", 14.0, FontStyle.Regular, GraphicsUnit.Pixel)
            small = Font("Microsoft YaHei UI", 12.0, FontStyle.Regular, GraphicsUnit.Pixel)
            form.Font = font
            ink = Color.FromArgb(224, 224, 224)
            muted = Color.FromArgb(150, 150, 150)
            red = Color.FromArgb(169, 35, 26)

            header = enable_double_buffer(Panel())
            header.Height = 44
            header.BackColor = Color.FromArgb(28, 28, 28)
            form.Controls.Add(header)
            title = Label()
            title.Text = "知行合一 · 交易逻辑专栏"
            title.ForeColor = ink
            title.Font = Font("Microsoft YaHei UI", 14.0, FontStyle.Bold, GraphicsUnit.Pixel)
            title.AutoSize = True
            title.Location = Point(13, 3)
            header.Controls.Add(title)
            identity = Label()
            identity.Text = "何志坚  ·  投顾编号 A0670626090043"
            identity.ForeColor = muted
            identity.Font = small
            identity.AutoSize = True
            identity.Location = Point(13, 24)
            header.Controls.Add(identity)

            def flat(text, width=48):
                button = Button()
                button.Text = text
                button.Size = Size(width, 26)
                button.FlatStyle = FlatStyle.Flat
                button.FlatAppearance.BorderColor = Color.FromArgb(58, 58, 58)
                button.BackColor = Color.FromArgb(35, 35, 35)
                button.ForeColor = ink
                button.Font = small
                return button

            settings = flat("设置", 64)
            header.Controls.Add(settings)
            teachers_only = flat("只看老师", 88)
            all_chat = flat("全部聊天", 88)
            teachers_only.BackColor = red
            header.Controls.Add(teachers_only)
            header.Controls.Add(all_chat)
            live_jump = flat("查看最新消息", 140)
            live_jump.BackColor = red
            live_jump.Visible = False
            header.Controls.Add(live_jump)
            status = Label()
            status.AutoSize = True
            status.Font = small
            status.Text = "等待连接"
            status.ForeColor = muted
            header.Controls.Add(status)
            rule = Panel()
            rule.Height = 2
            rule.BackColor = red
            form.Controls.Add(rule)
            scroll = enable_double_buffer(FlowLayoutPanel())
            scroll.AutoScroll = True
            scroll.FlowDirection = FlowDirection.TopDown
            scroll.WrapContents = False
            scroll.Padding = Padding(12, 4, 12, 10)
            scroll.BackColor = Color.FromArgb(13, 13, 13)
            form.Controls.Add(scroll)
            scrolls = {"teacher": scroll}
            all_scroll = enable_double_buffer(FlowLayoutPanel())
            all_scroll.AutoScroll = True
            all_scroll.FlowDirection = FlowDirection.TopDown
            all_scroll.WrapContents = False
            all_scroll.Padding = scroll.Padding
            all_scroll.BackColor = scroll.BackColor
            all_scroll.Visible = False
            form.Controls.Add(all_scroll)
            scrolls["all"] = all_scroll
            source_panel = Panel()
            source_panel.BackColor = Color.White
            source_panel.Visible = False
            form.Controls.Add(source_panel)
            footer = Label()
            footer.Font = small
            footer.ForeColor = muted
            footer.BackColor = Color.FromArgb(24, 24, 24)
            footer.Text = "只看老师公开发言"
            footer.Padding = Padding(12, 3, 0, 0)
            form.Controls.Add(footer)
            menu = ContextMenuStrip()
            menu.BackColor = Color.FromArgb(35, 35, 35)
            menu.ForeColor = ink
            menu.Font = small
            menu.ShowImageMargin = False
            show_reader = menu.Items.Add("老师消息")
            show_login = menu.Items.Add("约牛登录")
            reconnect = menu.Items.Add("重新连接")
            exit_item = menu.Items.Add("退出组件")
            settings.Click += lambda *_: menu.Show(settings, Point(settings.Width, settings.Height), ToolStripDropDownDirection.BelowLeft)
            older = flat("加载更早", 88)
            previous = flat("上一页", 64)
            following = flat("下一页", 64)
            latest = flat("最新", 52)
            for item in (older, previous, following, latest):
                form.Controls.Add(item)

            def change_page(direction):
                current = life["page_count"] - 1 if life["page"] < 0 else life["page"]
                life["page"] = max(0, min(life["page_count"] - 1, current + direction))
                life["scroll_top"] = True
                life["scroll_latest"] = False
                refresh()

            def go_latest(*_):
                life["page"] = -1
                life["scroll_top"] = False
                life["scroll_latest"] = True
                refresh()

            def select_view(mode):
                nonlocal scroll
                if life["view"] == mode:
                    return
                old = life["views"][life["view"]]
                old.update(page=life["page"], scroll=scroll.VerticalScroll.Value, history=life["history"], visited=True)
                old.update({key: life[key] for key in ("panels", "rows", "ids", "page_count")})
                scroll.Visible = False
                life["view"] = mode
                saved = life["views"][mode]
                scroll = scrolls[mode]
                life.update({key: saved[key] for key in ("panels", "rows", "ids", "page_count")})
                life.update(page=saved["page"], history=saved["history"],
                            restore_scroll=saved["scroll"], scroll_latest=not saved.get("visited", False), scroll_top=False)
                teachers_only.BackColor = red if mode == "teacher" else Color.FromArgb(35,35,35)
                all_chat.BackColor = red if mode == "all" else Color.FromArgb(35,35,35)
                select_source(False)
                if life["source"]:
                    life["source"].set_view_mode(mode)
                refresh()

            teachers_only.Click += lambda *_: select_view("teacher")
            all_chat.Click += lambda *_: select_view("all")

            previous.Click += lambda *_: change_page(-1)
            following.Click += lambda *_: change_page(1)
            latest.Click += go_latest
            live_jump.Click += go_latest
            older.Click += lambda *_: life["source"].request_history(
                "cancel" if life["history"]["state"] == "loading" else "older") if life["source"] else None

            def select_source(visible):
                life["source_visible"] = visible
                scroll.Visible = not visible
                source_panel.Visible = visible
                if visible:
                    source_panel.BringToFront()
                else:
                    scroll.BringToFront()
                header.BringToFront()
                rule.BringToFront()
                footer.BringToFront()
                for item in (older, previous, following, latest):
                    item.BringToFront()

            show_reader.Click += lambda *_: select_source(False)
            show_login.Click += lambda *_: select_source(True)
            exit_item.Click += lambda *_: form.Close()

            def dispose_row(row):
                row["panel"].Dispose()
                for bitmap in row["images"]:
                    bitmap.Dispose()
                row["images"].clear()

            def clear_rows():
                for row in life["rows"].values():
                    dispose_row(row)
                life["rows"].clear()
                life["panels"].clear()

            def text_label(text, color=ink, face=font):
                label = Label()
                label.AutoSize = True
                label.UseMnemonic = False
                label.Text = text
                label.ForeColor = color
                label.Font = face
                return label

            def load_picture(url, picture, image_store):
                if life["closing"]:
                    return
                # This is a public image URL already checked by TeacherFeed.
                def worker():
                    try:
                        if life["closing"] or picture.IsDisposed:
                            return
                        import os
                        from urllib.request import build_opener, ProxyHandler
                        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
                        with build_opener(ProxyHandler({})).open(url, timeout=8) as response:
                            if response.status != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
                                return
                            raw = response.read(8 * 1024 * 1024 + 1)
                        if len(raw) > 8 * 1024 * 1024:
                            return
                        from System import Array, Byte
                        from System.IO import MemoryStream
                        from System.Drawing import Image, Bitmap
                        stream = MemoryStream(Array[Byte](raw))
                        original = Image.FromStream(stream)
                        if original.Width * original.Height > 25_000_000:
                            original.Dispose()
                            stream.Dispose()
                            return
                        bitmap = Bitmap(original)
                        original.Dispose()
                        stream.Dispose()
                        def apply():
                            if picture.IsDisposed or life["closing"]:
                                bitmap.Dispose()
                                return
                            picture.Image = bitmap
                            image_store.append(bitmap)
                        form.BeginInvoke(Action(apply))
                    except Exception:
                        pass  # A failed image never hides the teacher's text.
                life["picture_pool"].submit(worker)

            def create_row(message, width):
                panel = enable_double_buffer(Panel())
                row = {"panel": panel, "images": []}
                if message is None:
                    panel.Width = width
                    panel.Height = 150
                    panel.Margin = Padding(0, 28, 0, 10)
                    label = text_label("正在连接所选聊天视图；尚未登录时请先登录约牛。", muted)
                    label.Location = Point(6, 15)
                    panel.Controls.Add(label)
                    login = flat("约牛登录", 92)
                    login.Location = Point(6, 55)
                    login.Click += lambda *_: select_source(True)
                    panel.Controls.Add(login)
                    return row
                try:
                    panel.Width = width
                    panel.Margin = Padding(0, 0, 0, 0)
                    panel.BackColor = scroll.BackColor
                    stamp = datetime.fromisoformat(message["published_at"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8)))
                    role = message.get("role", "teacher")
                    role_text = {"teacher": "老师", "student": "学员", "assistant": "助教"}[role]
                    name = message.get("display_name", "")
                    caption = role_text + (" · " + name if name else "") + "  " + stamp.strftime("%m-%d %H:%M:%S")
                    meta = text_label(caption, Color.FromArgb(202, 170, 102) if role == "teacher"
                                      else Color.FromArgb(143, 165, 176), small)
                    meta.Location = Point(4, 8)
                    meta.MaximumSize = Size(width - 14, 0)
                    panel.Controls.Add(meta)
                    body = text_label(message["text"])
                    body.MaximumSize = Size(width - 14, 0)
                    body_y = max(28, 12 + meta.PreferredSize.Height)
                    body.Location = Point(4, body_y)
                    panel.Controls.Add(body)
                    bottom = max(40, body_y + body.PreferredSize.Height)
                    for url in message["images"]:
                        picture = PictureBox()
                        picture.SizeMode = PictureBoxSizeMode.Zoom
                        picture.BackColor = Color.FromArgb(23, 23, 23)
                        picture.SetBounds(4, bottom + 8, min(width - 12, 700), 240)
                        panel.Controls.Add(picture)
                        load_picture(url, picture, row["images"])
                        bottom += 248
                    reply = message.get("reply_context")
                    if reply:
                        quote = enable_double_buffer(Panel())
                        quote.BackColor = Color.FromArgb(25, 25, 25)
                        quote.Width = width - 12
                        quote.Location = Point(4, bottom + 10)
                        caption = "回复的提问" if role == "teacher" else "引用消息"
                        if reply.get("display_name"):
                            caption += " · @" + reply["display_name"]
                        if reply["published_at"]:
                            quote_time = datetime.fromisoformat(reply["published_at"].replace("Z", "+00:00"))
                            caption += "  " + quote_time.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
                        heading = text_label(caption, Color.FromArgb(185, 165, 130), small)
                        heading.MaximumSize = Size(width - 42, 0)
                        heading.Location = Point(12, 9)
                        quote.Controls.Add(heading)
                        question = text_label(reply["text"], Color.FromArgb(179, 179, 179))
                        question.MaximumSize = Size(width - 42, 0)
                        question_y = 12 + heading.PreferredSize.Height
                        question.Location = Point(12, question_y)
                        quote.Controls.Add(question)
                        quote_bottom = max(38, question_y + question.PreferredSize.Height)
                        for url in reply["images"]:
                            picture = PictureBox()
                            picture.SizeMode = PictureBoxSizeMode.Zoom
                            picture.BackColor = quote.BackColor
                            picture.SetBounds(12, quote_bottom + 8, min(width - 42, 600), 190)
                            quote.Controls.Add(picture)
                            load_picture(url, picture, row["images"])
                            quote_bottom += 198
                        quote.Height = quote_bottom + 12
                        accent = Panel()
                        accent.BackColor = Color.FromArgb(93, 66, 52)
                        accent.SetBounds(0, 0, 2, quote.Height)
                        quote.Controls.Add(accent)
                        panel.Controls.Add(quote)
                        bottom += 10 + quote.Height
                    separator = Panel()
                    separator.BackColor = Color.FromArgb(47, 47, 47)
                    separator.SetBounds(4, bottom + 10, width - 8, 1)
                    panel.Controls.Add(separator)
                    panel.Height = bottom + 19
                except Exception:
                    dispose_row(row)
                    raise
                return row

            def position_reader(anchor=None, old_scroll=0, at_bottom=False):
                if life["scroll_latest"] and life["panels"]:
                    scroll.ScrollControlIntoView(life["panels"][-1])
                elif life["scroll_top"]:
                    scroll.AutoScrollPosition = Point(0, 0)
                elif life["restore_scroll"] is not None:
                    scroll.AutoScrollPosition = Point(0, min(life["restore_scroll"], scroll.VerticalScroll.Maximum))
                elif at_bottom and life["panels"]:
                    scroll.ScrollControlIntoView(life["panels"][-1])
                elif anchor is not None and anchor[0] in life["rows"]:
                    panel = life["rows"][anchor[0]]["panel"]
                    position = scroll.VerticalScroll.Value + panel.Top - anchor[1]
                    scroll.AutoScrollPosition = Point(0, max(0, position))
                elif old_scroll:
                    scroll.AutoScrollPosition = Point(0, min(old_scroll, scroll.VerticalScroll.Maximum))
                life.update(scroll_latest=False, scroll_top=False, restore_scroll=None)

            def render(force=False):
                if life["closing"] or life["rendering"]:
                    return
                all_messages = feeds[life["view"]].snapshot()["messages"]
                start, end, current, pages = message_page_bounds(len(all_messages), life["page"])
                life["page_count"] = pages
                messages = all_messages[start:end]
                newer = len(all_messages) - end
                live_jump.Visible = newer > 0
                live_jump.Text = f"查看最新（{newer} 条）"
                previous.Enabled = current > 0
                following.Enabled = current < pages - 1
                # Reserve the scrollbar gutter even on an empty/short page.
                # Clearing/adding rows must not alternate the measured width.
                width = max(240, scroll.Width - 42 - SystemInformation.VerticalScrollBarWidth)
                signature = (width, tuple(json.dumps(message, ensure_ascii=False, sort_keys=True)
                                          for message in messages))
                if not force and life["ids"] == signature:
                    if life["scroll_latest"] or life["scroll_top"] or life["restore_scroll"] is not None:
                        position_reader()
                    return
                at_bottom = (scroll.VerticalScroll.Value + scroll.ClientSize.Height
                             >= scroll.VerticalScroll.Maximum - 40)
                old_scroll = scroll.VerticalScroll.Value
                anchor = next(((key, row["panel"].Top) for key, row in life["rows"].items()
                               if row["panel"].Bottom > scroll.Padding.Top), None)
                life["rendering"] = True
                try:
                    with suspend_redraw(scroll):
                        scroll.SuspendLayout()
                        try:
                            created, reused, removed = reconcile_message_rows(
                                life["rows"], messages, width, create_row, dispose_row)
                            panels = [row["panel"] for row in life["rows"].values()]
                            for index, panel in enumerate(panels):
                                if panel.Parent != scroll:
                                    scroll.Controls.Add(panel)
                                if scroll.Controls.GetChildIndex(panel) != index:
                                    scroll.Controls.SetChildIndex(panel, index)
                            life["panels"][:] = panels
                            life["row_creations"] += created
                            life["row_reuses"] += reused
                            life["row_disposals"] += removed
                            life["render_count"] += 1
                            life["ids"] = signature
                        finally:
                            scroll.ResumeLayout(True)
                        position_reader(anchor, old_scroll, at_bottom)
                finally:
                    life["rendering"] = False

            def layout(*_):
                if life["closing"]:
                    return
                width, height = form.ClientSize.Width, form.ClientSize.Height
                header.SetBounds(0, 0, width, 78)
                settings.Location = Point(max(0, width - 77), 9)
                teachers_only.Location = Point(13, 46)
                all_chat.Location = Point(107, 46)
                live_jump.Location = Point(max(0, width - 405), 9)
                status.Location = Point(max(330, width - 238), 14)
                rule.SetBounds(0, 78, width, 1)
                body_height = max(0, height - 117)
                for cached_scroll in scrolls.values():
                    cached_scroll.SetBounds(0, 79, width, body_height)
                source_panel.SetBounds(0, 79, width, body_height)
                footer.SetBounds(0, max(79, height - 38), max(0, width - 320), 38)
                x = max(0, width - 310)
                for item in (older, previous, following, latest):
                    item.Location = Point(x, max(45, height - 32))
                    x += item.Width + 6
                render()

            form.Resize += layout
            layout()

            def on_payload(payload):
                if life["closing"]:
                    return
                try:
                    source_view = payload.get("view_mode", "teacher")
                    feeds[source_view].ingest(payload)
                except (ValidationError, ValueError, TypeError):
                    return
                if payload["source_ready"]:
                    if not life["first_ready"]:
                        life["first_ready"] = True
                        select_source(False)
                else:
                    for feed in feeds.values():
                        with feed.lock:
                            feed.messages.clear()
                    for saved in life["views"].values():
                        saved.update(page=-1, scroll=0)
                        saved["ids"] = None
                    for saved in life["views"].values():
                        for row in saved["rows"].values():
                            dispose_row(row)
                        saved["rows"].clear()
                        saved["panels"].clear()
                    life["ids"] = None
                    life["first_ready"] = False
                refresh()

            def on_status(details):
                record(source_state=details.get("state"), source_reason=details.get("reason"))

            def on_history(details):
                if life["closing"]:
                    return
                source_view = details.get("view_mode", "teacher")
                life["views"][source_view]["history"] = details
                if source_view != life["view"]:
                    return
                life["history"] = details
                older.Text = "暂停加载" if details["state"] == "loading" else "加载更早"
                older.Enabled = details["has_more"] and details["state"] not in ("unavailable", "limit")
                record(history_state=details["state"], history_pages=details["pages"],
                       history_loaded=details["loaded"], history_oldest=details["oldest"],
                       source_audit=details.get("audit"), latest_sync=details.get("latest_sync"),
                       latest_state=details.get("latest_state"))
                refresh()

            def refresh(*_):
                if life["closing"]:
                    return
                feed = feeds[life["view"]]
                summary = feed.status()
                fresh = summary["freshness"] == "fresh" and life["history"].get("latest_state") != "error"
                count = summary["message_count"]
                status.Text = ("● 在线" if fresh else "● 等待连接") + f"  {count} 条"
                status.ForeColor = Color.FromArgb(120, 180, 128) if fresh else muted
                older.Text = "暂停加载" if life["history"]["state"] == "loading" else "加载更早"
                older.Enabled = life["history"]["has_more"] and life["history"]["state"] not in ("unavailable", "limit")
                render()
                current = life["page_count"] if life["page"] < 0 else min(life["page"] + 1, life["page_count"])
                history_state = {"loading": "正在加载历史", "exhausted": "当前来源已到底",
                                 "error": "历史加载暂停，可重试", "unavailable": "当前来源暂不支持补历史",
                                 "limit": "已保留最近 5000 条", "cancelled": "历史加载已暂停"}.get(life["history"]["state"], "可继续加载历史")
                view_state = "查看最新" if life["page"] < 0 else "正在查看历史"
                filter_text = "只看老师" if life["view"] == "teacher" else "全部聊天"
                footer.Text = f"{filter_text} · {view_state} · {history_state}    第 {current}/{life['page_count']} 页 · 共 {count} 条"
                earliest = life["history"].get("oldest")
                if earliest:
                    footer.Text += " · 来源最早 " + datetime.fromisoformat(earliest.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                synced = life["history"].get("latest_sync")
                if synced:
                    footer.Text += " · 校准 " + datetime.fromisoformat(synced.replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
                messages = feed.snapshot()["messages"]
                first_index,last_index,_,_ = message_page_bounds(len(messages),life["page"])
                reader = {"reader_page": current, "reader_pages": life["page_count"],
                          "view_mode": life["view"],
                          "reader_render_count": life["render_count"],
                          "reader_row_creations": life["row_creations"],
                          "reader_row_reuses": life["row_reuses"],
                          "reader_row_disposals": life["row_disposals"],
                          "teacher_cached": feeds["teacher"].status()["message_count"],
                          "all_cached": feeds["all"].status()["message_count"],
                          "reader_students": sum(message.get("role") == "student" for message in messages[first_index:last_index]),
                          "reader_reply_names": sum(bool(message.get("reply_context", {}).get("display_name")) for message in messages[first_index:last_index]),
                          "reader_reply_ids": sum(bool(message.get("reply_context", {}).get("source_id")) for message in messages[first_index:last_index]),
                          "reader_first": messages[first_index]["published_at"] if messages else None,
                          "reader_last": messages[last_index-1]["published_at"] if messages else None,
                          "latest_message_at": messages[-1]["published_at"] if messages else None,
                          "reply_context_count": sum(bool(message.get("reply_context")) for message in messages),
                          "reader_reply_contexts": sum(bool(message.get("reply_context")) for message in messages[first_index:last_index])}
                if (output.get("message_count") != count or output.get("freshness") != summary["freshness"]
                        or any(output.get(key) != value for key,value in reader.items())):
                    record(message_count=count, freshness=summary["freshness"], **reader)

            def close(*_):
                if life["closing"]:
                    return
                life["closing"] = True
                life["picture_pool"].shutdown(wait=False, cancel_futures=True)
                if life["timer"]:
                    life["timer"].Stop()
                    life["timer"].Dispose()
                if life["source"]:
                    life["source"].dispose()
                if life["board"]:
                    life["board"].dispose()
                clear_rows()
                for saved in life["views"].values():
                    if saved["rows"] is not life["rows"]:
                        for row in saved["rows"].values():
                            dispose_row(row)
                        saved["rows"].clear()
                        saved["panels"].clear()
                record(mode="closed")

            form.FormClosing += close
            form.FormClosed += lambda *_: Application.ExitThread()
            child_hwnd = int(form.Handle.ToInt64())
            record(child_hwnd=child_hwnd)
            if board_mode:
                from desktop_board import BoardAttachment
                def board_status(details):
                    record(**details, mode="embedded" if details.get("verified_parent") else "waiting_for_board")
                life["board"] = BoardAttachment(form, board_path, board_status)
                life["board"].start()
            elif args.parent_hwnd:
                from win32_embed import Win32Host
                host = Win32Host()
                fp = host.validate_ths_target(args.parent_hwnd, args.expected_root_hwnd)
                host.attach(child_hwnd, args.parent_hwnd, fp)
                host.resize_to_parent(child_hwnd, args.parent_hwnd)
                form.Show()
            else:
                form.Show()
                record(mode="standalone")

            # The host has its final native parent before any browser is created.
            script = (resources / "desktop" / "desktop-source.js").read_text(encoding="utf-8")
            source = create_source_control(source_panel, base / "webview-profile", script, on_payload, on_status, on_history)
            life["source"] = source
            reconnect.Click += lambda *_: source.navigate_to_source()
            source.navigate_to_source()
            life["timer"] = Timer()
            life["timer"].Interval = 2000
            life["timer"].Tick += refresh
            life["timer"].Start()
            refresh()
            Application.Run()
        except Exception as error:
            import traceback
            record(mode="failed", error=type(error).__name__)
            (base / "startup_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            if form is not None:
                form.Dispose()

    worker = Thread(ThreadStart(gui))
    worker.SetApartmentState(ApartmentState.STA)
    worker.Start()
    worker.Join()
