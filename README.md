# Codex Telegram Bridge

A small, local-first bridge that sends actionable Codex confirmation requests
to a private Telegram bot and relays bounded **Yes / No** answers back into the
exact running Codex task.

It is useful when Codex is working on a Mac while you are away from the desk:
you can receive a concise result, approve or decline the pending action from
Telegram, and let the same task continue.

> [!IMPORTANT]
> This is an independent community project. It is not an official OpenAI or
> Telegram product. The inbound relay uses the `codex queue` command available
> in current Codex desktop builds and may need adjustment when Codex changes.

## What it does

- receives Codex `agent-turn-complete` notify payloads and suppresses routine
  completion messages and technical payloads;
- removes absolute paths, commit hashes, file citations, and relay boilerplate;
- formats results for a phone-sized Telegram view;
- recognizes Russian and English confirmation questions and localizes the
  `✅ Yes` / `⛔ No` buttons to the message language;
- accepts replies only from one private Telegram chat;
- routes the answer into the exact originating Codex task;
- posts a persistent acceptance receipt after Telegram sends the answer to
  Codex, so mobile users do not have to rely on a short-lived popup;
- expires pending confirmations after 24 hours;
- stores the Telegram token and allowed chat ID in macOS Keychain;
- keeps runtime state and logs outside the repository.

It deliberately does **not** accept arbitrary Telegram prompts. This keeps the
bridge narrow: it is a remote confirmation channel, not a general remote shell.

## Requirements

- macOS;
- Python 3.10 or newer;
- Codex desktop or a `codex` executable with the `queue` command;
- a private Telegram bot created through [@BotFather](https://t.me/BotFather).

The bridge has no third-party Python dependencies.

## Quick start

### 1. Create and activate a Telegram bot

Create a bot with BotFather, open its private chat, press **Start**, and send
`/start` once.

### 2. Clone and configure the bridge

```bash
git clone https://github.com/mxmsmnv/codex-telegram-bridge.git
cd codex-telegram-bridge
python3 scripts/codex_telegram_bridge.py --setup
```

The setup prompt reads the Bot API token without echoing it, validates the bot,
finds the latest private `/start` chat, and stores both values in Keychain.

### 3. Connect the Codex notify hook

Add the bridge to `~/.codex/config.toml` using the absolute clone path:

```toml
notify = [
  "/Users/YOU/dev/codex-telegram-bridge/scripts/codex_telegram_bridge.py"
]
```

Codex invokes the script with its JSON notification payload as the final
argument. If another tool already owns the single `notify` hook, preserve that
tool and configure it to call this script as its downstream/previous notify
command instead of overwriting it.

### 4. Install the reply listener

```bash
scripts/install_listener.sh
```

The installer creates a per-user LaunchAgent at:

```text
~/Library/LaunchAgents/com.mxmsmnv.codex-telegram-bridge.plist
```

It records the active `python3` executable in the LaunchAgent, so the listener
uses the same supported Python 3.10+ runtime that was used for installation.

### 5. Test

```bash
python3 scripts/codex_telegram_bridge.py --test
python3 -m unittest tests/test_codex_telegram_bridge.py
```

## How confirmation routing works

1. Codex finishes a turn and calls the notify hook.
2. The bridge detects a confirmation question and stores a short-lived pending
   record containing the Codex thread ID and working directory.
3. Telegram receives compact context, one actionable sentence, and two buttons.
4. A button or plain `yes` / `no` / `y` / `n` reply is accepted only from the
   configured private chat. Russian `да` / `нет` / `д` / `н` also work.
5. The listener runs `codex queue --thread … --message …`.
6. The pending record is consumed only after Codex accepts the queued message.
7. Telegram receives a persistent confirmation that the answer was accepted.

A newer confirmation in the same Codex task supersedes an older unanswered
one. Plain replies target the newest visible pending question. Exact fallback
commands are also available:

```text
/yes CODE
/no CODE
```

## Security model

- Bot tokens and chat IDs are stored in macOS Keychain, never in source files.
- Runtime state is written with user-only permissions under
  `~/.codex/telegram-bridge/`.
- Logs are stored under `~/.codex/log/`.
- The listener accepts only the documented yes/no variants for an existing
  pending question.
- Answers are bound to a specific Codex task and expire after 24 hours.
- Passwords, OTPs, CAPTCHA values, tokens, addresses, and other critical
  secrets should never be placed in confirmation text.

See [SECURITY.md](SECURITY.md) before exposing a bot beyond a private chat.

## Configuration

Optional environment variables:

| Variable | Purpose |
|---|---|
| `CODEX_BINARY` | Override the `codex` executable path |
| `CODEX_TELEGRAM_TOKEN_SERVICE` | Override the Keychain token service name |
| `CODEX_TELEGRAM_CHAT_SERVICE` | Override the Keychain chat-ID service name |

The default executable discovery order is `CODEX_BINARY`, `codex` on `PATH`,
the current bundled `CodexCLI.app` executable, and then the legacy bundled
Codex executable path.

## Uninstall

```bash
scripts/uninstall_listener.sh
```

The uninstaller stops and removes only the LaunchAgent. It intentionally leaves
Keychain credentials and runtime logs in place so uninstalling is reversible.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
```

Contributions are welcome. Please keep the relay bounded, local-first, and free
of credentials or personal data.

## License

[MIT](LICENSE)
