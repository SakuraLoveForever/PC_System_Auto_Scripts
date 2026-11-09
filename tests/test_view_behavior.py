"""Check view behavior without opening desktop windows."""
import unittest

import main


class ViewBehaviorTests(unittest.TestCase):
    def test_startup_rows_have_no_floating_detail_window(self):
        self.assertFalse(hasattr(main.App, "_show_cell_tip"))
