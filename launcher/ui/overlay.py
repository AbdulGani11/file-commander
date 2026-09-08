"""The launcher overlay window.

Window composition follows `MainWindow.xaml`: a frameless, transparent,
always-on-top window whose visible surface is a single rounded root border
containing a vertical stack of query box, separator, and result list.

Threading: `Dispatcher.query()` blocks, so it runs on a worker thread and
delivers results back through a queued signal. The UI thread only ever paints.
Each request carries a sequence number and late replies from superseded
queries are dropped, which complements the cancellation inside the dispatcher.
"""

from typing import List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QThread,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QPainter, QPen
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
from .theme import DEFAULT_THEME, Theme

# Outer transparent padding reserved for the drop shadow.
SHADOW_PAD = 24


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
        self.theme = Theme(theme_name)
        self._seq = 0
        self._results: List[Result] = []

        self._build_window()
        self._build_ui()
        self._start_worker()
        self.apply_theme(self.theme)

        # Queued automatically, because emitter and receiver are on different threads
        self.hotkey_pressed.connect(self.toggle)

    # construction

    def _build_window(self) -> None:
        # Tool keeps it off the taskbar and stops it stealing activation the
        # way a normal top-level window would.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(self.theme.m("window_width") + SHADOW_PAD * 2)

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
        self._resize_to_results()

    # query flow

    def _on_text_changed(self, text: str) -> None:
        self._seq += 1
        if not text.strip():
            self._dispatcher.cancel()
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
        height = self.result_list.visible_height()
        self.result_list.setFixedHeight(height)
        self.separator.setVisible(height > 0)
        self.adjustSize()

    # interaction

    def paintEvent(self, event) -> None:
        """Draw the rounded window surface and its drop shadow."""
        painter = QPainter(self)
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

        # Alt+1..9 quick launch
        if event.modifiers() & Qt.AltModifier:
            if Qt.Key_1 <= key <= Qt.Key_9:
                index = key - Qt.Key_1
                if index < len(self._results):
                    self.result_list.setCurrentRow(index)
                    self._activate()
                return

        super().keyPressEvent(event)

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
        self._centre()
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
        self._dispatcher.cancel()
        self.hide()
        self.query_box.clear()
        self._set_results([])

    def toggle(self) -> None:
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

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
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
