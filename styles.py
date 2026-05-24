"""Style definitions — Magic UI inspired desktop design system."""

from dataclasses import dataclass
from typing import Dict


@dataclass
class DesignStyle:
    name: str
    description: str

    # Surface colors
    canvas: str          # Main background (base-100)
    surface: str         # Sidebar/secondary surfaces (base-200)
    card: str            # Card/panel backgrounds (base-200 elevated)
    card_elevated: str   # Hovered/elevated cards

    # Text colors (base-content)
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

    # Semantic status colors
    success: str = "#36d399"
    warning: str = "#fbbd23"
    error: str = "#f87272"
    info: str = "#3abff8"

    # Geometry
    button_radius: int = 10
    card_radius: int = 14
    input_radius: int = 8

    # Typography
    font_family: str = "Inter, Segoe UI, sans-serif"
    font_size_title: int = 19
    font_size_body: int = 17
    font_size_caption: int = 15

    # Misc
    sidebar_width: int = 200


STYLES: Dict[str, DesignStyle] = {
    # ── Magic Dark (default) ────────────────────────────────────────────
    "Magic Dark": DesignStyle(
        name="Magic Dark",
        description="Near-black glass surfaces with violet-to-pink Magic UI accents",
        canvas="#050510",
        surface="#0a0a16",
        card="#0f1020",
        card_elevated="#17182c",
        text_primary="#f7f7fb",
        text_secondary="#b8bbd4",
        text_muted="#767a99",
        accent="#9e7aff",
        accent_hover="#fe8bbb",
        accent_text="#ffffff",
        border="#27283f",
        border_strong="#54577a",
        info="#8ab4ff",
        button_radius=999,
        card_radius=8,
        input_radius=8,
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    # ── Magic Slate ─────────────────────────────────────────────────────
    "Magic Slate": DesignStyle(
        name="Magic Slate",
        description="Cool graphite dashboard with cyan perimeter highlights",
        canvas="#020617",
        surface="#07111f",
        card="#0b1220",
        card_elevated="#111c2f",
        text_primary="#f8fafc",
        text_secondary="#a8b3cf",
        text_muted="#64748b",
        accent="#67e8f9",
        accent_hover="#38bdf8",
        accent_text="#0f172a",
        border="#1d2a44",
        border_strong="#385174",
        info="#67e8f9",
        button_radius=999,
        card_radius=8,
        input_radius=8,
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    # ── Magic Aurora ────────────────────────────────────────────────────
    "Magic Aurora": DesignStyle(
        name="Magic Aurora",
        description="Deep green-black with emerald glow and soft glass panels",
        canvas="#04110d",
        surface="#071711",
        card="#0b1d16",
        card_elevated="#10291f",
        text_primary="#ecfdf5",
        text_secondary="#a7f3d0",
        text_muted="#6a8f80",
        accent="#34d399",
        accent_hover="#5eead4",
        accent_text="#03110c",
        border="#1d3a31",
        border_strong="#3e7c68",
        success="#34d399",
        info="#5eead4",
        button_radius=999,
        card_radius=8,
        input_radius=8,
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    # ── Magic Ember ─────────────────────────────────────────────────────
    "Magic Ember": DesignStyle(
        name="Magic Ember",
        description="Warm dark glass with orange-pink shimmer accents",
        canvas="#130b0b",
        surface="#1a0f10",
        card="#1f1114",
        card_elevated="#2b171b",
        text_primary="#fff7ed",
        text_secondary="#fed7aa",
        text_muted="#9a7563",
        accent="#fb7185",
        accent_hover="#f97316",
        accent_text="#ffffff",
        border="#40212a",
        border_strong="#7c3f45",
        warning="#fbbf24",
        info="#fb7185",
        button_radius=999,
        card_radius=8,
        input_radius=8,
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
    # ── Magic Ocean ─────────────────────────────────────────────────────
    "Magic Ocean": DesignStyle(
        name="Magic Ocean",
        description="Blue-black work surface with electric aqua focus rings",
        canvas="#020c16",
        surface="#061423",
        card="#081a2b",
        card_elevated="#0d243a",
        text_primary="#e0f7ff",
        text_secondary="#98d6ef",
        text_muted="#567b91",
        accent="#22d3ee",
        accent_hover="#60a5fa",
        accent_text="#0b1120",
        border="#14324d",
        border_strong="#285d85",
        info="#38bdf8",
        button_radius=999,
        card_radius=8,
        input_radius=8,
        font_size_title=19,
        font_size_body=17,
        font_size_caption=15,
    ),
}

_LEGACY_THEME_PREFIX = "daisy" + "UI"

STYLE_ALIASES = {
    f"{_LEGACY_THEME_PREFIX} Dark": "Magic Dark",
    f"{_LEGACY_THEME_PREFIX} Night": "Magic Slate",
    f"{_LEGACY_THEME_PREFIX} Forest": "Magic Aurora",
    f"{_LEGACY_THEME_PREFIX} Sunset": "Magic Ember",
    f"{_LEGACY_THEME_PREFIX} Ocean": "Magic Ocean",
    "Linear": "Magic Dark",
    "Apple": "Magic Slate",
    "Claude": "Magic Ember",
    "NVIDIA": "Magic Aurora",
    "Spotify": "Magic Ocean",
}

DEFAULT_STYLE = "Magic Dark"
