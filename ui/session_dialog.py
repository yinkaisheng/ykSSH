#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from ui.dialog_i18n import translate_button_box
from ui.widgets import ArrowComboBox, GlyphSpinBox


def _normalize_local_path(path: str) -> str:
    text = (path or '').strip()
    if not text:
        return ''
    return os.path.normpath(text)


class SessionDialog(QDialog):
    """Create or edit an SSH session profile."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        session: Optional[SessionItem] = None,
        title: Optional[str] = None,
        initial_password: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._password = ''
        self._initial_password = initial_password or ''
        self.setWindowTitle(title or (
            tr('sessions.dialog_title_edit') if session else tr('sessions.dialog_title_new')
        ))
        self.setMinimumWidth(700)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._build_ui()
        if session is not None:
            self._load_session(session)
        QTimer.singleShot(0, self._focus_name_edit)

    def _focus_name_edit(self) -> None:
        self.name_edit.setFocus(Qt.OtherFocusReason)
        self.name_edit.selectAll()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = GlyphSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.username_edit = QLineEdit()

        self.auth_combo = ArrowComboBox()
        self.auth_combo.addItem(tr('sessions.auth_password'), AUTH_PASSWORD)
        self.auth_combo.addItem(tr('sessions.auth_publickey'), AUTH_PUBLIC_KEY)
        self.auth_combo.currentIndexChanged.connect(self._on_auth_changed)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.key_path_edit = QLineEdit()
        browse_btn = QPushButton(tr('sessions.browse_key'))
        browse_btn.clicked.connect(self._browse_key)
        key_layout.addWidget(self.key_path_edit, 1)
        key_layout.addWidget(browse_btn)

        form.addRow(tr('sessions.name'), self.name_edit)
        form.addRow(tr('sessions.host'), self.host_edit)
        form.addRow(tr('sessions.port'), self.port_spin)
        form.addRow(tr('sessions.username'), self.username_edit)
        form.addRow(tr('sessions.auth_type'), self.auth_combo)
        self.password_label = QLabel(tr('sessions.password'))
        form.addRow(self.password_label, self.password_edit)
        self.key_label = QLabel(tr('sessions.key_path'))
        form.addRow(self.key_label, key_row)

        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.local_path_edit = QLineEdit()
        self.local_path_edit.setPlaceholderText(tr('sessions.path_home_hint'))
        browse_local_btn = QPushButton(tr('sessions.browse_local'))
        browse_local_btn.clicked.connect(self._browse_local)
        local_layout.addWidget(self.local_path_edit, 1)
        local_layout.addWidget(browse_local_btn)

        self.remote_path_edit = QLineEdit()
        self.remote_path_edit.setPlaceholderText(tr('sessions.path_home_hint'))

        form.addRow(tr('sessions.local_path'), local_row)
        form.addRow(tr('sessions.remote_path'), self.remote_path_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        translate_button_box(buttons)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout.addLayout(form)
        layout.addWidget(buttons)
        self._on_auth_changed()

    def _on_auth_changed(self) -> None:
        auth_type = self.auth_combo.currentData()
        is_password = auth_type == AUTH_PASSWORD
        self.password_label.setVisible(is_password)
        self.password_edit.setVisible(is_password)
        self.key_label.setVisible(not is_password)
        self.key_path_edit.parentWidget().setVisible(not is_password)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('sessions.select_key_title'),
            self.key_path_edit.text() or '',
            tr('sessions.key_filter'),
        )
        if path:
            self.key_path_edit.setText(path)

    def _browse_local(self) -> None:
        start = self.local_path_edit.text().strip() or ''
        path = QFileDialog.getExistingDirectory(
            self,
            tr('sessions.select_local_title'),
            start,
        )
        if path:
            self.local_path_edit.setText(_normalize_local_path(path))

    def _load_session(self, session: SessionItem) -> None:
        self.name_edit.setText(session.name)
        self.host_edit.setText(session.host)
        self.port_spin.setValue(session.port)
        self.username_edit.setText(session.username)
        idx = self.auth_combo.findData(session.auth_type)
        if idx >= 0:
            self.auth_combo.setCurrentIndex(idx)
        self.key_path_edit.setText(session.key_path)
        self.local_path_edit.setText(_normalize_local_path(session.local_path))
        self.remote_path_edit.setText(session.remote_path)
        if self._initial_password:
            self.password_edit.setText(self._initial_password)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            return
        if not self.host_edit.text().strip():
            return
        self._password = self.password_edit.text()
        self.accept()

    def get_session(self) -> SessionItem:
        session_id = self._session.id if self._session is not None else SessionItem().id
        return SessionItem(
            id=session_id,
            name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.username_edit.text().strip(),
            auth_type=self.auth_combo.currentData(),
            key_path=self.key_path_edit.text().strip(),
            local_path=_normalize_local_path(self.local_path_edit.text()),
            remote_path=self.remote_path_edit.text().strip(),
        )

    def get_password(self) -> str:
        return self._password
