# Security Policy

## Supported versions

Only the latest version on the default branch is supported.

## Reporting a vulnerability

Please report security issues privately through GitHub Security Advisories for
this repository. Do not open a public issue containing a Telegram token, chat
ID, Codex task identifier, local path, log excerpt with personal data, or any
other credential.

## Operational guidance

- Use a dedicated Telegram bot in a private one-to-one chat.
- Never commit the bot token or allowed chat ID.
- Keep macOS Keychain and the local user account protected.
- Do not expand the listener into an arbitrary command or shell interface.
- Review confirmation text before adding new sensitive workflows.
- Rotate the Bot API token through BotFather if it may have been exposed.
- Treat the Codex `queue` interface as version-sensitive and test after Codex
  desktop updates.
