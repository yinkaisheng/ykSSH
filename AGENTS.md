# AGENTS.md

面向 AI 代理与协作者：这里记录**改动边界与必守规则**。架构、数据流和详细验证见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

## 项目概况

- **ykSSH**：本地 PyQt5 SSH 客户端，包含 Session 树、多 Tab 终端和 SFTP 文件管理。
- **技术栈**：PyQt5 + asyncssh + qasync + pyte；改动时保持该技术栈，不要切换到 PyQt6 或 paramiko。
- 界面文案与注释默认中文；**用户可见字符串**必须走 i18n（`tr('namespace.key')`），不要在业务逻辑中硬编码中英文。

## 必守规则

- asyncssh / SFTP 操作必须在 **qasync 事件循环**内执行；UI 更新走 **pyqtSignal** 或 `asyncio.create_task()`。禁止在 QThread 中另起 asyncio 循环连接 SSH。
- UI 框架保持 PyQt5 写法（如 `Qt.StrongFocus` 等 enum）。
- Session 密码只经 Fernet 加密存入 `config/credentials.json`，密钥在 `config/secret.key`；禁止写入 `sessions.json` 或日志。私钥路径可存 config。
- 保持最小改动，不顺手重构无关模块；`terminal_vt_widget.py` 体量大，仅在终端行为相关时修改。
- 新增 UI 文案时，同时更新 `i18n/builtin_strings.py`、`Languages/en/strings.txt` 与 `Languages/zh-CN/strings.txt`；相关 Widget 注册 `register_retranslator(self.retranslate_ui)` 并实现 `retranslate_ui()`。
- 文件面板 SFTP 操作经 `SftpUiHandler` 桥接，不要在 `ui/file_panel/` 中直接调用 asyncssh。
- Tab 关闭时 `ConnectionManager.close_tab()` 必须 cancel 读任务并 `disconnect()`。
- 不要提交 `config/` 下的运行时文件（含 `secret.key`、`credentials.json`）或任何真实凭据。
- 影响架构、配置 schema、连接/文件面板/终端关键行为或已知限制时，同步更新 [IMPLEMENTATION.md](IMPLEMENTATION.md)；纯样式、拼写或不改变行为的小修可跳过。
- 当前处于开发阶段：修改配置 schema、API、配置格式或行为时直接按新设计落地，不保留旧字段、旧路径或旧行为兼容分支。

## 代码约定

- Python 3.10+，`from __future__ import annotations`，UTF-8，`# -*- coding: utf-8 -*-`。
- 类型标注使用 Python 3.10+ 现代语法：使用 `list[str]`、`dict[str, int]`、`tuple[...]`、`T | None`，不要使用 `typing.List`、`typing.Dict`、`typing.Tuple`、`typing.Optional`、`typing.Union` 等旧式别名；`Any`、`Callable`、`Literal`、`Protocol`、`Sequence` 等仍按需从 `typing` 导入。
- 新代码匹配现有风格：类型标注、简短 docstring、分层清晰（models → core → storage → ui）。
- 移植或对齐 `../http-requester`、`../nebula-shell` 时保持 PyQt5，并改为 ykSSH 包结构。

## 验证

```powershell
cd E:\codes\python\ykSSH
pip install -r requirements.txt
python -c "from ui.main_window import MainWindow"
python main.py
```

无自动化测试套件。改动后至少确认：

1. `MainWindow` 可 import，主窗口能启动。
2. Session 树可新建分组/Session。
3. 若改动 SSH/SFTP：能连接、终端有输出、远程文件列表可刷新。

若环境无法启动 GUI，执行 `python -m compileall .` 并在汇报中说明限制。
