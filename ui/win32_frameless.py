#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows helpers so a Qt frameless window still minimizes from the taskbar."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from PyQt5.QtWidgets import QWidget

_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_WM_NCCALCSIZE = 0x0083
_SM_CXFRAME = 32
_SM_CYFRAME = 33
_SM_CXPADDEDBORDER = 92

_TASKBAR_STYLE_BITS = (
    _WS_CAPTION | _WS_SYSMENU | _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX
)


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [('rgrc', wintypes.RECT * 3), ('lppos', ctypes.c_void_p)]


def _window_long_funcs():
    user32 = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        getter = user32.GetWindowLongPtrW
        setter = user32.SetWindowLongPtrW
        getter.restype = ctypes.c_longlong
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        setter.restype = ctypes.c_longlong
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        return getter, setter
    return user32.GetWindowLongW, user32.SetWindowLongW


def apply_taskbar_minimize_style(widget: QWidget) -> bool:
    """Keep a native minimize box so Explorer can toggle the window from the taskbar."""
    if sys.platform != 'win32':
        return False
    try:
        hwnd = int(widget.winId())
        getter, setter = _window_long_funcs()
        style = int(getter(hwnd, _GWL_STYLE) or 0)
        new_style = style | _TASKBAR_STYLE_BITS
        if new_style != style:
            setter(hwnd, _GWL_STYLE, new_style)
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _resize_border_thickness(horizontal: bool) -> int:
    user32 = ctypes.windll.user32
    frame = user32.GetSystemMetrics(_SM_CXFRAME if horizontal else _SM_CYFRAME)
    padded = user32.GetSystemMetrics(_SM_CXPADDEDBORDER)
    return int(frame) + int(padded)


def _adjust_maximized_client_rect(rect: wintypes.RECT) -> None:
    dx = _resize_border_thickness(True)
    dy = _resize_border_thickness(False)
    rect.left += dx
    rect.top += dy
    rect.right -= dx
    rect.bottom -= dy


def handle_native_event(widget: QWidget, event_type: Any, message: Any) -> tuple[bool, int] | None:
    """Hide the native caption while keeping WS_CAPTION for taskbar minimize."""
    if sys.platform != 'win32':
        return None
    raw_type = bytes(event_type) if event_type is not None else b''
    if raw_type != b'windows_generic_MSG':
        return None
    try:
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != _WM_NCCALCSIZE:
            return None
        if widget.isMaximized() and msg.lParam:
            if msg.wParam:
                params = ctypes.cast(msg.lParam, ctypes.POINTER(_NCCALCSIZE_PARAMS)).contents
                _adjust_maximized_client_rect(params.rgrc[0])
            else:
                rect = ctypes.cast(msg.lParam, ctypes.POINTER(wintypes.RECT)).contents
                _adjust_maximized_client_rect(rect)
        return True, 0
    except (OSError, ValueError, TypeError, AttributeError):
        return None
