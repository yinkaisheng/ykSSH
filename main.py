#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import traceback
from pathlib import Path

import qasync
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QStyleFactory

from log_util import config_logger, logger

exe_path = Path(sys.executable).resolve()
script_path = Path(__file__).resolve()
if 'python' not in exe_path.name.lower():
    os.chdir(exe_path.parent)
else:
    os.chdir(script_path.parent)

from storage.app_config import get_app_config, init_app_config
from i18n import init_i18n, tr
from ui.dialog_i18n import install_dialog_translations
from ui.widgets import install_edit_context_menu_translations
from ui.main_window import MainWindow
from ui.theme import (
    apply_app_font,
    apply_app_theme,
    normalize_terminal_font_family,
    normalize_terminal_font_size,
    normalize_theme_name,
)


def _load_app_icon() -> QIcon:
    app_icon_path = Path(__file__).parent / 'shell.ico'
    if app_icon_path.is_file():
        return QIcon(str(app_icon_path))
    return QIcon()


def run_qt_app() -> None:
    init_app_config()
    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))
    install_edit_context_menu_translations(app)
    install_dialog_translations(app)
    init_i18n(get_app_config().language)
    app.setApplicationName(tr('main.window_title'))

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    appearance = get_app_config().appearance
    theme = normalize_theme_name(appearance.theme)
    terminal_font_size = normalize_terminal_font_size(appearance.terminal_font_size_px)
    terminal_font_family = normalize_terminal_font_family(appearance.terminal_font_family)
    apply_app_font(app)
    apply_app_theme(app, theme, terminal_font_size, terminal_font_family)

    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()

    with loop:
        loop.run_forever()


def main() -> None:
    config_logger(logger, log_dir='logs', log_file='ykshell.log', log_to_stdout=bool(sys.stdout))

    logger.info('========================================\n')
    logger.info(f'executable={exe_path}, pid={os.getpid()}, working_directory={os.getcwd()}')
    logger.info(f'__file__={script_path}, argv={sys.argv}')

    try:
        run_qt_app()
    except Exception as ex:
        from storage.secret_key import InvalidSecretKeyError

        if isinstance(ex, InvalidSecretKeyError):
            logger.error(str(ex))
            sys.exit(1)
        logger.error(
            'An unexpected error occurred:\n'
            f'{"".join(traceback.format_exception(type(ex), ex, ex.__traceback__))}'
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
