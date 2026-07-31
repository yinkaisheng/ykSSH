#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reusable tree widget for session/folder hierarchies with drag-drop."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QDrag
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from i18n import tr
from ui.theme import active_theme_palette

ITEM_TYPE_FOLDER = 'folder'
ITEM_TYPE_SESSION = 'session'

ROLE_TYPE = Qt.UserRole
ROLE_ITEM_ID = Qt.UserRole + 1


class FavoriteTreeWidget(QTreeWidget):
    """QTreeWidget with drag-drop, cut/paste, and empty-state hint."""

    itemMoved = pyqtSignal(QTreeWidgetItem)
    renameRequested = pyqtSignal(QTreeWidgetItem)
    deleteRequested = pyqtSignal(QTreeWidgetItem)

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._tree_id = uuid.uuid4().hex

        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

        self._sibling_dragging = False
        self._sibling_indicator_target: Optional[QTreeWidgetItem] = None
        self._sibling_above = True

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return

        indexes = self.selectedIndexes()
        if not indexes:
            idx = self.currentIndex()
            if idx.isValid():
                indexes = [idx]
        mime_data = None
        if indexes:
            mime_data = self.model().mimeData(indexes)

        rect = self.visualItemRect(item)
        if item.isExpanded():
            for i in range(item.childCount()):
                child_rect = self.visualItemRect(item.child(i))
                if child_rect.isValid():
                    rect = rect.united(child_rect)
        rect.adjust(-2, -2, 4, 4)

        pixmap = self.viewport().grab(rect)
        transparent = QPixmap(pixmap.size())
        transparent.fill(Qt.transparent)
        painter = QPainter(transparent)
        painter.setOpacity(0.5)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        drag = QDrag(self)
        drag.setPixmap(transparent)
        if mime_data is not None:
            drag.setMimeData(mime_data)
        drag.setHotSpot(QPoint(0, 8))
        drag.exec_(supportedActions)

    def dragEnterEvent(self, event):
        event.accept()

    @staticmethod
    def _is_folder(item: QTreeWidgetItem) -> bool:
        return item.data(0, ROLE_TYPE) == ITEM_TYPE_FOLDER

    @staticmethod
    def _is_descendant_of(item: QTreeWidgetItem, ancestor: QTreeWidgetItem) -> bool:
        p = item.parent()
        while p is not None:
            if p is ancestor:
                return True
            p = p.parent()
        return False

    def dragMoveEvent(self, event):
        target = self.itemAt(event.pos())

        if target is not None:
            rect = self.visualItemRect(target)
            if event.pos().x() < rect.left():
                cur = self.currentItem()
                if cur is not None and (target is cur or self._is_descendant_of(target, cur)):
                    self._clear_sibling_state()
                    event.ignore()
                    return

                mid_y = rect.top() + rect.height() // 2
                self._sibling_above = event.pos().y() < mid_y
                self._sibling_indicator_target = target
                self._sibling_dragging = True
                event.accept()
                self.viewport().update()
                return

        self._clear_sibling_state()

        if target is not None and not self._is_folder(target):
            if self.dropIndicatorPosition() == QAbstractItemView.OnItem:
                event.ignore()
                return

        super().dragMoveEvent(event)

    def dropEvent(self, event):
        item = self.currentItem()
        if item is None:
            self._clear_sibling_state()
            super().dropEvent(event)
            return

        if self._sibling_dragging and self._sibling_indicator_target is not None:
            target = self._sibling_indicator_target
            if target is not item:
                expanded_states = self._collect_expanded(item)
                QTimer.singleShot(0, lambda it=item, tg=target,
                                  ab=self._sibling_above, es=expanded_states:
                                  self._execute_sibling_move(it, tg, ab, es))
            self._clear_sibling_state()
            return

        target = self.itemAt(event.pos())
        if target is not None and not self._is_folder(target):
            if self.dropIndicatorPosition() == QAbstractItemView.OnItem:
                event.ignore()
                self._clear_sibling_state()
                return

        if target is not None and item.parent() is target:
            if self.dropIndicatorPosition() == QAbstractItemView.OnItem:
                event.ignore()
                self._clear_sibling_state()
                return

        expanded_states = self._collect_expanded(item)
        super().dropEvent(event)
        QTimer.singleShot(0, lambda it=item, es=expanded_states:
                          self._execute_internal_move_finish(it, es))

    def paintEvent(self, event):
        super().paintEvent(event)

        if self.topLevelItemCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(active_theme_palette().text_disabled))
            font = painter.font()
            font.setPointSize(font.pointSize() + 1)
            painter.setFont(font)
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, tr('sessions.empty_hint'))
            painter.end()

        if self._sibling_dragging and self._sibling_indicator_target is not None:
            painter = QPainter(self.viewport())
            accent = QColor(active_theme_palette().highlight)
            pen = QPen(accent, 3)
            painter.setPen(pen)

            rect = self.visualItemRect(self._sibling_indicator_target)
            y = rect.top() if self._sibling_above else rect.bottom()
            painter.drawLine(rect.left(), y, self.viewport().width(), y)
            painter.end()

    def dragLeaveEvent(self, event):
        self._clear_sibling_state()
        super().dragLeaveEvent(event)

    def _clear_sibling_state(self):
        self._sibling_dragging = False
        self._sibling_indicator_target = None
        self.viewport().update()

    @property
    def tree_id(self) -> str:
        return self._tree_id

    def get_item_path(self, item: QTreeWidgetItem) -> List[int]:
        path: List[int] = []
        node: Optional[QTreeWidgetItem] = item
        while node is not None:
            parent = node.parent()
            if parent:
                path.append(parent.indexOfChild(node))
            else:
                path.append(self.indexOfTopLevelItem(node))
            node = parent
        path.reverse()
        return path

    def get_item_by_path(self, path: List[int]) -> Optional[QTreeWidgetItem]:
        node: Optional[QTreeWidgetItem] = None
        for idx in path:
            if node is None:
                if 0 <= idx < self.topLevelItemCount():
                    node = self.topLevelItem(idx)
                else:
                    return None
            else:
                if 0 <= idx < node.childCount():
                    node = node.child(idx)
                else:
                    return None
        return node

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F2:
            item = self.currentItem()
            if item is not None:
                self.renameRequested.emit(item)
            event.accept()
            return
        if event.key() == Qt.Key_Delete:
            item = self.currentItem()
            if item is not None:
                self.deleteRequested.emit(item)
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _collect_expanded(item: QTreeWidgetItem) -> Dict[int, bool]:
        states = {id(item): item.isExpanded()}
        for i in range(item.childCount()):
            states.update(FavoriteTreeWidget._collect_expanded(item.child(i)))
        return states

    @staticmethod
    def _restore_expanded_map(item: QTreeWidgetItem, states: Dict[int, bool]):
        if id(item) in states:
            item.setExpanded(states[id(item)])
        for i in range(item.childCount()):
            FavoriteTreeWidget._restore_expanded_map(item.child(i), states)
        parent = item.parent()
        if parent is not None and FavoriteTreeWidget._is_folder(parent):
            parent.setExpanded(True)

    def _insert_as_sibling(self, item: QTreeWidgetItem,
                           target: QTreeWidgetItem, above: bool) -> QTreeWidgetItem:
        item_parent = item.parent()
        target_parent = target.parent()

        if target_parent:
            target_idx = target_parent.indexOfChild(target)
        else:
            target_idx = self.indexOfTopLevelItem(target)

        if item_parent:
            item_idx = item_parent.indexOfChild(item)
            moved = item_parent.takeChild(item_idx)
            if moved is None:
                moved = item
            if item_parent is target_parent and item_idx < target_idx:
                target_idx -= 1
        else:
            item_idx = self.indexOfTopLevelItem(item)
            moved = self.takeTopLevelItem(item_idx)
            if moved is None:
                moved = item
            if item_parent is target_parent or (target_parent is None and item_idx < target_idx):
                target_idx -= 1

        insert_idx = target_idx if above else target_idx + 1
        if target_parent:
            target_parent.insertChild(insert_idx, moved)
            target_parent.setExpanded(True)
        else:
            self.insertTopLevelItem(insert_idx, moved)

        return moved

    def _execute_sibling_move(self, item: QTreeWidgetItem,
                              target: QTreeWidgetItem, above: bool,
                              expanded_states: Dict[int, bool]):
        if target is item or self._is_descendant_of(target, item):
            self.setCurrentItem(item)
            return
        moved = self._insert_as_sibling(item, target, above)
        self._restore_expanded_map(moved, expanded_states)
        self.itemMoved.emit(moved)
        self.setCurrentItem(moved)

    def _execute_internal_move_finish(self, item: QTreeWidgetItem,
                                      expanded_states: Dict[int, bool]):
        self._restore_expanded_map(item, expanded_states)
        self.itemMoved.emit(item)
        self.setCurrentItem(item)
