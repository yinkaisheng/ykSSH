# -*- coding: utf-8 -*-
"""按 Tab、方向显示当前传输与等待任务的非模态窗口。"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.transfer_queue import TransferKind
from i18n import register_retranslator, tr, unregister_retranslator
from log_util import logger
from storage.app_config import get_app_config, save_transfer_dialog_layout
from storage.file_panel_defaults import TRANSFER_TABLE_COLUMNS
from ui.dialog_i18n import translate_button_box
from ui.file_panel.helpers import _format_size
from ui.sftp_ui_handler import SftpUiHandler


class TransferDialog(QDialog):
    def __init__(
        self, handler: SftpUiHandler, kind: TransferKind, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._handler = handler
        self._kind = kind
        self._rows: dict[str, tuple[QTreeWidgetItem, QPushButton]] = {}
        self._layout_ready = False
        self._pending_layout: tuple[int, int, dict[str, int]] | None = None
        cfg = get_app_config().file_panel
        self.setMinimumSize(640, 240)
        self.resize(cfg.transfer_dialog_width, cfg.transfer_dialog_height)

        layout = QVBoxLayout(self)
        self._empty_label = QLabel()
        layout.addWidget(self._empty_label)
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QTreeWidget.SingleSelection)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(QHeaderView.Interactive)
        for column, name in enumerate(TRANSFER_TABLE_COLUMNS):
            header.resizeSection(column, cfg.transfer_column_widths[name])
        layout.addWidget(self._tree, 1)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self._buttons.rejected.connect(self.close)
        layout.addWidget(self._buttons)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._persist_layout)
        header.sectionResized.connect(self._schedule_layout_save)

        handler.transfer_jobs_changed.connect(self.refresh)
        handler.destroyed.connect(self.close)
        register_retranslator(self.retranslate_ui)
        self.destroyed.connect(
            lambda _obj=None, callback=self.retranslate_ui: unregister_retranslator(callback)
        )
        # 父 Tab 销毁时也保存已捕获的布局快照，不再访问被销毁的控件。
        self.destroyed.connect(lambda _obj=None: self._persist_layout())
        self.retranslate_ui()

    def _layout_snapshot(self) -> tuple[int, int, dict[str, int]]:
        return self.width(), self.height(), {
            name: self._tree.columnWidth(column)
            for column, name in enumerate(TRANSFER_TABLE_COLUMNS)
        }

    def _schedule_layout_save(self, *_args) -> None:
        if not self._layout_ready:
            return
        current = self._layout_snapshot()
        self._pending_layout = current if current != self._saved_layout else None
        if self._pending_layout is not None:
            self._save_timer.start()
        else:
            self._save_timer.stop()

    def _persist_layout(self) -> None:
        if self._pending_layout is None:
            return
        width, height, widths = self._pending_layout
        try:
            save_transfer_dialog_layout(width=width, height=height, column_widths=widths)
        except OSError as exc:
            logger.warning(f'Transfer dialog layout save failed: tab_id={self._handler.tab_id}, error={exc}')
            return
        self._saved_layout = self._pending_layout
        self._pending_layout = None

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._layout_ready:
            self._saved_layout = self._layout_snapshot()
            self._layout_ready = True

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._schedule_layout_save()

    def done(self, result: int) -> None:
        self._schedule_layout_save()
        self._save_timer.stop()
        self._persist_layout()
        super().done(result)

    def refresh(self) -> None:
        jobs = self._handler.transfer_jobs(self._kind)
        ids = {job.id for job in jobs}
        for job_id in list(self._rows):
            if job_id not in ids:
                item, _button = self._rows.pop(job_id)
                self._tree.removeItemWidget(item, 4)
                self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(item))
        for job in jobs:
            if job.id not in self._rows:
                item = QTreeWidgetItem(self._tree)
                button = QPushButton()
                button.setAutoDefault(False)
                # 捕获稳定 ID，不依赖行号或绘制时的状态。
                button.clicked.connect(lambda _checked=False, job_id=job.id: self._handler.cancel_transfer(job_id))
                self._tree.setItemWidget(item, 4, button)
                self._rows[job.id] = (item, button)
            item, button = self._rows[job.id]
            item.setText(0, '; '.join(job.paths))
            item.setToolTip(0, '\n'.join(job.paths))
            item.setText(1, job.destination)
            item.setToolTip(1, job.destination)
            if job.state == 'running' and job.calculating:
                status_key = 'file.transfers.calculating'
            else:
                status_key = f'file.transfers.{job.state}'
            item.setText(2, tr(status_key))
            progress_text = ''
            if job.state != 'queued' and not job.calculating:
                percent = min(1000, job.transferred * 1000 // job.total) if job.total > 0 else 0
                progress_text = (
                    f'{_format_size(job.transferred)}/{_format_size(job.total)}  '
                    f'{percent // 10}.{percent % 10}%'
                )
                if job.speed:
                    progress_text = f'{job.speed}  {progress_text}'
            item.setText(3, progress_text)
            item.setToolTip(3, progress_text)
            button.setText(tr('file.transfers.remove' if job.state == 'queued' else 'file.transfers.cancel'))
            button.setEnabled(job.state != 'cancelling')
        self._empty_label.setVisible(not jobs)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr(f'file.transfers.{self._kind}_title'))
        self._empty_label.setText(tr('file.transfers.empty'))
        self._tree.setHeaderLabels([
            tr('file.transfers.source'), tr('file.transfers.destination'),
            tr('file.transfers.status'), tr('file.transfers.progress'),
            tr('file.transfers.action'),
        ])
        translate_button_box(self._buttons)
        self.refresh()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._handler.transfer_jobs_changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass  # Tab 的 handler 可能已随父对象销毁。
        unregister_retranslator(self.retranslate_ui)
        super().closeEvent(event)
