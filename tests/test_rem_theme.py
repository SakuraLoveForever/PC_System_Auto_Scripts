"""Character assets keep their colours and transparency in every theme."""
import unittest

from PIL import ImageChops

import rem_theme


class RemArtTests(unittest.TestCase):
    def test_rem_theme_available_and_chinese_only(self):
        from i18n import I18n, SUPPORTED_LANGS, T
        from styles import STYLES, DEFAULT_STYLE

        self.assertIn("Rem", STYLES)
        self.assertEqual(DEFAULT_STYLE, "Rem")
        self.assertEqual(SUPPORTED_LANGS, ["zh"])
        self.assertEqual(I18n("en").t("app.title"), "PC 系统自动脚本")
        self.assertTrue(all(set(entry) == {"zh"} for entry in T.values()))

    def test_style_switching_is_wired(self):
        """Six design systems exist, so the UI must expose a picker for them."""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
        methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertIn("_switch_style", methods)

    def test_theme_does_not_recolour_character(self):
        original = rem_theme.draw_rem(128)
        tinted = rem_theme.draw_rem(128, {"hair": "#ff0000", "skin": "#00ff00"})
        self.assertIsNone(ImageChops.difference(original, tinted).getbbox(alpha_only=False))

    def test_backdrop_preserves_transparent_character_edges(self):
        from unittest.mock import patch
        from PIL import Image

        transparent = Image.new("RGBA", (128, 128), (255, 0, 0, 0))
        with patch.object(rem_theme, "rem_sprite", return_value=transparent):
            rem_theme._backdrop.cache_clear()
            result = rem_theme.backdrop(400, 300, {**rem_theme.CHAR_DEFAULTS, "hair_l": "#101820"},
                                        "#101820", "#101820")
        self.assertEqual(result.getpixel((399, 150)), (16, 24, 32, 255))
        rem_theme._backdrop.cache_clear()
