#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from cryptography.fernet import Fernet
from PyQt5.QtWidgets import QApplication

from models.command_item import CommandItem
from models.session_item import SessionItem
from storage.command_history_store import CommandHistoryStore
from storage.command_store import CommandStore
from storage.credential_store import CredentialStore
from storage.host_key_store import HostKeyStore
from storage.session_profile_store import SessionProfileStore
from ui.side_panel import SidePanel


class TreeExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _panel(
        self,
        temp_dir: str,
        *,
        sessions: list[SessionItem] | None = None,
        commands: list[CommandItem] | None = None,
    ) -> SidePanel:
        session_store = SessionProfileStore(Path(temp_dir) / 'sessions.json')
        command_store = CommandStore(Path(temp_dir) / 'commands.json')
        if sessions is not None:
            session_store.save_items(sessions)
        if commands is not None:
            command_store.save_items(commands)
        panel = SidePanel(
            session_store,
            credential_store=CredentialStore(
                Path(temp_dir) / 'credentials.json',
                fernet=Fernet(Fernet.generate_key()),
            ),
            command_store=command_store,
            history_store=CommandHistoryStore(),
            host_key_store=HostKeyStore(Path(temp_dir) / 'host_keys.json'),
        )
        panel.show()
        self.app.processEvents()
        return panel

    def test_command_folder_omits_expanded_when_open(self) -> None:
        folder = CommandItem(name='group', children=[CommandItem(name='ls', command='ls')])
        self.assertTrue(folder.expanded)
        self.assertNotIn('expanded', folder.to_dict())
        collapsed = CommandItem.from_dict({
            'id': 'g',
            'name': 'group',
            'expanded': False,
            'children': [{'id': 'c', 'name': 'ls', 'command': 'ls'}],
        })
        self.assertFalse(collapsed.expanded)
        missing = CommandItem.from_dict({'id': 'g', 'name': 'group'})
        self.assertTrue(missing.expanded)

    def test_side_panel_restores_collapsed_session_and_command_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = self._panel(
                temp_dir,
                sessions=[
                    SessionItem(
                        id='folder-1',
                        name='group',
                        expanded=False,
                        children=[SessionItem(id='sess-1', name='box', host='192.0.2.1')],
                    ),
                    SessionItem(
                        id='folder-2',
                        name='open-group',
                        children=[SessionItem(id='sess-2', name='web', host='192.0.2.2')],
                    ),
                ],
                commands=[
                    CommandItem(
                        id='cmd-folder',
                        name='ops',
                        expanded=False,
                        children=[CommandItem(id='cmd-1', name='uptime', command='uptime')],
                    ),
                ],
            )
            try:
                session_folder = panel.tree.topLevelItem(0)
                open_folder = panel.tree.topLevelItem(1)
                command_folder = panel.commands_tree.topLevelItem(0)
                self.assertIsNotNone(session_folder)
                self.assertIsNotNone(open_folder)
                self.assertIsNotNone(command_folder)
                self.assertFalse(session_folder.isExpanded())
                self.assertTrue(open_folder.isExpanded())
                self.assertFalse(command_folder.isExpanded())
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    def test_missing_expanded_field_loads_as_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'sessions.json'
            path.write_text(
                json.dumps({
                    'version': 1,
                    'items': [{
                        'id': 'folder-1',
                        'name': 'group',
                        'children': [{
                            'id': 'sess-1',
                            'name': 'box',
                            'host': '192.0.2.1',
                        }],
                    }],
                }),
                encoding='utf-8',
            )
            panel = self._panel(temp_dir)
            try:
                folder = panel.tree.topLevelItem(0)
                self.assertIsNotNone(folder)
                self.assertTrue(folder.isExpanded())
                self.assertTrue(panel._items[0].expanded)
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    def test_collapsing_folder_persists_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = self._panel(
                temp_dir,
                sessions=[
                    SessionItem(
                        id='folder-1',
                        name='group',
                        children=[SessionItem(id='sess-1', name='box', host='192.0.2.1')],
                    ),
                ],
                commands=[
                    CommandItem(
                        id='cmd-folder',
                        name='ops',
                        children=[CommandItem(id='cmd-1', name='uptime', command='uptime')],
                    ),
                ],
            )
            try:
                panel.tree.topLevelItem(0).setExpanded(False)
                panel.commands_tree.topLevelItem(0).setExpanded(False)
                self.app.processEvents()
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

            sessions = SessionProfileStore(Path(temp_dir) / 'sessions.json').load()
            commands = CommandStore(Path(temp_dir) / 'commands.json').load()
            self.assertFalse(sessions[0].expanded)
            self.assertFalse(commands[0].expanded)

            panel = self._panel(temp_dir)
            try:
                self.assertFalse(panel.tree.topLevelItem(0).isExpanded())
                self.assertFalse(panel.commands_tree.topLevelItem(0).isExpanded())
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()

    def test_filter_temporarily_expands_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = self._panel(
                temp_dir,
                sessions=[
                    SessionItem(
                        id='folder-1',
                        name='group',
                        expanded=False,
                        children=[SessionItem(id='sess-1', name='box', host='192.0.2.1')],
                    ),
                ],
            )
            try:
                panel._set_active_drawer('sessions')
                panel._filter_edit.setText('box')
                self.app.processEvents()
                self.assertTrue(panel.tree.topLevelItem(0).isExpanded())
                self.assertFalse(panel._items[0].expanded)
                panel._filter_edit.clear()
                self.app.processEvents()
                self.assertFalse(panel.tree.topLevelItem(0).isExpanded())
                self.assertFalse(panel._items[0].expanded)
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()
