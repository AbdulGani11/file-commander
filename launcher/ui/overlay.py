"""The launcher overlay window.

Window composition follows `MainWindow.xaml`: a frameless, transparent,
always-on-top window whose visible surface is a single rounded root border
containing a vertical stack of query box, separator, and result list.

Threading: `Dispatcher.query()` blocks, so it runs on a worker thread and
delivers results back through a queued signal. The UI thread only ever paints.
Each request carries a sequence number and late replies from superseded
queries are dropped, which complements the cancellation inside the dispatcher.
"""

import json
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from ..dispatcher import Dispatcher
from ..models import Result
from .result_view import RESULT_ROLE, ResultList
from .theme import DEFAULT_SCALE, DEFAULT_THEME, Theme, clamp_scale

# Outer transparent padding reserved for the drop shadow.
SHADOW_PAD = 24

# Where the window remembers its position and scale between runs. Sits beside
# the engine's own dotfiles, and like them a failure to read or write it is
# never worth interrupting the user for.
SETTINGS_PATH = Path.home() / ".filefind_launcher.json"

# Side of the scale handle drawn at the right of the query bar.
#
# It is a real child widget on painted pixels, not a zone of the shadow padding.
# The first attempt put the resize zone in that padding and nobody could find
# it: the padding is invisible, so there is nothing to aim at. (It does mostly
# receive clicks -- only its outermost pixels, where the shadow's alpha reaches
# zero, fall through to the window behind -- but being hittable is not the same
# as being discoverable, and only the second one was the problem.)
GRIP_SIZE = 22

# How far the pointer must travel inside the query box before the press is
# treated as dragging the window rather than placing the caret.
DRAG_THRESHOLD = 4


class QueryWorker(QObject):
    """Runs dispatcher queries off the UI thread."""

    finished = Signal(int, object)      # (sequence, list[Result])

    def __init__(self, dispatcher: Dispatcher):
        super().__init__()
        self._dispatcher = dispatcher

    @Slot(int, str)
    def run(self, seq: int, text: str) -> None:
        try:
            results = self._dispatcher.query(text)
        except Exception:
            results = []
        self.finished.emit(seq, results)


class ScaleGrip(QWidget):
    """The visible handle at the right of the query bar that scales the window.

    A child widget, not a region of the parent's paint, for two reasons: it is
    opaque so Windows will deliver clicks to it, and it gives the user
    something to see. The previous version put the resize zone in the
    transparent shadow padding, where it was both invisible and unclickable.
    """

    def __init__(self, overlay: "LauncherOverlay"):
        super().__init__(overlay.root)
        self._overlay = overlay
        self.setFixedSize(GRIP_SIZE, GRIP_SIZE)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("Drag to resize; double-click to reset")
        self._origin = QPoint()
        self._start_scale = DEFAULT_SCALE
        self._dragging = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(self._overlay.theme.c("hotkey_fg")), 1.4))

        # Three stepped diagonals, the usual grip shorthand
        span = GRIP_SIZE - 8
        for offset in (0, 5, 10):
            painter.drawLine(4 + offset, span, span, 4 + offset)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._dragging = True
        self._origin = event.globalPosition().toPoint()
        self._start_scale = self._overlay.theme.scale

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        delta = event.globalPosition().toPoint().x() - self._origin.x()
        base = max(1, self._overlay.width())
        self._overlay.set_scale(self._start_scale * (base + delta * 2) / base)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self._overlay._save_settings()

    def mouseDoubleClickEvent(self, event) -> None:
        """Back to 1.0, since a badly scaled window is awkward to drag back."""
        self._overlay.set_scale(DEFAULT_SCALE)
        self._overlay._save_settings()


class LauncherOverlay(QWidget):
    """Frameless search overlay driven by a Dispatcher."""

    request_query = Signal(int, str)

    # Emitted from the global hotkey thread. A Qt signal is the only safe way
    # to reach the UI from another thread: it queues the call onto the Qt event
    # loop. QTimer.singleShot does not work here, because a timer belongs to
    # the thread that created it and the hotkey thread runs no event loop.
    hotkey_pressed = Signal()

    def __init__(self, dispatcher: Dispatcher, theme_name: str = DEFAULT_THEME):
        super().__init__()
        self._dispatcher = dispatcher
        self._seq = 0
        self._results: List[Result] = []

        # Cached window surface, rebuilt only when the size or theme changes.
        # Set before any widget exists, because a paint can arrive at any point
        # once the window is built.
        self._surface: Optional[QPixmap] = None
        self._surface_key = None

        # Position and scale carried over from the last run
        self._settings = self._load_settings()
        self.theme = Theme(theme_name, self._settings.get("scale", DEFAULT_SCALE))

        # Window-move state. _drag_mode is None, "armed" once the bar is pressed
        # somewhere draggable, then "move" once the pointer has actually
        # travelled. Scaling keeps its own state inside ScaleGrip.
        self._drag_mode: Optional[str] = None
        self._drag_origin = QPoint()
        self._drag_window_pos = QPoint()

        # Hiding is explicit: Escape, the hotkey, or opening a result. Anything
        # else that hides this window is undone; see hideEvent.
        self._hiding_deliberately = False
        self._wants_visible = False

        self._build_window()
        self._build_ui()
        self._start_worker()
        self.apply_theme(self.theme)

        # Queued automatically, because emitter and receiver are on different threads
        self.hotkey_pressed.connect(self.toggle)
        self._install_row_shortcuts()

    # persisted geometry

    @staticmethod
    def _load_settings() -> dict:
        """Read the remembered position and scale. Never raises."""
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self) -> None:
        """Remember where the window is and how big. Never raises."""
        self._settings["scale"] = self.theme.scale
        if not self.pos().isNull():
            self._settings["x"] = self.pos().x()
            self._settings["y"] = self.pos().y()
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
                json.dump(self._settings, handle)
        except Exception:
            pass                # losing the position is not worth a crash

    # construction

    def _build_window(self) -> None:
        # Tool keeps it off the taskbar and stops it stealing activation the
        # way a normal top-level window would.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(self.theme.m("window_width") + SHADOW_PAD * 2)

        # Needed for the move and scale cursors to appear on hover rather than
        # only once a button is already down.
        self.setMouseTracking(True)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW_PAD, SHADOW_PAD, SHADOW_PAD, SHADOW_PAD)

        self.root = QFrame(self)
        self.root.setObjectName("Root")
        outer.addWidget(self.root)

        # The window background and drop shadow are painted in paintEvent
        # rather than with QGraphicsDropShadowEffect. A graphics effect renders
        # its widget into an offscreen surface, which drops the contents of
        # complex children such as the item view; painting directly avoids that
        # entirely and costs less per frame.

        stack = QVBoxLayout(self.root)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        self.query_box = QLineEdit(self.root)
        self.query_box.setObjectName("QueryBox")
        self.query_box.setFixedHeight(self.theme.m("query_height"))
        self.query_box.setPlaceholderText("Search")
        self.query_box.textChanged.connect(self._on_text_changed)
        stack.addWidget(self.query_box)

        self.separator = QFrame(self.root)
        self.separator.setObjectName("Separator")
        self.separator.setFixedHeight(1)
        self.separator.hide()
        stack.addWidget(self.separator)

        self.result_list = ResultList(self.theme, self.root)
        self.result_list.setFixedHeight(0)
        self.result_list.itemActivated.connect(lambda _: self._activate())
        self.result_list.itemClicked.connect(lambda _: self._activate())
        stack.addWidget(self.result_list)

        # Visible scale handle, floated over the right end of the query bar in
        # the padding the stylesheet already reserves there.
        self.grip = ScaleGrip(self)

        # Flow Launcher hangs MouseDown on the whole window border and calls
        # DragMove (MainWindow.xaml:217). The equivalent surface here is the
        # query bar, which is the only chrome always on screen, so the filter
        # below turns a press-and-drag there into a window move.
        self.query_box.installEventFilter(self)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(self.theme.m("animation_ms"))
        # CircleEase / EaseInOut in MainWindow.xaml.cs
        self._anim.setEasingCurve(QEasingCurve.InOutCirc)

    def _start_worker(self) -> None:
        self._thread = QThread(self)
        self._worker = QueryWorker(self._dispatcher)
        self._worker.moveToThread(self._thread)
        self.request_query.connect(self._worker.run)
        self._worker.finished.connect(self._on_results)
        self._thread.start()

    # theming

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.setStyleSheet(theme.stylesheet())
        self.result_list.set_theme(theme)
        self.query_box.setFixedHeight(theme.m("query_height"))
        self.setFixedWidth(theme.m("window_width") + SHADOW_PAD * 2)

        # Row height comes from the delegate's sizeHint, which the view caches
        # because uniform item sizes are on. Without this a scale change moves
        # the text and icons but leaves the rows their old height.
        self.result_list.doItemsLayout()
        self._resize_to_results()
        self._place_grip()

    def _place_grip(self) -> None:
        """Sit the scale handle at the right of the query bar, on top of it."""
        if not hasattr(self, "grip"):
            return
        query_height = self.theme.m("query_height")
        self.grip.move(
            self.root.width() - GRIP_SIZE - self.theme.m("hotkey_margin_right"),
            max(0, (query_height - GRIP_SIZE) // 2),
        )
        self.grip.raise_()          # above the query box, which fills the row
        self.grip.update()          # its pen colour follows the theme

    # query flow

    def _on_text_changed(self, text: str) -> None:
        self._seq += 1

        # Cancel from here rather than leaving it to the next Dispatcher.query.
        # Requests are queued onto a single worker thread and each runs to
        # completion, so by the time the next query starts the previous one has
        # already finished and there is nothing left to cancel: the token checks
        # in the plugins were measured firing once in fifteen keystrokes. The
        # cancel has to come from this thread to reach a query still in flight.
        self._dispatcher.cancel()

        if not text.strip():
            self._set_results([])
            return
        self.request_query.emit(self._seq, text)

    @Slot(int, object)
    def _on_results(self, seq: int, results: object) -> None:
        if seq != self._seq:
            return                      # a newer keystroke superseded this
        self._set_results(list(results or []))

    def _set_results(self, results: List[Result]) -> None:
        self._results = results
        self.result_list.clear()

        # An empty window cannot be told apart from a broken one, so say so.
        # Only when something was actually typed: an empty box is not a failure.
        if not results and self.query_box.text().strip():
            self._show_empty_state()
            return

        for i, result in enumerate(results):
            item = QListWidgetItem()
            item.setData(RESULT_ROLE, result)
            if i < 9:
                # Alt+N quick launch, as shown in the Flow Launcher UI
                result.context.setdefault("hotkey", "Alt+%d" % (i + 1))
            self.result_list.addItem(item)

        if results:
            self.result_list.setCurrentRow(0)

        self._resize_to_results()

    def _show_empty_state(self) -> None:
        """Render a single 'nothing matched' row instead of a blank window."""
        query = self.query_box.text().strip()
        notice = Result(
            title="No results for “%s”" % query,
            subtitle="Check the spelling, or try fewer letters",
            # No action, so Enter does nothing and the window stays open
            context={"placeholder": True},
        )

        item = QListWidgetItem()
        item.setData(RESULT_ROLE, notice)
        # Not selectable, so the arrow keys cannot land on it and Enter has
        # nothing to run. _activate is also safe on its own, because
        # self._results is empty while this row is showing.
        item.setFlags(Qt.NoItemFlags)
        self.result_list.addItem(item)

        self.result_list.setCurrentRow(-1)
        self._resize_to_results()

    def _resize_to_results(self) -> None:
        """Resize the window to fit exactly the rows currently shown.

        The height is computed rather than left to adjustSize(). adjustSize
        grows a top level window happily but will not always shrink it again,
        so clearing the results left the window at its previous larger size
        with empty space below the search box until the next query.
        """
        list_height = self.result_list.visible_height()
        self.result_list.setFixedHeight(list_height)
        self.separator.setVisible(list_height > 0)

        height = (
            SHADOW_PAD * 2
            + self.theme.m("query_height")
            + (self.separator.height() if list_height > 0 else 0)
            + list_height
        )
        self.setFixedHeight(height)
        self._place_grip()

    # interaction

    def paintEvent(self, event) -> None:
        """Draw the rounded window surface and its drop shadow."""
        QPainter(self).drawPixmap(0, 0, self._surface_pixmap())

    def _surface_pixmap(self) -> QPixmap:
        """The window surface, drawn once per size and reused after that.

        The shadow is two dozen stacked rounded outlines, and re-stroking them
        on every repaint cost more than the rest of the window put together.
        The geometry only changes when the window is resized or the theme is
        swapped, so it is rendered once and blitted from then on.
        """
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), self.theme.name, ratio)
        if key == self._surface_key and self._surface is not None:
            return self._surface

        pixmap = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)          # the window is translucent

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)

        radius = self.theme.m("window_radius")
        body = self.rect().adjusted(
            SHADOW_PAD, SHADOW_PAD, -SHADOW_PAD, -SHADOW_PAD
        )

        # Layered rounded outlines approximate a gaussian falloff cheaply and,
        # unlike a graphics effect, leave child rendering untouched.
        painter.setBrush(Qt.NoBrush)
        for i in range(SHADOW_PAD, 0, -1):
            alpha = int(70 * (1.0 - i / SHADOW_PAD) ** 2.2)
            if alpha <= 0:
                continue
            painter.setPen(QPen(QColor(0, 0, 0, alpha), 1))
            painter.drawRoundedRect(
                body.adjusted(-i, -i + 2, i, i + 2), radius + i, radius + i
            )

        painter.setPen(QPen(QColor(self.theme.c("window_border")), 1))
        painter.setBrush(QColor(self.theme.c("window_bg")))
        painter.drawRoundedRect(body, radius, radius)
        painter.end()

        self._surface_key = key
        self._surface = pixmap
        return pixmap

    # moving and scaling

    def _is_on_text(self, pos: QPoint) -> bool:
        """True if a point in the query box sits on typed text.

        Dragging over text has to keep selecting it. Everywhere else in the bar
        is free to move the window, which for an empty box is the whole width.
        """
        text = self.query_box.text()
        if not text:
            return False
        metrics = QFontMetrics(self.query_box.font())
        left = self.query_box.contentsRect().left()
        return pos.x() <= left + metrics.horizontalAdvance(text) + 4

    def eventFilter(self, watched, event):
        """Turn a press-and-drag on the query bar into a window move."""
        if watched is not self.query_box:
            return super().eventFilter(watched, event)

        kind = event.type()

        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            if not self._is_on_text(event.position().toPoint()):
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_window_pos = self.pos()
                self._drag_mode = "armed"       # not moving until it travels
            return False                        # let the caret land as usual

        if kind == QEvent.Type.MouseMove and self._drag_mode:
            travelled = event.globalPosition().toPoint() - self._drag_origin
            if self._drag_mode == "armed":
                if travelled.manhattanLength() < DRAG_THRESHOLD:
                    return False                # still just a click
                self._drag_mode = "move"
            self.move(self._drag_window_pos + travelled)
            return True                         # consumed: no text selection

        if kind == QEvent.Type.MouseButtonRelease and self._drag_mode:
            moved = self._drag_mode == "move"
            self._drag_mode = None
            if moved:
                self._save_settings()
                return True
            return False

        return super().eventFilter(watched, event)

    def set_scale(self, scale: float) -> None:
        """Resize the whole interface, text and rows included."""
        scale = clamp_scale(scale)
        if abs(scale - self.theme.scale) < 0.005:
            return                  # ignore sub-pixel jitter while dragging
        self.apply_theme(self.theme.rescaled(scale))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()

        if key == Qt.Key_Escape:
            self.hide_overlay()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._activate()
            return
        if key == Qt.Key_Down:
            self._move(1)
            return
        if key == Qt.Key_Up:
            self._move(-1)
            return

        # Alt+1..9 is handled by QShortcut, not here. See _install_row_shortcuts.
        super().keyPressEvent(event)

    def _install_row_shortcuts(self) -> None:
        """Bind Alt+1 to Alt+9 to the first nine rows.

        These cannot be read from keyPressEvent. Alt is the menu mnemonic
        modifier on Windows, so the query box consumes the digit and only the
        bare Alt keypress ever reaches this widget. A QShortcut is matched
        before normal key delivery, which sidesteps that entirely.
        """
        self._row_shortcuts = []
        for number in range(1, 10):
            shortcut = QShortcut(QKeySequence("Alt+%d" % number), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(
                lambda index=number - 1: self._activate_row(index)
            )
            self._row_shortcuts.append(shortcut)

    def _activate_row(self, index: int) -> None:
        """Open the row at `index`, if there is one."""
        if 0 <= index < len(self._results):
            self.result_list.setCurrentRow(index)
            self._activate()

    def _move(self, delta: int) -> None:
        count = self.result_list.count()
        if count == 0:
            return
        row = (self.result_list.currentRow() + delta) % count
        self.result_list.setCurrentRow(row)

    def _activate(self) -> None:
        row = self.result_list.currentRow()
        if not (0 <= row < len(self._results)):
            return
        should_hide = self._results[row].run()
        if should_hide:
            self.hide_overlay()

    # visibility

    def show_overlay(self) -> None:
        self._wants_visible = True
        self._place()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.query_box.setFocus()
        self.query_box.selectAll()

        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_overlay(self) -> None:
        self._wants_visible = False
        self._dispatcher.cancel()

        self._hiding_deliberately = True
        try:
            self.hide()
        finally:
            self._hiding_deliberately = False

        self.query_box.clear()
        self._set_results([])

    def hideEvent(self, event) -> None:
        """Undo a hide this window did not ask for.

        Closing on focus loss is what most launchers do, and it is what this one
        did, but it loses a half-typed query the moment anything else is
        clicked. Hiding is now deliberate only: Escape, the hotkey, or opening a
        result. Anything else -- the window manager withdrawing a tool window
        when the application deactivates, for one -- is reversed on the next
        turn of the event loop.
        """
        super().hideEvent(event)
        if self._hiding_deliberately or not self._wants_visible:
            return
        QTimer.singleShot(0, self._restore_if_wanted)

    def _restore_if_wanted(self) -> None:
        """Bring the window back after an outside hide, without stealing focus."""
        if self._wants_visible and not self.isVisible():
            self.show()
            self.raise_()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def _place(self) -> None:
        """Put the window where the user last left it, or centre it."""
        if "x" in self._settings and "y" in self._settings:
            point = QPoint(int(self._settings["x"]), int(self._settings["y"]))
            if self._on_a_screen(point):
                self.move(point)
                return
            # The screen it was on is gone, so fall back rather than open
            # the window off the edge of the desktop
        self._centre()

    @staticmethod
    def _on_a_screen(point: QPoint) -> bool:
        """True if a saved position still lands on a connected display."""
        return any(
            screen.availableGeometry().contains(point)
            for screen in QGuiApplication.screens()
        )

    def _centre(self) -> None:
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        width = self.width()
        x = area.x() + (area.width() - width) // 2
        y = area.y() + int(area.height() * 0.22)
        self.move(x, y)

    def shutdown(self) -> None:
        """Stop the query thread. Safe to call more than once.

        Closing the widget is not enough on its own: quitting from the terminal
        never closes a hidden window, so the thread outlived the application and
        Qt reported "QThread: Destroyed while thread is still running".
        """
        self._wants_visible = False
        self._save_settings()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
