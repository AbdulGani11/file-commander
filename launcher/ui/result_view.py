"""Result list rendering.

The row layout follows `ResultListBox.xaml`, which lays each row out as a three
column grid:

    [ icon area ] [ title / subtitle (stretch) ] [ hotkey badge (auto) ]

Rows are custom painted rather than assembled from nested widgets. With one
delegate instead of four widgets per row, changing the theme is a repaint and
scrolling stays smooth even when the list is rebuilt on every keystroke.
"""

from PySide6.QtCore import QFileInfo, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFileIconProvider,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
)

from .theme import Theme

# Payload role holding the Result object for a row.
RESULT_ROLE = Qt.UserRole + 1

# Asking the shell for an icon touches the disk, so results are cached. Without
# this the same icons would be fetched again on every keystroke, since the list
# is rebuilt each time the query changes.
_ICON_CACHE = {}
_ICON_PROVIDER = None


def _icon_for(path: str):
    """Return the Windows shell icon for a path, or None if unavailable."""
    global _ICON_PROVIDER

    if path in _ICON_CACHE:
        return _ICON_CACHE[path]

    try:
        if _ICON_PROVIDER is None:
            _ICON_PROVIDER = QFileIconProvider()
        icon = _ICON_PROVIDER.icon(QFileInfo(path))
    except Exception:
        icon = None

    _ICON_CACHE[path] = icon
    return icon


class ResultDelegate(QStyledItemDelegate):
    """Paints one result row: icon, title, subtitle, and hotkey hint."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, self.theme.m("item_height"))

    def paint(self, painter: QPainter, option, index) -> None:
        result = index.data(RESULT_ROLE)
        if result is None:
            return

        t = self.theme
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = option.rect

        # The "no results" notice is a message, not a row you can act on, so it
        # gets no icon, no shortcut badge and no selection highlight.
        if result.context and result.context.get("placeholder"):
            self._paint_placeholder(painter, rect, result)
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        # selection pill
        if selected:
            path = QPainterPath()
            inner = QRect(
                rect.left() + 4, rect.top() + 2, rect.width() - 8, rect.height() - 4
            )
            path.addRoundedRect(
                inner, t.m("item_radius"), t.m("item_radius")
            )
            painter.fillPath(path, QColor(t.c("item_selected_bg")))

        # column 1: icon
        icon_size = t.m("icon_size")
        icon_x = rect.left() + t.m("query_margin_left") + t.m("icon_left_margin")
        icon_rect = QRect(
            icon_x,
            rect.top() + (rect.height() - icon_size) // 2,
            icon_size,
            icon_size,
        )
        self._paint_icon(painter, icon_rect, result)

        # column 3: hotkey hint (Alt+N), measured first so text can avoid it
        hotkey = result.context.get("hotkey", "") if result.context else ""
        hotkey_w = 0
        if hotkey:
            hk_font = QFont(option.font)
            hk_font.setPixelSize(t.m("subtitle_font_size"))
            hotkey_w = QFontMetrics(hk_font).horizontalAdvance(hotkey) + 16
            hk_rect = QRect(
                rect.right() - t.m("hotkey_margin_right") - hotkey_w,
                rect.top(),
                hotkey_w,
                rect.height(),
            )
            painter.setFont(hk_font)
            painter.setPen(QColor(t.c("hotkey_fg")))
            painter.drawText(hk_rect, Qt.AlignRight | Qt.AlignVCenter, hotkey)

        # column 2: title and subtitle
        text_left = icon_rect.right() + t.m("text_margin_left") + 6
        text_right = rect.right() - t.m("text_margin_right") - hotkey_w
        text_w = max(0, text_right - text_left)

        has_subtitle = bool(result.subtitle)

        title_font = QFont(option.font)
        title_font.setPixelSize(t.m("title_font_size"))
        painter.setFont(title_font)
        painter.setPen(
            QColor(t.c("title_selected_fg") if selected else t.c("title_fg"))
        )

        fm_title = QFontMetrics(title_font)
        if has_subtitle:
            title_rect = QRect(text_left, rect.top() + 8, text_w, fm_title.height())
        else:
            title_rect = QRect(text_left, rect.top(), text_w, rect.height())

        painter.drawText(
            title_rect,
            Qt.AlignLeft | (Qt.AlignTop if has_subtitle else Qt.AlignVCenter),
            fm_title.elidedText(result.title, Qt.ElideRight, text_w),
        )

        if has_subtitle:
            sub_font = QFont(option.font)
            sub_font.setPixelSize(t.m("subtitle_font_size"))
            painter.setFont(sub_font)
            painter.setPen(
                QColor(
                    t.c("subtitle_selected_fg") if selected else t.c("subtitle_fg")
                )
            )
            fm_sub = QFontMetrics(sub_font)
            sub_rect = QRect(
                text_left,
                title_rect.bottom() + 2,
                text_w,
                fm_sub.height(),
            )
            painter.drawText(
                sub_rect,
                Qt.AlignLeft | Qt.AlignTop,
                fm_sub.elidedText(result.subtitle, Qt.ElideMiddle, text_w),
            )

        painter.restore()

    def _paint_placeholder(self, painter: QPainter, rect: QRect, result) -> None:
        """Draw the 'no results' notice: centred, dim, clearly not a result."""
        t = self.theme
        left = rect.left() + t.m("query_margin_left")
        width = rect.width() - t.m("query_margin_left") - t.m("text_margin_right")

        title_font = QFont(painter.font())
        title_font.setPixelSize(t.m("title_font_size"))
        painter.setFont(title_font)
        painter.setPen(QColor(t.c("subtitle_fg")))
        fm = QFontMetrics(title_font)
        painter.drawText(
            QRect(left, rect.top() + 8, width, fm.height()),
            Qt.AlignLeft | Qt.AlignTop,
            fm.elidedText(result.title, Qt.ElideRight, width),
        )

        if result.subtitle:
            sub_font = QFont(painter.font())
            sub_font.setPixelSize(t.m("subtitle_font_size"))
            painter.setFont(sub_font)
            painter.setPen(QColor(t.c("query_suggestion")))
            fm_sub = QFontMetrics(sub_font)
            painter.drawText(
                QRect(left, rect.top() + 8 + fm.height() + 2, width, fm_sub.height()),
                Qt.AlignLeft | Qt.AlignTop,
                result.subtitle,
            )

    def _paint_icon(self, painter: QPainter, rect: QRect, result) -> None:
        """Draw the real Windows icon for this row, falling back to a glyph.

        QFileIconProvider asks the Windows shell for a path's icon, which is the
        same icon Explorer shows. That covers executables, shortcuts and
        documents without any icon extraction code of our own.
        """
        path = result.context.get("path") if result.context else None
        if path is not None:
            icon = _icon_for(str(path))
            if icon is not None and not icon.isNull():
                icon.paint(painter, rect, Qt.AlignCenter)
                return

        # No path, or the shell had no icon for it
        t = self.theme
        glyph = {"folder": "\U0001F4C1", "file": "\U0001F4C4"}.get(
            result.icon or "", "●"
        )
        font = QFont(painter.font())
        font.setPixelSize(int(t.m("icon_size") * 0.62))
        painter.setFont(font)
        painter.setPen(QColor(t.c("title_fg")))
        painter.drawText(rect, Qt.AlignCenter, glyph)


class ResultList(QListWidget):
    """The result list, sized to show at most `max_visible_items` rows."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._delegate = ResultDelegate(theme, self)

        self.setObjectName("ResultList")
        self.setItemDelegate(self._delegate)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setFocusPolicy(Qt.NoFocus)          # the query box keeps focus
        self.setFrameShape(QListWidget.NoFrame)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._delegate.set_theme(theme)
        self.viewport().update()

    def visible_height(self) -> int:
        """Pixel height needed for the current rows, capped at the maximum."""
        rows = min(self.count(), self.theme.m("max_visible_items"))
        return rows * self.theme.m("item_height")
