# YKShell

YKShell is a local desktop SSH client built with PyQt5. It provides a WindTerm-like workspace with a session tree, tabbed terminal sessions, and a dual-pane local/remote file manager backed by SFTP.

The project is currently in active development and primarily targets Windows.

## Features

- SSH session tree with folders and saved connection profiles
- Multiple terminal tabs with VT-style rendering via `pyte`
- Integrated SFTP file manager with local and remote panes
- Per-tab file panel state
- Password-based and private-key authentication
- Encrypted password storage using Fernet
- Frameless PyQt5 window
- Built-in themes: Solarized, Light, and Dark
- English and Simplified Chinese UI strings
- Persistent window, splitter, theme, font, and session settings

## Tech Stack

- Python 3.10+
- PyQt5
- asyncssh
- qasync
- pyte
- cryptography
- loguru

## Installation

```powershell
git clone https://github.com/yinkaisheng/YKShell.git
cd YKShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

YKShell stores runtime data under `config/` and writes logs under `logs/`. These directories are intentionally ignored by Git.

## Configuration And Security

Session profiles are stored in `config/sessions.json`.

Passwords are not stored in `sessions.json`. They are encrypted with Fernet and written to:

- `config/credentials.json`
- `config/secret.key`

Private key paths may be stored in session profiles, but private key contents are not copied into the project configuration.

Do not commit files from `config/`, `logs/`, or any file containing real credentials.

## Project Layout

```text
YKShell/
├── main.py                      # Application entry point
├── app_info.py                  # App metadata
├── log_util.py                  # Logging helpers
├── core/                        # SSH, SFTP, connection lifecycle
├── models/                      # Session and favorite path models
├── storage/                     # Config, sessions, credentials
├── ui/                          # PyQt5 widgets and dialogs
├── i18n/                        # Translation engine and fallback strings
├── Languages/                   # English and zh-CN translation files
├── AGENTS.md                    # Contributor and AI-agent guidelines
└── IMPLEMENTATION.md            # Detailed architecture notes
```

## Development Notes

- Keep the UI framework on PyQt5.
- Keep SSH and SFTP operations inside the qasync event loop.
- Add user-facing text through the i18n system instead of hard-coding strings.
- Use `SftpUiHandler` as the bridge between file-panel UI actions and SFTP operations.
- Keep runtime credentials out of source control.

For deeper architecture details, see [IMPLEMENTATION.md](IMPLEMENTATION.md).

## Validation

There is no automated test suite yet. After making changes, run at least:

```powershell
python -c "from ui.main_window import MainWindow"
python -m compileall .
python main.py
```

If GUI startup is not available in your environment, run `python -m compileall .` and document the limitation.

## License

This project is licensed under the MIT License.
