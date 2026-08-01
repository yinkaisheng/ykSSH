#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QGridLayout, QLabel, QWidget

from ui.widgets import ArrowComboBox


def create_dialog(parent: QWidget, title: str, *, min_width: int = 400) -> QDialog:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(min_width)
    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    return dialog


def create_form_grid() -> QGridLayout:
    grid = QGridLayout()
    grid.setColumnStretch(1, 1)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(10)
    return grid


def add_form_field(grid: QGridLayout, row: int, label_text: str, field: QWidget) -> QLabel:
    label = QLabel(label_text)
    grid.addWidget(label, row, 0, Qt.AlignRight | Qt.AlignVCenter)
    grid.addWidget(field, row, 1)
    return label


def refresh_combo_items(combo: ArrowComboBox, items: list[tuple[str, object]]) -> None:
    current = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for text, data in items:
        combo.addItem(text, data)
    if current is not None:
        select_combo_by_data(combo, current)
    combo.blockSignals(False)


def select_combo_by_data(combo: ArrowComboBox, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    combo.setCurrentIndex(0)
