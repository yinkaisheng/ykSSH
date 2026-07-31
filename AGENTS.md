# AGENTS.md

本文件面向在此仓库中工作的代码代理与协作者，说明 MyPyShell 的真实结构、约定与改动边界，避免套用与项目不符的通用模板。

## 项目概览

- **MyPyShell** 是一个本地 **PyQt5 SSH 客户端**（类似 WindTerm），支持多 Tab 终端与 SFTP 文件管理。
- 技术栈：**PyQt5** + **asyncssh** + **qasync** + **pyte**。
- UI/主题/多语言参考 [`http-requester`](../http-requester)；终端 VT 控件参考 [`nebula-shell`](../nebula-shell)（已移植为 PyQt5）。
- 界面文案与注释默认使用中文；用户可见字符串通过 i18n 系统管理，**不要**硬编码中文/英文到业务逻辑中。

## 目录与职责

```
MyPyShell/
├── main.py                 # 入口：QApplication + qasync 事件循环 + 主题/i18n 初始化
├── app_info.py             # APP_NAME / APP_VERSION
├── log_util.py             # 日志工具（可复用，非核心业务）
├── models/
│   └── session_item.py     # SessionItem 树节点（folder / session leaf）
├── core/
│   ├── ssh_session.py      # asyncssh 连接、Shell PTY、SFTP 客户端
│   ├── connection_manager.py  # tab_id ↔ SSHSession 映射
│   ├── path_resolver.py    # 本地/远程 Session 路径解析（无效则回退 ~）
│   ├── sftp_service.py     # SFTP 异步操作（listdir/upload/download/…）
│   └── sftp_ui_handler.py    # 文件面板 ↔ SFTP 的 UI 桥接
├── storage/
│   ├── paths.py            # 路径常量（config.json、sessions.json 等）
│   ├── app_config.py       # 主题/语言/外观配置
│   ├── session_store.py    # 窗口尺寸、splitter 比例等 UI 状态
│   ├── session_profile_store.py  # Session 树持久化（sessions.json）
│   ├── credential_store.py # Fernet 加密密码（config/credentials.json）
│   ├── secret_key.py       # 本地密钥（config/secret.key）
│   └── keyring_store.py    # CredentialStore 兼容别名
├── i18n/                   # tr() 翻译引擎
├── Languages/              # en / zh-CN 语言包（strings.txt）
└── ui/
    ├── main_window.py      # 主布局：Session 树 | 终端 Tab + 文件面板
    ├── session_tree_panel.py   # Session 树面板（仿 http-requester FavoritePanel）
    ├── favorite_tree_widget.py # 可拖拽 QTreeWidget（从 http-requester 提取）
    ├── session_dialog.py   # 新建/编辑 Session 对话框
    ├── terminal_tab_widget.py  # 多 Tab 终端容器
    ├── terminal_vt_widget.py   # pyte + QPainter 终端控件（~2000 行，慎改）
    ├── file_table_panel.py # 本地/远程双文件 Table
    ├── theme.py            # QSS 主题（来自 http-requester）
    ├── dialogs.py          # 设置/关于对话框
    └── widgets.py          # ArrowComboBox 等通用控件
```

## 界面布局

```
┌─────────────────────────────────────────────────┐
│ [Connect]  [设置]  [关于]                        │  顶栏全宽
├────────────┬────────────────────────────────────┤
│ Session 树 │  Terminal Tab Bar                  │
│ (分组/     │  TerminalVTWidget                  │  同高
│  Session)  │                                    │
├────────────┴────────────────────────────────────┤
│  本地文件 Table        │  远程 SFTP Table        │  全宽
└─────────────────────────────────────────────────┘
```

- 顶栏：Connect / 设置 / 关于，横跨窗口全宽。
- 上区水平 splitter：左 Session 树 ~280px，右终端 Tab + `TerminalVTWidget`（二者同高）。
- 下区文件面板：本地 Table | 远程 Table，占满 Session 树 + 终端的整行宽度。
- 垂直 splitter：上区（Session + 终端）~65%，下区（文件面板）~35%。
- Splitter 比例持久化到 `config/session.json`（`main_splitter`、`vertical_splitter`）。

## 核心架构模式

### asyncio + Qt：qasync

所有 asyncssh 操作必须在 **qasync 事件循环**内执行，UI 更新通过 **pyqtSignal** 或 `asyncio.create_task()`：

```python
# main.py 已配置
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)
```

**禁止**在 QThread 中另起 asyncio 循环连接 SSH，除非有充分理由并同步文档。

### Session 树数据模型

- **分组（folder）**：`SessionItem` 无 `host`，可有 `children`（子分组或 Session）。
- **Session（leaf）**：含 `host/port/username/auth_type/key_path/local_path/remote_path`；密码存 `config/credentials.json`（Fernet 加密），不写入 `sessions.json`。
- 树控件使用 `FavoriteTreeWidget`（`ITEM_TYPE_FOLDER` / `ITEM_TYPE_SESSION`）。
- 拖拽/CRUD 后通过 `_sync_data_model()` 从 widget 树同步回 `List[SessionItem]` 并持久化。

### 终端 I/O 数据流

```
SSH channel → SSHSession.data_received → TerminalVTWidget.write_text()
TerminalVTWidget.input_received → SSHSession.write() → SSH channel
```

Tab 关闭时 `ConnectionManager.close_tab()` 必须 cancel 读任务并 `disconnect()`。

### SFTP 文件面板

- 远程列表通过 `ConnectionManager.get_remote_list_callback()` 提供同步回调；实际列表由 `refresh_remote_list()` 异步填充并 emit `remote_list_updated`。
- 上传/下载等操作经 `SftpUiHandler` 桥接，不要直接在 `file_table_panel.py` 里调用 asyncssh。

## 参考项目

| 需求 | 参考路径 |
|------|----------|
| 主题 / QSS / 字体 | `../http-requester/ui/theme.py` |
| i18n（strings.txt + tr()） | `../http-requester/i18n/` |
| 可拖拽树控件 | `../http-requester/ui/favorite_panel.py` → `_FavoriteTreeWidget` |
| Session 面板模式 | `../http-requester/ui/favorite_panel.py` → `FavoritePanel` |
| 终端 VT 实现 | `../nebula-shell/ui/terminal_vt_widget.py`（本项目为 PyQt5 版） |

移植或对齐参考项目时，保持 **PyQt5**（不要引入 PyQt6），并改 import 路径为 MyPyShell 包结构。

## i18n 规则

- 用户可见字符串：`tr('namespace.key')`，key 定义在 `i18n/builtin_strings.py` 与 `Languages/*/strings.txt`。
- 新增 UI 文案时，**同时**更新 `builtin_strings.py`（英文 fallback）和 `Languages/zh-CN/strings.txt`。
- 对话框按钮翻译走 `ui/dialog_i18n.py`，不要用 Qt `.qm` 文件。
- Widget 语言切换：注册 `register_retranslator(self.retranslate_ui)` 并实现 `retranslate_ui()`。

## 配置与持久化

| 文件 | 内容 |
|------|------|
| `config/config.json` | 主题、语言、字体等 appearance |
| `config/session.json` | 窗口尺寸、splitter 比例 |
| `config/sessions.json` | Session 树（嵌套 JSON，含 `local_path`/`remote_path`，无密码） |
| `config/credentials.json` | Session 密码（Fernet 加密后的 `passwords[session_id]`） |
| `config/secret.key` | 本地加密密钥（与 `credentials.json` 配套，**必须一起迁移**） |

修改配置 schema 时保持向后兼容，或在 `app_config.py` / store 中做 normalize。

### 配置目录迁移（WindTerm 风格）

将整个 `config/` 目录复制到另一台电脑的 MyPyShell 程序目录下（与 `main.py` 同级），即可直接使用，无需重新配置 Session。

**必须一并复制的文件：**

| 文件 | 说明 |
|------|------|
| `config.json` | 主题、语言、终端/UI 字体 |
| `session.json` | 窗口大小、splitter 比例 |
| `sessions.json` | Session 树（主机、端口、用户名、本地/远程路径等） |
| `credentials.json` | 加密后的 Session 密码 |
| `secret.key` | 解密密码所需的本地密钥 |

**迁移步骤：**

1. 在源机器上关闭 MyPyShell。
2. 复制整个 `config/` 文件夹到目标机器同名路径（例如 `E:\codes\python\MyPyShell\config\`）。
3. 在目标机器启动 MyPyShell；Session 树、密码、外观设置应自动生效。
4. 连接 Session 时，文件面板会打开 Session 中配置的本地/远程路径；路径无效或留空时回退到各自的主目录（`~`）。

**注意事项：**

- `secret.key` 与 `credentials.json` 必须配对；只复制其中一个会导致密码无法解密。
- 首次从旧版明文 `credentials.json`（version 1）启动时，程序会自动用 `secret.key` 重新加密并升级至 version 2。
- 私钥文件本身不在 `config/` 内；若 Session 使用公钥认证，需确保目标机器上 `key_path` 指向的私钥文件存在（或使用 `~/.ssh/id_rsa` 等相对路径）。
- `config/` 含敏感信息，请勿提交到 git 或分享给不可信方。

## 运行与验证

```powershell
cd E:\codes\python\MyPyShell
pip install -r requirements.txt
python main.py
```

无自动化测试套件。改动后至少验证：

1. `python -c "from ui.main_window import MainWindow"` 可 import
2. `python main.py` 能启动主窗口
3. Session 树可新建分组/Session
4. 若改动 SSH/SFTP：能连接、终端有输出、远程文件列表可刷新

若环境无法启动 GUI，执行 `python -m compileall .` 并在汇报中说明限制。

## 代码约定

- Python 3.10+，`from __future__ import annotations`，UTF-8，`# -*- coding: utf-8 -*-`。
- 新代码匹配现有风格：类型标注、简短 docstring、分层清晰（models → core → storage → ui）。
- **最小改动**：不要顺手重构无关模块；`terminal_vt_widget.py` 体量大，仅在与终端行为相关时修改。
- UI 框架 **仅 PyQt5**；enum 用 `Qt.StrongFocus` 等 PyQt5 写法，不要用 PyQt6 的 `Qt.FocusPolicy.StrongFocus`。
- 密码/私钥：**禁止**写入 `sessions.json` 或日志；密码经 Fernet 加密存 `credentials.json`，密钥在 `secret.key`；私钥路径可存 config。
- 不要提交 `config/` 下的运行时文件（含 `secret.key`、`credentials.json`）或含真实凭据的内容。

## 已知限制与待完善（改动前可优先处理）

- SSH `known_hosts=None`（未校验 host key）—— 生产环境需补充。
- 文件面板拖拽互传尚未完整实现（Phase 6）。
- 终端配色尚未完全跟随 app theme（solarized/light/dark）。
- 远程目录首次加载可能需二次刷新（async 缓存时序）。
- `terminal_vt_widget.py` 从 nebula-shell 移植，可能仍有 PyQt5 边角兼容问题。

## 改动检查清单

- [ ] 新增 UI 字符串已加入 i18n（en + zh-CN）
- [ ] asyncssh 操作在 qasync 循环内，UI 更新走 signal
- [ ] Tab 关闭/窗口关闭时 SSH 连接已 disconnect
- [ ] Session 密码未写入 sessions.json
- [ ] 未引入 PyQt6 或 paramiko（本项目统一 asyncssh）
- [ ] 改动范围与任务相关，未无关重构
