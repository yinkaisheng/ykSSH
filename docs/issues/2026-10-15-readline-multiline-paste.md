# 问题记录：远端 Readline 超屏多行粘贴回显

记录日期：2026-10-15

## 现象与状态

已定位到远端行编辑器的超屏回显限制，尚未升级远端软件。用户粘贴长 SQL 时，确认框中的内容分行正常；终端前面若干行正常，超过一屏后的行会在底行相互覆盖，例如显示为 `LIMIT 100;t_id_responses DESC,set_id)3741824, 3) AS total_size_gib,`。用户确认实际执行没有报错，因此本次现象不能作为 SQL 内容损坏或语法错误的依据。粘贴后的反显来自远端 Readline 的 SGR 7/27，并非 ykSSH 本地鼠标选区。

## 已验证环境

测试会话所在服务器为 Ubuntu 24.04.4 LTS，Bash 包版本 `5.2.21-2ubuntu4`，系统 `libreadline8t64` 与 `readline-common` 包版本均为 `8.2-4build1`。当时服务器本地 APT 索引中的候选版本与已安装版本相同；未执行 `apt update`，这不代表后续软件源永远没有更新。发行版仍在维护，不意味着其中的软件包已采用上游最新主版本。

## 复现步骤与证据

1. 新建 SSH 终端，无需运行 psql，也无需拖动分隔条或调整字体。本地终端与远端 PTY 均设为 191 列、37 行。
2. 在远端明确开启 bracketed paste 后，粘贴 70 行带编号、长短不一的普通测试文本；不按 Enter，不执行粘贴内容。
3. 通过原有 `ConnectionManager → SSHSession` 链路，在 qasync 事件循环中抓取原始 SSH 回显。一次测试包含 37 个 LF；超过一屏后，行间只剩 CR，不再有 LF。CR 仅将光标移回行首，后续文本因此覆盖底行并留下较长旧行的尾部。
4. 末尾六行预期为第 65～70 行，实际显示为第 33～37 行加上被第 70 行覆盖的底行。在本地与远端行列数一致的条件下仍能复现，故此前发现并修复的尺寸同步缺陷不能解释此次复现。

对照实验中，将括号粘贴正文的换行由 CR 改为 LF、关闭远端 `enable-active-region`、将文本分为多次括号粘贴发送，均未消除同一覆盖现象。诊断只在临时 SSH Shell 内调整选项，未执行测试文本或用户 SQL，未写入远端启动配置。原始回显可直接解释屏幕上的覆盖结果；不应在客户端全局将 CR 改为 CRLF，也不应自动取消 bracketed paste，这些做法会改变普通终端控制或输入执行语义。

## 处理方式与验证边界

- 临时绕过：启动 psql 时增加 `-n`，关闭 Readline 行编辑与历史功能；也可在 psql 中使用 `\e` 通过编辑器处理长 SQL，或用 `\i` 读取 SQL 文件。操作语义见 [psql 官方文档](https://www.postgresql.org/docs/current/app-psql.html)。本次未对实际 psql 进程的运行主机、容器或所链接的行编辑库进行检查。
- 上游方向：[Readline 8.3 变更记录](https://tiswww.case.edu/php/chet/readline/CHANGES) 已列出对超过物理屏幕行数的输入的支持，可评估远端 Bash 5.3 / Readline 8.3。尚未在同一台 Ubuntu 服务器上完成升级前后对照，不能将上游说明视为本环境已验证修复。
- Bash 与共享库分别处理：测试服务器的 `/bin/bash` 未动态链接 `libreadline`，单独安装新版系统 Readline 共享库不会更新其内置实现。[Bash 官方安装说明](https://www.gnu.org/software/bash/manual/bash.html#Optional-Features) 也说明默认使用随 Bash 构建的静态 Readline。建议先将新版 Bash 并行安装到用户目录验证，再决定是否调整默认 Shell。
- psql 使用的库需要单独确认；如果运行在容器中，应在对应容器内检查和更新。仅升级宿主机 Bash 不能据此认定 psql 已获得新版 Readline。
