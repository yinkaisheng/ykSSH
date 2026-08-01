#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from app_info import APP_NAME, APP_VERSION, GITHUB_URL
from i18n import tr
from ui.dialog_common import create_dialog
from ui.dialog_i18n import translate_button_box
from ui.theme import format_link_html


def show_about_dialog(parent: QWidget) -> None:
    dialog = create_dialog(parent, tr('about.title'), min_width=360)

    layout = QVBoxLayout(dialog)
    layout.setSpacing(8)

    title = QLabel(APP_NAME)
    title.setAlignment(Qt.AlignCenter)
    layout.addWidget(title)

    version = QLabel(tr('about.version', version=APP_VERSION))
    version.setAlignment(Qt.AlignCenter)
    layout.addWidget(version)

    link = QLabel(format_link_html(GITHUB_URL))
    link.setAlignment(Qt.AlignCenter)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextBrowserInteraction)
    layout.addWidget(link)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dialog)
    translate_button_box(buttons)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)

    dialog.exec_()
