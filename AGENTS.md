# AGENTS.md

面向 AI 代理与协作者：**改动边界、约束与检查清单**。架构与实现细节见 [IMPLEMENTATION.md](IMPLEMENTATION.md)。

## 项目是什么

- **MyPyShell**：本地 PyQt5 SSH 客户端（类似 WindTerm），多 Tab 终端 + SFTP 文件管理。
- **技术栈：** PyQt5 + asyncssh + qasync + pyte。
- 界面文案与注释默认使用中文；**用户可见字符串**通过 i18n（`tr('namespace.key')`）管理，业务逻辑中勿硬编码中英文。

## 改动时必须遵守

- 所有 asyncssh / SFTP 操作在 **qasync 事件循环**内执行；UI 更新走 **pyqtSignal** 或 `asyncio.create_task()`。**禁止**在 QThread 中另起 asyncio 循环连接 SSH。
- UI 框架 **仅 PyQt5**（enum 用 `Qt.StrongFocus` 等 PyQt5 写法）；**禁止**引入 PyQt6 或 paramiko。
- Session 密码经 Fernet 加密存 `config/credentials.json`，密钥在 `config/secret.key`；**禁止**写入 `sessions.json` 或日志。私钥路径可存 config。
- **最小改动**：不要顺手重构无关模块；`terminal_vt_widget.py` 体量大（~2000 行），仅在与终端行为相关时修改。
- 新增 UI 文案时，**同时**更新 `i18n/builtin_strings.py`、`Languages/en/strings.txt` 与 `Languages/zh-CN/strings.txt`；Widget 注册 `register_retranslator(self.retranslate_ui)` 并实现 `retranslate_ui()`。
- 文件面板 SFTP 操作经 `SftpUiHandler` 桥接，不要在 `file_table_panel.py` 里直接调用 asyncssh。
- Tab 关闭时 `ConnectionManager.close_tab()` 须 cancel 读任务并 `disconnect()`。
- 不要提交 `config/` 下的运行时文件（含 `secret.key`、`credentials.json`）或含真实凭据的内容。
- 若改动影响**架构、配置 schema、连接/文件面板/终端关键行为或已知限制**，同步更新 [IMPLEMENTATION.md](IMPLEMENTATION.md) 对应章节；纯样式微调、文案拼写、不改变对外行为的小修且文档未过时则可跳过。
- 当前处于开发阶段，修改配置 schema、API、配置格式或行为时直接按新设计落地；不保留旧配置、旧字段、旧路径或旧行为的兼容分支。稳定版发布后再按发布策略补充兼容/迁移规则。

## 代码约定

- Python 3.10+，`from __future__ import annotations`，UTF-8，`# -*- coding: utf-8 -*-`。
- 新代码匹配现有风格：类型标注、简短 docstring、分层清晰（models → core → storage → ui）。
- 修改配置 schema 时直接采用新结构，不保留旧字段兼容层。
- 移植或对齐参考项目（`../http-requester`、`../nebula-shell`）时保持 PyQt5，改 import 路径为 MyPyShell 包结构。

## 改动后验证

```powershell
cd d:\Codes\Python\MyPyShell
pip install -r requirements.txt
python -c "from ui.main_window import MainWindow"
python main.py
```

无自动化测试套件。改动后至少验证：

1. `MainWindow` 可 import，主窗口能启动。
2. Session 树可新建分组/Session。
3. 若改动 SSH/SFTP：能连接、终端有输出、远程文件列表可刷新。

若环境无法启动 GUI，执行 `python -m compileall .` 并在汇报中说明限制。

## 改动检查清单

- [ ] 新增 UI 字符串已加入 i18n（en + zh-CN）
- [ ] asyncssh 操作在 qasync 循环内，UI 更新走 signal
- [ ] Tab 关闭/窗口关闭时 SSH 连接已 disconnect
- [ ] Session 密码未写入 sessions.json
- [ ] 未引入 PyQt6 或 paramiko（本项目统一 asyncssh）
- [ ] 改动范围与任务相关，未无关重构
- [ ] 未为旧格式/旧字段保留向后兼容分支
- [ ] 若有架构/schema/关键行为变化，已同步更新 IMPLEMENTATION.md

## 延伸阅读

| 主题 | IMPLEMENTATION.md |
|------|-------------------|
| 启动流程与目录结构 | §3、§4 |
| 主窗口布局与文件面板 | §5、§6.3 |
| 连接生命周期与数据流 | §6.4、§6.5 |
| 配置 Schema 与密码存储 | §8 |
| 配置目录迁移（WindTerm 风格） | §8.4 |
| 主题 / i18n / 扩展指南 | §9、§10、§14 |
| 已知限制 | §13 |
| 完整验证清单 | §15 |
