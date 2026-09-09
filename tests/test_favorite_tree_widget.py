#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QDragMoveEvent, QDropEvent
from PyQt5.QtWidgets import QApplication, QTreeWidgetItem

from ui.favorite_tree_widget import ITEM_TYPE_FOLDER, ROLE_TYPE, FavoriteTreeWidget


class FavoriteTreeWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tree = FavoriteTreeWidget()
        self.moved_items = []
        self.tree.itemMoved.connect(self.moved_items.append)
        self.tree.resize(400, 400)
        self.reset_items()
        self.tree.show()
        self.app.processEvents()

    def reset_items(self, nested=False):
        self.tree.clear()
        parent = self.tree.invisibleRootItem()
        if nested:
            parent = QTreeWidgetItem(self.tree, ['parent'])
            parent.setData(0, ROLE_TYPE, ITEM_TYPE_FOLDER)
            parent.setExpanded(True)
        self.items = {}
        for name in 'abcd':
            item = QTreeWidgetItem([name])
            item.setData(0, ROLE_TYPE, ITEM_TYPE_FOLDER)
            parent.addChild(item)
            self.items[name] = item
        self.app.processEvents()
        return parent

    def tearDown(self) -> None:
        self.tree.close()
        self.tree.deleteLater()

    def drop_as_sibling(self, source, target, above) -> None:
        self.moved_items.clear()
        self.tree.setCurrentItem(source)
        rect = self.tree.visualItemRect(target)
        pos = QPoint(rect.left() - 1, rect.top() if above else rect.bottom())
        mime = self.tree.model().mimeData(self.tree.selectedIndexes())
        move = QDragMoveEvent(pos, Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.tree.dragMoveEvent(move)
        self.assertTrue(move.isAccepted())
        self.assertTrue(self.tree._sibling_dragging)
        self.assertIs(self.tree._sibling_indicator_target, target)
        self.assertEqual(self.tree._sibling_above, above)
        drop = QDropEvent(QPointF(pos), Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.tree.dropEvent(drop)
        self.app.processEvents()
        self.assertEqual(len(self.moved_items), 1)
        self.assertIs(self.moved_items[0], source)
        self.assertIs(self.tree.currentItem(), source)
        self.assertIs(source.treeWidget(), self.tree)

    def root_names(self):
        return [self.tree.topLevelItem(i).text(0)
                for i in range(self.tree.topLevelItemCount())]

    def test_drag_last_folder_up_between_middle_folders(self) -> None:
        self.drop_as_sibling(self.items['d'], self.items['c'], above=True)
        self.assertEqual(self.root_names(), ['a', 'b', 'd', 'c'])

    def test_drag_last_folder_before_first_keeps_all_folders(self) -> None:
        self.drop_as_sibling(self.items['d'], self.items['a'], above=True)
        self.assertEqual(self.root_names(), ['d', 'a', 'b', 'c'])

    def test_sibling_drop_order_at_root_and_inside_folder(self) -> None:
        for nested in (False, True):
            for source in 'abcd':
                for target in 'abcd':
                    if source == target:
                        continue
                    for above in (True, False):
                        with self.subTest(nested=nested, source=source,
                                          target=target, above=above):
                            parent = self.reset_items(nested)
                            expected = [name for name in 'abcd' if name != source]
                            expected.insert(expected.index(target) + (not above), source)
                            self.drop_as_sibling(self.items[source], self.items[target], above)
                            actual = [parent.child(i).text(0)
                                      for i in range(parent.childCount())]
                            self.assertEqual(actual, expected)

    def test_move_to_first_preserves_descendants_and_expansion(self) -> None:
        source = self.items['d']
        child = QTreeWidgetItem(source, ['child'])
        child.setData(0, ROLE_TYPE, ITEM_TYPE_FOLDER)
        leaf = QTreeWidgetItem(child, ['leaf'])
        source.setExpanded(True)
        child.setExpanded(True)
        self.app.processEvents()

        self.drop_as_sibling(source, self.items['a'], above=True)

        self.assertEqual(self.root_names(), ['d', 'a', 'b', 'c'])
        self.assertIs(source.child(0), child)
        self.assertIs(child.child(0), leaf)
        self.assertTrue(source.isExpanded())
        self.assertTrue(child.isExpanded())


if __name__ == '__main__':
    unittest.main()
