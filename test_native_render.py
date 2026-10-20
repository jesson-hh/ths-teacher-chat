"""Message-update regression checks, including invisible owned WinForms controls."""

import copy
import os
import queue
import unittest

from native_board_app import reconcile_message_rows, enable_double_buffer, suspend_redraw


class MessageRowsTest(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        self.disposed = []

    def create(self, message, width):
        return {"panel": object(), "images": [object()], "text": message, "width": width}

    def update(self, messages, width=700, create=None):
        return reconcile_message_rows(self.rows, messages, width, create or self.create, self.disposed.append)

    def test_new_message_reuses_59_rows_and_their_loaded_images(self):
        messages = [{"id": str(index), "text": "消息"} for index in range(60)]
        self.assertEqual(self.update(messages), (60, 0, 0))
        before = dict(self.rows)
        self.assertEqual(self.update(messages[1:] + [{"id": "60", "text": "新消息"}]), (1, 59, 1))
        for key in map(str, range(1, 60)):
            self.assertIs(self.rows[key], before[key])
            self.assertIs(self.rows[key]["images"][0], before[key]["images"][0])
        self.assertEqual(self.disposed, [before["0"]])
        self.assertEqual(list(self.rows), list(map(str, range(1, 61))))

    def test_identical_snapshots_do_not_create_or_dispose_rows(self):
        messages = [{"id": "a", "text": "原文", "reply_context": {"text": "提问"}}]
        self.update(messages)
        original = self.rows["a"]
        for _ in range(30):
            self.assertEqual(self.update(copy.deepcopy(messages)), (0, 1, 0))
        self.assertIs(self.rows["a"], original)
        self.assertEqual(self.disposed, [])

    def test_late_reply_updates_only_the_changed_message(self):
        messages = [{"id": "a", "text": "回答"}, {"id": "b", "text": "第二条"}]
        self.update(messages)
        before = dict(self.rows)
        messages[0]["reply_context"] = {"text": "补全的提问", "display_name": "学员"}
        self.assertEqual(self.update(messages), (1, 1, 1))
        self.assertIs(self.rows["b"], before["b"])
        self.assertIsNot(self.rows["a"], before["a"])

    def test_failed_row_build_preserves_previous_page(self):
        self.update([{"id": "a", "text": "保留页面"}])
        before = dict(self.rows)
        def failing(message, width):
            if message["id"] == "bad":
                raise ValueError("simulated control construction failure")
            return self.create(message, width)
        with self.assertRaises(ValueError):
            self.update([{"id": "new"}, {"id": "bad"}], create=failing)
        self.assertEqual(self.rows, before)
        self.assertEqual(len(self.disposed), 1)
        self.assertIsNot(self.disposed[0], before["a"])

    def test_empty_state_and_width_changes(self):
        self.assertEqual(self.update([]), (1, 0, 0))
        self.assertEqual(self.update([]), (0, 1, 0))
        self.assertEqual(self.update([{"id": "a"}]), (1, 0, 1))
        self.assertEqual(self.update([{"id": "a"}], width=900), (1, 0, 1))

    @unittest.skipUnless(os.name == "nt", "Windows-only hidden control check")
    def test_buffering_and_redraw_cleanup_on_owned_hidden_controls(self):
        from desktop_webview import _load_dotnet
        _load_dotnet()
        from System.Threading import Thread, ThreadStart, ApartmentState
        from System.Reflection import BindingFlags
        from System.Windows.Forms import Form, Panel
        result = queue.Queue()
        def worker():
            form = Form()
            try:
                panel = enable_double_buffer(Panel())
                form.Controls.Add(panel)
                _ = panel.Handle
                prop = panel.GetType().GetProperty("DoubleBuffered", BindingFlags.Instance | BindingFlags.NonPublic)
                self.assertTrue(prop.GetValue(panel, None))
                self.assertFalse(panel.Visible)
                with self.assertRaises(ValueError):
                    with suspend_redraw(panel):
                        raise ValueError("layout failed")
                self.assertFalse(panel.Visible)
                self.assertFalse(form.Visible)
                result.put(None)
            except BaseException as error:
                result.put(error)
            finally:
                form.Dispose()
        thread = Thread(ThreadStart(worker))
        thread.SetApartmentState(ApartmentState.STA)
        thread.Start()
        thread.Join()
        error = result.get(timeout=2)
        if error is not None:
            raise error


if __name__ == "__main__":
    unittest.main(verbosity=2)
