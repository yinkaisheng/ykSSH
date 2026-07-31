#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import QModelIndex, QPoint, QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen, QPolygon
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFontComboBox,
    QListView,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from typing import Callable, Optional, Type

from PyQt5.QtGui import QContextMenuEvent, QKeySequence
from PyQt5.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit

from i18n import tr
from ui.theme import check_mark_color, popup_list_font

ARROW_GLYPH_BASE_PX = 8
ARROW_GLYPH_HEIGHT_PX = 6
COMBO_DROPDOWN_WIDTH_PX = 26


def _arrow_color(widget) -> QColor:
    return widget.palette().color(QPalette.WindowText)


def _paint_check_mark(painter: QPainter, rect: QRect) -> None:
    side = min(rect.width(), rect.height())
    margin = side * 0.2
    x0 = rect.x() + margin
    y0 = rect.y() + side * 0.54
    x1 = rect.x() + side * 0.4
    y1 = rect.y() + side * 0.74
    x2 = rect.x() + side - margin
    y2 = rect.y() + side * 0.3

    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(check_mark_color())
    pen.setWidthF(max(1.6, side * 0.12))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _paint_triangle(
    painter: QPainter,
    cx: float,
    cy: float,
    base_w: int,
    height: int,
    *,
    up: bool,
) -> None:
    if base_w < 4 or height < 4:
        return
    half_base = base_w / 2.0
    half_height = height / 2.0
    if up:
        y_top = cy - half_height
        y_bottom = cy + half_height
        points = [
            QPoint(int(round(cx)), int(round(y_top))),
            QPoint(int(round(cx - half_base)), int(round(y_bottom))),
            QPoint(int(round(cx + half_base)), int(round(y_bottom))),
        ]
    else:
        y_top = cy - half_height
        y_bottom = cy + half_height
        points = [
            QPoint(int(round(cx - half_base)), int(round(y_top))),
            QPoint(int(round(cx + half_base)), int(round(y_top))),
            QPoint(int(round(cx)), int(round(y_bottom))),
        ]
    painter.drawPolygon(QPolygon(points))


def _paint_combo_dropdown_arrow(combo: QComboBox) -> None:
    opt = QStyleOptionComboBox()
    combo.initStyleOption(opt)
    style = combo.style()
    arrow_rect = style.subControlRect(
        QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxArrow, combo
    )
    if arrow_rect.isNull() or arrow_rect.width() < 2:
        arrow_rect = QRect(
            combo.width() - COMBO_DROPDOWN_WIDTH_PX,
            0,
            COMBO_DROPDOWN_WIDTH_PX,
            combo.height(),
        )
    if arrow_rect.isNull() or arrow_rect.width() < 2:
        return

    center = QRectF(arrow_rect).center()
    painter = QPainter(combo)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_arrow_color(combo))
        _paint_triangle(
            painter,
            center.x(),
            center.y(),
            ARROW_GLYPH_BASE_PX,
            ARROW_GLYPH_HEIGHT_PX,
            up=False,
        )
    finally:
        painter.end()


class _AppFontItemDelegate(QStyledItemDelegate):
    """Force combo popup rows to use the same pixel size as global QSS."""

    def initStyleOption(self, option: QStyleOptionViewItem, index: 'QModelIndex') -> None:
        super().initStyleOption(option, index)
        option.font = popup_list_font()


class ArrowComboBox(QComboBox):
    """Paint a dropdown glyph after the style pass; Fusion+QSS often hides native arrows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_popup_view()

    def _setup_popup_view(self) -> None:
        view = QListView(self)
        view.setObjectName('comboPopupListView')
        view.setItemDelegate(_AppFontItemDelegate(view))
        self.setView(view)

    def _sync_popup_font(self) -> None:
        font = popup_list_font()
        view = self.view()
        if view is not None:
            view.setFont(font)

    def showPopup(self) -> None:
        self._sync_popup_font()
        super().showPopup()
        view = self.view()
        if view is not None:
            popup = view.window()
            if popup is not view:
                popup.setFont(popup_list_font())

    def paintEvent(self, event: 'QPaintEvent') -> None:
        super().paintEvent(event)
        _paint_combo_dropdown_arrow(self)


class ArrowFontComboBox(QFontComboBox):
    """Monospace font picker with a painted dropdown glyph."""

    def paintEvent(self, event: 'QPaintEvent') -> None:
        super().paintEvent(event)
        _paint_combo_dropdown_arrow(self)


class GlyphSpinBox(QSpinBox):
    """Paint up/down glyphs after the style pass; Fusion+QSS often hides native arrows."""

    def paintEvent(self, event: 'QPaintEvent') -> None:
        super().paintEvent(event)
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        style = self.style()
        up_rect = style.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxUp, self)
        down_rect = style.subControlRect(QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxDown, self)
        if up_rect.isNull() or down_rect.isNull():
            return

        up_center = QRectF(up_rect).center()
        down_center = QRectF(down_rect).center()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_arrow_color(self))
            _paint_triangle(
                painter,
                up_center.x(),
                up_center.y(),
                ARROW_GLYPH_BASE_PX,
                ARROW_GLYPH_HEIGHT_PX,
                up=True,
            )
            _paint_triangle(
                painter,
                down_center.x(),
                down_center.y(),
                ARROW_GLYPH_BASE_PX,
                ARROW_GLYPH_HEIGHT_PX,
                up=False,
            )
        finally:
            painter.end()


class AccentCheckBox(QCheckBox):
    """Checkbox with accent indicator and a painted check mark when selected."""

    def __init__(self, text: str = '', parent=None):
        super().__init__(text, parent)
        self.setObjectName('sslVerifyCheck')
        self.setFocusPolicy(Qt.NoFocus)
        self.stateChanged.connect(lambda _state: self.update())

    def paintEvent(self, event: 'QPaintEvent') -> None:
        super().paintEvent(event)
        if not self.isChecked():
            return

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        indicator = self.style().subElementRect(
            QStyle.SE_CheckBoxIndicator, opt, self
        )
        if indicator.isNull() or indicator.width() < 2:
            return

        painter = QPainter(self)
        try:
            _paint_check_mark(painter, indicator)
        finally:
            painter.end()


_EDIT_MENU_LABELS = {
    'Undo': 'edit.undo',
    'Redo': 'edit.redo',
    'Cut': 'edit.cut',
    'Copy': 'edit.copy',
    'Paste': 'edit.paste',
    'Delete': 'edit.delete',
    'Select All': 'edit.select_all',
    # Qt qt_zh_CN.qm defaults (used when matching by visible label).
    '撤消': 'edit.undo',
    '撤销': 'edit.undo',
    '恢复': 'edit.redo',
    '重做': 'edit.redo',
    '剪切': 'edit.cut',
    '复制': 'edit.copy',
    '粘贴': 'edit.paste',
    '删除': 'edit.delete',
    '全选': 'edit.select_all',
    '选择全部': 'edit.select_all',
}

_EDIT_MENU_SHORTCUTS = {
    'Ctrl+Z': 'edit.undo',
    'Ctrl+Y': 'edit.redo',
    'Ctrl+X': 'edit.cut',
    'Ctrl+C': 'edit.copy',
    'Ctrl+V': 'edit.paste',
    'Ctrl+A': 'edit.select_all',
}


def _standard_edit_action_label(text: str) -> str:
    """Strip mnemonic (&), shortcut tab suffix, and (U) style hints."""
    if not text:
        return ''
    label = text.split('\t', 1)[0].replace('&', '')
    if '(' in label:
        label = label.split('(', 1)[0]
    return label.strip()


def _standard_edit_action_shortcut(text: str, action) -> str:
    if '\t' in text:
        return text.split('\t', 1)[1].strip()
    shortcut = action.shortcut()
    return shortcut.toString() if not shortcut.isEmpty() else ''


def _edit_menu_i18n_key(action) -> Optional[str]:
    text = action.text()
    label = _standard_edit_action_label(text)
    key = _EDIT_MENU_LABELS.get(label)
    if key is not None:
        return key
    shortcut_text = _standard_edit_action_shortcut(text, action)
    if shortcut_text:
        key = _EDIT_MENU_SHORTCUTS.get(shortcut_text)
        if key is not None:
            return key
    shortcut = action.shortcut()
    if shortcut.isEmpty():
        return None
    for std_key, i18n_key in (
        (QKeySequence.Undo, 'edit.undo'),
        (QKeySequence.Redo, 'edit.redo'),
        (QKeySequence.Cut, 'edit.cut'),
        (QKeySequence.Copy, 'edit.copy'),
        (QKeySequence.Paste, 'edit.paste'),
        (QKeySequence.Delete, 'edit.delete'),
        (QKeySequence.SelectAll, 'edit.select_all'),
    ):
        if shortcut.matches(QKeySequence(std_key)):
            return i18n_key
    return None


def _translate_standard_edit_menu(menu) -> None:
    for action in menu.actions():
        if action.isSeparator() or not action.text():
            continue
        key = _edit_menu_i18n_key(action)
        if key is None:
            continue
        text = action.text()
        shortcut_suffix = _standard_edit_action_shortcut(text, action)
        translated = tr(key)
        action.setText(f'{translated}\t{shortcut_suffix}' if shortcut_suffix else translated)


def _wrap_context_menu_event(original: Callable) -> Callable:
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self.createStandardContextMenu()
        if menu is not None:
            _translate_standard_edit_menu(menu)
            menu.exec_(event.globalPos())
            menu.deleteLater()
        else:
            original(self, event)

    return contextMenuEvent


_EDIT_CONTEXT_MENU_CLASSES: tuple[Type, ...] = (QLineEdit, QPlainTextEdit, QTextEdit)
_INSTALLED = False


def install_edit_context_menu_translations(app=None) -> None:
    """Patch standard text widgets so their built-in context menus use tr()."""
    del app  # kept for call-site compatibility
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    for widget_cls in _EDIT_CONTEXT_MENU_CLASSES:
        widget_cls.contextMenuEvent = _wrap_context_menu_event(widget_cls.contextMenuEvent)
