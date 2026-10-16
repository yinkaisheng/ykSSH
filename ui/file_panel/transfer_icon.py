# -*- coding: utf-8 -*-
"""以矢量路径绘制适配主题和高 DPI 的传输队列图标。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def transfer_queue_icon(kind: str, color: QColor, pixel_ratio: float) -> QIcon:
    ratio = max(1.0, pixel_ratio)
    pixmap = QPixmap(round(24 * ratio), round(20 * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath()
    path.moveTo(8, 3)
    path.lineTo(8, 12)
    if kind == 'upload':
        path.moveTo(4.5, 6.5)
        path.lineTo(8, 3)
        path.lineTo(11.5, 6.5)
    else:
        path.moveTo(4.5, 8.5)
        path.lineTo(8, 12)
        path.lineTo(11.5, 8.5)
    path.moveTo(2, 12)
    path.lineTo(2, 17)
    path.lineTo(14, 17)
    path.lineTo(14, 12)
    for y in (5, 9, 13):
        path.moveTo(18, y)
        path.lineTo(22, y)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)
