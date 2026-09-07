"""Theme tokens and stylesheet generation.

Structure mirrors Flow Launcher's theming model: a `BASE` dictionary of layout
metrics that every theme shares, plus named colour palettes that override only
what they need. Their WPF equivalent is `Themes/Base.xaml` merged with a theme
resource dictionary such as `Themes/Darker.xaml`.

The layout metrics below were read from `Themes/Base.xaml` so proportions match
the original rather than being guessed from a screenshot.
"""

from typing import Dict

# Layout metrics, from Themes/Base.xaml.
BASE: Dict[str, int] = {
    "window_width": 600,          # BaseWindowStyle Width
    "window_radius": 5,           # BaseWindowBorderStyle CornerRadius
    "query_height": 48,           # BaseQueryBoxStyle Height
    "query_font_size": 28,        # BaseQueryBoxStyle FontSize
    "query_margin_left": 16,      # BaseQueryBoxStyle Margin "16 7 0 7"
    "query_margin_v": 7,
    "query_padding_right": 68,    # room for the search glyph / plugin icon
    "item_height": 52,            # ResultItemHeight
    "item_radius": 6,             # ItemRadius (themes override; 0 in Base)
    "item_margin_bottom": 0,
    "icon_size": 32,              # BaseItemImageStyle 32x32
    "icon_left_margin": 9,
    "text_margin_left": 6,        # result text Grid Margin "6 0 10 0"
    "text_margin_right": 10,
    "title_font_size": 16,        # BaseItemTitleStyle
    "subtitle_font_size": 13,     # BaseItemSubTitleStyle
    "hotkey_margin_right": 10,
    "max_visible_items": 5,       # Flow's default MaxResultsToShow
    "animation_ms": 360,          # AnimationSpeeds.Medium (Fast 160 / Slow 560)
    "shadow_radius": 24,
}

# Colour palettes. Keys intentionally match the roles Flow Launcher names in
# its theme dictionaries (window background, item selected background, subtitle
# foreground, and so on) so a theme is a drop-in swap.
THEMES: Dict[str, Dict[str, str]] = {
    # Close to the dark theme shown on flowlauncher.com
    "Dark": {
        "window_bg": "#202020",
        "window_border": "#3d3d3d",
        "query_bg": "#272727",
        "query_fg": "#ffffff",
        "query_placeholder": "#7a7a7a",
        "query_suggestion": "#5c5c5c",
        "separator": "#333333",
        "item_bg": "transparent",
        "item_selected_bg": "#0f4a91",
        "title_fg": "#ffffff",
        "title_selected_fg": "#ffffff",
        "subtitle_fg": "#9a9a9a",
        "subtitle_selected_fg": "#cfe3ff",
        "hotkey_fg": "#9a9a9a",
        "hotkey_bg": "#2f2f2f",
        "scrollbar": "#4a4a4a",
        "highlight_fg": "#4cc2ff",     # matched-character highlight
    },
    # Port of Themes/Darker.xaml
    "Darker": {
        "window_bg": "#2F2F2F",
        "window_border": "#2F2F2F",
        "query_bg": "#2F2F2F",
        "query_fg": "#ffffff",
        "query_placeholder": "#8f8f8f",
        "query_suggestion": "#8f8f8f",
        "separator": "#3a3a3a",
        "item_bg": "transparent",
        "item_selected_bg": "#4d4d4d",
        "title_fg": "#ffffff",
        "title_selected_fg": "#ffffff",
        "subtitle_fg": "#8f8f8f",
        "subtitle_selected_fg": "#8f8f8f",
        "hotkey_fg": "#8f8f8f",
        "hotkey_bg": "#3a3a3a",
        "scrollbar": "#5a5a5a",
        "highlight_fg": "#4cc2ff",
    },
    "Light": {
        "window_bg": "#f9f9f9",
        "window_border": "#e5e5e5",
        "query_bg": "#ffffff",
        "query_fg": "#1a1a1a",
        "query_placeholder": "#9a9a9a",
        "query_suggestion": "#b5b5b5",
        "separator": "#e5e5e5",
        "item_bg": "transparent",
        "item_selected_bg": "#cfe4ff",
        "title_fg": "#1a1a1a",
        "title_selected_fg": "#0b2545",
        "subtitle_fg": "#6b6b6b",
        "subtitle_selected_fg": "#2c4a72",
        "hotkey_fg": "#6b6b6b",
        "hotkey_bg": "#ececec",
        "scrollbar": "#c4c4c4",
        "highlight_fg": "#0a63c9",
    },
}

DEFAULT_THEME = "Dark"


class Theme:
    """A resolved theme: shared layout metrics plus one colour palette."""

    def __init__(self, name: str = DEFAULT_THEME):
        self.name = name if name in THEMES else DEFAULT_THEME
        self.colors = dict(THEMES[self.name])
        self.metrics = dict(BASE)

    def c(self, key: str) -> str:
        return self.colors[key]

    def m(self, key: str) -> int:
        return self.metrics[key]

    @staticmethod
    def names():
        return list(THEMES)

    def stylesheet(self) -> str:
        """Build the Qt stylesheet for this theme.

        QSS is the closest analogue to the WPF resource dictionary: the widget
        tree stays fixed and only these values change between themes.
        """
        c, m = self.colors, self.metrics
        return f"""
        /* The window surface itself is painted in LauncherOverlay.paintEvent,
           so the root frame stays transparent and only hosts the layout. */
        #Root {{
            background: transparent;
            border: none;
        }}

        #QueryBox {{
            background: transparent;
            border: none;
            color: {c['query_fg']};
            font-size: {m['query_font_size']}px;
            padding-left: {m['query_margin_left']}px;
            padding-right: {m['query_padding_right']}px;
            selection-background-color: {c['item_selected_bg']};
            selection-color: {c['title_selected_fg']};
        }}

        #Separator {{
            background: {c['separator']};
            border: none;
        }}

        #ResultList {{
            background: transparent;
            border: none;
            outline: none;
        }}

        #ResultList::item {{
            background: {c['item_bg']};
            border: none;
        }}

        QScrollBar:vertical {{
            background: transparent;
            width: 5px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['scrollbar']};
            border-radius: 2px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        """
