#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import atexit
import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import qasync
from PyQt5.QtCore import QEvent, QObject
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
from storage.paths import LOGS_DIR
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

_QT_QUIT_EVENT_TYPE = 20  # QEvent::Quit is not exposed as QEvent.Quit by PyQt5.


class ApplicationExitEventFilter(QObject):
    """Record the Qt events which can end the application event loop."""

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        event_type = event.type()
        if int(event_type) in (int(QEvent.Close), _QT_QUIT_EVENT_TYPE):
            try:
                object_name = watched.objectName()
            except (AttributeError, RuntimeError):
                object_name = ''
            logger.info(
                '[EXIT-DIAG] Qt exit-related event: '
                f'type={int(event_type)}, receiver={type(watched).__name__}, '
                f'object_name={object_name!r}, spontaneous={event.spontaneous()}'
            )
        return False


def _flush_logs() -> None:
    complete = getattr(logger, 'complete', None)
    if callable(complete):
        complete()


def _enable_fault_handler():
    """Keep a separate trace for native crashes which bypass Python logging."""
    fault_path = Path(LOGS_DIR) / 'ykssh-fault.log'
    fault_file = fault_path.open('a', encoding='utf-8', buffering=1)
    fault_file.write(
        f'\n{datetime.now().isoformat(timespec="milliseconds")} '
        f'[EXIT-DIAG] fault handler enabled, pid={os.getpid()}\n'
    )
    fault_file.flush()
    faulthandler.enable(file=fault_file, all_threads=True)
    return fault_file


def _install_exception_diagnostics() -> None:
    """Log exceptions which Qt callbacks and worker threads report globally."""
    default_sys_excepthook = sys.__excepthook__
    default_thread_excepthook = threading.__excepthook__

    def _sys_excepthook(exc_type, exc, tb) -> None:
        logger.error(
            '[EXIT-DIAG] Unhandled exception reached sys.excepthook:\n'
            f'{"".join(traceback.format_exception(exc_type, exc, tb))}'
        )
        _flush_logs()
        default_sys_excepthook(exc_type, exc, tb)

    def _thread_excepthook(args) -> None:
        logger.error(
            '[EXIT-DIAG] Unhandled thread exception: '
            f'thread={args.thread.name if args.thread else "unknown"}\n'
            f'{"".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))}'
        )
        _flush_logs()
        default_thread_excepthook(args)

    sys.excepthook = _sys_excepthook
    threading.excepthook = _thread_excepthook


def _load_app_icon() -> QIcon:
    app_icon_path = Path(__file__).parent / 'shell.ico'
    if app_icon_path.is_file():
        return QIcon(str(app_icon_path))
    return QIcon()


def run_qt_app() -> None:
    init_app_config()
    app = QApplication(sys.argv)
    exit_event_filter = ApplicationExitEventFilter(app)
    app.installEventFilter(exit_event_filter)
    app.aboutToQuit.connect(
        lambda: logger.info('[EXIT-DIAG] QApplication.aboutToQuit emitted')
    )
    app.lastWindowClosed.connect(
        lambda: logger.info('[EXIT-DIAG] QApplication.lastWindowClosed emitted')
    )
    app.setStyle(QStyleFactory.create('Fusion'))
    install_edit_context_menu_translations(app)
    install_dialog_translations(app)
    init_i18n(get_app_config().language)
    app.setApplicationName(tr('main.window_title'))

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    def _asyncio_exception_handler(active_loop, context) -> None:
        error = context.get('exception')
        logger.error(
            '[EXIT-DIAG] Unhandled asyncio event-loop error: '
            f'message={context.get("message", "")}, error={error!r}'
        )
        _flush_logs()
        active_loop.default_exception_handler(context)

    loop.set_exception_handler(_asyncio_exception_handler)

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
    window.destroyed.connect(
        lambda: logger.info('[EXIT-DIAG] MainWindow.destroyed emitted')
    )
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()

    logger.info(
        '[EXIT-DIAG] Qt event loop starting: '
        f'quit_on_last_window_closed={app.quitOnLastWindowClosed()}'
    )
    try:
        with loop:
            loop.run_forever()
            logger.info(
                '[EXIT-DIAG] Qt event loop run_forever returned: '
                f'closing_down={app.closingDown()}, '
                f'visible_top_level_windows='
                f'{sum(widget.isVisible() for widget in app.topLevelWidgets())}'
            )
    finally:
        logger.info('[EXIT-DIAG] Qt event loop context exited')
        _flush_logs()


def main() -> None:
    config_logger(logger, log_dir=str(LOGS_DIR), log_file='ykssh.log', log_to_stdout=bool(sys.stdout))

    logger.info('========================================\n')
    logger.info(f'executable={exe_path}, pid={os.getpid()}, working_directory={os.getcwd()}')
    logger.info(f'__file__={script_path}, argv={sys.argv}')
    _install_exception_diagnostics()
    fault_file = _enable_fault_handler()

    def _log_interpreter_exit() -> None:
        logger.info('[EXIT-DIAG] Python interpreter atexit callback reached')
        _flush_logs()

    atexit.register(_log_interpreter_exit)

    try:
        run_qt_app()
        logger.info('[EXIT-DIAG] run_qt_app returned normally')
    except KeyboardInterrupt:
        logger.warning('[EXIT-DIAG] KeyboardInterrupt reached main')
        raise
    except SystemExit as ex:
        logger.warning(f'[EXIT-DIAG] SystemExit reached main: code={ex.code!r}')
        raise
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
    finally:
        if faulthandler.is_enabled():
            faulthandler.disable()
        fault_file.close()
        logger.info('[EXIT-DIAG] main() finalizer reached')
        _flush_logs()


if __name__ == '__main__':
    main()
