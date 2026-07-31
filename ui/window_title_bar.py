#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QMenuBar, QToolButton, QWidget

from i18n import tr


class WindowTitleBar(QWidget):
    """Custom title bar with embedded menu bar and window controls."""

    DEFAULT_HEIGHT = 32
    BOTTOM_BORDER_PX = 1

    def __init__(self, window: QMainWindow, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._window = window
        self._height = self.DEFAULT_HEIGHT
        self._border_width = 0
        self.setObjectName('WindowTitleBar')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self._height)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(0)

        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName('WindowMenuBar')
        self.menu_bar.setNativeMenuBar(False)
        layout.addWidget(self.menu_bar, 0)

        self._title_label = QLabel(tr('main.window_title'), self)
        self._title_label.setObjectName('WindowTitleLabel')
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.installEventFilter(self)
        layout.addWidget(self._title_label, 1)

        self._controls_box = QWidget(self)
        self._controls_box.setObjectName('WindowTitleControls')
        self._controls_layout = QHBoxLayout(self._controls_box)
        self._controls_layout.setContentsMargins(0, 0, 0, 0)
        self._controls_layout.setSpacing(0)

        self._min_btn = self._make_window_button('SP_TitleBarMinButton')
        self._max_btn = self._make_window_button('SP_TitleBarMaxButton')
        self._close_btn = self._make_window_button('SP_TitleBarCloseButton')
        self._close_btn.setObjectName('WindowCloseButton')
        self._window_buttons = (self._min_btn, self._max_btn, self._close_btn)
        self._min_btn.clicked.connect(self._window.showMinimized)
        self._max_btn.clicked.connect(self._toggle_maximize)
        self._close_btn.clicked.connect(self._window.close)
        for btn in self._window_buttons:
            self._controls_layout.addWidget(btn, 0)
        layout.addWidget(self._controls_box, 0)

        self._window.installEventFilter(self)

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def apply_layout(self, height: int, *, border_width: int = 0) -> None:
        self._height = max(24, min(48, int(height)))
        self._border_width = max(0, min(8, int(border_width)))
        self.setFixedHeight(self._height)

        top_inset = self._border_width
        bottom_inset = self.BOTTOM_BORDER_PX
        right_inset = self._border_width
        btn_size = max(16, self._height - top_inset - bottom_inset)
        self._controls_layout.setContentsMargins(0, top_inset, right_inset, bottom_inset)
        self._controls_box.setFixedHeight(self._height)
        for btn in self._window_buttons:
            btn.setFixedSize(btn_size, btn_size)

    def apply_height(self, height: int) -> None:
        self.apply_layout(height, border_width=self._border_width)

    def _make_window_button(self, icon_name: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName('WindowTitleButton')
        btn.setAutoRaise(True)
        btn.setFixedSize(16, 16)
        icon = getattr(self.style().StandardPixmap, icon_name)
        btn.setIcon(self.style().standardIcon(icon))
        return btn

    def _toggle_maximize(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _start_system_move(self) -> None:
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._title_label and isinstance(event, QMouseEvent):
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._toggle_maximize()
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._start_system_move()
                return True
        if watched is self._window and event.type() == QEvent.WindowStateChange:
            if self._window.isMaximized():
                icon = self.style().StandardPixmap.SP_TitleBarNormalButton
            else:
                icon = self.style().StandardPixmap.SP_TitleBarMaxButton
            self._max_btn.setIcon(self.style().standardIcon(icon))
        return super().eventFilter(watched, event)
