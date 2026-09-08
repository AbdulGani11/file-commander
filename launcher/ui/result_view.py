"""Result list rendering.

The row layout follows `ResultListBox.xaml`, which lays each row out as a three
column grid:

    [ icon area ] [ title / subtitle (stretch) ] [ hotkey badge (auto) ]

Rows are custom painted rather than assembled from nested widgets. With one
delegate instead of four widgets per row, changing the theme is a repaint and
scrolling stays smooth even when the list is rebuilt on every keystroke.
"""

import os
import sys
import time
from collections import OrderedDict
from typing import List

from PySide6.QtCore import QFileInfo, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileIconProvider,
    QListWidget,
    QStyle,
    QStyledItemDelegate,
)

from .shell_icon import is_shell_path, pixmap_for_shell_path
from .theme import Theme

# Payload role holding the Result object for a row.
RESULT_ROLE = Qt.UserRole + 1

# Asking the shell for an icon touches the disk, so results are cached. Without
# this the same icons would be fetched again on every keystroke, since the list
# is rebuilt each time the query changes.
#
# What is stored matters as much as the caching. QFileIconProvider.icon() is
# lazy: it returns a QIcon without touching the shell, and the extraction only
# happens when a pixmap is finally asked for -- inside paint(), on the UI
# thread. Caching the QIcon cached the promise and left the real work to be
# repeated on every repaint: the delegate spent 2,379ms painting rows across a
# short typing run, against 348ms once the pixmap was what got cached.
_ICON_CACHE = OrderedDict()
_ICON_PROVIDER = None

# Ceiling on cached pixmaps, discarding the least recently drawn. A rendered
# pixmap holds real memory, unlike the empty handle this used to keep, so an
# unbounded cache would grow all session.
MAX_CACHED_ICONS = 512

# How long one batch of icon rendering may run before yielding to the event
# loop. Roughly half a 60Hz frame, so a keystroke never waits for a whole batch.
ICON_SLICE_SECONDS = 0.008

# Windows gives these types an icon of their own per file, read out of the file
# itself. Everything else shows one icon per file type, so a hundred .txt rows
# are a hundred copies of the same picture and need only one shell call between
# them. At 2-10ms each that is the difference between a fresh result set
# costing one extraction and costing twenty; it cut a typing run from 285 to 11.
PER_FILE_SUFFIXES = {".exe", ".lnk", ".url", ".ico", ".cpl", ".msc", ".scr"}


def _icon_identity(path: str, kind: str) -> str:
    """What two rows must have in common to share one rendered icon."""
    if is_shell_path(path):
        # Never share these. A shell identifier has no file extension, and
        # splitting one produces nonsense: everything after the last dot in
        # Microsoft.WindowsCalculator_8wekyb3d8bbwe!App would become the "type".
        return path
    if kind == "folder":
        return "<folder>"
    suffix = os.path.splitext(path)[1].lower()
    if kind == "app" or suffix in PER_FILE_SUFFIXES:
        return path                          # this file has its own icon
    return "<type>" + suffix


def _icon_key(path: str, size: int, ratio: float, kind: str = ""):
    return (_icon_identity(path, kind), size, round(ratio, 2))


def _cached_pixmap(key):
    """Return an already-rendered icon, or None if it has not been made yet."""
    pixmap = _ICON_CACHE.get(key)
    if pixmap is not None:
        _ICON_CACHE.move_to_end(key)        # least recently drawn falls out first
    return pixmap


def warm_icon_provider() -> None:
    """Pay the shell's one-time icon start-up cost before the user can see it.

    The first extraction in a process costs around 300ms whatever file it is
    asked about -- 325ms for a program, 272ms for a text file, in whichever
    order they are requested. That is the shell starting up, not the icon.
    Every extraction after it costs 2-10ms. Doing it here, while the window is
    still hidden, keeps it off the first keystroke.
    """
    global _ICON_PROVIDER

    try:
        if _ICON_PROVIDER is None:
            _ICON_PROVIDER = QFileIconProvider()
        # Not stored: this is about waking the shell, not about this icon
        _ICON_PROVIDER.icon(QFileInfo(sys.executable)).pixmap(QSize(32, 32))
    except Exception:
        pass


def _extract_pixmap(key, path: str = "") -> QPixmap:
    """Ask the shell for an icon and render it. Slow: never call from paint().

    Costs 2-10ms once the shell is warm, and around 300ms if it is not; see
    warm_icon_provider. A shortcut pointing at an unreachable network share can
    block for far longer, which is the case the deferred queue really guards.
    """
    global _ICON_PROVIDER

    identity, size, ratio = key
    path = path or identity                  # identity is the path, when unshared
    try:
        if is_shell_path(path):
            # A Store app has no file to read an icon from; the shell knows it
            # by identifier instead.
            pixmap = pixmap_for_shell_path(path, size)
        else:
            if _ICON_PROVIDER is None:
                _ICON_PROVIDER = QFileIconProvider()
            icon = _ICON_PROVIDER.icon(QFileInfo(path))
            try:
                pixmap = icon.pixmap(QSize(size, size), ratio)
            except TypeError:
                # Older bindings have no device-pixel-ratio overload
                pixmap = icon.pixmap(QSize(size, size))
    except Exception:
        pixmap = QPixmap()

    _ICON_CACHE[key] = pixmap
    if len(_ICON_CACHE) > MAX_CACHED_ICONS:
        _ICON_CACHE.popitem(last=False)
    return pixmap


def _pixmap_for(path: str, size: int, ratio: float = 1.0, kind: str = "") -> QPixmap:
    """Return the shell icon for a path, rendering it now if it is not cached.

    `ratio` is the painter's device pixel ratio, so the icon stays sharp on a
    display scaled above 100 percent. Painting uses the deferred path instead;
    this is the direct form, for callers that can afford to wait.
    """
    key = _icon_key(path, size, ratio, kind)
    cached = _cached_pixmap(key)
    if cached is not None:
        return cached
    return _extract_pixmap(key, path)


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

    def _paint_pending_icon(self, painter: QPainter, rect: QRect) -> None:
        """Hold an icon's place with a plain outline while the shell is asked.

        Deliberately not the emoji glyph used for the no-icon case. An emoji is
        a colour glyph from a separate font, and drawing one costs far more than
        the row it sits in -- fine as a rare fallback, but this runs on the
        first paint of every uncached row, which is every new result.
        """
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(self.theme.c("separator")), 1))
        inset = rect.adjusted(4, 4, -4, -4)
        painter.drawRoundedRect(inset, 3, 3)
        painter.restore()

    def _paint_icon(self, painter: QPainter, rect: QRect, result) -> None:
        """Draw the real Windows icon for this row, falling back to a glyph.

        QFileIconProvider asks the Windows shell for a path's icon, which is the
        same icon Explorer shows. That covers executables, shortcuts and
        documents without any icon extraction code of our own.
        """
        path = result.context.get("path") if result.context else None
        if path is not None:
            ratio = painter.device().devicePixelRatio()
            # A Store app names what its icon should be looked up by, because
            # its "path" is an identifier with no file behind it.
            text = str(result.context.get("icon_target") or path)
            key = _icon_key(text, rect.width(), ratio, result.icon or "")
            pixmap = _cached_pixmap(key)

            if pixmap is None:
                # Not rendered yet. Hand it to the view to fetch between paints
                # so no keystroke ever waits on the shell, and hold the space
                # with a plain outline until it arrives.
                view = self.parent()
                if isinstance(view, ResultList):
                    view.request_icon(key, text)
                self._paint_pending_icon(painter, rect)
                return
            elif not pixmap.isNull():
                # Centre rather than stretch, matching QIcon.paint's AlignCenter
                width = int(pixmap.width() / pixmap.devicePixelRatio())
                height = int(pixmap.height() / pixmap.devicePixelRatio())
                painter.drawPixmap(
                    rect.left() + (rect.width() - width) // 2,
                    rect.top() + (rect.height() - height) // 2,
                    pixmap,
                )
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

        # Icons the delegate asked for but that are not rendered yet.
        self._icon_queue: List[tuple] = []
        self._icon_wanted = set()
        self._icon_timer = QTimer(self)
        self._icon_timer.setSingleShot(True)
        self._icon_timer.setInterval(0)          # next turn of the event loop
        self._icon_timer.timeout.connect(self._render_queued_icons)

    def request_icon(self, key, path: str) -> None:
        """Queue one icon for rendering after this paint finishes.

        The path is carried alongside the key because a key shared by every file
        of one type no longer names a file the shell can be asked about.
        """
        if key in self._icon_wanted:
            return
        self._icon_wanted.add(key)
        self._icon_queue.append((key, path))
        self._icon_timer.start()

    def _render_queued_icons(self) -> None:
        """Render queued icons in short slices, repainting as they arrive.

        Extraction used to happen inside paint(), which is why the query box
        fell behind typing. Doing it here keeps every slice short enough that
        keystrokes still get through between them, and means a pathological
        case -- a shortcut whose target is an unreachable share -- delays an
        icon rather than the window.
        """
        deadline = time.perf_counter() + ICON_SLICE_SECONDS
        rendered = False

        while self._icon_queue:
            key, path = self._icon_queue.pop(0)
            self._icon_wanted.discard(key)
            _extract_pixmap(key, path)
            rendered = True
            if time.perf_counter() >= deadline:
                break

        if rendered:
            self.viewport().update()
        if self._icon_queue:
            self._icon_timer.start()             # finish the rest next turn

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._delegate.set_theme(theme)
        self.viewport().update()

    def visible_height(self) -> int:
        """Pixel height needed for the current rows, capped at the maximum."""
        rows = min(self.count(), self.theme.m("max_visible_items"))
        return rows * self.theme.m("item_height")
