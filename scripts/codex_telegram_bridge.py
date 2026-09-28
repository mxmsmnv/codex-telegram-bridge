#!/usr/bin/env python3
"""Send useful Codex results to Telegram and relay bounded confirmations back."""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


TOKEN_SERVICE = os.environ.get(
    "CODEX_TELEGRAM_TOKEN_SERVICE",
    "com.mxmsmnv.codex-telegram-bridge.bot-token",
)
CHAT_SERVICE = os.environ.get(
    "CODEX_TELEGRAM_CHAT_SERVICE",
    "com.mxmsmnv.codex-telegram-bridge.chat-id",
)
ACCOUNT = getpass.getuser()


def resolve_codex_binary() -> Path:
    override = os.environ.get("CODEX_BINARY")
    candidates = [
        override,
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return Path(candidates[-1])


CODEX_BINARY = resolve_codex_binary()
STATE_DIR = Path.home() / ".codex" / "telegram-bridge"
STATE_PATH = STATE_DIR / "state.json"
LOCK_PATH = STATE_DIR / "state.lock"
LOG_PATH = Path.home() / ".codex" / "log" / "telegram-bridge.log"
MAX_TELEGRAM_TEXT = 3900
MAX_RESULT_TEXT = 520
MAX_CONFIRMATION_CONTEXT = 180
MAX_CONFIRMATION_QUESTION = 260
PENDING_TTL_SECONDS = 24 * 60 * 60

CONFIRMATION_RE = re.compile(
    r"(?i)(подтвержд|разреша|соглас(?:ен|на)|отправля|передач[уи]|загруз|"
    r"confirm|approve|permission|authorize|submit|upload|send|address|адрес)"
)

CONFIRMATION_REQUEST_RE = re.compile(
    r"(?i)(?:\bподтверди(?:те)?\b|\bответь(?:те)?\s+(?:одним\s+)?[\u00ab\"]?(?:да|нет)|"
    r"\bplease\s+confirm\b|\bconfirm\s+(?:by|that|whether)\b)"
)

RELAY_ECHO_RE = re.compile(
    r"(?is)^\s*(?:да|yes),?\s*подтверждаю\s+указанное\s+действие\.\s*"
    r"продолжай:\s*"
)

FILE_CITATION_RE = re.compile(r":codex-file-citation\{[^}]*\}")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:])/(?:Users|tmp|var|private|opt)/[^\s\])}>,;]+"
)
COMMIT_CLAUSE_RE = re.compile(
    r"(?i)(?:[,;.]?\s*(?:commit|коммит|push)\s*(?:[:№-]?\s*)?`?[0-9a-f]{7,40}`?)"
)
BARE_COMMIT_RE = re.compile(
    r"(?i)(?:\(|\[)\s*(?=[0-9a-f]{7,40}\s*(?:\)|\]))(?=[0-9a-f]*[a-f])"
    r"[0-9a-f]{7,40}\s*(?:\)|\])"
)
NOISE_LINE_RE = re.compile(
    r"(?i)^\s*(?:[-*]\s*)?(?:проект|ветка|commit|коммит|push)\s*:"
)


def log_event(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message[:2000]}\n")


def keychain_get(service: str) -> str:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            ACCOUNT,
            "-s",
            service,
            "-w",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def keychain_set(service: str, value: str) -> None:
    subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            ACCOUNT,
            "-s",
            service,
            "-w",
            value,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )


def telegram_request(
    token: str,
    method: str,
    params: dict[str, str] | None = None,
    *,
    timeout: int = 15,
) -> dict[str, Any]:
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}")
    return payload


def send_telegram(
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    params = {
        "chat_id": keychain_get(CHAT_SERVICE),
        "text": text[:MAX_TELEGRAM_TEXT],
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        params["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    telegram_request(keychain_get(TOKEN_SERVICE), "sendMessage", params)


def _read_state_unlocked() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {"offset": 0, "pending": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"offset": 0, "pending": {}}
    state.setdefault("offset", 0)
    state.setdefault("pending", {})
    return state


def _write_state_unlocked(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=STATE_DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, STATE_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_state(mutator: Callable[[dict[str, Any]], Any]) -> Any:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        os.chmod(LOCK_PATH, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read_state_unlocked()
        result = mutator(state)
        _write_state_unlocked(state)
        return result


def first_value(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def current_branch(cwd: str) -> str | None:
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", cwd, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def compact_text(text: str, limit: int = 2800) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def mobile_text(text: str, limit: int) -> str:
    """Remove transport noise and keep a phone-sized, human-readable result."""

    cleaned = RELAY_ECHO_RE.sub("", text.strip())
    cleaned = FILE_CITATION_RE.sub("", cleaned)
    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = ABSOLUTE_PATH_RE.sub(
        lambda match: Path(match.group(0)).name or "файл",
        cleaned,
    )
    cleaned = COMMIT_CLAUSE_RE.sub("", cleaned)
    cleaned = BARE_COMMIT_RE.sub("", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    lines = [
        line.strip()
        for line in cleaned.splitlines()
        if line.strip() and not NOISE_LINE_RE.match(line)
    ]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip(" \n,;-")
    if len(cleaned) <= limit:
        return cleaned

    tail_size = min(220, max(120, limit // 4))
    head_size = limit - tail_size - 3
    return f"{cleaned[:head_size].rstrip()}…\n{cleaned[-tail_size:].lstrip()}"


def compact_result(message: str) -> str:
    """Keep a result useful on a phone without reproducing an audit log."""

    cleaned = mobile_text(message, MAX_RESULT_TEXT)
    lines = cleaned.splitlines()
    if len(lines) <= 6:
        return cleaned
    return "\n".join([*lines[:5], "…"])


def is_russian_text(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text))


def compact_confirmation_question(question: str) -> str:
    """Reduce recurring application prompts to the decision that matters."""

    normalized = " ".join(question.split())
    lowered = normalized.casefold()
    russian = is_russian_text(normalized)
    if "заяв" in lowered or "application" in lowered:
        if ("код" in lowered or "code" in lowered) and (
            "почт" in lowered or "mail" in lowered
        ):
            return (
                "Открыть почту, ввести код и завершить отправку заявки?"
                if russian
                else "Open email, enter the code, and finish the application?"
            )
        if "captcha" in lowered or "провер" in lowered:
            return (
                "Пройти проверку и отправить заявку?"
                if russian
                else "Complete verification and submit the application?"
            )
        if "загруз" in lowered or "upload" in lowered:
            return (
                "Заполнить форму, загрузить персональное резюме и отправить заявку?"
                if russian
                else "Complete the form, upload the tailored resume, and submit?"
            )
        return "Отправить заявку?" if russian else "Submit the application?"
    return mobile_text(normalized, MAX_CONFIRMATION_QUESTION)


def confirmation_copy(message: str) -> tuple[str, str]:
    """Return short context and the actionable final question."""

    cleaned = mobile_text(message, MAX_TELEGRAM_TEXT)
    matches = list(CONFIRMATION_REQUEST_RE.finditer(cleaned))
    if not matches:
        return "", mobile_text(cleaned, MAX_CONFIRMATION_QUESTION)

    start = matches[-1].start()
    context = cleaned[:start].strip(" \n:.-")
    first_sentence = re.split(r"(?<=[.!?])\s+", context, maxsplit=1)[0]
    if first_sentence:
        context = first_sentence
    question = cleaned[start:].strip()
    question = re.sub(
        r"(?is)^подтверди(?:те)?\s+(?:одним\s+)?[«\"]?(?:да|нет)[»\"]?\s*:\s*",
        "",
        question,
    )
    return (
        compact_text(context, MAX_CONFIRMATION_CONTEXT),
        compact_confirmation_question(question),
    )


def needs_confirmation(message: str) -> bool:
    tail = message.strip()[-700:]
    return bool(CONFIRMATION_REQUEST_RE.search(tail)) or (
        "?" in tail and bool(CONFIRMATION_RE.search(tail))
    )


def pending_id_for(thread_id: str, turn_id: str, message: str) -> str:
    seed = "\0".join((thread_id, turn_id, message)).encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:8].upper()


def store_pending(
    pending_id: str,
    *,
    thread_id: str,
    turn_id: str,
    cwd: str,
    question: str,
) -> None:
    def mutate(state: dict[str, Any]) -> None:
        now = int(time.time())
        pending = state["pending"]
        for key in list(pending):
            if now - int(pending[key].get("created_at", 0)) > PENDING_TTL_SECONDS:
                del pending[key]
        for key, item in pending.items():
            if (
                key != pending_id
                and not item.get("consumed")
                and item.get("thread_id") == thread_id
            ):
                item["consumed"] = True
                item["response"] = "superseded"
                item["responded_at"] = now
        pending[pending_id] = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "cwd": cwd,
            "question": question,
            "created_at": now,
            "consumed": False,
        }

    update_state(mutate)


def build_notification(notification: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    cwd = first_value(notification, "cwd")
    thread_id = first_value(notification, "thread-id", "thread_id", "threadId")
    turn_id = first_value(notification, "turn-id", "turn_id", "turnId")
    assistant = first_value(
        notification,
        "last-assistant-message",
        "last_assistant_message",
        "lastAssistantMessage",
    )

    if not assistant:
        assistant = "Задача завершена, но Codex не передал текст результата в notify payload."

    if thread_id and needs_confirmation(assistant):
        pending_id = pending_id_for(thread_id, turn_id, assistant)
        store_pending(
            pending_id,
            thread_id=thread_id,
            turn_id=turn_id,
            cwd=cwd,
            question=assistant,
        )
        context, question = confirmation_copy(assistant)
        russian = is_russian_text(assistant)
        parts = ["⏸ Нужен ответ" if russian else "⏸ Confirmation needed"]
        if context:
            parts.extend(["", context])
        parts.extend(["", question])
        text = "\n".join(parts)
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Да" if russian else "✅ Yes",
                        "callback_data": f"yes:{pending_id}",
                    },
                    {
                        "text": "⛔ Нет" if russian else "⛔ No",
                        "callback_data": f"no:{pending_id}",
                    },
                ]
            ]
        }
        return text, keyboard

    heading = "✅ Готово" if is_russian_text(assistant) else "✅ Done"
    return "\n".join([heading, "", compact_result(assistant)]), None


def latest_private_chat_id(token: str) -> tuple[str, str]:
    payload = telegram_request(token, "getUpdates")
    for update in reversed(payload.get("result", [])):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None and chat.get("type") == "private":
            label = chat.get("username") or chat.get("first_name") or str(chat_id)
            return str(chat_id), str(label)
    raise RuntimeError(
        "No private chat found. Open the bot, press Start, send /start, and rerun setup."
    )


def setup() -> int:
    print("Telegram Bot Token будет сохранён только в macOS Keychain.")
    token = getpass.getpass("Bot Token: ").strip()
    if not token:
        print("Token не введён.", file=sys.stderr)
        return 1
    try:
        bot = telegram_request(token, "getMe").get("result", {})
        print(f"Бот подтверждён: @{bot.get('username', 'unknown')}")
        print("Откройте этого бота в Telegram, нажмите Start и отправьте /start.")
        input("После этого нажмите Enter здесь: ")
        chat_id, chat_label = latest_private_chat_id(token)
        keychain_set(TOKEN_SERVICE, token)
        keychain_set(CHAT_SERVICE, chat_id)
        send_telegram("🔔 Telegram-мост Codex подключён.")
        print(f"Готово. Тест отправлен в личный чат: {chat_label}.")
        return 0
    except (RuntimeError, urllib.error.URLError, subprocess.SubprocessError) as error:
        print(f"Настройка не завершена: {type(error).__name__}", file=sys.stderr)
        return 1


def resolve_pending(action: str, requested_id: str | None) -> tuple[str, dict[str, Any]]:
    def mutate(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        now = int(time.time())
        candidates = {
            key: value
            for key, value in state["pending"].items()
            if not value.get("consumed")
            and now - int(value.get("created_at", 0)) <= PENDING_TTL_SECONDS
        }
        pending_id = (requested_id or "").upper()
        if not pending_id:
            if not candidates:
                raise LookupError("Нет активного вопроса.")
            # A plain mobile reply always refers to the newest visible prompt.
            # Exact inline buttons and /yes CODE remain available for old prompts.
            pending_id = next(reversed(candidates))
        item = candidates.get(pending_id)
        if item is None:
            raise LookupError("Активное подтверждение с таким кодом не найдено.")
        return pending_id, dict(item)

    return update_state(mutate)


def mark_pending_consumed(pending_id: str, action: str) -> None:
    def mutate(state: dict[str, Any]) -> None:
        item = state["pending"].get(pending_id)
        if item is None:
            raise LookupError("Активное подтверждение с таким кодом не найдено.")
        item["consumed"] = True
        item["response"] = action
        item["responded_at"] = int(time.time())

    update_state(mutate)


def dispatch_to_codex(action: str, pending_id: str | None) -> str:
    resolved_id, item = resolve_pending(action, pending_id)
    thread_id = str(item["thread_id"])
    if action == "yes":
        prompt = "Да. Продолжай последнее ожидающее действие."
    else:
        prompt = "Нет. Не выполняй последнее ожидающее действие."

    if not CODEX_BINARY.is_file():
        raise RuntimeError("Codex binary not found")

    cwd = str(item.get("cwd") or Path.home())
    if not Path(cwd).is_dir():
        cwd = str(Path.home())
    try:
        result = subprocess.run(
            [
                str(CODEX_BINARY),
                "queue",
                "--thread",
                thread_id,
                "--message",
                prompt,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as error:
        log_event(f"queue timeout for {resolved_id}: {error}")
        raise RuntimeError("Codex не успел принять ответ. Подтверждение можно повторить.") from error
    except subprocess.CalledProcessError as error:
        detail = compact_text((error.stderr or error.stdout or "").strip(), 600)
        log_event(f"queue failed for {resolved_id}: {detail}")
        raise RuntimeError("Codex не принял ответ. Подтверждение можно повторить.") from error

    log_event(f"queued Telegram response {resolved_id}: {compact_text(result.stdout, 300)}")
    mark_pending_consumed(resolved_id, action)
    return resolved_id


def parse_text_action(text: str) -> tuple[str, str | None] | None:
    normalized = text.strip()
    match = re.fullmatch(r"/(yes|no)(?:\s+([A-Za-z0-9]+))?", normalized, re.I)
    if match:
        return match.group(1).lower(), match.group(2)
    if normalized.casefold() in {"да", "д", "yes", "y", "подтверждаю"}:
        return "yes", None
    if normalized.casefold() in {"нет", "н", "no", "n", "отказываю"}:
        return "no", None
    return None


def answer_callback(token: str, callback_id: str, text: str) -> None:
    telegram_request(
        token,
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text[:180]},
    )


def pending_question(pending_id: str) -> str:
    def read_question(state: dict[str, Any]) -> str:
        item = state.get("pending", {}).get(pending_id, {})
        return str(item.get("question") or "")

    return update_state(read_question)


def confirmation_receipt(action: str, question: str) -> str:
    russian = is_russian_text(question)
    if action == "yes":
        return (
            "✅ Принято. Ответ «Да» передан в Codex."
            if russian
            else "✅ Accepted. The Yes response was sent to Codex."
        )
    return (
        "⏸ Принято. Ответ «Нет» передан в Codex."
        if russian
        else "⏸ Accepted. The No response was sent to Codex."
    )


def send_confirmation_receipt(action: str, pending_id: str) -> None:
    try:
        send_telegram(confirmation_receipt(action, pending_question(pending_id)))
    except Exception as error:  # noqa: BLE001 - the Codex reply is already queued
        log_event(f"confirmation receipt failed: {type(error).__name__}: {error}")


def process_update(token: str, allowed_chat_id: str, update: dict[str, Any]) -> None:
    callback = update.get("callback_query") or {}
    if callback:
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        if chat_id != allowed_chat_id:
            return
        data = str(callback.get("data") or "")
        match = re.fullmatch(r"(yes|no):([A-Fa-f0-9]{8})", data)
        if not match:
            answer_callback(token, str(callback.get("id") or ""), "Неизвестная команда")
            return
        try:
            action = match.group(1)
            pending_id = dispatch_to_codex(action, match.group(2))
            answer_callback(token, str(callback.get("id") or ""), "Отправлено в Codex")
            send_confirmation_receipt(action, pending_id)
        except (LookupError, RuntimeError, OSError) as error:
            answer_callback(token, str(callback.get("id") or ""), str(error))
        return

    message = update.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if chat_id != allowed_chat_id:
        return
    action = parse_text_action(str(message.get("text") or ""))
    if action is None:
        return
    try:
        pending_id = dispatch_to_codex(*action)
    except (LookupError, RuntimeError, OSError) as error:
        send_telegram(f"⚠️ {error}")
    else:
        send_confirmation_receipt(action[0], pending_id)


def listen() -> int:
    token = keychain_get(TOKEN_SERVICE)
    allowed_chat_id = keychain_get(CHAT_SERVICE)

    def current_offset() -> int:
        return int(update_state(lambda state: state.get("offset", 0)))

    if current_offset() == 0:
        initial = telegram_request(token, "getUpdates", {"timeout": "0"})
        updates = initial.get("result", [])
        if updates:
            newest = int(updates[-1]["update_id"]) + 1
            update_state(lambda state: state.__setitem__("offset", newest))

    log_event("Telegram listener started")
    while True:
        try:
            offset = current_offset()
            payload = telegram_request(
                token,
                "getUpdates",
                {
                    "offset": str(offset),
                    "timeout": "25",
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
                timeout=35,
            )
            for update in payload.get("result", []):
                next_offset = int(update["update_id"]) + 1
                update_state(lambda state, value=next_offset: state.__setitem__("offset", value))
                process_update(token, allowed_chat_id, update)
        except Exception as error:  # noqa: BLE001 - long-running bridge must recover
            log_event(f"listener error: {type(error).__name__}: {error}")
            time.sleep(5)


def notify(raw_notification: str) -> int:
    try:
        notification = json.loads(raw_notification)
        if notification.get("type") != "agent-turn-complete":
            return 0
        text, keyboard = build_notification(notification)
        if keyboard is None:
            log_event("suppressed routine turn-complete notification")
            return 0
        send_telegram(text, reply_markup=keyboard)
    except Exception as error:  # noqa: BLE001 - notification hooks must fail safely
        log_event(f"notify error: {type(error).__name__}: {error}")
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--setup":
        return setup()
    if len(sys.argv) == 2 and sys.argv[1] == "--test":
        try:
            send_telegram(
                "🔔 Telegram-мост работает.\n\n"
                "Результаты приходят кратко. Для вопроса достаточно ответить "
                "«да» или «нет» либо нажать кнопку."
            )
            print("Тестовое сообщение отправлено.")
            return 0
        except Exception as error:  # noqa: BLE001
            print(f"Тест не отправлен: {type(error).__name__}", file=sys.stderr)
            return 1
    if len(sys.argv) == 2 and sys.argv[1] == "listen":
        return listen()
    if len(sys.argv) < 2:
        return 0
    return notify(sys.argv[-1])


if __name__ == "__main__":
    raise SystemExit(main())
