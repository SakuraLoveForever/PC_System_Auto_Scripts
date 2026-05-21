"""Style definitions for all 5 design systems."""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class DesignStyle:
    name: str
    description: str

    # Surface colors
    canvas: str          # Main background
    surface: str         # Sidebar/secondary surfaces
    card: str            # Card/panel backgrounds
    card_elevated: str   # Hovered/elevated cards

    # Text colors
    text_primary: str
    text_secondary: str
    text_muted: str

    # Accent
    accent: str          # Primary CTA / interactive
    accent_hover: str    # Hover state
    accent_text: str     # Text on accent background

    # Borders
    border: str
    border_strong: str

    # Geometry
    button_radius: int   # px
    card_radius: int     # px
    input_radius: int    # px

    # Typography
    font_family: str
    font_size_title: int
    font_size_body: int
    font_size_caption: int

    # Misc
    sidebar_width: int = 200


STYLES: Dict[str, DesignStyle] = {
    "Apple": DesignStyle(
        name="Apple",
        description="Dark-tile surfaces, Action Blue accent, SF Pro typography",
        canvas="#000000",
        surface="#1d1d1f",
        card="#272729",
        card_elevated="#2a2a2c",
        text_primary="#ffffff",
        text_secondary="#cccccc",
        text_muted="#999999",
        accent="#2997ff",
        accent_hover="#0071e3",
        accent_text="#ffffff",
        border="#3a3a3c",
        border_strong="#5a5a5c",
        button_radius=12,
        card_radius=11,
        input_radius=8,
        font_family="SF Pro Text, Segoe UI, sans-serif",
        font_size_title=20,
        font_size_body=17,
        font_size_caption=15,
    ),
    "Claude": DesignStyle(
        name="Claude",
        description="Warm cream+coral on dark navy, serif editorial voice",
        canvas="#181715",
        surface="#252320",
        card="#1f1e1b",
        card_elevated="#2a2824",
        text_primary="#faf9f5",
        text_secondary="#a09d96",
        text_muted="#6c6a64",
        accent="#cc785c",
        accent_hover="#a9583e",
        accent_text="#ffffff",
        border="#3a3732",
        border_strong="#5a5650",
        button_radius=8,
        card_radius=12,
        input_radius=8,
        font_family="Inter, Segoe UI, sans-serif",
        font_size_title=21,
        font_size_body=17,
        font_size_caption=16,
    ),
    "Linear": DesignStyle(
        name="Linear",
        description="Near-black canvas, lavender-blue accent, software-craft density",
        canvas="#010102",
        surface="#0f1011",
        card="#141516",
        card_elevated="#18191a",
        text_primary="#f7f8f8",
        text_secondary="#8a8f98",
        text_muted="#62666d",
        accent="#5e6ad2",
        accent_hover="#828fff",
        accent_text="#ffffff",
        border="#23252a",
        border_strong="#34343a",
        button_radius=8,
        card_radius=12,
        input_radius=8,
        font_family="Inter, Segoe UI, sans-serif",
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    "NVIDIA": DesignStyle(
        name="NVIDIA",
        description="Engineering-grade: black canvas, NVIDIA Green, 2px angular geometry",
        canvas="#000000",
        surface="#1a1a1a",
        card="#0d0d0d",
        card_elevated="#141414",
        text_primary="#ffffff",
        text_secondary="#b3b3b3",
        text_muted="#757575",
        accent="#76b900",
        accent_hover="#5a8d00",
        accent_text="#000000",
        border="#3a3a3a",
        border_strong="#5e5e5e",
        button_radius=2,
        card_radius=2,
        input_radius=2,
        font_family="Inter, Segoe UI, sans-serif",
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    "Spotify": DesignStyle(
        name="Spotify",
        description="Immersive near-black, Spotify Green, pill geometry, heavy shadows",
        canvas="#121212",
        surface="#181818",
        card="#1f1f1f",
        card_elevated="#252525",
        text_primary="#ffffff",
        text_secondary="#b3b3b3",
        text_muted="#999999",
        accent="#1ed760",
        accent_hover="#1fdf64",
        accent_text="#000000",
        border="#4d4d4d",
        border_strong="#7c7c7c",
        button_radius=12,
        card_radius=8,
        input_radius=8,
        font_family="Segoe UI, CircularSp, sans-serif",
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
}

DEFAULT_STYLE = "Linear"
