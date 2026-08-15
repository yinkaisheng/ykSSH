from __future__ import annotations

import re
import os
import time
import string
import unicodedata
from dataclasses import dataclass
from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QPoint, QPointF, QRectF, QEvent
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontInfo,
    QFontMetrics,
    QFontMetricsF,
    QPainter,
    QPen,
    QGuiApplication,
    QInputMethodEvent,
    QPixmap,
)
from PyQt5.QtWidgets import QWidget, QSizePolicy, QToolTip

try:
    from PyQt5 import sip
except ImportError:  # pragma: no cover
    sip = None

from i18n import tr as t
from storage.app_config import get_app_config, get_setting
from storage.paths import LOGS_DIR
from ui.menu_shortcuts import ShortcutMenu, add_menu_key, exec_menu
from ui.prompt_dialog import prompt_multiline_confirm
from ui.theme import (
    active_theme_palette,
    terminal_font_family_css,
    normalize_terminal_font_family,
    normalize_terminal_font_size,
)


import pyte  # type: ignore


@dataclass(frozen=True)
class _Cell:
    ch: str
    fg: QColor
    bg: QColor
    bold: bool = False
    underline: bool = False


def _visible_command_marker_rows(
    command_start_rows: list[int],
    viewport_top: int,
    viewport_rows: int,
    *,
    in_alt_screen: bool = False,
) -> tuple[int, ...]:
    """Map committed command start rows to visible viewport rows."""
    if in_alt_screen or viewport_rows <= 0:
        return ()
    top = max(0, int(viewport_top))
    bottom = top + int(viewport_rows)
    return tuple(int(row) - top for row in command_start_rows if top <= int(row) < bottom)


class TerminalVTWidget(QWidget):
    """
    A VT/xterm-like terminal viewport backed by pyte.
    - Proper cursor movement / clear / overwrite / alternate screen for TUI apps (vim/top/less)
    - Paints a cell grid with colors/styles (best-effort depending on pyte attrs)
    """

    input_received = pyqtSignal(bytes)
    command_submitted = pyqtSignal(str, str, int)
    reconnect_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._reconnect_enabled = False
        self.setFocusPolicy(Qt.StrongFocus)
        # Enable IME (e.g. Chinese/Japanese/Korean input methods). Without this, some platforms/IMEs
        # will consume letter/navigation keys during composition and the terminal appears "dead".
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setAcceptDrops(False)
        # Ensure the widget actually expands/shrinks inside QSplitter layouts
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Settings
        self._default_fg = QColor("#C8C8C8")
        self._default_bg = QColor("#1E1E1E")

        # Font — from app appearance settings (px, monospace)
        self.setObjectName('terminalVTWidget')
        terminal_font = self._build_terminal_font()
        self.setFont(terminal_font)
        self._sync_font_stylesheet(terminal_font)
        self._recalc_metrics()

        # Screen
        cols, rows = self._calc_cols_rows()
        history = int(get_setting("terminal_scrollback_lines", 5000))
        history = max(200, min(200000, history))
        self._history_lines = history

        # We manage alternate screen explicitly for reliable vim behavior:
        # - Main screen: HistoryScreen (scrollback)
        # - Alt screen: Screen (no scrollback)
        self._main_screen = pyte.HistoryScreen(cols, rows, history=history)  # type: ignore[attr-defined]
        self._main_stream = pyte.Stream(self._main_screen)  # type: ignore[attr-defined]
        self._alt_screen = None
        self._alt_stream = None
        self._in_alt_screen = False

        self.screen = self._main_screen
        self.stream = self._main_stream

        # Handle escape sequences potentially split across packets
        self._esc_pending = ""
        self._decset_re = re.compile(r"\x1b\[\?([0-9;]*)([hl])")
        self._tui_control_re = re.compile(r"\x1b\[[?]?[0-9;:<>]*[ABCDHJKSTfhhlr]")
        self._full_clear_re = re.compile(r"\x1b\[(?:0*2|0*3)J|\x1b\[H\x1b\[(?:0*2|0*3)J|\x1b\[(?:0*2|0*3)J\x1b\[H")
        self._scrollback_clear_re = re.compile(r"\x1b\[0*3J")
        self._osc_color_query_re = re.compile(r"\x1b\](1[012]);\?(?:\x07|\x1b\\)")
        self._sgr_re = re.compile(r"\x1b\[([0-9:;]*)m")

        # Modes negotiated by applications (vim uses these heavily)
        self._app_cursor_keys = False  # DECCKM: ESC[?1h / ESC[?1l
        self._bracketed_paste_mode = False  # DECSET 2004: ESC[?2004h / ESC[?2004l
        # Mouse reporting
        self._mouse_track_1000 = False  # click tracking
        self._mouse_track_1002 = False  # button-event tracking (drag)
        self._mouse_track_1003 = False  # any-motion tracking
        self._mouse_sgr_1006 = False    # SGR extended coords
        self._pressed_mouse_buttons: set[int] = set()

        # Reflow support: keep a bounded raw output buffer and replay it on resize/font changes
        # (main screen only) so long lines re-wrap to the new width like modern terminals.
        self._raw_buffer = ""
        try:
            self._raw_buffer_limit = int(get_setting("terminal_reflow_buffer_chars", 200000))
        except Exception:
            self._raw_buffer_limit = 200000
        self._raw_buffer_limit = max(20000, min(2000000, self._raw_buffer_limit))
        # Raw replay reflow is intentionally opt-in. Replaying old shell bytes
        # on resize cannot faithfully re-create programs which formatted their
        # output for the previous PTY width (for example multi-column `ls`),
        # and can make prompts appear in the middle of old output.
        self._reflow_enabled = bool(get_setting("terminal_experimental_raw_reflow_on_resize", False))
        self._raw_buffer_has_tui_control = False

        # Paint throttling
        self._dirty = True
        self._full_repaint_needed = True
        self._dirty_lines: set[int] = set(range(rows))
        self._backing = QPixmap(self.size())
        self._backing.fill(self._terminal_bg())
        self._color_cache: dict[Any, QColor] = {}
        self._last_cursor_cell: tuple[int, int] | None = None
        self._last_paint_t = 0.0
        self._last_double_click_t = 0.0
        self._last_double_click_pos: QPoint | None = None
        self._viewport_top_row = 0
        self._command_start_rows: list[int] = []
        self._command_start_times: dict[int, str] = {}
        self._pending_command_start_y: int | None = None
        self._pending_command_start_x: int | None = None
        self._pending_command_text = ''
        self._pending_command_history_forced = False
        self._command_start_commands: dict[int, str] = {}
        self._last_gutter_tooltip_row: int | None = None
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self.update)

        # Debounced PTY resize
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._apply_resize)
        self._pending_cols_rows: tuple[int, int] | None = None

        # Selection (cell coords)
        self._sel_anchor: tuple[int, int] | None = None
        self._sel_head: tuple[int, int] | None = None
        self._is_selecting = False
        self._word_select_chars = set(string.ascii_letters + string.digits + "_-.")

        # Local viewport scrollback (0 = follow bottom)
        self._scroll_lines = 0
        self._is_scrollbar_dragging = False
        self._scrollbar_drag_offset_y = 0.0

        self._debug_log_path = os.path.join(str(LOGS_DIR), "terminal_debug.log")
        self._debug_history_jump_log_path = os.path.join(str(LOGS_DIR), "terminal_history_jump.log")
        self._debug_input_count = 0
        self._debug_after_resize_until = 0.0
        self._debug_enabled = bool(False)
        self._debug_gutter_selection = bool(get_setting("terminal_debug_gutter_selection", False))
        self._debug_history_jump = bool(get_setting("terminal_debug_history_jump", False))
        self._debug_cell_dump_count = 0

    def _debug_log(self, message: str) -> None:
        if not self._debug_enabled:
            return
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self._debug_log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} [TerminalVTWidget] {message}\n")
        except Exception:
            pass

    def _debug_escape_sample(self, text: str, limit: int = 500) -> str:
        sample = (text or "")[:limit]
        return sample.encode("unicode_escape", errors="replace").decode("ascii", errors="replace")

    def _debug_gutter_log(self, message: str) -> None:
        if not self._debug_gutter_selection:
            return
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self._debug_log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} [TerminalVTWidget.gutter] {message}\n")
        except Exception:
            pass

    def _debug_history_jump_log(self, message: str) -> None:
        if not self._debug_history_jump:
            return
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(self._debug_history_jump_log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} [TerminalVTWidget.history-jump] {message}\n")
        except Exception:
            pass

    def _debug_history_jump_viewport_sample(self, label: str, limit_rows: int = 8) -> None:
        if not self._debug_history_jump:
            return
        try:
            rows = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
            sample_rows = []
            for y in range(max(0, min(rows, limit_rows))):
                abs_y = self._visible_y_to_abs_y(y)
                text = self._line_text(y).rstrip()
                sample_rows.append(f"{y}/{abs_y}:{text!r}")
            self._debug_history_jump_log(f"{label} visible_sample={sample_rows}")
        except Exception as exc:
            self._debug_history_jump_log(f"{label} visible_sample_error={exc!r}")

    def _debug_visible_line_sample(self, y: int, limit: int = 100) -> str:
        try:
            _cols, rows = self._calc_cols_rows()
            if y < 0 or y >= rows:
                return "<offscreen>"
            return self._line_text(y)[:limit]
        except Exception:
            return ""

    def _debug_selection_state(self) -> str:
        return (
            f"viewport_top={self._viewport_top_row}, scroll_lines={self._scroll_lines}, "
            f"history_top={self._history_top_len()}, history_bottom={self._history_bottom_len()}, "
            f"cmd_abs={self._command_start_rows}, pending_abs={self._pending_command_start_y}, "
            f"sel_anchor={self._sel_anchor}, sel_head={self._sel_head}"
        )

    def event(self, event):
        # Handle ShortcutOverride to ensure terminal captures keys before they are
        # treated as application/system shortcuts (critical for macOS/Linux).
        if event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True

        # Intercept navigation and focus-stealing keys in the event loop.
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Up, Qt.Key_Down,
                       Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End,
                       Qt.Key_PageUp, Qt.Key_PageDown):
                self.keyPressEvent(event)
                return True

        return super().event(event)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        super().focusOutEvent(event)
        self.update()

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:  # type: ignore[override]
        """
        Handle IME commit strings (e.g. when a user types using a CJK input method).
        We ignore preedit text and only forward committed characters to the remote.
        """
        try:
            commit = event.commitString() or ""
        except Exception:
            commit = ""

        if commit:
            self._prepare_for_terminal_input()
            self._record_pending_command_start()
            self.input_received.emit(commit.encode("utf-8"))
            event.accept()
            return

        # No committed text; let Qt handle preedit/candidate UI if any.
        event.ignore()

    # -------------------------
    # Sizing / metrics
    # -------------------------
    @staticmethod
    def _build_terminal_font(
        family: str | None = None,
        size_px: int | None = None,
    ) -> QFont:
        appearance = get_app_config().appearance
        family = normalize_terminal_font_family(family or appearance.terminal_font_family)
        size_px = normalize_terminal_font_size(
            size_px if size_px is not None else appearance.terminal_font_size_px
        )
        candidates = [family, *appearance.terminal_font_families, 'Consolas', 'Courier New']
        seen: set[str] = set()
        for name in candidates:
            if not name or name in seen:
                continue
            seen.add(name)
            font = QFont(name)
            font.setStyleHint(QFont.Monospace)
            font.setFixedPitch(True)
            font.setPixelSize(size_px)
            if QFontInfo(font).fixedPitch():
                return font
        font = QFont('Courier New')
        font.setStyleHint(QFont.Monospace)
        font.setFixedPitch(True)
        font.setPixelSize(size_px)
        return font

    def _sync_font_stylesheet(self, font: QFont | None = None) -> None:
        """Override global QSS font-size; pass the intended QFont (setFont alone is overridden by app QSS)."""
        source = font if font is not None else self.font()
        size_px = source.pixelSize()
        if size_px <= 0:
            size_px = max(8, int(source.pointSizeF()))
        family_css = terminal_font_family_css(source.family())
        self.setStyleSheet(
            f'font-family: {family_css}; font-size: {size_px}px;'
        )

    def apply_terminal_font(
        self,
        family: str | None = None,
        size_px: int | None = None,
    ) -> None:
        """Apply terminal font from settings and reflow the grid."""
        terminal_font = self._build_terminal_font(family, size_px)
        self.setFont(terminal_font)
        self._sync_font_stylesheet(terminal_font)
        self._recalc_metrics()
        self._reset_backing_store()
        self._pending_cols_rows = self._calc_cols_rows()
        try:
            self._apply_resize()
        except Exception:
            self._resize_timer.start(1)
        self._mark_dirty()
        try:
            self.updateGeometry()
        except Exception:
            pass
        self.update()

    def _recalc_metrics(self) -> None:
        fm = QFontMetricsF(self.font())
        # Grid width must match ASCII monospace advance. Wide glyphs use span=2.
        self._cell_w = max(1.0, fm.horizontalAdvance('M'))
        self._cell_h = max(1.0, fm.lineSpacing())
        self._ascent = fm.ascent()

    def _setting_color(self, key: str, fallback: QColor | str) -> QColor:
        fallback_color = QColor(fallback)
        value = get_setting(key, '')
        if isinstance(value, str) and value.strip():
            color = QColor(value.strip())
            if color.isValid():
                return color
        return fallback_color

    def _terminal_bg(self) -> QColor:
        return self._setting_color("terminal_background_color", self._default_bg)

    def _selection_bg(self) -> QColor:
        return self._setting_color("terminal_selection_background_color", "#094771")

    def _left_gutter_width(self) -> int:
        try:
            return max(0, min(200, int(get_setting("terminal_left_gutter_width_px", 16))))
        except Exception:
            return 16

    def _right_scrollbar_width(self) -> int:
        try:
            return max(0, min(200, int(get_setting("terminal_scrollbar_width_px", 10))))
        except Exception:
            return 10

    def _text_origin_x(self) -> float:
        return float(self._left_gutter_width())

    def _gutter_bg(self) -> QColor:
        return self._setting_color("terminal_gutter_background_color", "#353535")

    def _gutter_command_bg(self) -> QColor:
        return self._setting_color("terminal_gutter_command_background_color", "#606060")

    def _scrollbar_bg(self) -> QColor:
        return self._setting_color("terminal_scrollbar_background_color", "#353535")

    def _scrollbar_thumb_bg(self) -> QColor:
        return self._setting_color("terminal_scrollbar_thumb_color", "#6A6A6A")

    def _focus_border_color(self) -> QColor:
        color = QColor(getattr(active_theme_palette(), "terminal_focus_border", "#f0a030"))
        return color if color.isValid() else QColor("#f0a030")

    def _is_alive(self) -> bool:
        if sip is None:
            return True
        try:
            return not sip.isdeleted(self)
        except Exception:
            return False

    def _calc_cols_rows(self) -> tuple[int, int]:
        if not self._is_alive():
            return 80, 24
        vp = self.size()
        # 给予至少 4 像素的右侧安全边距，并使用浮点数除法
        text_width = max(0.0, vp.width() - self._left_gutter_width() - self._right_scrollbar_width() - 4.0)
        cols = max(20, int(text_width // self._cell_w))
        rows = max(5, int(max(0.0, vp.height()) // self._cell_h))
        return cols, rows

    def terminal_size(self) -> tuple[int, int]:
        """Return the PTY dimensions required by the connection layer."""
        return self._calc_cols_rows()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._reset_backing_store()
        cols, rows = self._calc_cols_rows()
        current_cols = int(getattr(self.screen, "columns", cols) or cols)
        current_rows = int(getattr(self.screen, "lines", rows) or rows)
        self._pending_cols_rows = (cols, rows)

        if (cols, rows) != (current_cols, current_rows):
            # Apply terminal-grid changes before Qt gets a chance to repaint
            # the resized widget. Otherwise a shrink can briefly clip the
            # prompt with the old row count, then jump to the corrected view
            # when the debounce timer fires.
            if self._resize_timer.isActive():
                self._resize_timer.stop()
            self._apply_resize()
        else:
            # Pixel size changed but cell geometry did not; a repaint is enough.
            self._mark_dirty()

    def _apply_resize(self) -> None:
        if not self._pending_cols_rows:
            return
        cols, rows = self._pending_cols_rows
        self._pending_cols_rows = None
        tui_like = self._in_alt_screen or self._raw_buffer_has_tui_control
        can_reflow_main = (
            self._reflow_enabled
            and not tui_like
            and self._scroll_lines == 0
            and bool(self._raw_buffer)
        )
        self._debug_after_resize_until = time.monotonic() + 3.0
        self._debug_log(
            f"resize apply: cols={cols}, rows={rows}, in_alt={self._in_alt_screen}, "
            f"tui_control={self._raw_buffer_has_tui_control}, reflow={can_reflow_main}"
        )
        try:
            # Resize local emulator
            try:
                if can_reflow_main:
                    # Plain shell output is rebuilt below directly at the target
                    # geometry. This avoids pyte's resize behavior of appending
                    # blank rows at the bottom before reflow, and preserves the
                    # expected top-vs-bottom anchoring from the replayed output itself.
                    pass
                else:
                    self._resize_main_screen_keep_bottom(cols, rows)
                if self._alt_screen is not None:
                    self._alt_screen.resize(lines=rows, columns=cols)  # type: ignore[attr-defined]
            except Exception:
                pass
        finally:
            # Reflow only plain shell output. Full-screen TUIs repaint after PTY
            # resize; replaying or preserving old cells causes duplicated rows.
            if can_reflow_main:
                try:
                    self._reflow_from_buffer(cols, rows)
                except Exception:
                    pass
            self._mark_dirty()

    def _resize_main_screen_keep_bottom(self, cols: int, rows: int) -> None:
        """
        Resize pyte's main screen while keeping the visible bottom anchored.

        pyte adds new rows at the bottom when growing the screen. For an
        interactive shell prompt that makes the prompt drift upward and can
        leave stale-looking bottom rows. Real terminal viewports generally keep
        the current bottom attached to the widget bottom when following output.
        """
        old_rows = int(getattr(self._main_screen, "lines", rows) or rows)
        following_bottom = self._scroll_lines == 0
        old_screen_filled_to_bottom = self._main_screen_filled_to_bottom(old_rows)

        if not old_screen_filled_to_bottom:
            # pyte.resize always removes rows from the top when shrinking.
            # That is correct once output has filled the viewport, but wrong
            # for short login banners or a few shell lines sitting at the top
            # with empty space below them. In that case, resize against the
            # bottom whitespace until the new height can no longer contain all
            # visible content.
            content_bottom = self._main_screen_content_bottom_row(old_rows)
            if rows > content_bottom:
                self._resize_main_screen_top_aligned(cols, rows)
            else:
                self._resize_main_screen_short_keep_cursor(cols, rows, content_bottom)
            return

        if following_bottom and rows < old_rows:
            # For a full viewport, shrinking hides top rows. Store those rows
            # so growing the widget can pull them back before adding bottom
            # whitespace.
            self._save_clipped_top_lines(old_rows - rows)
            self._viewport_top_row += old_rows - rows

        self._main_screen.resize(lines=rows, columns=cols)  # type: ignore[attr-defined]

        cursor = getattr(self._main_screen, "cursor", None)
        if cursor is not None:
            cursor.x = max(0, min(int(getattr(cursor, "x", 0)), cols - 1))
            cursor.y = max(0, min(int(getattr(cursor, "y", 0)), rows - 1))

        if not following_bottom or rows <= old_rows:
            return

        delta = rows - old_rows
        try:
            restored = self._pop_top_history_lines(delta)
            restore_count = len(restored)
            if restore_count <= 0:
                return
            self._viewport_top_row = max(0, self._viewport_top_row - restore_count)

            buffer = getattr(self._main_screen, "buffer", None)
            if buffer is None:
                return
            # pyte has already added blank rows at the bottom. Shift only the
            # old visible rows down by the number of history rows we can
            # actually restore; any extra new height remains blank at the
            # bottom once history is exhausted.
            for y in range(min(rows - 1, old_rows + restore_count - 1), restore_count - 1, -1):
                src = y - restore_count
                if src in buffer:
                    buffer[y] = buffer.pop(src)
                else:
                    buffer.pop(y, None)
            for y, line in enumerate(restored):
                buffer[y] = line
            if cursor is not None:
                # Restoring history rows moves the old visible screen down.
                # The cursor must follow that vertical shift even when the
                # resize also changes the column count, such as maximizing.
                cursor.y = max(0, min(rows - 1, int(getattr(cursor, "y", 0)) + restore_count))
            dirty = getattr(self._main_screen, "dirty", None)
            if dirty is not None:
                dirty.update(range(rows))
        except Exception:
            pass

    def _resize_main_screen_top_aligned(self, cols: int, rows: int) -> None:
        """Resize short, not-yet-full output by trimming/adding blank space at the bottom."""
        try:
            buffer = getattr(self._main_screen, "buffer", None)
            old_rows = int(getattr(self._main_screen, "lines", rows) or rows)
            old_cols = int(getattr(self._main_screen, "columns", cols) or cols)

            if buffer is not None:
                if rows < old_rows:
                    for y in range(rows, old_rows):
                        buffer.pop(y, None)
                if cols < old_cols:
                    for line in buffer.values():
                        for x in range(cols, old_cols):
                            line.pop(x, None)

            self._main_screen.lines = rows
            self._main_screen.columns = cols
            try:
                self._main_screen.set_margins()
            except Exception:
                pass

            cursor = getattr(self._main_screen, "cursor", None)
            if cursor is not None:
                cursor.x = max(0, min(int(getattr(cursor, "x", 0)), cols - 1))
                cursor.y = max(0, min(int(getattr(cursor, "y", 0)), rows - 1))

            dirty = getattr(self._main_screen, "dirty", None)
            if dirty is not None:
                dirty.update(range(rows))
        except Exception:
            self._main_screen.resize(lines=rows, columns=cols)  # type: ignore[attr-defined]

    def _resize_main_screen_short_keep_cursor(self, cols: int, rows: int, content_bottom: int) -> None:
        """Shrink short output past its content height while keeping the prompt/cursor visible."""
        try:
            buffer = getattr(self._main_screen, "buffer", None)
            old_rows = int(getattr(self._main_screen, "lines", rows) or rows)
            old_cols = int(getattr(self._main_screen, "columns", cols) or cols)
            drop = max(0, content_bottom - rows + 1)
            # Once the widget becomes shorter than the visible content, the
            # top-most lines must move into scrollback and the prompt should
            # remain visible near the bottom, matching normal terminal behavior.
            self._save_clipped_top_lines(drop)
            self._viewport_top_row += drop

            if buffer is not None:
                for y in range(rows):
                    src = y + drop
                    if src in buffer:
                        buffer[y] = buffer.pop(src)
                    else:
                        buffer.pop(y, None)
                for y in range(rows, old_rows):
                    buffer.pop(y, None)
                if cols < old_cols:
                    for line in buffer.values():
                        for x in range(cols, old_cols):
                            line.pop(x, None)

            self._main_screen.lines = rows
            self._main_screen.columns = cols
            try:
                self._main_screen.set_margins()
            except Exception:
                pass

            cursor = getattr(self._main_screen, "cursor", None)
            if cursor is not None:
                cursor.x = max(0, min(int(getattr(cursor, "x", 0)), cols - 1))
                cursor.y = max(0, min(rows - 1, int(getattr(cursor, "y", 0)) - drop))

            dirty = getattr(self._main_screen, "dirty", None)
            if dirty is not None:
                dirty.update(range(rows))
        except Exception:
            self._main_screen.resize(lines=rows, columns=cols)  # type: ignore[attr-defined]

    def _main_screen_filled_to_bottom(self, rows: int) -> bool:
        """Return True once content has reached the last visible row or scrollback exists."""
        try:
            history = getattr(self._main_screen, "history", None)
            top = getattr(history, "top", None)
            if top is not None and len(top) > 0:
                return True
        except Exception:
            pass

        try:
            display = self._safe_display_lines_for_screen(self._main_screen)
            if not display or rows <= 0:
                return False
            bottom_idx = min(rows, len(display)) - 1
            return bool(display[bottom_idx].strip())
        except Exception:
            return False

    def _main_screen_content_bottom_row(self, rows: int) -> int:
        """Return the lowest row containing visible text or the cursor."""
        bottom = 0
        try:
            cursor = getattr(self._main_screen, "cursor", None)
            if cursor is not None:
                bottom = max(bottom, int(getattr(cursor, "y", 0)))
        except Exception:
            pass
        try:
            display = self._safe_display_lines_for_screen(self._main_screen)
            limit = min(rows, len(display))
            for y in range(limit - 1, -1, -1):
                if display[y].strip():
                    return max(bottom, y)
        except Exception:
            pass
        return bottom

    def _save_clipped_top_lines(self, count: int) -> None:
        """Preserve top rows clipped by pyte.resize so a later grow can restore them."""
        if count <= 0:
            return
        try:
            history = getattr(self._main_screen, "history", None)
            top = getattr(history, "top", None)
            buffer = getattr(self._main_screen, "buffer", None)
            if top is None or buffer is None:
                return
            for y in range(max(0, count)):
                if y in buffer:
                    top.append(buffer[y])
        except Exception:
            pass

    def _pop_top_history_lines(self, count: int) -> list[Any]:
        """Pop up to count lines from top scrollback in visual order."""
        if count <= 0:
            return []
        try:
            history = getattr(self._main_screen, "history", None)
            top = getattr(history, "top", None)
            if top is None:
                return []
            restored = []
            for _ in range(min(count, len(top))):
                restored.append(top.pop())
            restored.reverse()
            return restored
        except Exception:
            return []

    def _reset_active_screen_for_tui_resize(self) -> None:
        """Reset visible TUI state after resize and wait for the remote repaint."""
        try:
            self.screen.reset()  # type: ignore[attr-defined]
        except Exception:
            self._clear_active_screen()
            return
        self._esc_pending = ""
        self._sel_anchor = None
        self._sel_head = None
        self._scroll_lines = 0
        self._viewport_top_row = 0
        self._clear_command_marks()
        self._pending_command_start_y = None
        self._mark_full_repaint()

    def _clear_active_screen(self) -> None:
        """Clear the visible screen without changing main/alternate screen ownership."""
        try:
            self.screen.erase_in_display(2)  # type: ignore[attr-defined]
            self.screen.cursor_position(0, 0)  # type: ignore[attr-defined]
        except Exception:
            try:
                self.screen.reset()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._esc_pending = ""
        self._sel_anchor = None
        self._sel_head = None
        self._scroll_lines = 0
        self._viewport_top_row = 0
        self._clear_command_marks()
        self._pending_command_start_y = None
        self._mark_full_repaint()

    def _clear_history_queues(self) -> None:
        try:
            history = getattr(self.screen, "history", None)
            top = getattr(history, "top", None)
            bottom = getattr(history, "bottom", None)
            if hasattr(top, "clear"):
                top.clear()
            if hasattr(bottom, "clear"):
                bottom.clear()
            if hasattr(history, "_replace"):
                try:
                    self.screen.history = history._replace(position=getattr(history, "size", 0))  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass

    def _reset_coordinate_state_after_remote_clear(self, *, clear_scrollback: bool) -> None:
        """Reset local absolute-coordinate state after the remote clears the main screen."""
        self._clear_history_queues()
        self._sel_anchor = None
        self._sel_head = None
        self._scroll_lines = 0
        self._viewport_top_row = 0
        self._clear_command_marks()
        self._pending_command_start_y = None
        self._mark_full_repaint()
        self._debug_gutter_log(
            "remote-clear "
            f"remote_requested_scrollback_clear={clear_scrollback}, {self._debug_selection_state()}"
        )

    # -------------------------
    # Public API (compatible with legacy TerminalWidget)
    # -------------------------
    def set_reconnect_enabled(self, enabled: bool) -> None:
        """When enabled, Enter requests reconnect instead of sending CR to SSH."""
        self._reconnect_enabled = bool(enabled)

    def is_reconnect_enabled(self) -> bool:
        return self._reconnect_enabled

    def write_text(self, text: str) -> None:
        if not self._is_alive():
            return
        self._process_input(text, record=True)

    def _process_input(self, text: str, record: bool) -> None:
        if not text:
            return
        t0 = time.perf_counter()
        history_top_before = self._history_top_len()
        was_scrolled_up = bool(self._scroll_lines > 0 and not self._in_alt_screen)
        desired_viewport_top = self._viewport_top_row
        self._debug_input_count += 1
        should_sample = self._debug_input_count <= 30 or time.monotonic() < self._debug_after_resize_until
        if should_sample:
            self._debug_log(
                f"input #{self._debug_input_count}: chars={len(text)}, "
                f"sample={self._debug_escape_sample(text)}"
            )
        if record and self._reflow_enabled and not self._in_alt_screen:
            if self._raw_buffer_has_tui_control or self._tui_control_re.search(text):
                self._raw_buffer_has_tui_control = True
                self._raw_buffer = ""
            else:
                # Keep bounded buffer for plain shell output only. Full-screen TUI
                # output should not be replayed during resize.
                self._raw_buffer += text
                if len(self._raw_buffer) > self._raw_buffer_limit:
                    self._raw_buffer = self._raw_buffer[-self._raw_buffer_limit :]

        # Intercept alternate-screen switching reliably: ESC[?1049h / ESC[?1049l
        # (also accept 47/1047 which are commonly used as aliases).
        data = self._esc_pending + (text or "")
        self._esc_pending = ""
        data = self._normalize_sgr(data)
        data = self._answer_terminal_queries(data)
        remote_full_clear = (not self._in_alt_screen) and bool(self._full_clear_re.search(data))
        remote_scrollback_clear = remote_full_clear and bool(self._scrollback_clear_re.search(data))
        i = 0

        def feed(chunk: str) -> None:
            if not chunk:
                return
            try:
                self.stream.feed(chunk)
            except Exception:
                try:
                    self.stream.feed(chunk.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))
                except Exception:
                    return

        while i < len(data):
            j = data.find("\x1b[?", i)
            if j == -1:
                feed(data[i:])
                i = len(data)
                break
            # Feed any text before the sequence
            if j > i:
                feed(data[i:j])

            # Attempt to find the end of this CSI sequence (terminator @-~)
            end_idx = -1
            # Limit lookahead to avoid stalling on binary garbage
            limit = min(len(data), j + 64)
            for k in range(j + 2, limit):
                if 0x40 <= ord(data[k]) <= 0x7E:
                    end_idx = k
                    break

            if end_idx == -1:
                if len(data) - j >= 64:
                    # Too long or invalid, just pass the start and move on
                    feed(data[j:j+3])
                    i = j + 3
                    continue
                # Incomplete sequence; keep remainder
                self._esc_pending = data[j:]
                break

            m = self._decset_re.match(data, j, end_idx + 1)
            if not m:
                # Not a DECSET/DECRST (e.g. DECRQM like \x1b[?1049$p), pass through
                feed(data[j : end_idx + 1])
                i = end_idx + 1
                continue

            params_raw = m.group(1) or ""
            action = m.group(2)  # 'h' or 'l'
            params = []
            if params_raw:
                for p in params_raw.split(";"):
                    if not p:
                        continue
                    try:
                        params.append(int(p))
                    except Exception:
                        continue

            # Alternate screen
            alt_params = {47, 1047, 1049}
            if any(p in alt_params for p in params):
                if action == "h":
                    self._enter_alt_screen()
                else:
                    self._exit_alt_screen()

            # Application cursor keys (vim uses this to switch arrow key sequences)
            if 1 in params:
                self._app_cursor_keys = (action == "h")
            if 2004 in params:
                self._bracketed_paste_mode = (action == "h")

            # Mouse tracking modes
            if 1000 in params:
                self._mouse_track_1000 = (action == "h")
            if 1002 in params:
                self._mouse_track_1002 = (action == "h")
            if 1003 in params:
                self._mouse_track_1003 = (action == "h")
            if 1006 in params:
                self._mouse_sgr_1006 = (action == "h")

            # Keep pyte's terminal mode state in sync. Some TUI apps send combined
            # private modes (for example cursor visibility + bracketed paste +
            # alternate screen); swallowing a locally handled sequence here makes
            # the screen model diverge from the remote terminal.
            feed(m.group(0))
            i = m.end()

        if remote_full_clear and not self._in_alt_screen:
            self._reset_coordinate_state_after_remote_clear(clear_scrollback=remote_scrollback_clear)

        history_top_after = self._history_top_len()
        if was_scrolled_up:
            self._restore_viewport_top_after_output(desired_viewport_top)
        elif history_top_after > history_top_before and self._scroll_lines == 0:
            rows = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
            old_viewport_top = self._viewport_top_row
            self._viewport_top_row += history_top_after - history_top_before
            self._shift_selection_rows(-(history_top_after - history_top_before), rows)
            self._debug_gutter_log(
                "output-scroll "
                f"history_top_before={history_top_before}, history_top_after={history_top_after}, "
                f"delta={history_top_after - history_top_before}, "
                f"viewport_top={old_viewport_top}->{self._viewport_top_row}, rows={rows}, "
                f"{self._debug_selection_state()}"
            )

        # If user is at bottom (not scrolled up), keep following output.
        # If user scrolled up, keep their viewport stable.
        if self._scroll_lines == 0:
            try:
                # Some pyte versions support history reset via next_page until end; ignore if absent.
                if hasattr(self.screen, "history") and hasattr(self.screen.history, "position"):
                    self.screen.history.position = self.screen.history.size  # type: ignore[attr-defined]
            except Exception:
                pass

        self._mark_dirty()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        if should_sample or dt_ms > 20:
            cursor = getattr(self.screen, "cursor", None)
            cx = int(getattr(cursor, "x", -1)) if cursor else -1
            cy = int(getattr(cursor, "y", -1)) if cursor else -1
            self._debug_log(
                f"input #{self._debug_input_count} processed: dt_ms={dt_ms:.1f}, "
                f"screen={getattr(self.screen, 'columns', '?')}x{getattr(self.screen, 'lines', '?')}, "
                f"cursor={cx},{cy}, in_alt={self._in_alt_screen}"
            )
        if self._debug_enabled:
            self._debug_dump_colored_numeric_cells()

    def _answer_terminal_queries(self, data: str) -> str:
        """
        Respond to terminal status queries commonly emitted by TUI frameworks.
        Some apps block briefly waiting for these answers before first paint.
        """
        if not data:
            return data

        if "\x1b[6n" in data:
            cursor = getattr(self.screen, "cursor", None)
            row = int(getattr(cursor, "y", 0)) + 1 if cursor else 1
            col = int(getattr(cursor, "x", 0)) + 1 if cursor else 1
            response = f"\x1b[{row};{col}R".encode("ascii")
            self.input_received.emit(response)
            self._debug_log(f"answered DSR cursor query: row={row}, col={col}")
            data = data.replace("\x1b[6n", "")

        if "\x1b]10;?" in data or "\x1b]11;?" in data or "\x1b]12;?" in data:
            colors = {
                "10": self._default_fg,
                "11": self._terminal_bg(),
                "12": self._default_fg,
            }

            def answer_color(m: re.Match[str]) -> str:
                code = m.group(1)
                color = colors.get(code, self._default_fg)
                response = (
                    f"\x1b]{code};rgb:{color.red():02x}{color.red():02x}/"
                    f"{color.green():02x}{color.green():02x}/"
                    f"{color.blue():02x}{color.blue():02x}\x1b\\"
                ).encode("ascii")
                self.input_received.emit(response)
                self._debug_log(f"answered OSC {code} color query: {color.name()}")
                return ""

            data = self._osc_color_query_re.sub(answer_color, data)

        return data

    def _normalize_sgr(self, data: str) -> str:
        """Normalize common xterm SGR variants that pyte may not parse."""
        if "\x1b[" not in data or ":" not in data:
            return data

        def repl(m: re.Match[str]) -> str:
            params = m.group(1)
            if ":" not in params:
                return m.group(0)

            out: list[str] = []
            for param in params.split(";"):
                if ":" not in param:
                    if param:
                        out.append(param)
                    continue

                parts = param.split(":")
                try:
                    code = int(parts[0])
                except Exception:
                    continue
                if code not in (38, 48) or len(parts) < 3:
                    continue

                mode = parts[1]
                values = [p for p in parts[2:] if p != ""]
                if mode == "2" and len(values) >= 3:
                    try:
                        r = max(0, min(255, int(values[0])))
                        g = max(0, min(255, int(values[1])))
                        b = max(0, min(255, int(values[2])))
                    except Exception:
                        continue
                    out.extend([str(code), "2", str(r), str(g), str(b)])
                elif mode == "5" and values:
                    try:
                        n = max(0, min(255, int(values[0])))
                    except Exception:
                        continue
                    out.extend([str(code), "5", str(n)])

            if not out:
                return m.group(0)
            return f"\x1b[{';'.join(out)}m"

        return self._sgr_re.sub(repl, data)

    def _debug_dump_colored_numeric_cells(self) -> None:
        if self._debug_cell_dump_count >= 20:
            return
        if not self._in_alt_screen:
            return
        display = self._safe_display_lines()
        buffer = getattr(self.screen, "buffer", None)
        if buffer is None:
            return
        interesting = re.compile(
            r"\bPID\b|\bCPU%|\bMem\b|\b\d+(?:\.\d+)?%|"
            r"\b\d+(?:\.\d+)?\s+(?:KB|MB|GB|TB)|\b\d{3,}\b"
        )
        for y, line_text in enumerate(display):
            if not interesting.search(line_text):
                continue
            if not ("PID" in line_text or "CPU" in line_text or "Mem" in line_text or "%" in line_text or "GB" in line_text or "MB" in line_text):
                continue
            try:
                line = buffer[y]
            except Exception:
                continue
            cells = []
            cols = min(len(line_text), getattr(self.screen, "columns", self._calc_cols_rows()[0]))
            for x in range(cols):
                try:
                    c = line[x]
                    ch = getattr(c, "data", " ") or " "
                    fg = getattr(c, "fg", None)
                    bg = getattr(c, "bg", None)
                except Exception:
                    ch, fg, bg = " ", None, None
                if ch.strip() or fg != "default" or bg != "default":
                    cells.append(f"{x}:{ch}:{fg}/{bg}")
            self._debug_log(f"cell attrs line {y}: text={line_text.rstrip()!r} cells={' | '.join(cells[:220])}")
            self._debug_cell_dump_count += 1
            if self._debug_cell_dump_count >= 20:
                return

    def _reset_emulator_state(self, cols: int, rows: int) -> None:
        """Reset emulator to a clean main-screen state with given geometry."""
        history = self._history_lines
        self._main_screen = pyte.HistoryScreen(cols, rows, history=history)  # type: ignore[attr-defined]
        self._main_stream = pyte.Stream(self._main_screen)  # type: ignore[attr-defined]
        self._alt_screen = None
        self._alt_stream = None
        self._in_alt_screen = False
        self.screen = self._main_screen
        self.stream = self._main_stream

        self._esc_pending = ""
        self._app_cursor_keys = False
        self._bracketed_paste_mode = False
        self._mouse_track_1000 = False
        self._mouse_track_1002 = False
        self._mouse_track_1003 = False
        self._mouse_sgr_1006 = False
        self._pressed_mouse_buttons.clear()
        self._scroll_lines = 0
        self._viewport_top_row = 0
        self._raw_buffer_has_tui_control = False
        self._mark_full_repaint()

    def _reflow_from_buffer(self, cols: int | None = None, rows: int | None = None) -> None:
        """
        Reflow main screen by replaying buffered raw output through pyte with the current cols/rows.
        This makes long lines wrap to new window/font size like modern terminals.
        """
        if not self._raw_buffer:
            return
        if self._in_alt_screen:
            return
        if self._raw_buffer_has_tui_control:
            return
        if cols is None or rows is None:
            cols, rows = self._calc_cols_rows()
        snapshot = self._raw_buffer
        self._reset_emulator_state(cols, rows)
        # Replay without recording again
        self._process_input(snapshot, record=False)

    def apply_scrollback_lines(self, lines: int) -> None:
        try:
            lines = int(lines)
        except Exception:
            return
        lines = max(200, min(200000, lines))
        self._history_lines = lines
        # pyte history size can't always be changed in-place reliably; re-create if needed.
        try:
            cols = getattr(self.screen, "columns", None) or self._calc_cols_rows()[0]
            rows = getattr(self.screen, "lines", None) or self._calc_cols_rows()[1]
            self._main_screen = pyte.HistoryScreen(cols, rows, history=lines)  # type: ignore[attr-defined]
            self._main_stream = pyte.Stream(self._main_screen)  # type: ignore[attr-defined]
            if not self._in_alt_screen:
                self.screen = self._main_screen
                self.stream = self._main_stream
        except Exception:
            pass
        self._scroll_lines = 0
        self._viewport_top_row = 0
        self._clear_command_marks()
        self._pending_command_start_y = None
        self._mark_full_repaint()
        self._mark_dirty()

    def _scroll_view(self, delta_lines: int) -> None:
        """
        Scroll viewport through HistoryScreen by a fixed number of lines.
        delta_lines > 0 means scroll up (older).
        """
        if delta_lines == 0:
            return
        # In alt-screen, scrolling is usually owned by the app; do nothing here.
        if self._in_alt_screen:
            return

        if self._scroll_history_lines(delta_lines):
            self._mark_dirty()
            return

        # Prefer pyte's history navigation if present.
        if hasattr(self.screen, "prev_line") and hasattr(self.screen, "next_line"):
            step = 1 if delta_lines > 0 else -1
            for _ in range(abs(delta_lines)):
                try:
                    if step > 0:
                        self.screen.prev_line()  # type: ignore[attr-defined]
                        self._scroll_lines += 1
                    else:
                        self.screen.next_line()  # type: ignore[attr-defined]
                        self._scroll_lines = max(0, self._scroll_lines - 1)
                except Exception:
                    break
            self._mark_dirty()
            return

        if hasattr(self.screen, "prev_page") and hasattr(self.screen, "next_page"):
            # Approximate by pages if only page APIs exist
            try:
                if delta_lines > 0:
                    self.screen.prev_page()  # type: ignore[attr-defined]
                    self._scroll_lines += max(1, abs(delta_lines))
                else:
                    self.screen.next_page()  # type: ignore[attr-defined]
                    self._scroll_lines = max(0, self._scroll_lines - max(1, abs(delta_lines)))
            except Exception:
                pass
            self._mark_dirty()
            return

        # Fallback: no history nav available; nothing we can do safely.
        return

    def _scroll_history_lines(self, delta_lines: int) -> bool:
        """
        Scroll pyte HistoryScreen by exact lines.

        Some pyte versions expose only prev_page/next_page, whose page size is
        a ratio of the viewport height. That makes one wheel notch jump about
        half a screen. Manipulating the same history queues with a small line
        count gives the usual terminal feel: one wheel notch is about 3 lines.
        """
        try:
            history = getattr(self.screen, "history", None)
            buffer = getattr(self.screen, "buffer", None)
            if history is None or buffer is None:
                return False
            top = getattr(history, "top", None)
            bottom = getattr(history, "bottom", None)
            if top is None or bottom is None:
                return False

            rows = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
            if rows <= 0:
                return False

            old_viewport_top = self._viewport_top_row
            old_scroll_lines = self._scroll_lines
            old_top_len = len(top)
            old_bottom_len = len(bottom)
            remaining = abs(delta_lines)
            total_applied = 0
            if delta_lines > 0:
                while remaining > 0 and len(top) > 0:
                    mid = min(remaining, len(top), rows)
                    if mid <= 0:
                        break
                    bottom.extendleft(buffer[y] for y in range(rows - 1, rows - mid - 1, -1))
                    history = history._replace(position=max(0, history.position - mid))  # type: ignore[attr-defined]
                    self.screen.history = history
                    for y in range(rows - 1, mid - 1, -1):
                        buffer[y] = buffer[y - mid]
                    for y in range(mid - 1, -1, -1):
                        buffer[y] = top.pop()
                    self._scroll_lines += mid
                    self._viewport_top_row -= mid
                    total_applied += mid
                    remaining -= mid
                if total_applied <= 0:
                    return True
                self._shift_selection_rows(total_applied, rows)
            else:
                while remaining > 0 and len(bottom) > 0:
                    mid = min(remaining, len(bottom), rows)
                    if mid <= 0:
                        break
                    top.extend(buffer[y] for y in range(mid))
                    history = history._replace(position=min(history.size, history.position + mid))  # type: ignore[attr-defined]
                    self.screen.history = history
                    for y in range(rows - mid):
                        buffer[y] = buffer[y + mid]
                    for y in range(rows - mid, rows):
                        buffer[y] = bottom.popleft()
                    self._scroll_lines = max(0, self._scroll_lines - mid)
                    self._viewport_top_row += mid
                    total_applied += mid
                    remaining -= mid
                if total_applied <= 0:
                    self._scroll_lines = 0
                    return True
                self._shift_selection_rows(-total_applied, rows)

            self._debug_gutter_log(
                "manual-scroll "
                f"request={delta_lines}, applied={total_applied}, rows={rows}, "
                f"viewport_top={old_viewport_top}->{self._viewport_top_row}, "
                f"scroll_lines={old_scroll_lines}->{self._scroll_lines}, "
                f"history_top={old_top_len}->{len(top)}, history_bottom={old_bottom_len}->{len(bottom)}, "
                f"{self._debug_selection_state()}"
            )

            dirty = getattr(self.screen, "dirty", None)
            if dirty is not None:
                dirty.update(range(rows))
            return True
        except Exception:
            return False

    def _restore_viewport_top_after_output(self, desired_top: int) -> None:
        """
        Keep the user's scrolled-back viewport stable after new output arrives.

        pyte may collapse its history.bottom queue and move the visible buffer
        back near the newest output when data is fed while the user is scrolled
        up. Our selection and command markers use absolute buffer rows, so the
        visible viewport must be restored to the absolute top row the user was
        looking at before the output arrived.
        """
        rows = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
        bottom_top = max(0, self._history_top_len())
        target_top = max(0, min(int(desired_top), bottom_top))

        old_viewport_top = self._viewport_top_row
        old_scroll_lines = self._scroll_lines
        old_top = self._history_top_len()
        old_bottom = self._history_bottom_len()

        self._scroll_lines = 0
        self._viewport_top_row = bottom_top
        if bottom_top > target_top:
            self._scroll_history_lines(bottom_top - target_top)
        else:
            self._mark_dirty()

        self._debug_gutter_log(
            "restore-after-output "
            f"desired_top={desired_top}, target_top={target_top}, bottom_top={bottom_top}, rows={rows}, "
            f"viewport_top={old_viewport_top}->{self._viewport_top_row}, "
            f"scroll_lines={old_scroll_lines}->{self._scroll_lines}, "
            f"history_top={old_top}->{self._history_top_len()}, history_bottom={old_bottom}->{self._history_bottom_len()}, "
            f"{self._debug_selection_state()}"
        )

    def _shift_selection_rows(self, delta_y: int, rows: int) -> None:
        """Selection rows are absolute buffer rows; viewport movement does not rewrite them."""
        return

    def _history_top_len(self) -> int:
        try:
            history = getattr(self.screen, "history", None)
            top = getattr(history, "top", None)
            return len(top) if top is not None else 0
        except Exception:
            return 0

    def _history_bottom_len(self) -> int:
        try:
            history = getattr(self.screen, "history", None)
            bottom = getattr(history, "bottom", None)
            return len(bottom) if bottom is not None else 0
        except Exception:
            return 0

    def _visible_y_to_abs_y(self, y: int) -> int:
        return max(0, int(self._viewport_top_row) + int(y))

    def _abs_y_to_visible_y(self, y: int) -> int:
        return int(y) - max(0, int(self._viewport_top_row))

    def _visible_cell_to_abs_cell(self, cell: tuple[int, int]) -> tuple[int, int]:
        x, y = cell
        return x, self._visible_y_to_abs_y(y)

    def _abs_cell_to_visible_cell(self, cell: tuple[int, int]) -> tuple[int, int]:
        x, y = cell
        return x, self._abs_y_to_visible_y(y)

    def _cursor_abs_y(self) -> int | None:
        cursor = getattr(self.screen, "cursor", None)
        if cursor is None:
            return None
        try:
            y = int(getattr(cursor, "y", -1))
        except Exception:
            return None
        _cols, widget_rows = self._calc_cols_rows()
        rows = int(getattr(self.screen, "lines", widget_rows) or widget_rows)
        if not 0 <= y < rows:
            return None
        return self._visible_y_to_abs_y(y) + self._history_bottom_len()

    def _max_scroll_lines(self) -> int:
        return max(0, int(self._scroll_lines) + self._history_top_len())

    def _scrollbar_track_rect(self) -> QRectF:
        width = self._right_scrollbar_width()
        if width <= 0:
            return QRectF()
        return QRectF(max(0, self.width() - width), 0, width, self.height())

    def _scrollbar_rects(self) -> tuple[QRectF, QRectF, int]:
        track = self._scrollbar_track_rect()
        if track.isNull() or track.width() <= 0 or track.height() <= 0:
            return track, QRectF(), 0
        _cols, rows = self._calc_cols_rows()
        max_scroll = self._max_scroll_lines()
        if max_scroll <= 0:
            return track, QRectF(track.x(), track.y(), track.width(), track.height()), 0
        track_h = float(track.height())
        thumb_h = max(24.0, track_h * rows / max(1, rows + max_scroll))
        thumb_h = min(track_h, thumb_h)
        travel = max(0.0, track_h - thumb_h)
        scroll_ratio = min(1.0, max(0.0, float(self._scroll_lines) / max_scroll))
        thumb_y = track.y() + travel * (1.0 - scroll_ratio)
        thumb = QRectF(track.x(), thumb_y, track.width(), thumb_h)
        return track, thumb, max_scroll

    def _draw_scrollbar(self, painter: QPainter) -> None:
        track, thumb, _max_scroll = self._scrollbar_rects()
        if track.isNull() or track.width() <= 0:
            return
        painter.fillRect(track, self._scrollbar_bg())
        if not thumb.isNull() and thumb.height() > 0:
            painter.fillRect(thumb, self._scrollbar_thumb_bg())

    def _pos_in_scrollbar(self, pos: QPoint) -> bool:
        track = self._scrollbar_track_rect()
        return not track.isNull() and track.contains(QPointF(pos))

    def _set_scrollbar_target(self, target: int) -> None:
        max_scroll = self._max_scroll_lines()
        target = max(0, min(max_scroll, int(target)))
        delta = target - int(self._scroll_lines)
        if delta:
            self._scroll_view(delta)
        else:
            self._mark_dirty()

    def _scrollbar_target_from_y(self, y: float, thumb_offset: float = 0.0) -> int:
        track, thumb, max_scroll = self._scrollbar_rects()
        if max_scroll <= 0 or track.isNull():
            return 0
        thumb_h = max(1.0, float(thumb.height()))
        travel = max(1.0, float(track.height()) - thumb_h)
        thumb_top = max(float(track.top()), min(float(track.bottom()) - thumb_h, float(y) - thumb_offset))
        ratio_from_top = (thumb_top - float(track.top())) / travel
        return int(round((1.0 - ratio_from_top) * max_scroll))

    def _handle_scrollbar_press(self, pos: QPoint) -> bool:
        if not self._pos_in_scrollbar(pos):
            return False
        track, thumb, max_scroll = self._scrollbar_rects()
        if max_scroll <= 0:
            return True
        if thumb.contains(QPointF(pos)):
            self._is_scrollbar_dragging = True
            self._scrollbar_drag_offset_y = float(pos.y()) - float(thumb.top())
            return True
        self._set_scrollbar_target(self._scrollbar_target_from_y(float(pos.y()), float(thumb.height()) / 2.0))
        return True

    def _drag_scrollbar_to(self, pos: QPoint) -> None:
        self._set_scrollbar_target(self._scrollbar_target_from_y(float(pos.y()), self._scrollbar_drag_offset_y))

    def _enter_alt_screen(self) -> None:
        if self._in_alt_screen:
            return
        cols, rows = self._calc_cols_rows()
        if self._alt_screen is None:
            try:
                self._alt_screen = pyte.Screen(cols, rows)  # type: ignore[attr-defined]
                self._alt_stream = pyte.Stream(self._alt_screen)  # type: ignore[attr-defined]
            except Exception:
                self._alt_screen = None
                self._alt_stream = None
                return
        # Clear alt screen on entry (typical 1049 semantics)
        try:
            self._alt_screen.reset()  # type: ignore[union-attr]
        except Exception:
            pass
        self._in_alt_screen = True
        self.screen = self._alt_screen  # type: ignore[assignment]
        self.stream = self._alt_stream  # type: ignore[assignment]
        self._debug_cell_dump_count = 0
        # Clear selection to avoid confusing highlight across buffers
        self._sel_anchor = None
        self._sel_head = None
        self._mark_full_repaint()

    def _exit_alt_screen(self) -> None:
        if not self._in_alt_screen:
            return
        self._in_alt_screen = False
        self.screen = self._main_screen
        self.stream = self._main_stream
        self._sel_anchor = None
        self._sel_head = None
        # Best-effort: clear alt to avoid stale content if re-entered
        try:
            if self._alt_screen is not None:
                self._alt_screen.reset()
        except Exception:
            pass
        self._mark_full_repaint()

    # -------------------------
    # Wheel -> keys (vim-friendly)
    # -------------------------
    def _cell_from_event(self, event) -> tuple[int, int]:
        try:
            pos = event.pos()
        except Exception:
            pos = QPoint(int(event.x()), int(event.y()))
        return self._pos_to_cell(pos)

    def _mouse_enabled(self) -> bool:
        return bool(self._mouse_sgr_1006 and (self._mouse_track_1000 or self._mouse_track_1002 or self._mouse_track_1003))

    def _mods_mask(self, mods) -> int:
        m = 0
        if mods & Qt.ShiftModifier:
            m |= 4
        if mods & Qt.AltModifier:
            m |= 8
        if mods & Qt.ControlModifier:
            m |= 16
        return m

    def _send_mouse_sgr(self, btn_code: int, x: int, y: int, pressed: bool) -> None:
        # SGR coords are 1-based; M=press, m=release
        x = max(1, min(2000, int(x)))
        y = max(1, min(2000, int(y)))
        suf = "M" if pressed else "m"
        seq = f"\x1b[<{btn_code};{x};{y}{suf}".encode("ascii", errors="ignore")
        self.input_received.emit(seq)

    def wheelEvent(self, event):  # type: ignore[override]
        # Ctrl + wheel is an explicit fast-scroll gesture. It takes precedence
        # over remote mouse reporting and moves one visible page per wheel step.
        if event.modifiers() & Qt.ControlModifier:
            angle = event.angleDelta()
            pixel = event.pixelDelta()
            if angle.y() != 0:
                dy = int(angle.y())
                pages = max(1, min(10, int(round(abs(dy) / 120.0))))
            elif pixel.y() != 0:
                dy = int(pixel.y())
                pages = 1
            else:
                event.ignore()
                return

            if not self._in_alt_screen:
                # Keep one overlapping line between pages so users retain
                # visual context across each fast-scroll step.
                page_lines = max(1, int(getattr(self.screen, 'lines', 1)) - 1)
                self._scroll_view(page_lines * pages if dy > 0 else -page_lines * pages)
            else:
                seq = b"\x1b[5~" if dy > 0 else b"\x1b[6~"
                for _ in range(pages):
                    self.input_received.emit(seq)
            event.accept()
            return

        # If remote enabled mouse reporting, send wheel events as mouse buttons 64/65 (SGR 1006).
        if self._mouse_enabled() and not (event.modifiers() & Qt.ShiftModifier):
            angle = event.angleDelta()
            pixel = event.pixelDelta()
            dy = 0
            if angle.y() != 0:
                dy = int(angle.y())
                steps = max(1, min(10, int(round(abs(dy) / 120.0))))
            elif pixel.y() != 0:
                dy = int(pixel.y())
                steps = max(1, min(10, int(round(abs(dy) / 40.0))))
            else:
                event.ignore()
                return

            (cx, cy) = self._cell_from_event(event)
            x = cx + 1
            y = cy + 1
            base = 64 if dy > 0 else 65
            btn = base | self._mods_mask(event.modifiers())
            for _ in range(steps):
                self._send_mouse_sgr(btn, x, y, True)
            event.accept()
            return

        angle = event.angleDelta()
        pixel = event.pixelDelta()
        dy = 0
        if angle.y() != 0:
            dy = int(angle.y())
            steps = max(1, min(20, int(round(abs(dy) / 120.0))))
        elif pixel.y() != 0:
            dy = int(pixel.y())
            steps = max(1, min(40, int(round(abs(dy) / 30.0))))
        else:
            event.ignore()
            return

        # Default behavior: local scrollback in main screen (more usable than sending PgUp/PgDn to shell).
        if not self._in_alt_screen and not self._mouse_enabled():
            # Up wheel => older lines
            lines = steps * (3 if angle.y() != 0 else 1)
            self._scroll_view(lines if dy > 0 else -lines)
            event.accept()
            return

        # In alt-screen (vim) without mouse reporting, fallback to PageUp/Down.
        seq = b"\x1b[5~" if dy > 0 else b"\x1b[6~"
        for _ in range(steps):
            self.input_received.emit(seq)
        event.accept()

    # -------------------------
    # Mouse reporting (SGR 1006)
    # -------------------------
    def mousePressEvent(self, event):  # type: ignore[override]
        self._hide_gutter_command_tooltip()

        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if self._handle_scrollbar_press(event.pos()):
                event.accept()
                return

        # When mouse reporting is enabled by the remote app (e.g. vim :set mouse=a),
        # forward events unless Shift is held (Shift = local select/copy like many terminals).
        if (
            self._mouse_enabled()
            and not (event.modifiers() & Qt.ShiftModifier)
            and not self._pos_in_gutter(event.pos())
        ):
            btn_map = {
                Qt.LeftButton: 0,
                Qt.MiddleButton: 1,
                Qt.RightButton: 2,
            }
            if event.button() in btn_map:
                cx, cy = self._cell_from_event(event)
                btn = btn_map[event.button()] | self._mods_mask(event.modifiers())
                self._pressed_mouse_buttons.add(btn_map[event.button()])
                self._send_mouse_sgr(btn, cx + 1, cy + 1, True)
                event.accept()
                return

        # Local selection
        if event.button() == Qt.LeftButton:
            if self._is_triple_click(event.pos()):
                self._select_line_at_pos(event.pos())
                self._is_selecting = False
                self._last_double_click_t = 0.0
                self._last_double_click_pos = None
                self._mark_dirty()
                event.accept()
                return
            self._sel_anchor = self._pos_to_selection_start_cell(event.pos())
            self._sel_head = self._pos_to_selection_head_cell(event.pos())
            self._is_selecting = True
            self._mark_dirty()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        if self._pos_in_gutter(event.pos()):
            self._select_command_block_at_pos(event.pos())
            self._is_selecting = False
            self._mark_dirty()
            event.accept()
            return

        if self._mouse_enabled() and not (event.modifiers() & Qt.ShiftModifier):
            super().mouseDoubleClickEvent(event)
            return

        cell = self._pos_to_cell(event.pos())
        if self._select_word_at(cell):
            self._is_selecting = False
            self._last_double_click_t = time.monotonic()
            self._last_double_click_pos = QPoint(event.pos())
            self._mark_dirty()
            event.accept()
            return

        self._last_double_click_t = time.monotonic()
        self._last_double_click_pos = QPoint(event.pos())

        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._is_scrollbar_dragging:
            self._is_scrollbar_dragging = False
            event.accept()
            return

        if self._mouse_enabled() and not (event.modifiers() & Qt.ShiftModifier):
            btn_map = {
                Qt.LeftButton: 0,
                Qt.MiddleButton: 1,
                Qt.RightButton: 2,
            }
            if event.button() in btn_map:
                cx, cy = self._cell_from_event(event)
                btn = btn_map[event.button()] | self._mods_mask(event.modifiers())
                try:
                    self._pressed_mouse_buttons.discard(btn_map[event.button()])
                except Exception:
                    pass
                self._send_mouse_sgr(btn, cx + 1, cy + 1, False)
                event.accept()
                return

        if event.button() == Qt.LeftButton:
            self._is_scrollbar_dragging = False
            self._is_selecting = False
            self._mark_dirty()

        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        self._update_gutter_command_tooltip(event)

        if self._is_scrollbar_dragging:
            self._drag_scrollbar_to(event.pos())
            event.accept()
            return

        if self._mouse_enabled() and not (event.modifiers() & Qt.ShiftModifier):
            # Motion reporting depends on mode:
            # - 1002: only while a button is pressed
            # - 1003: any motion
            if self._mouse_track_1003 or (self._mouse_track_1002 and self._pressed_mouse_buttons):
                cx, cy = self._cell_from_event(event)
                # Motion flag is 32, plus current pressed button (prefer left if unknown)
                pressed = 0
                if self._pressed_mouse_buttons:
                    pressed = sorted(self._pressed_mouse_buttons)[0]
                btn = (32 + pressed) | self._mods_mask(event.modifiers())
                self._send_mouse_sgr(btn, cx + 1, cy + 1, True)
                event.accept()
                return

        if self._is_selecting and self._sel_anchor:
            new_head = self._pos_to_selection_head_cell(event.pos())
            if new_head != self._sel_head:
                self._sel_head = new_head
                self._mark_dirty()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self._hide_gutter_command_tooltip()
        super().leaveEvent(event)

    # -------------------------
    # Rendering
    # -------------------------
    def _reset_backing_store(self) -> None:
        try:
            self._backing = QPixmap(max(1, self.width()), max(1, self.height()))
            self._backing.fill(self._terminal_bg())
        except Exception:
            self._backing = QPixmap()
        self._mark_full_repaint()

    def _mark_full_repaint(self) -> None:
        self._full_repaint_needed = True
        _, rows = self._calc_cols_rows()
        self._dirty_lines = set(range(rows))

    def _collect_pyte_dirty_lines(self) -> None:
        dirty = getattr(self.screen, "dirty", None)
        if dirty is None:
            self._mark_full_repaint()
            return
        try:
            _, rows = self._calc_cols_rows()
            self._dirty_lines.update(int(y) for y in dirty if 0 <= int(y) < rows)
            dirty.clear()
        except Exception:
            self._mark_full_repaint()

    def _mark_dirty(self) -> None:
        if not self._is_alive():
            return
        self._collect_pyte_dirty_lines()
        cursor = getattr(self.screen, "cursor", None)
        cx = int(getattr(cursor, "x", -1)) if cursor else -1
        cy = int(getattr(cursor, "y", -1)) if cursor else -1
        if self._last_cursor_cell:
            self._dirty_lines.add(self._last_cursor_cell[1])
        if cy >= 0:
            self._dirty_lines.add(cy)

        # Throttle redraw to ~60fps max
        now = time.monotonic()
        if (now - self._last_paint_t) >= (1.0 / 60.0):
            self.update()
        else:
            if not self._paint_timer.isActive():
                self._paint_timer.start(16)

    def _color_from_pyte(self, val: Any, default: QColor) -> QColor:
        if val is None:
            return default
        if isinstance(val, QColor):
            return val
        if isinstance(val, int):
            return self._xterm_256_color(val)
        if isinstance(val, str):
            if val in ("default", ""):
                return default
            # pyte represents 256-color and true-color SGR values as bare
            # six-digit hex strings (for example "0087ff"), while QColor
            # expects the leading '#'.
            if re.fullmatch(r"[0-9a-fA-F]{6}", val):
                return QColor(f"#{val}")
            if val.startswith("#") and len(val) in (4, 7):
                return QColor(val)
            # pyte may return color names like "red"/"brightred", or "ansiRed"/"ansiBrightRed"
            # depending on version/config. Normalize.
            name = val
            if name.startswith("ansi"):
                name = name[4:]
            name = name.strip().lower().replace("_", "").replace("-", "")
            # Common aliases
            if name.startswith("light") and len(name) > 5:
                # lightred -> brightred
                name = "bright" + name[5:]
            if name == "brown":
                name = "yellow"
            if name == "brightbrown":
                name = "brightyellow"
            if name == "lightgray":
                name = "white"
            if name == "darkgray":
                name = "brightblack"
            if name == "bfightmagenta":
                name = "brightmagenta"

            m = {
                "black": QColor("#000000"),
                "red": QColor("#FF5555"),
                "green": QColor("#50FA7B"),
                "yellow": QColor("#F1FA8C"),
                "blue": QColor("#BD93F9"),
                "magenta": QColor("#FF79C6"),
                "cyan": QColor("#8BE9FD"),
                "white": QColor("#BFBFBF"),
                "brightblack": QColor("#4D4D4D"),
                "brightred": QColor("#FF6E6E"),
                "brightgreen": QColor("#69FF94"),
                "brightyellow": QColor("#FFFFA5"),
                "brightblue": QColor("#D6ACFF"),
                "brightmagenta": QColor("#FF92DF"),
                "brightcyan": QColor("#A4FFFF"),
                "brightwhite": QColor("#FFFFFF"),
            }
            if name in m:
                return m[name]
            if val.startswith("color") and val[5:].isdigit():
                return self._xterm_256_color(int(val[5:]))
        return default

    def _xterm_256_color(self, n: int) -> QColor:
        n = max(0, min(255, int(n)))
        if 0 <= n <= 15:
            base = {
                0: QColor("#000000"), 1: QColor("#FF5555"), 2: QColor("#50FA7B"), 3: QColor("#F1FA8C"),
                4: QColor("#BD93F9"), 5: QColor("#FF79C6"), 6: QColor("#8BE9FD"), 7: QColor("#BFBFBF"),
                8: QColor("#4D4D4D"), 9: QColor("#FF6E6E"), 10: QColor("#69FF94"), 11: QColor("#FFFFA5"),
                12: QColor("#D6ACFF"), 13: QColor("#FF92DF"), 14: QColor("#A4FFFF"), 15: QColor("#FFFFFF"),
            }
            return base.get(n, QColor("#FFFFFF"))
        if 16 <= n <= 231:
            n -= 16
            r = n // 36
            g = (n % 36) // 6
            b = n % 6
            def level(v: int) -> int:
                return 0 if v == 0 else 55 + v * 40
            return QColor(level(r), level(g), level(b))
        gray = 8 + (n - 232) * 10
        return QColor(gray, gray, gray)

    def _display_width(self, ch: str) -> int:
        if not ch:
            return 0
        if len(ch) == 1 and ord(ch) < 128:
            return 1
        return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1

    def _cell_style(
        self,
        line: Any,
        x: int,
        y: int,
        sel: tuple[tuple[int, int], tuple[int, int]] | None,
        cursor_cell: tuple[int, int] | None,
    ) -> tuple[str, QColor, QColor, bool, bool]:
        ch = self._cell_data(line, x)
        fg = self._default_fg
        bg = self._terminal_bg()
        bold = False
        underline = False
        try:
            c = line[x] if line is not None else None
            if c is not None and ch:
                fg = self._color_from_pyte(getattr(c, 'fg', None), self._default_fg)
                bg = self._color_from_pyte(getattr(c, 'bg', None), self._default_bg)
                bold = bool(getattr(c, 'bold', False))
                underline = bool(getattr(c, 'underscore', False) or getattr(c, 'underline', False))
                if bool(getattr(c, 'reverse', False)):
                    fg, bg = bg, fg
        except Exception:
            pass
        if sel and self._cell_in_selection(x, y, sel):
            bg = self._selection_bg()
        if cursor_cell and (x, y) == cursor_cell:
            fg, bg = bg, fg
        return ch, fg, bg, bold, underline

    def _cell_data(self, line: Any, x: int) -> str:
        try:
            c = line[x] if line is not None else None
            if c is not None:
                data = getattr(c, 'data', None)
                if data is None:
                    return ''
                return data
        except Exception:
            pass
        return ''

    def _is_wide_leading_cell(self, line: Any, x: int, cols: int) -> bool:
        ch = self._cell_data(line, x)
        if not ch or x + 1 >= cols:
            return False
        if self._cell_data(line, x + 1) != '':
            return False
        return self._display_width(ch) >= 2

    def _is_wide_continuation_cell(self, line: Any, x: int) -> bool:
        if x <= 0 or self._cell_data(line, x) != '':
            return False
        return self._cell_data(line, x - 1) != ''

    def _cell_span(self, line: Any, x: int, cols: int) -> int:
        return 2 if self._is_wide_leading_cell(line, x, cols) else 1

    def _line_from_screen_buffer(self, screen: Any, y: int) -> Any:
        try:
            rows = int(getattr(screen, "lines", self._calc_cols_rows()[1]) or 0)
            if y < 0 or y >= rows:
                return None
        except Exception:
            if y < 0:
                return None
        buffer = getattr(screen, "buffer", None)
        if buffer is None:
            return None
        try:
            return buffer[y]
        except Exception:
            return None

    def _line_from_buffer(self, y: int) -> Any:
        return self._line_from_screen_buffer(self.screen, y)

    def _safe_display_line_for_screen(self, screen: Any, y: int, cols: int) -> str:
        """Build one display line from pyte buffer; avoids Screen.display on empty cells."""
        if cols <= 0:
            return ""
        line = self._line_from_screen_buffer(screen, y)
        if line is None:
            return ""
        chars: list[str] = []
        x = 0
        while x < cols:
            if self._is_wide_continuation_cell(line, x):
                x += 1
                continue
            ch = self._cell_data(line, x)
            if not ch:
                x += 1
                continue
            chars.append(ch)
            x += self._cell_span(line, x, cols)
        return "".join(chars)

    def _safe_display_lines_for_screen(self, screen: Any) -> list[str]:
        try:
            rows = int(getattr(screen, "lines", 0) or 0)
            cols = int(getattr(screen, "columns", self._calc_cols_rows()[0]) or 0)
        except Exception:
            return []
        if rows <= 0:
            return []
        return [self._safe_display_line_for_screen(screen, y, cols) for y in range(rows)]

    def _safe_display_lines(self) -> list[str]:
        return self._safe_display_lines_for_screen(self.screen)

    def _render_lines(
        self,
        painter: QPainter,
        line_nums: set[int] | range,
        *,
        include_selection: bool,
        include_cursor: bool,
        clear_line: bool,
    ) -> None:
        painter.setFont(self.font())
        buffer = getattr(self.screen, "buffer", None)
        cursor = getattr(self.screen, "cursor", None)
        cx = int(getattr(cursor, "x", -1)) if cursor else -1
        cy = int(getattr(cursor, "y", -1)) if cursor else -1
        cursor_cell = (cx, cy) if include_cursor and cx >= 0 and cy >= 0 else None

        sel = self._normalized_selection() if include_selection else None

        widget_cols, widget_rows = self._calc_cols_rows()
        text_origin_x = self._text_origin_x()
        screen_cols = int(getattr(self.screen, 'columns', widget_cols) or widget_cols)
        cols = min(widget_cols, screen_cols)
        try:
            screen_rows = int(getattr(self.screen, "lines", widget_rows) or widget_rows)
        except Exception:
            screen_rows = widget_rows
        rows = min(screen_rows, widget_rows)
        for y in sorted(line_nums):
            if y < 0 or y >= rows:
                continue
            row_rect = QRectF(text_origin_x, y * self._cell_h, max(0.0, self.width() - text_origin_x), self._cell_h)
            if clear_line:
                painter.fillRect(row_rect, self._terminal_bg())
            # Text glyphs such as underscore can extend slightly outside the
            # nominal font metrics. Clip each row so a glyph from one line
            # cannot leave stale pixels in the next row after scrolling.
            painter.save()
            painter.setClipRect(row_rect)
            # Cell-level drawing needs pyte's buffer. Without it, leave the
            # cleared row blank rather than calling Screen.display.
            if buffer is None:
                painter.restore()
                continue

            # buffer[y] is a line of cells
            try:
                line = buffer[y]
            except Exception:
                line = None

            x = 0
            while x < cols:
                if self._is_wide_continuation_cell(line, x):
                    x += 1
                    continue

                ch, fg, bg, bold, underline = self._cell_style(line, x, y, sel, cursor_cell)
                span = self._cell_span(line, x, cols)

                if not ch:
                    x += 1
                    continue

                if span > 1:
                    # Wide glyphs are drawn independently instead of being
                    # merged into a run; Qt's normal text layout would apply
                    # glyph advance again and make the following placeholder
                    # cell look like an extra visible space.
                    px = text_origin_x + x * self._cell_w
                    py = y * self._cell_h
                    painter.fillRect(QRectF(px, py, self._cell_w * span, self._cell_h), bg)
                    if bold:
                        f = QFont(self.font())
                        f.setBold(True)
                        painter.setFont(f)
                    else:
                        painter.setFont(self.font())
                    painter.setPen(fg)
                    painter.drawText(QPointF(px, py + self._ascent), ch)

                    if underline:
                        painter.setPen(fg)
                        painter.drawLine(
                            QPointF(px, py + self._ascent + 1),
                            QPointF(px + self._cell_w * span, py + self._ascent + 1),
                        )

                    x += span
                    continue

                run_x = x
                run_chars = [ch]
                run_fg = fg
                run_bg = bg
                run_bold = bold
                run_ul = underline

                def same_style(nx: int) -> bool:
                    if nx >= cols:
                        return False
                    if self._is_wide_leading_cell(line, nx, cols) or self._is_wide_continuation_cell(line, nx):
                        return False
                    _ch2, fg2, bg2, b2, u2 = self._cell_style(line, nx, y, sel, cursor_cell)
                    return (_ch2 != "" and fg2 == run_fg and bg2 == run_bg and b2 == run_bold and u2 == run_ul)

                while same_style(run_x + 1):
                    run_x += 1
                    ch2, _, _, _, _ = self._cell_style(line, run_x, y, sel, cursor_cell)
                    run_chars.append(ch2)

                # Draw run text
                px = text_origin_x + x * self._cell_w
                py = y * self._cell_h
                # Fill background for the whole run (fixes missing background/colors when drawing in runs)
                painter.fillRect(QRectF(px, py, self._cell_w * len(run_chars), self._cell_h), run_bg)
                if run_bold:
                    f = QFont(self.font())
                    f.setBold(True)
                    painter.setFont(f)
                else:
                    painter.setFont(self.font())
                painter.setPen(run_fg)
                for i, run_ch in enumerate(run_chars):
                    painter.drawText(
                        QPointF(px + i * self._cell_w, py + self._ascent),
                        run_ch,
                    )

                if run_ul:
                    # Simple underline
                    painter.setPen(run_fg)
                    painter.drawLine(QPointF(px, py + self._ascent + 1),
                               QPointF(px + self._cell_w * len(run_chars), py + self._ascent + 1))

                x = run_x + 1
            painter.restore()

    def _render_dirty_to_backing(self) -> None:
        t0 = time.perf_counter()
        if self._backing.isNull() or self._backing.size() != self.size():
            self._reset_backing_store()
        if self._full_repaint_needed:
            self._backing.fill(self._terminal_bg())
            _, rows = self._calc_cols_rows()
            line_nums: set[int] | range = range(rows)
            line_count = rows
        else:
            line_nums = set(self._dirty_lines)
            line_count = len(line_nums)
        if not line_nums:
            return
        painter = QPainter(self._backing)
        self._render_lines(painter, line_nums, include_selection=False, include_cursor=False, clear_line=True)
        painter.end()
        self._dirty_lines.clear()
        self._full_repaint_needed = False
        dt_ms = (time.perf_counter() - t0) * 1000.0
        if dt_ms > 20 or time.monotonic() < self._debug_after_resize_until:
            self._debug_log(f"render backing: lines={line_count}, dt_ms={dt_ms:.1f}")

    def _selection_lines(self) -> set[int]:
        sel = self._normalized_selection()
        if not sel:
            return set()
        (_, sy), (_, ey) = sel
        rows = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
        start = max(0, self._abs_y_to_visible_y(sy))
        end = min(rows - 1, self._abs_y_to_visible_y(ey))
        if rows <= 0 or start > end:
            return set()
        return set(range(start, end + 1))

    def _draw_gutter_command_markers(self, painter: QPainter, gutter_width: int) -> None:
        """Draw committed command starts at their current viewport rows."""
        if gutter_width <= 0:
            return
        viewport_rows = int(getattr(self.screen, "lines", 0) or 0)
        marker_rows = _visible_command_marker_rows(
            self._command_start_rows,
            self._viewport_top_row,
            viewport_rows,
            in_alt_screen=self._in_alt_screen,
        )
        marker_bg = self._gutter_command_bg()
        for row in marker_rows:
            painter.fillRect(
                QRectF(0, row * self._cell_h, gutter_width, self._cell_h),
                marker_bg,
            )

    def paintEvent(self, event):  # type: ignore[override]
        t0 = time.perf_counter()
        self._last_paint_t = time.monotonic()
        self._render_dirty_to_backing()

        p = QPainter(self)
        p.fillRect(self.rect(), self._terminal_bg())
        if not self._backing.isNull():
            p.drawPixmap(0, 0, self._backing)
        gutter_width = self._left_gutter_width()
        if gutter_width > 0:
            p.fillRect(QRectF(0, 0, gutter_width, self.height()), self._gutter_bg())
            self._draw_gutter_command_markers(p, gutter_width)
        self._draw_scrollbar(p)

        overlay_lines = self._selection_lines()
        cursor = getattr(self.screen, "cursor", None)
        cx = int(getattr(cursor, "x", -1)) if cursor else -1
        cy = int(getattr(cursor, "y", -1)) if cursor else -1
        if cy >= 0 and self._scroll_lines == 0:
            overlay_lines.add(cy)
            self._last_cursor_cell = (cx, cy)

        if overlay_lines:
            self._render_lines(p, overlay_lines, include_selection=True, include_cursor=True, clear_line=False)
        if self.hasFocus():
            pen = QPen(self._focus_border_color())
            pen.setWidth(1)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()
        dt_ms = (time.perf_counter() - t0) * 1000.0
        if dt_ms > 20 or time.monotonic() < self._debug_after_resize_until:
            self._debug_log(f"paintEvent: overlay_lines={len(overlay_lines)}, dt_ms={dt_ms:.1f}")

    # -------------------------
    # Selection + copy
    # -------------------------
    def _pos_to_cell(self, pos: QPoint) -> tuple[int, int]:
        cw = max(1.0, float(self._cell_w))
        ch = max(1.0, float(self._cell_h))
        cols, rows = self._calc_cols_rows()
        text_x = max(0.0, float(pos.x()) - self._text_origin_x())
        x = max(0, min(cols - 1, int(text_x / cw)))
        y = max(0, min(rows - 1, int(pos.y() / ch)))
        line = self._line_from_buffer(y)
        if self._is_wide_continuation_cell(line, x):
            x = max(0, x - 1)
        return x, y

    def _is_triple_click(self, pos: QPoint) -> bool:
        if self._last_double_click_pos is None:
            return False
        interval_ms = 500
        try:
            hints = QGuiApplication.styleHints()
            interval_ms = int(hints.mouseDoubleClickInterval())
            distance = int(hints.startDragDistance())
        except Exception:
            distance = 8
        if (time.monotonic() - self._last_double_click_t) * 1000.0 > interval_ms:
            return False
        delta = pos - self._last_double_click_pos
        return abs(delta.x()) <= distance and abs(delta.y()) <= distance

    def _line_last_content_cell(self, y: int) -> int:
        cols, rows = self._calc_cols_rows()
        if rows <= 0:
            return 0
        y = max(0, min(rows - 1, y))
        line = self._line_from_buffer(y)
        last = 0
        x = 0
        while x < cols:
            if self._is_wide_continuation_cell(line, x):
                x += 1
                continue
            ch = self._cell_data(line, x)
            span = self._cell_span(line, x, cols)
            if ch and ch != ' ':
                last = min(cols - 1, x + span - 1)
            x += span
        return last

    def _line_text(self, y: int) -> str:
        cols, rows = self._calc_cols_rows()
        if rows <= 0:
            return ''
        y = max(0, min(rows - 1, y))
        line = self._line_from_buffer(y)
        chars: list[str] = []
        x = 0
        while x < cols:
            if self._is_wide_continuation_cell(line, x):
                x += 1
                continue
            ch = self._cell_data(line, x)
            if ch:
                chars.append(ch)
            x += self._cell_span(line, x, cols)
        return ''.join(chars).rstrip()

    def _current_input_continues(self) -> bool:
        y = self._cursor_y()
        if y is None:
            return False
        return self._line_text(y).endswith('\\')

    def _input_line_y(self) -> int | None:
        if self._scroll_lines != 0:
            return None
        cursor = getattr(self.screen, "cursor", None)
        if cursor is None:
            return None
        try:
            y = int(getattr(cursor, "y", -1))
        except Exception:
            return None
        _cols, rows = self._calc_cols_rows()
        return y if 0 <= y < rows else None

    def _virtual_input_line_y(self) -> int | None:
        """Return the current input row in the local viewport coordinate space."""
        abs_y = self._cursor_abs_y()
        return None if abs_y is None else self._abs_y_to_visible_y(abs_y)

    def _cursor_y(self) -> int | None:
        cursor = getattr(self.screen, "cursor", None)
        if cursor is None:
            return None
        try:
            y = int(getattr(cursor, "y", -1))
        except Exception:
            return None
        _cols, rows = self._calc_cols_rows()
        return y if 0 <= y < rows else None

    def _record_pending_command_start(self) -> None:
        if self._in_alt_screen or self._scroll_lines != 0:
            return
        if self._pending_command_start_y is not None:
            return
        self._pending_command_start_y = self._cursor_abs_y()
        cursor = getattr(self.screen, "cursor", None)
        try:
            self._pending_command_start_x = int(getattr(cursor, "x", 0)) if cursor is not None else 0
        except Exception:
            self._pending_command_start_x = 0

    def _commit_command_start(self, *, check_continuation: bool = False) -> None:
        if self._in_alt_screen:
            self._pending_command_start_y = None
            self._pending_command_start_x = None
            self._pending_command_text = ''
            return
        if check_continuation and self._current_input_continues():
            self._record_pending_command_start()
            return
        start_y = self._pending_command_start_y
        if start_y is None:
            start_y = self._cursor_abs_y()
        self._pending_command_start_y = None
        if start_y is None:
            self._emit_pending_command_submitted('', None)
            self._pending_command_start_x = None
            return
        if self._command_start_rows and self._command_start_rows[-1] == start_y:
            self._emit_pending_command_submitted(self._command_start_times.get(start_y, ''), start_y)
            self._pending_command_start_x = None
            return
        self._command_start_rows.append(start_y)
        self._command_start_rows = sorted(set(self._command_start_rows))[-1000:]
        sent_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._command_start_times[start_y] = sent_at
        keep = set(self._command_start_rows)
        self._command_start_times = {
            row: sent_at
            for row, sent_at in self._command_start_times.items()
            if row in keep
        }
        self._command_start_commands = {
            row: command
            for row, command in self._command_start_commands.items()
            if row in keep
        }
        self._emit_pending_command_submitted(sent_at, start_y)
        self._pending_command_start_x = None

    def _clear_command_marks(self) -> None:
        self._command_start_rows.clear()
        self._command_start_times.clear()
        self._pending_command_start_y = None
        self._pending_command_start_x = None
        self._pending_command_text = ''
        self._pending_command_history_forced = False
        self._command_start_commands.clear()
        self._last_gutter_tooltip_row = None

    def _append_pending_command_text(self, text: str, *, force_history: bool = False) -> None:
        if not text:
            return
        self._record_pending_command_start()
        self._pending_command_text += text
        self._pending_command_history_forced = self._pending_command_history_forced or force_history

    def _delete_pending_command_char(self) -> None:
        if self._pending_command_text:
            self._pending_command_text = self._pending_command_text[:-1]

    def _delete_pending_command_word(self) -> None:
        text = self._pending_command_text.rstrip()
        if not text:
            self._pending_command_text = ''
            return
        i = len(text)
        while i > 0 and text[i - 1].isalnum():
            i -= 1
        if i == len(text):
            i -= 1
        self._pending_command_text = text[:i]

    def _emit_pending_command_submitted(self, sent_at: str, start_y: int | None) -> None:
        command = self._pending_command_text.strip()
        force_history = self._pending_command_history_forced
        visible_command = self._visible_command_text_for_history(start_y, command)
        if visible_command:
            command = visible_command
        self._pending_command_text = ''
        self._pending_command_history_forced = False
        if command and sent_at and (force_history or self._pending_command_was_visible(command)):
            if start_y is not None:
                self._command_start_commands[start_y] = command
            self.command_submitted.emit(command, sent_at, int(start_y))

    def _visible_command_text_for_history(self, start_y: int | None, typed_command: str) -> str:
        if start_y is None:
            return ''
        visible_y = self._abs_y_to_visible_y(start_y)
        _cols, rows = self._calc_cols_rows()
        if visible_y < 0 or visible_y >= rows:
            return ''
        line = self._line_text(visible_y).rstrip()
        if not line:
            return ''
        start_x = max(0, int(self._pending_command_start_x or 0))
        candidate = line[start_x:].strip()
        if not candidate:
            return ''
        return candidate

    def _pending_command_was_visible(self, command: str) -> bool:
        y = self._cursor_y()
        if y is None:
            return False
        visible = self._line_text(y).rstrip()
        if not visible:
            return False
        tail = command.splitlines()[-1].rstrip()
        return bool(tail) and visible.endswith(tail)

    def send_command_text(self, command: str, *, execute: bool = True) -> None:
        command = (command or '').strip()
        if not command:
            return
        self._prepare_for_terminal_input()
        payload_text = command.replace('\r\n', '\n').replace('\r', '\n')
        self._append_pending_command_text(payload_text, force_history=execute)
        payload = payload_text.replace('\n', '\r').encode('utf-8', errors='replace')
        if execute:
            self._commit_command_start()
            payload += b'\r'
        self.input_received.emit(payload)

    def scroll_to_command(self, command: str, sent_at: str, command_start_row: int = -1) -> bool:
        rows_count = int(getattr(self.screen, "lines", self._calc_cols_rows()[1]) or 0)
        current_top = int(self._viewport_top_row)
        bottom_top = self._max_scroll_lines()
        self._debug_history_jump_log(
            "request "
            f"command={command!r}, sent_at={sent_at!r}, command_start_row={command_start_row}, "
            f"rows={rows_count}, current_top={current_top}, bottom_top={bottom_top}, "
            f"scroll_lines={self._scroll_lines}, in_alt={self._in_alt_screen}, "
            f"starts={self._command_start_rows}, times={self._command_start_times}, "
            f"commands={self._command_start_commands}"
        )
        if self._in_alt_screen or not sent_at:
            self._debug_history_jump_log("abort reason=alt-screen-or-missing-time")
            return False
        if rows_count <= 0:
            self._debug_history_jump_log("abort reason=no-rows")
            return False

        if command_start_row >= 0:
            matches = [command_start_row] if command_start_row in self._command_start_rows else []
        else:
            matches = [
                row for row in self._command_start_rows
                if self._command_start_times.get(row) == sent_at
                and self._command_start_commands.get(row) == command
            ]
            if not matches:
                matches = [
                    row for row in self._command_start_rows
                    if self._command_start_times.get(row) == sent_at
                ]
        if not matches:
            self._debug_history_jump_log("abort reason=no-matching-command-start")
            return False

        start_y = matches[-1]
        actual_sent_at = self._command_start_times.get(start_y)
        if actual_sent_at != sent_at:
            self._debug_history_jump_log(
                "abort reason=time-mismatch "
                f"start_y={start_y}, expected={sent_at!r}, actual={actual_sent_at!r}"
            )
            return False
        actual_command = self._command_start_commands.get(start_y)
        if command and actual_command not in (None, command):
            self._debug_history_jump_log(
                "abort reason=command-mismatch "
                f"start_y={start_y}, expected={command!r}, actual={actual_command!r}"
            )
            return False
        max_abs_y = bottom_top + rows_count - 1
        if start_y < 0 or start_y > max_abs_y:
            self._debug_history_jump_log(
                "abort reason=out-of-range "
                f"start_y={start_y}, max_abs_y={max_abs_y}, bottom_top={bottom_top}"
            )
            return False

        self._debug_history_jump_viewport_sample("before-scroll")
        target_top = max(0, min(start_y, bottom_top))
        target_scroll = max(0, bottom_top - target_top)
        before_top = self._viewport_top_row
        before_scroll = self._scroll_lines
        self._set_scrollbar_target(target_scroll)
        self._mark_full_repaint()
        self.update()
        self._debug_history_jump_log(
            "done "
            f"start_y={start_y}, target_top={target_top}, target_scroll={target_scroll}, "
            f"viewport_top={before_top}->{self._viewport_top_row}, "
            f"scroll_lines={before_scroll}->{self._scroll_lines}, "
            f"history_top={self._history_top_len()}, history_bottom={self._history_bottom_len()}"
        )
        self._debug_history_jump_viewport_sample("after-scroll")
        return True

    def _command_start_row_for_abs_y(self, abs_y: int) -> int | None:
        if self._in_alt_screen or not self._command_start_rows:
            return None
        start_row: int | None = None
        for row in sorted(self._command_start_rows):
            if row <= abs_y:
                start_row = row
            else:
                break
        return start_row

    def _update_gutter_command_tooltip(self, event) -> None:
        if event.buttons() != Qt.NoButton:
            self._hide_gutter_command_tooltip()
            return
        if not self._pos_in_gutter(event.pos()):
            self._hide_gutter_command_tooltip()
            return
        y = self._line_y_from_pos(event.pos())
        abs_y = self._visible_y_to_abs_y(y)
        start_row = self._command_start_row_for_abs_y(abs_y)
        sent_at = self._command_start_times.get(start_row) if start_row is not None else None
        if not sent_at:
            self._hide_gutter_command_tooltip()
            return
        self._last_gutter_tooltip_row = start_row
        QToolTip.showText(event.globalPos(), t("terminal.command_sent_time", time=sent_at), self)

    def _hide_gutter_command_tooltip(self) -> None:
        if self._last_gutter_tooltip_row is None:
            return
        self._last_gutter_tooltip_row = None
        QToolTip.hideText()

    def _line_selection_limit(self, y: int) -> int | None:
        if self._input_line_y() != y:
            return None
        return self._line_last_content_cell(y)

    def _clamp_cell_to_line_content(self, cell: tuple[int, int]) -> tuple[int, int]:
        x, y = cell
        limit = self._line_selection_limit(y)
        return (min(x, limit), y) if limit is not None else cell

    def _pos_in_gutter(self, pos: QPoint) -> bool:
        return self._left_gutter_width() > 0 and pos.x() < self._left_gutter_width()

    def _line_y_from_pos(self, pos: QPoint) -> int:
        _cols, rows = self._calc_cols_rows()
        ch = max(1.0, float(self._cell_h))
        return max(0, min(rows - 1, int(pos.y() / ch)))

    def _gutter_line_cell(self, pos: QPoint, *, at_end: bool) -> tuple[int, int]:
        y = self._line_y_from_pos(pos)
        x = self._line_last_content_cell(y) if at_end else 0
        return x, self._visible_y_to_abs_y(y)

    def _pos_to_selection_start_cell(self, pos: QPoint) -> tuple[int, int]:
        if self._pos_in_gutter(pos):
            return self._gutter_line_cell(pos, at_end=False)
        return self._visible_cell_to_abs_cell(self._clamp_cell_to_line_content(self._pos_to_cell(pos)))

    def _pos_to_selection_head_cell(self, pos: QPoint) -> tuple[int, int]:
        if self._pos_in_gutter(pos):
            y = self._line_y_from_pos(pos)
            abs_y = self._visible_y_to_abs_y(y)
            anchor_y = self._sel_anchor[1] if self._sel_anchor is not None else abs_y
            return self._gutter_line_cell(pos, at_end=abs_y >= anchor_y)
        return self._visible_cell_to_abs_cell(self._clamp_cell_to_line_content(self._pos_to_cell(pos)))

    def _select_line_at_pos(self, pos: QPoint) -> None:
        _cols, rows = self._calc_cols_rows()
        ch = max(1.0, float(self._cell_h))
        y = max(0, min(rows - 1, int(pos.y() / ch)))
        abs_y = self._visible_y_to_abs_y(y)
        self._sel_anchor = (0, abs_y)
        self._sel_head = (self._line_last_content_cell(y), abs_y)

    def _select_command_block_at_pos(self, pos: QPoint) -> bool:
        if self._in_alt_screen or not self._command_start_rows:
            self._debug_gutter_log(
                "double-click fallback-line "
                f"reason={'alt-screen' if self._in_alt_screen else 'no-command-start'}, "
                f"pos=({pos.x()},{pos.y()}), {self._debug_selection_state()}"
            )
            self._select_line_at_pos(pos)
            return False
        y = self._line_y_from_pos(pos)
        abs_y = self._visible_y_to_abs_y(y)
        starts = sorted(self._command_start_rows)
        self._debug_gutter_log(
            "double-click begin "
            f"pos=({pos.x()},{pos.y()}), visible_y={y}, abs_y={abs_y}, "
            f"line_sample={self._debug_visible_line_sample(y)!r}, starts={starts}, "
            f"{self._debug_selection_state()}"
        )
        index = -1
        for i, start in enumerate(starts):
            if start <= abs_y:
                index = i
            else:
                break
        if index < 0:
            self._debug_gutter_log(
                "double-click fallback-line reason=no-start-before-click, "
                f"visible_y={y}, abs_y={abs_y}, starts={starts}, {self._debug_selection_state()}"
            )
            self._select_line_at_pos(pos)
            return False
        start_abs_y = starts[index]
        if index + 1 < len(starts):
            end_abs_y = starts[index + 1] - 1
            end_reason = "next-command"
        else:
            input_y = self._virtual_input_line_y()
            input_abs_y = None if input_y is None else self._visible_y_to_abs_y(input_y)
            end_abs_y = (input_abs_y - 1) if input_abs_y is not None else abs_y
            end_reason = f"input-line input_y={input_y}, input_abs_y={input_abs_y}"
        end_abs_y = max(start_abs_y, end_abs_y)
        end_y = self._abs_y_to_visible_y(end_abs_y)
        self._sel_anchor = (0, start_abs_y)
        _cols, rows = self._calc_cols_rows()
        end_x = self._line_last_content_cell(end_y) if 0 <= end_y < rows else max(0, _cols - 1)
        self._sel_head = (end_x, end_abs_y)
        self._debug_gutter_log(
            "double-click selected "
            f"index={index}, start_abs={start_abs_y}, end_abs={end_abs_y}, "
            f"end_reason={end_reason}, start_visible={self._abs_y_to_visible_y(start_abs_y)}, "
            f"end_visible={end_y}, end_x={end_x}, end_line_sample={self._debug_visible_line_sample(end_y)!r}, "
            f"{self._debug_selection_state()}"
        )
        return True

    def _expand_selection_endpoint(self, x: int, y: int, *, is_end: bool) -> tuple[int, int]:
        # Selection is still stored as terminal cell coordinates, but a CJK
        # glyph occupies two cells. Normalize endpoints so drag/copy never
        # captures only the continuation half of a wide glyph.
        cols, _ = self._calc_cols_rows()
        visible_y = self._abs_y_to_visible_y(y)
        line = self._line_from_buffer(visible_y)
        if self._is_wide_continuation_cell(line, x):
            return max(0, x - 1), y
        if is_end and self._is_wide_leading_cell(line, x, cols):
            x = min(cols - 1, x + 1)
        if is_end:
            limit = self._line_selection_limit(visible_y)
            if limit is not None and 0 <= visible_y < self._calc_cols_rows()[1]:
                x = min(x, limit)
        return x, y

    def _is_word_char(self, ch: str) -> bool:
        if not ch:
            return False
        if ch in self._word_select_chars:
            return True
        # Treat CJK letters/numbers as word content. Punctuation and path
        # separators such as "/" remain boundaries, so double-clicking
        # "/mnt/sdb test" on "sdb" selects only "sdb".
        return any(c.isalnum() for c in ch)

    def _select_word_at(self, cell: tuple[int, int]) -> bool:
        x, y = cell
        cols = int(getattr(self.screen, "columns", self._calc_cols_rows()[0]) or 0)
        if cols <= 0:
            return False

        line = self._line_from_buffer(y)
        if line is None:
            return False
        if self._is_wide_continuation_cell(line, x):
            x = max(0, x - 1)

        ch = self._cell_data(line, x)
        if not self._is_word_char(ch):
            self._sel_anchor = None
            self._sel_head = None
            return False

        start = x
        while start > 0:
            prev = start - 1
            if self._is_wide_continuation_cell(line, prev):
                prev = max(0, prev - 1)
            if not self._is_word_char(self._cell_data(line, prev)):
                break
            start = prev

        end = x
        while end + 1 < cols:
            nxt = end + self._cell_span(line, end, cols)
            if nxt >= cols or not self._is_word_char(self._cell_data(line, nxt)):
                break
            end = nxt

        abs_y = self._visible_y_to_abs_y(y)
        self._sel_anchor = (start, abs_y)
        self._sel_head = self._expand_selection_endpoint(end, abs_y, is_end=True)
        return True

    def _normalized_selection(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if not self._sel_anchor or not self._sel_head:
            return None
        (ax, ay) = self._sel_anchor
        (hx, hy) = self._sel_head
        if (hy, hx) < (ay, ax):
            (sx, sy), (ex, ey) = (hx, hy), (ax, ay)
        else:
            (sx, sy), (ex, ey) = (ax, ay), (hx, hy)
        return self._expand_selection_endpoint(sx, sy, is_end=False), self._expand_selection_endpoint(ex, ey, is_end=True)

    def _cell_in_selection(self, x: int, y: int, sel: tuple[tuple[int, int], tuple[int, int]]) -> bool:
        (sx, sy), (ex, ey) = sel
        abs_y = self._visible_y_to_abs_y(y)
        line_limit = self._line_selection_limit(y)
        if sy == ey:
            end = min(ex, line_limit) if line_limit is not None and abs_y == sy else ex
            return abs_y == sy and sx <= x <= end
        if abs_y == sy:
            end = line_limit if line_limit is not None else self._calc_cols_rows()[0] - 1
            return sx <= x <= end
        if abs_y == ey:
            end = min(ex, line_limit) if line_limit is not None else ex
            return x <= end
        if sy < abs_y < ey:
            return x <= line_limit if line_limit is not None else True
        return False

    def _selected_text(self) -> str:
        sel = self._normalized_selection()
        if not sel:
            return ""
        (sx, sy), (ex, ey) = sel
        cols, _ = self._calc_cols_rows()

        out: list[str] = []
        for abs_y in range(sy, ey + 1):
            visible_y = self._abs_y_to_visible_y(abs_y)
            line = self._line_from_buffer(visible_y)
            start = sx if abs_y == sy else 0
            end = ex if abs_y == ey else cols - 1
            chars: list[str] = []
            x = start
            while x <= end:
                if self._is_wide_continuation_cell(line, x):
                    x += 1
                    continue
                ch = self._cell_data(line, x)
                if ch:
                    chars.append(ch)
                x += self._cell_span(line, x, cols)
            out.append("".join(chars).rstrip())
        return "\n".join(out).rstrip()

    def copy(self) -> None:  # noqa: A003 (Qt naming)
        txt = self._selected_text()
        if not txt:
            return
        try:
            QGuiApplication.clipboard().setText(txt)
        except Exception:
            pass

    def contextMenuEvent(self, event):  # type: ignore[override]
        menu = ShortcutMenu(self)

        has_sel = bool(self._selected_text())
        act_copy = menu.addAction(t("context.copy"))
        add_menu_key(menu, act_copy, Qt.Key_C)
        act_copy.setEnabled(has_sel)
        act_copy.triggered.connect(self.copy)
        act_paste = menu.addAction(t("context.paste"))
        add_menu_key(menu, act_paste, Qt.Key_V)
        act_paste.triggered.connect(self._paste_from_clipboard)
        menu.addSeparator()
        act_select_all = menu.addAction(t("context.select_all"))
        add_menu_key(menu, act_select_all, Qt.Key_A)
        act_select_all.triggered.connect(self._select_all_visible)
        act_clear = menu.addAction(t("context.clear"))
        add_menu_key(menu, act_clear, Qt.Key_X)
        act_clear.triggered.connect(self._clear_terminal_view)
        menu.addSeparator()
        act_follow = menu.addAction(t("context.follow_output"))
        add_menu_key(menu, act_follow, Qt.Key_F)
        act_follow.triggered.connect(self._follow_output)

        exec_menu(menu, event.globalPos())

    def _select_all_visible(self) -> None:
        # Select everything visible; use our selection coords for consistent highlight.
        cols, rows = self._calc_cols_rows()
        self._sel_anchor = (0, self._visible_y_to_abs_y(0))
        self._sel_head = (max(0, cols - 1), self._visible_y_to_abs_y(max(0, rows - 1)))
        self._mark_dirty()

    def _clear_terminal_view(self) -> None:
        # Clear locally and also clear remote view (Ctrl+L) for typical shells.
        try:
            cols, rows = self._calc_cols_rows()
            self._reset_emulator_state(cols, rows)
            self._raw_buffer = ""
            self._raw_buffer_has_tui_control = False
        except Exception:
            pass
        self.input_received.emit(b"\x0c")
        self._mark_dirty()

    def _follow_output(self) -> None:
        # Return to bottom (new output follows)
        if not self._in_alt_screen and self._scroll_lines > 0:
            self._scroll_view(-self._scroll_lines)
        self._scroll_lines = 0
        try:
            if hasattr(self.screen, "history") and hasattr(self.screen.history, "position"):
                self.screen.history.position = self.screen.history.size  # type: ignore[attr-defined]
        except Exception:
            pass
        self._mark_dirty()

    def _scroll_to_history_start(self) -> None:
        """Jump to the oldest locally available scrollback line."""
        if self._in_alt_screen:
            return
        self._scroll_view(self._max_scroll_lines())

    def _prepare_for_terminal_input(self) -> None:
        """Return a scrolled main-screen viewport to the newest output."""
        if not self._in_alt_screen and self._scroll_lines > 0:
            self._follow_output()

    # -------------------------
    # Paste + keys
    # -------------------------
    def keyPressEvent(self, event):  # type: ignore[override]
        key = event.key()
        mods = event.modifiers()
        text = event.text()

        if (mods & Qt.ShiftModifier) and not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            if key == Qt.Key_Delete:
                self.copy()
                event.accept()
                return
            if key == Qt.Key_Insert:
                self._paste_from_clipboard()
                event.accept()
                return

        # Terminal-local scrollback shortcuts. Restrict these to the main
        # screen so alternate-screen TUIs keep receiving Home/End normally.
        if (
            (mods & Qt.ControlModifier)
            and (mods & Qt.ShiftModifier)
            and not (mods & (Qt.AltModifier | Qt.MetaModifier))
            and not self._in_alt_screen
        ):
            if key == Qt.Key_Home:
                self._scroll_to_history_start()
                event.accept()
                return
            if key == Qt.Key_End:
                self._follow_output()
                event.accept()
                return
        # Ctrl+Shift+C / Ctrl+Shift+V
        if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            if key == Qt.Key_C:
                self.copy()
                event.accept()
                return
            if key == Qt.Key_V:
                self._paste_from_clipboard()
                event.accept()
                return

        if key not in (
            Qt.Key_Shift,
            Qt.Key_Control,
            Qt.Key_Alt,
            Qt.Key_Meta,
            Qt.Key_AltGr,
            Qt.Key_CapsLock,
            Qt.Key_NumLock,
            Qt.Key_ScrollLock,
        ):
            self._prepare_for_terminal_input()

        # Ctrl+L
        if (mods & Qt.ControlModifier) and key == Qt.Key_L:
            self.input_received.emit(b"\x0c")
            event.accept()
            return

        # ESC key (critical for vim mode switching)
        if key == Qt.Key_Escape:
            self.input_received.emit(b"\x1b")
            event.accept()
            return

        # Map navigation/function keys to ANSI.
        #
        # NOTE: Even if apps toggle DECCKM (ESC[?1h/l), in practice many vim + terminfo
        # setups on xterm-256color expect CSI cursor sequences. Using SS3 (ESC O A/B/C/D)
        # can make arrow keys appear “dead”. However, some TUIs (like vim) explicitly
        # enable DECCKM and expect ESC O ... sequences.
        if self._app_cursor_keys:
            up, down, right, left = b"\x1bOA", b"\x1bOB", b"\x1bOC", b"\x1bOD"
        else:
            up, down, right, left = b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D"

        home = b"\x1b[H"
        end = b"\x1b[F"

        # Standard readline/zsh/fish Emacs-style word navigation/editing.
        if (
            (mods & Qt.AltModifier)
            and not (mods & (Qt.ControlModifier | Qt.MetaModifier))
            and key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Delete)
        ):
            sequence = {
                Qt.Key_Left: b"\x1bb",
                Qt.Key_Right: b"\x1bf",
                Qt.Key_Delete: b"\x1bd",
            }[key]
            self.input_received.emit(sequence)
            event.accept()
            return

        key_map = {
            Qt.Key_Up: up,
            Qt.Key_Down: down,
            Qt.Key_Right: right,
            Qt.Key_Left: left,
            Qt.Key_Home: home,
            Qt.Key_End: end,
            Qt.Key_Insert: b"\x1b[2~",
            Qt.Key_Delete: b"\x1b[3~",
            Qt.Key_PageUp: b"\x1b[5~",
            Qt.Key_PageDown: b"\x1b[6~",
            Qt.Key_F1: b"\x1bOP",
            Qt.Key_F2: b"\x1bOQ",
            Qt.Key_F3: b"\x1bOR",
            Qt.Key_F4: b"\x1bOS",
            Qt.Key_F5: b"\x1b[15~",
            Qt.Key_F6: b"\x1b[17~",
            Qt.Key_F7: b"\x1b[18~",
            Qt.Key_F8: b"\x1b[19~",
            Qt.Key_F9: b"\x1b[20~",
            Qt.Key_F10: b"\x1b[21~",
            Qt.Key_F11: b"\x1b[23~",
            Qt.Key_F12: b"\x1b[24~",
        }
        if key in key_map:
            self.input_received.emit(key_map[key])
            event.accept()
            return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._reconnect_enabled:
                self.reconnect_requested.emit()
                event.accept()
                return
            self._commit_command_start(check_continuation=True)
            self.input_received.emit(b"\r")
            event.accept()
            return
        if key == Qt.Key_Backspace:
            if (mods & Qt.AltModifier) and not (mods & (Qt.ControlModifier | Qt.MetaModifier)):
                self._delete_pending_command_word()
                self.input_received.emit(b"\x1b\x7f")
                event.accept()
                return
            self._delete_pending_command_char()
            self.input_received.emit(b"\x7f")
            event.accept()
            return
        if key == Qt.Key_Tab:
            self._record_pending_command_start()
            self.input_received.emit(b"\t")
            event.accept()
            return
        if key == Qt.Key_Backtab:
            self.input_received.emit(b"\x1b[Z")
            event.accept()
            return

        # Handle Ctrl+<Key> combinations for the remote terminal
        if mods & Qt.ControlModifier:
            # For Ctrl+A to Ctrl+Z, and some others
            if Qt.Key_A <= key <= Qt.Key_Z:
                if key in (Qt.Key_C, Qt.Key_U):
                    self._pending_command_text = ''
                    self._pending_command_history_forced = False
                ctrl_char = bytes([key - Qt.Key_A + 1])
                self.input_received.emit(ctrl_char)
                event.accept()
                return
            # Common symbols like Ctrl+[, Ctrl+\, Ctrl+], Ctrl+^, Ctrl+_
            symbol_map = {
                Qt.Key_BracketLeft: b"\x1b",
                Qt.Key_Backslash: b"\x1c",
                Qt.Key_BracketRight: b"\x1d",
                Qt.Key_AsciiCircum: b"\x1e",
                Qt.Key_Underscore: b"\x1f",
            }
            if key in symbol_map:
                self.input_received.emit(symbol_map[key])
                event.accept()
                return

        # Handle regular text input
        if text:
            self._append_pending_command_text(text)
            self.input_received.emit(text.encode("utf-8"))
            event.accept()
            return

        # Handle special key combinations and ensure all input reaches vim/other TUI apps
        # This fallback ensures that even if Qt doesn't provide text for a key,
        # we still send the appropriate character to the remote terminal
        if not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            # Handle all printable characters that might not have text in event.text()
            # This is critical for vim and other TUI applications

            # Letters A-Z (handle case sensitivity)
            if Qt.Key_A <= key <= Qt.Key_Z:
                base_char = ord('a') + (key - Qt.Key_A)
                char = chr(base_char).upper() if (mods & Qt.ShiftModifier) else chr(base_char)
                self._append_pending_command_text(char)
                self.input_received.emit(char.encode("utf-8"))
                event.accept()
                return

            # Numbers 0-9 (handle shifted symbols)
            if Qt.Key_0 <= key <= Qt.Key_9:
                base_char = ord('0') + (key - Qt.Key_0)
                if mods & Qt.ShiftModifier:
                    shifted = ")!@#$%^&*("
                    char = shifted[key - Qt.Key_0]
                else:
                    char = chr(base_char)
                self._append_pending_command_text(char)
                self.input_received.emit(char.encode("utf-8"))
                event.accept()
                return

            # Space
            if key == Qt.Key_Space:
                self._append_pending_command_text(" ")
                self.input_received.emit(b" ")
                event.accept()
                return

            # All other printable keys - comprehensive mapping
            key_to_char = {
                # Punctuation and symbols
                Qt.Key_Exclam: '!',
                Qt.Key_At: '@',
                Qt.Key_NumberSign: '#',
                Qt.Key_Dollar: '$',
                Qt.Key_Percent: '%',
                Qt.Key_AsciiCircum: '^',
                Qt.Key_Ampersand: '&',
                Qt.Key_Asterisk: '*',
                Qt.Key_ParenLeft: '(',
                Qt.Key_ParenRight: ')',
                Qt.Key_Minus: '-',
                Qt.Key_Underscore: '_',
                Qt.Key_Equal: '=',
                Qt.Key_Plus: '+',
                Qt.Key_BracketLeft: '[',
                Qt.Key_BracketRight: ']',
                Qt.Key_BraceLeft: '{',
                Qt.Key_BraceRight: '}',
                Qt.Key_Backslash: '\\',
                Qt.Key_Bar: '|',
                Qt.Key_Semicolon: ';',
                Qt.Key_Colon: ':',
                Qt.Key_Apostrophe: "'",
                Qt.Key_QuoteDbl: '"',
                Qt.Key_Comma: ',',
                Qt.Key_Less: '<',
                Qt.Key_Period: '.',
                Qt.Key_Greater: '>',
                Qt.Key_Slash: '/',
                Qt.Key_Question: '?',
                Qt.Key_QuoteLeft: '`',
            }

            if key in key_to_char:
                char = key_to_char[key]
                self._append_pending_command_text(char)
                self.input_received.emit(char.encode("utf-8"))
                event.accept()
                return

        event.ignore()

    def _paste_from_clipboard(self) -> None:
        try:
            text = QGuiApplication.clipboard().text() or ""
        except Exception:
            text = ""
        if text:
            self._send_paste_text(text)

    def _send_paste_text(self, text: str) -> None:
        confirm_multiline = bool(get_setting("terminal_paste_confirm_multiline", True))
        bracketed = bool(get_setting("terminal_bracketed_paste", True)) and self._bracketed_paste_mode

        if confirm_multiline and ("\n" in text or "\r" in text):
            edited = prompt_multiline_confirm(
                self,
                t("dialog.paste_confirm.title"),
                t("dialog.paste_confirm.text"),
                text,
            )
            if edited is None:
                return
            text = edited

        self._prepare_for_terminal_input()
        history_text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = history_text.replace("\n", "\r")
        payload = text.encode("utf-8", errors="replace")
        if bracketed:
            payload = b"\x1b[200~" + payload + b"\x1b[201~"
        self._append_pending_command_text(history_text)
        if b"\r" in payload:
            self._commit_command_start()
        self.input_received.emit(payload)
