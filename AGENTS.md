# AGENTS.md

面向 AI Agent 与协作者：本文件只保留所有任务都必须遵守的全局护栏和规范入口。架构与真实数据流见 [IMPLEMENTATION.md](IMPLEMENTATION.md)，分领域工程细则见 `.trellis/spec/`。

## 项目与全局护栏

- **ykSSH**：本地 PyQt5 SSH 客户端，包含 Session 树、多 Tab 终端和 SFTP 文件管理。
- **技术栈固定**：保持 PyQt5 + asyncssh + qasync + pyte，不切换到 PyQt6、PySide 或 paramiko。
- **异步边界**：SSH、SFTP 和远程编辑操作只在主 qasync 事件循环中执行；禁止在线程中创建第二个 asyncio 循环连接 SSH。
- **凭据安全**：Session 密码只以 Fernet 密文进入 `config/credentials.json`，不得进入 `sessions.json`、日志、测试夹具或任务文档；任何真实凭据和 `config/` 运行时文件都不得提交。
- **改动边界**：保持最小改动，保留用户已有修改，不借当前任务重构无关模块。
- **Git 授权**：Skill 可以生成 spec、plan、research 等本地中间产物；未经用户明确要求，不执行 `git add`、`git commit`，也不把这些产物加入 Git。
- **设计阶段策略**：配置 schema、API、路径或行为直接按新设计落地，不增加旧字段、旧路径或旧行为兼容分支。
- **文档同步**：改变架构、配置 schema、连接、文件面板、终端关键行为或已知限制时，同步更新 [IMPLEMENTATION.md](IMPLEMENTATION.md)。
- **语言**：界面文案与注释默认中文；所有用户可见字符串必须通过 `tr('namespace.key')` 获取。
- 移植或对齐 `../http-requester`、`../nebula-shell` 时保持 PyQt5，并改为 ykSSH 包结构。

## 修改前按范围读取规范

任何代码修改先阅读 `.trellis/spec/desktop/index.md`，再按改动范围读取下列专项规范。一个任务涉及多个领域时，读取所有匹配项；不要无差别加载整个目录。

| 改动范围 | 必读规范 |
| --- | --- |
| 新模块、跨层数据流、职责或目录归属 | `.trellis/spec/desktop/architecture.md` |
| SSH、qasync、连接、重连、Tab 关闭或后台任务 | `.trellis/spec/desktop/async-ssh-lifecycle.md` |
| 配置、Session 字段、JSON 存储、凭据或 host key | `.trellis/spec/desktop/storage-security.md` |
| PyQt5 Widget、用户文案、i18n、主题或快捷键 | `.trellis/spec/desktop/ui-i18n-theme.md` |
| SFTP、文件面板、上传下载、远程 CRUD 或远程编辑 | `.trellis/spec/desktop/sftp-file-operations.md` |
| 终端渲染、pyte、输入输出、滚动历史或 gutter | `.trellis/spec/desktop/terminal.md` |
| 日志、异常、取消或用户错误提示 | `.trellis/spec/desktop/logging-errors.md` |
| 任何 Python 代码、测试或最终验证 | `.trellis/spec/desktop/quality-testing.md` |

修改配置、signal、Tab 状态、SSH/SFTP、远程缓存或用户文案等跨层行为时，同时阅读 `.trellis/spec/guides/cross-layer-thinking-guide.md`。新增 helper、字段、文案、主题 token 或本地/远端对称行为时，同时阅读 `.trellis/spec/guides/code-reuse-thinking-guide.md`。

## 验证与完成条件

根据 `.trellis/spec/desktop/quality-testing.md` 选择并运行与改动对应的测试。代码改动的最低自动化验证为：

```powershell
python -c "from ui.main_window import MainWindow"
python -m compileall .
python -m unittest discover -s tests -v
```

涉及 GUI 时运行 `python main.py` 并检查目标交互；涉及 SSH/SFTP 时手工确认连接、终端输出、远程刷新和关闭清理。环境或凭据不允许手工验证时，在汇报中明确列出未验证项，不得声称已经验证。

<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->
