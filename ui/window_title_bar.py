#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QEvent, QPoint
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QMenuBar, QApplication, QSizePolicy, QToolButton, QWidget

from i18n import tr


class WindowTitleBar(QWidget):
    """Custom title bar with embedded menu bar and window controls.

    标题区拖动说明（Windows + FramelessWindowHint）：
    - 现象：点击标题文字后，最小化/最大化/关闭按钮的 :hover 高亮失效，需先点其它控件才恢复。
    - 根因：QWindow.startSystemMove() 在 MouseButtonPress 时调用后，系统接管鼠标，
      Qt 收不到完整的 press/release 序列，子控件 hover 状态异常。
    - 生效做法：标题区改用 window.move() 手动拖动，不调用 startSystemMove()。
    - 无效做法（均已验证）：
      1) startSystemMove 后 postEvent/sendEvent 合成 MouseButtonRelease（PyQt Demo 写法）；
      2) 手动补发 HoverEnter/HoverLeave 或同步 WA_UnderMouse；
      3) ReleaseCapture / mouseGrabber().releaseMouse()；
      4) 延迟到 MouseMove 再 startSystemMove（hover 可能恢复，但 Windows 下拖动并不可靠）。
    - 代价：标题栏拖动无 Windows 原生贴边吸附（Aero Snap）。
    """

    DEFAULT_HEIGHT = 32
    BOTTOM_BORDER_PX = 1

    def __init__(self, window: QMainWindow, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._window = window
        self._height = self.DEFAULT_HEIGHT
        self._border_width = 0
        self._drag_press_global: Optional[QPoint] = None
        self._drag_window_origin: Optional[QPoint] = None
        self.setObjectName('WindowTitleBar')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self._height)

        layout = QHBoxLayout(self)
        self._root_layout = layout
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(0)

        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName('WindowMenuBar')
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(self.menu_bar, 0, Qt.AlignVCenter)

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
        content_height = max(16, self._height - top_inset - bottom_inset)
        btn_size = content_height
        menu_height = content_height
        self._root_layout.setContentsMargins(4, top_inset, 0, bottom_inset)
        self.menu_bar.setFixedHeight(menu_height)
        self._controls_layout.setContentsMargins(0, 0, right_inset, 0)
        self._controls_layout.setAlignment(Qt.AlignVCenter)
        self._controls_box.setFixedHeight(content_height)
        for btn in self._window_buttons:
            btn_width = max(1, int(round(btn_size * 1.25)))
            btn.setFixedSize(btn_width, btn_size)
        self._root_layout.setAlignment(self.menu_bar, Qt.AlignVCenter)
        self._root_layout.setAlignment(self._controls_box, Qt.AlignVCenter)

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

    def _clear_title_drag(self) -> None:
        self._drag_press_global = None
        self._drag_window_origin = None

    def _move_window_by_title_drag(self, event: QMouseEvent) -> None:
        # 见类 docstring：不用 startSystemMove，避免窗口按钮 hover 失效。
        if self._drag_press_global is None or self._drag_window_origin is None:
            return
        if self._window.isMaximized():
            return
        if (event.globalPos() - self._drag_press_global).manhattanLength() < QApplication.startDragDistance():
            return
        self._window.move(self._drag_window_origin + event.globalPos() - self._drag_press_global)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._title_label and isinstance(event, QMouseEvent):
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._clear_title_drag()
                self._toggle_maximize()
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # 仅记录按下位置；return False 让 Qt 正常完成 press/release，勿在此调用 startSystemMove。
                self._drag_press_global = event.globalPos()
                self._drag_window_origin = self._window.frameGeometry().topLeft()
                return False
            if event.type() == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
                self._move_window_by_title_drag(event)
                return False
            if event.type() == QEvent.MouseButtonRelease:
                self._clear_title_drag()
                return False
        if watched is self._window and event.type() == QEvent.WindowStateChange:
            if self._window.isMaximized():
                icon = self.style().StandardPixmap.SP_TitleBarNormalButton
            else:
                icon = self.style().StandardPixmap.SP_TitleBarMaxButton
            self._max_btn.setIcon(self.style().standardIcon(icon))
        return super().eventFilter(watched, event)
