#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.ssh_config import SshConfigError, SshConfigHost, parse_ssh_config_file, resolve_proxy_chain
from i18n import register_retranslator, tr, unregister_retranslator
from log_util import logger
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from storage.app_config import get_app_config, save_session_edit_dialog_size
from ui.dialog_i18n import message_warning, translate_button_box
from ui.widgets import ArrowComboBox, GlyphSpinBox

CONNECT_DIRECT = 'direct'
CONNECT_SSH_CONFIG = 'ssh_config'


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
        session: SessionItem | None = None,
        title: str | None = None,
        initial_password: str | None = None,
        initial_hop_passwords: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._password = ''
        self._hop_passwords: dict[str, str] = {}
        self._initial_password = initial_password or ''
        self._initial_hop_passwords = dict(initial_hop_passwords or {})
        self._parsed_hosts: dict[str, SshConfigHost] = {}
        self._config_error = ''
        self._hop_password_edits: dict[str, QLineEdit] = {}
        self._custom_title = title
        self.setMinimumWidth(700)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._build_ui()
        register_retranslator(self.retranslate_ui)
        self.destroyed.connect(self._unregister_retranslator)
        self.retranslate_ui()
        if session is not None:
            cfg = get_app_config().side_panel
            self.resize(cfg.session_edit_dialog_width, cfg.session_edit_dialog_height)
            self._load_session(session)
        QTimer.singleShot(0, self._focus_name_edit)

    def done(self, result: int) -> None:  # type: ignore[override]
        self._unregister_retranslator()
        super().done(result)

    def _unregister_retranslator(self, *_args: object) -> None:
        unregister_retranslator(self.retranslate_ui)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._session is not None:
            try:
                save_session_edit_dialog_size(width=self.width(), height=self.height())
            except OSError as exc:
                logger.warning(f'Failed to save session edit dialog size: {exc}')
        super().closeEvent(event)

    def _focus_name_edit(self) -> None:
        self.name_edit.setFocus(Qt.OtherFocusReason)
        self.name_edit.selectAll()

    @staticmethod
    def _form_layout() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        return form

    @staticmethod
    def _form_host(*rows: tuple[QWidget | str, QWidget]) -> tuple[QWidget, QFormLayout]:
        host = QWidget()
        form = SessionDialog._form_layout()
        host.setLayout(form)
        for label, field in rows:
            form.addRow(label, field)
        return host, form

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.name_edit = QLineEdit()
        self.connect_mode_combo = ArrowComboBox()
        self.connect_mode_combo.addItem('', CONNECT_DIRECT)
        self.connect_mode_combo.addItem('', CONNECT_SSH_CONFIG)
        self.connect_mode_combo.currentIndexChanged.connect(self._on_connect_mode_changed)

        self.host_edit = QLineEdit()
        self.port_spin = GlyphSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.username_edit = QLineEdit()

        self.auth_combo = ArrowComboBox()
        self.auth_combo.addItem('', AUTH_PASSWORD)
        self.auth_combo.addItem('', AUTH_PUBLIC_KEY)
        self.auth_combo.currentIndexChanged.connect(self._on_auth_changed)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)

        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.key_path_edit = QLineEdit()
        self.browse_key_btn = QPushButton()
        self.browse_key_btn.clicked.connect(self._browse_key)
        key_layout.addWidget(self.key_path_edit, 1)
        key_layout.addWidget(self.browse_key_btn)

        config_row = QWidget()
        config_layout = QHBoxLayout(config_row)
        config_layout.setContentsMargins(0, 0, 0, 0)
        self.ssh_config_path_edit = QLineEdit()
        self.ssh_config_path_edit.editingFinished.connect(self._reload_ssh_config_hosts)
        self.browse_config_btn = QPushButton()
        self.browse_config_btn.clicked.connect(self._browse_ssh_config)
        config_layout.addWidget(self.ssh_config_path_edit, 1)
        config_layout.addWidget(self.browse_config_btn)

        self.ssh_config_host_combo = ArrowComboBox()
        self.ssh_config_host_combo.currentIndexChanged.connect(self._update_ssh_config_preview)
        self.ssh_config_preview = QLabel()
        self.ssh_config_preview.setWordWrap(True)
        self.ssh_config_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ssh_config_preview.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.name_label = QLabel()
        self.connect_mode_label = QLabel()
        top_host, self._top_form = self._form_host(
            (self.name_label, self.name_edit),
            (self.connect_mode_label, self.connect_mode_combo),
        )

        self.host_label = QLabel()
        self.port_label = QLabel()
        self.username_label = QLabel()
        self.auth_label = QLabel()
        self.password_label = QLabel()
        self.key_label = QLabel()
        direct_common, self._direct_form = self._form_host(
            (self.host_label, self.host_edit),
            (self.port_label, self.port_spin),
            (self.username_label, self.username_edit),
            (self.auth_label, self.auth_combo),
        )
        self.password_fields, self._password_form = self._form_host(
            (self.password_label, self.password_edit),
        )
        self.key_fields, self._key_form = self._form_host(
            (self.key_label, key_row),
        )
        self.direct_fields = QWidget()
        direct_layout = QVBoxLayout(self.direct_fields)
        direct_layout.setContentsMargins(0, 0, 0, 0)
        direct_layout.setSpacing(10)
        direct_layout.addWidget(direct_common)
        direct_layout.addWidget(self.password_fields)
        direct_layout.addWidget(self.key_fields)

        self.ssh_config_path_label = QLabel()
        self.ssh_config_host_label = QLabel()
        self.ssh_config_preview_label = QLabel()
        self.ssh_config_preview_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        config_common, self._config_form = self._form_host(
            (self.ssh_config_path_label, config_row),
            (self.ssh_config_host_label, self.ssh_config_host_combo),
            (self.ssh_config_preview_label, self.ssh_config_preview),
        )
        self.hop_passwords_host = QWidget()
        self.hop_passwords_form = self._form_layout()
        self.hop_passwords_host.setLayout(self.hop_passwords_form)
        self.config_fields = QWidget()
        config_layout_host = QVBoxLayout(self.config_fields)
        config_layout_host.setContentsMargins(0, 0, 0, 0)
        config_layout_host.setSpacing(10)
        config_layout_host.addWidget(config_common)
        config_layout_host.addWidget(self.hop_passwords_host)

        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.local_path_edit = QLineEdit()
        self.browse_local_btn = QPushButton()
        self.browse_local_btn.clicked.connect(self._browse_local)
        local_layout.addWidget(self.local_path_edit, 1)
        local_layout.addWidget(self.browse_local_btn)

        self.remote_path_edit = QLineEdit()

        self.info_edit = QPlainTextEdit()
        self.info_edit.setTabChangesFocus(True)
        info_line = self.info_edit.fontMetrics().lineSpacing()
        self.info_edit.setMinimumHeight(info_line * 2 + 16)
        self.info_label = QLabel()
        self.info_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.local_path_label = QLabel()
        self.remote_path_label = QLabel()
        bottom_host, self._bottom_form = self._form_host(
            (self.local_path_label, local_row),
            (self.remote_path_label, self.remote_path_edit),
            (self.info_label, self.info_edit),
        )

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout.addWidget(top_host)
        layout.addWidget(self.direct_fields)
        layout.addWidget(self.config_fields)
        layout.addWidget(bottom_host)
        layout.addWidget(self._buttons)
        self._on_connect_mode_changed()
        self._sync_label_column_width()

    def _sync_label_column_width(self) -> None:
        labels: list[QLabel] = []
        for form in (
            self._top_form,
            self._direct_form,
            self._password_form,
            self._key_form,
            self._config_form,
            self.hop_passwords_form,
            self._bottom_form,
        ):
            for index in range(form.rowCount()):
                item = form.itemAt(index, QFormLayout.LabelRole)
                widget = item.widget() if item is not None else None
                if isinstance(widget, QLabel):
                    widget.setMinimumWidth(0)
                    labels.append(widget)
        width = max((label.sizeHint().width() for label in labels), default=0)
        for label in labels:
            label.setMinimumWidth(width)

    def _uses_ssh_config(self) -> bool:
        return self.connect_mode_combo.currentData() == CONNECT_SSH_CONFIG

    def _on_connect_mode_changed(self) -> None:
        config_mode = self._uses_ssh_config()
        self.direct_fields.setVisible(not config_mode)
        self.config_fields.setVisible(config_mode)
        if config_mode:
            self._reload_ssh_config_hosts()
        else:
            self.hop_passwords_host.setVisible(False)
            self._on_auth_changed()
        self._sync_label_column_width()

    def _on_auth_changed(self) -> None:
        if self._uses_ssh_config():
            return
        is_password = self.auth_combo.currentData() == AUTH_PASSWORD
        self.password_fields.setVisible(is_password)
        self.key_fields.setVisible(not is_password)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('sessions.select_key_title'),
            self.key_path_edit.text() or '',
            tr('sessions.key_filter'),
        )
        if path:
            self.key_path_edit.setText(path)

    def _browse_ssh_config(self) -> None:
        start = self.ssh_config_path_edit.text().strip() or os.path.expanduser('~/.ssh')
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('sessions.select_ssh_config_title'),
            start,
            tr('sessions.ssh_config_filter'),
        )
        if path:
            self.ssh_config_path_edit.setText(os.path.normpath(path))
            self._reload_ssh_config_hosts()

    def _browse_local(self) -> None:
        start = self.local_path_edit.text().strip() or ''
        path = QFileDialog.getExistingDirectory(
            self,
            tr('sessions.select_local_title'),
            start,
        )
        if path:
            self.local_path_edit.setText(_normalize_local_path(path))

    def _reload_ssh_config_hosts(self, preferred_alias: str | None = None) -> None:
        if not self._uses_ssh_config():
            return
        path = self.ssh_config_path_edit.text().strip()
        current = preferred_alias
        if current is None:
            current = self.ssh_config_host_combo.currentData() or self.ssh_config_host_combo.currentText()
        self.ssh_config_host_combo.blockSignals(True)
        self.ssh_config_host_combo.clear()
        self._parsed_hosts = {}
        self._config_error = ''
        if path:
            try:
                self._parsed_hosts = parse_ssh_config_file(path)
                for alias in self._parsed_hosts:
                    self.ssh_config_host_combo.addItem(alias, alias)
                if current:
                    idx = self.ssh_config_host_combo.findData(current)
                    if idx >= 0:
                        self.ssh_config_host_combo.setCurrentIndex(idx)
                    else:
                        self.ssh_config_host_combo.addItem(current, current)
                        idx = self.ssh_config_host_combo.findData(current)
                        if idx >= 0:
                            self.ssh_config_host_combo.setCurrentIndex(idx)
                elif self.ssh_config_host_combo.count() > 0:
                    self.ssh_config_host_combo.setCurrentIndex(0)
            except SshConfigError as exc:
                self._config_error = tr(exc.message_key, **exc.params)
        self.ssh_config_host_combo.blockSignals(False)
        self._update_ssh_config_preview()

    def _update_ssh_config_preview(self) -> None:
        if self._config_error:
            self.ssh_config_preview.setText(self._config_error)
            self._rebuild_hop_password_fields([])
            return
        alias = self.ssh_config_host_combo.currentData() or self.ssh_config_host_combo.currentText()
        hop = self._parsed_hosts.get(str(alias or ''))
        if hop is None:
            self.ssh_config_preview.setText(tr('sessions.ssh_config_preview_empty'))
            self._rebuild_hop_password_fields([])
            return
        resolved = self._selected_hops()
        hops = resolved or [hop]
        lines: list[str] = []
        for index, item in enumerate(hops, start=1):
            if index > 1:
                lines.append('')
            if len(hops) > 1:
                lines.append(tr('sessions.ssh_config_hop', index=index, alias=item.alias))
            lines.extend([
                f"{tr('sessions.host')}: {item.connect_host}",
                f"{tr('sessions.port')}: {item.port}",
                f"{tr('sessions.username')}: {item.username or '-'}",
            ])
            if item.identity_files:
                lines.append(f"{tr('sessions.key_path')}: {', '.join(item.identity_files)}")
        if not resolved and hop.proxy_jump:
            lines.append(f"{tr('sessions.ssh_config_proxy_jump')}: {hop.proxy_jump}")
        password_hops = [item for item in resolved if not item.identity_files]
        if password_hops:
            lines.append(
                tr(
                    'sessions.ssh_config_password_preview',
                    aliases=', '.join(item.alias for item in password_hops),
                )
            )
        self.ssh_config_preview.setText('\n'.join(lines))
        self._rebuild_hop_password_fields(password_hops)

    def _selected_hops(self) -> list[SshConfigHost]:
        alias = str(self.ssh_config_host_combo.currentData() or self.ssh_config_host_combo.currentText() or '')
        if not alias or alias not in self._parsed_hosts:
            return []
        try:
            return resolve_proxy_chain(
                self._parsed_hosts,
                alias,
                path=self.ssh_config_path_edit.text().strip(),
            )
        except SshConfigError:
            return []

    def _rebuild_hop_password_fields(self, password_hops: list[SshConfigHost]) -> None:
        remembered = {alias: edit.text() for alias, edit in self._hop_password_edits.items()}
        while self.hop_passwords_form.rowCount():
            self.hop_passwords_form.removeRow(0)
        self._hop_password_edits = {}
        show = self._uses_ssh_config() and bool(password_hops) and not self._config_error
        self.hop_passwords_host.setVisible(show)
        if not show:
            return
        for hop in password_hops:
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            text = remembered.get(hop.alias, '')
            if not text:
                text = self._initial_hop_passwords.get(hop.alias, '')
            edit.setText(text)
            label = QLabel(tr('sessions.ssh_config_hop_password', alias=hop.alias))
            self.hop_passwords_form.addRow(label, edit)
            self._hop_password_edits[hop.alias] = edit
        self._sync_label_column_width()

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
        self.info_edit.setPlainText(session.info)
        if self._initial_password:
            self.password_edit.setText(self._initial_password)
        mode = CONNECT_SSH_CONFIG if session.uses_ssh_config() else CONNECT_DIRECT
        mode_idx = self.connect_mode_combo.findData(mode)
        self.ssh_config_path_edit.blockSignals(True)
        self.ssh_config_path_edit.setText(session.ssh_config_path)
        self.ssh_config_path_edit.blockSignals(False)
        if mode_idx >= 0:
            self.connect_mode_combo.setCurrentIndex(mode_idx)
        self._on_connect_mode_changed()
        if session.uses_ssh_config():
            self._reload_ssh_config_hosts(preferred_alias=session.host)

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            message_warning(self, tr('sessions.validation_title'), tr('sessions.validation_name_required'))
            self.name_edit.setFocus(Qt.OtherFocusReason)
            return
        if self._uses_ssh_config():
            if not self.ssh_config_path_edit.text().strip():
                message_warning(
                    self,
                    tr('sessions.validation_title'),
                    tr('sessions.validation_ssh_config_required'),
                )
                self.ssh_config_path_edit.setFocus(Qt.OtherFocusReason)
                return
            self._reload_ssh_config_hosts()
            if self._config_error:
                message_warning(self, tr('sessions.validation_title'), self._config_error)
                self.ssh_config_path_edit.setFocus(Qt.OtherFocusReason)
                return
            alias = self.ssh_config_host_combo.currentData() or self.ssh_config_host_combo.currentText()
            if not str(alias or '').strip():
                message_warning(
                    self,
                    tr('sessions.validation_title'),
                    tr('sessions.validation_ssh_config_host_required'),
                )
                self.ssh_config_host_combo.setFocus(Qt.OtherFocusReason)
                return
            if str(alias) not in self._parsed_hosts:
                message_warning(
                    self,
                    tr('sessions.validation_title'),
                    tr(
                        'ssh_config.host_not_found',
                        alias=str(alias),
                        path=self.ssh_config_path_edit.text().strip(),
                    ),
                )
                self.ssh_config_host_combo.setFocus(Qt.OtherFocusReason)
                return
            hops = self._selected_hops()
            missing_alias = ''
            for hop in hops:
                if hop.identity_files:
                    continue
                edit = self._hop_password_edits.get(hop.alias)
                if edit is None or not edit.text():
                    missing_alias = hop.alias
                    break
            if missing_alias:
                message_warning(
                    self,
                    tr('sessions.validation_title'),
                    tr('sessions.validation_ssh_config_hop_password_required', alias=missing_alias),
                )
                edit = self._hop_password_edits.get(missing_alias)
                if edit is not None:
                    edit.setFocus(Qt.OtherFocusReason)
                return
            self._hop_passwords = {
                alias: edit.text()
                for alias, edit in self._hop_password_edits.items()
                if edit.text()
            }
            self._password = ''
        elif not self.host_edit.text().strip():
            message_warning(self, tr('sessions.validation_title'), tr('sessions.validation_host_required'))
            self.host_edit.setFocus(Qt.OtherFocusReason)
            return
        else:
            self._hop_passwords = {}
            self._password = self.password_edit.text()
        self.accept()

    def retranslate_ui(self) -> None:
        if self._custom_title:
            self.setWindowTitle(self._custom_title)
        else:
            self.setWindowTitle(tr(
                'sessions.dialog_title_edit' if self._session is not None else 'sessions.dialog_title_new'
            ))
        self.name_label.setText(tr('sessions.name'))
        self.connect_mode_label.setText(tr('sessions.connect_mode'))
        self.connect_mode_combo.setItemText(0, tr('sessions.connect_mode_direct'))
        self.connect_mode_combo.setItemText(1, tr('sessions.connect_mode_ssh_config'))
        self.host_label.setText(tr('sessions.host'))
        self.port_label.setText(tr('sessions.port'))
        self.username_label.setText(tr('sessions.username'))
        self.auth_label.setText(tr('sessions.auth_type'))
        self.password_label.setText(tr('sessions.password'))
        self.key_label.setText(tr('sessions.key_path'))
        self.auth_combo.setItemText(0, tr('sessions.auth_password'))
        self.auth_combo.setItemText(1, tr('sessions.auth_publickey'))
        self.browse_key_btn.setText(tr('sessions.browse_key'))
        self.ssh_config_path_label.setText(tr('sessions.ssh_config_path'))
        self.ssh_config_host_label.setText(tr('sessions.ssh_config_host'))
        self.ssh_config_preview_label.setText(tr('sessions.ssh_config_preview'))
        self.ssh_config_path_edit.setPlaceholderText(tr('sessions.ssh_config_path_hint'))
        self.browse_config_btn.setText(tr('sessions.browse_ssh_config'))
        self.local_path_label.setText(tr('sessions.local_path'))
        self.remote_path_label.setText(tr('sessions.remote_path'))
        self.info_label.setText(tr('sessions.info'))
        self.local_path_edit.setPlaceholderText(tr('sessions.path_home_hint'))
        self.remote_path_edit.setPlaceholderText(tr('sessions.path_home_hint'))
        self.browse_local_btn.setText(tr('sessions.browse_local'))
        translate_button_box(self._buttons)
        if self._uses_ssh_config():
            self._reload_ssh_config_hosts()
        else:
            self._sync_label_column_width()

    def get_session(self) -> SessionItem:
        session_id = self._session.id if self._session is not None else SessionItem().id
        if self._uses_ssh_config():
            alias = self.ssh_config_host_combo.currentData() or self.ssh_config_host_combo.currentText()
            return SessionItem(
                id=session_id,
                name=self.name_edit.text().strip(),
                host=str(alias or '').strip(),
                ssh_config_path=_normalize_local_path(self.ssh_config_path_edit.text()),
                local_path=_normalize_local_path(self.local_path_edit.text()),
                remote_path=self.remote_path_edit.text().strip(),
                info=self.info_edit.toPlainText().rstrip(),
            )
        return SessionItem(
            id=session_id,
            name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.username_edit.text().strip(),
            auth_type=self.auth_combo.currentData(),
            key_path=self.key_path_edit.text().strip(),
            ssh_config_path='',
            local_path=_normalize_local_path(self.local_path_edit.text()),
            remote_path=self.remote_path_edit.text().strip(),
            info=self.info_edit.toPlainText().rstrip(),
        )

    def get_password(self) -> str:
        return self._password

    def get_hop_passwords(self) -> dict[str, str]:
        return dict(self._hop_passwords)
