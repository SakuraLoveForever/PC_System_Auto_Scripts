"""Check view behavior without opening desktop windows."""
import unittest
from unittest.mock import MagicMock

import main


class ViewBehaviorTests(unittest.TestCase):
    def test_compact_window_fits_two_rows_of_controls(self):
        app = MagicMock()
        app._compact_mode = False
        app.winfo_x.return_value = 10
        app.winfo_y.return_value = 20
        main.App._set_compact_mode(app, True)
        app.minsize.assert_called_once_with(260, 72)
        app.geometry.assert_called_once_with("260x72+10+20")

    def test_startup_rows_have_no_floating_detail_window(self):
        self.assertFalse(hasattr(main.App, "_show_cell_tip"))
