# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import QEvent, QRect, QRegularExpression, Qt
from PyQt5.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextOption,
)
from PyQt5.QtWidgets import QPlainTextEdit, QWidget

from ui.theme import active_theme_palette


# 空白标记相对于正文的透明度；越小越淡，取值范围为 0～1。
WHITESPACE_MARKER_OPACITY = 0.35
_WHITESPACE_PATTERN = QRegularExpression(r'[ \t]+')


class _WhitespaceHighlighter(QSyntaxHighlighter):
    """仅淡化空格与 Tab 的显示格式，不修改文档内容。"""

    def highlightBlock(self, text: str) -> None:
        color = QColor(active_theme_palette().text_primary)
        color.setAlphaF(WHITESPACE_MARKER_OPACITY)
        text_format = QTextCharFormat()
        text_format.setForeground(color)
        matches = _WHITESPACE_PATTERN.globalMatch(text)
        while matches.hasNext():
            match = matches.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), text_format)


class _LineNumberArea(QWidget):
    """随编辑器滚动的行号栏。"""

    def __init__(self, editor: LineNumberEdit) -> None:
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class LineNumberEdit(QPlainTextEdit):
    """带行号的纯文本输入框；每个实际换行对应一行，不自动折行。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('bodyTextEdit')
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        option = self.document().defaultTextOption()
        option.setFlags(option.flags() | QTextOption.ShowTabsAndSpaces)
        self.document().setDefaultTextOption(option)
        self.line_number_area = _LineNumberArea(self)
        self._whitespace_highlighter = _WhitespaceHighlighter(self.document())
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_numbers)
        self.cursorPositionChanged.connect(self.line_number_area.update)
        self._update_line_number_width()
        self.ensurePolished()

    def line_number_width(self) -> int:
        font = QFont(self.font())
        font.setBold(True)
        metrics = QFontMetrics(font)
        digit_width = max(metrics.horizontalAdvance(digit) for digit in '0123456789')
        return 12 + len(str(self.blockCount())) * digit_width

    def _update_line_number_width(self) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)
        self._position_line_numbers()

    def _position_line_numbers(self) -> None:
        viewport = self.viewport().geometry()
        width = self.line_number_width()
        self.line_number_area.setGeometry(
            viewport.left() - width, viewport.top(), width, viewport.height(),
        )

    def _update_line_numbers(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_line_numbers()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if hasattr(self, 'line_number_area') and event.type() in (
            QEvent.FontChange, QEvent.StyleChange, QEvent.PaletteChange,
        ):
            self._update_line_number_width()
            self.line_number_area.update()
            if hasattr(self, '_whitespace_highlighter'):
                self._whitespace_highlighter.rehighlight()

    def paint_line_numbers(self, event) -> None:
        palette = active_theme_palette()
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(palette.background_secondary))
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        current = self.textCursor().blockNumber()
        font = QFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            height = self.blockBoundingRect(block).height()
            if block.isVisible() and top + height >= event.rect().top():
                selected = block.blockNumber() == current
                font.setBold(selected)
                painter.setFont(font)
                painter.setPen(QColor(palette.text_primary if selected else palette.text_secondary))
                painter.drawText(
                    QRect(0, round(top), self.line_number_area.width() - 6, self.fontMetrics().height()),
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(block.blockNumber() + 1),
                )
            top += height
            block = block.next()
