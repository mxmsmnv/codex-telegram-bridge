import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "codex_telegram_bridge.py"
SPEC = importlib.util.spec_from_file_location("codex_telegram_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bridge)


class TelegramBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_patches = [
            mock.patch.object(bridge, "STATE_DIR", Path(self.temp_dir.name)),
            mock.patch.object(bridge, "STATE_PATH", Path(self.temp_dir.name) / "state.json"),
            mock.patch.object(bridge, "LOCK_PATH", Path(self.temp_dir.name) / "state.lock"),
            mock.patch.object(bridge, "LOG_PATH", Path(self.temp_dir.name) / "bridge.log"),
        ]
        for patcher in self.state_patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.state_patches):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_resolve_codex_binary_supports_current_desktop_bundle_without_path(self):
        current_bundle = (
            "/Applications/ChatGPT.app/Contents/Resources/codex-cli/"
            "CodexCLI.app/Contents/MacOS/codex"
        )
        with (
            mock.patch.dict(bridge.os.environ, {}, clear=True),
            mock.patch.object(bridge.shutil, "which", return_value=None),
            mock.patch.object(
                bridge.Path,
                "is_file",
                autospec=True,
                side_effect=lambda path: str(path) == current_bundle,
            ),
        ):
            self.assertEqual(str(bridge.resolve_codex_binary()), current_bundle)

    def test_confirmation_detection_is_bounded(self):
        self.assertTrue(bridge.needs_confirmation("Подтверждаешь передачу адреса?"))
        self.assertTrue(bridge.needs_confirmation("Confirm final submission?"))
        self.assertTrue(
            bridge.needs_confirmation(
                "Подтверди одним «да»: загрузить резюме и отправить заявку."
            )
        )
        self.assertTrue(
            bridge.needs_confirmation(
                "Жду ваше «да», чтобы нажать финальный Submit в Linde."
            )
        )
        self.assertTrue(
            bridge.needs_confirmation(
                "Waiting for your yes before clicking final Submit."
            )
        )
        self.assertFalse(bridge.needs_confirmation("Заявка отправлена."))
        self.assertFalse(bridge.needs_confirmation("Подтверждение вижу, продолжаю работу."))
        self.assertFalse(bridge.needs_confirmation("Что делать дальше?"))

    def test_waiting_for_yes_gate_is_sent_with_buttons(self):
        text, keyboard = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": (
                    "Анкета Linde полностью заполнена и проверена. "
                    "Жду ваше «да», чтобы нажать финальный Submit в Linde."
                ),
            }
        )
        self.assertIn("Нужен ответ", text)
        self.assertIn("Жду ваше «да»", text)
        self.assertIsNotNone(keyboard)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "✅ Да")

    def test_text_actions(self):
        self.assertEqual(bridge.parse_text_action("/yes A1B2C3D4"), ("yes", "A1B2C3D4"))
        self.assertEqual(bridge.parse_text_action("да"), ("yes", None))
        self.assertEqual(bridge.parse_text_action("нет"), ("no", None))
        self.assertIsNone(bridge.parse_text_action("продолжай поиск"))

    def test_notification_contains_actual_question_and_buttons(self):
        text, keyboard = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": "Подтверждаешь отправку заявки?",
            }
        )
        self.assertIn("Подтверждаешь отправку заявки?", text)
        self.assertIn("Нужен ответ", text)
        self.assertNotIn("Ответ: да / нет", text)
        self.assertNotIn("Проект:", text)
        self.assertNotIn("Ветка:", text)
        self.assertIsNotNone(keyboard)
        self.assertEqual(len(keyboard["inline_keyboard"][0]), 2)
        self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "✅ Да")
        self.assertEqual(keyboard["inline_keyboard"][0][1]["text"], "⛔ Нет")

    def test_notification_removes_paths_commit_and_relay_echo(self):
        text, _ = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": (
                    "Да, подтверждаю указанное действие. Продолжай: Cabin приняла "
                    "CAPTCHA. Файл: /Users/example/dev/resume.pdf; push `8b24673`.\n\n"
                    "Подтверди одним «да»: открыть почту и завершить отправку?"
                ),
            }
        )
        self.assertNotIn("Да, подтверждаю указанное действие", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("8b24673", text)
        self.assertNotIn("resume.pdf", text)
        self.assertIn("открыть почту и завершить отправку?", text)

    def test_application_confirmation_is_reduced_to_phone_sized_action(self):
        text, _ = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": (
                    "RHP Properties — Software Engineer №4440635465 подготовлена. "
                    "Сильное совпадение по PHP, Laravel, бизнес-приложениям, API, "
                    "модернизации и AI-assisted разработке. Персональное PDF проверено "
                    "и запушено (e50a27f). Подтверди одним «да»: передать RHP Properties "
                    "контактные и профессиональные данные, загрузить персональный PDF, "
                    "правдиво ответить на обязательные вопросы, отключить подписку и "
                    "отправить двадцатую заявку?"
                ),
            }
        )
        self.assertIn("RHP Properties — Software Engineer №4440635465 подготовлена.", text)
        self.assertIn(
            "Заполнить форму, загрузить персональное резюме и отправить заявку?",
            text,
        )
        self.assertNotIn("e50a27f", text)
        self.assertNotIn("контактные и профессиональные данные", text)
        self.assertLess(len(text), 360)

    def test_email_code_confirmation_is_short(self):
        text, _ = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": (
                    "Greenhouse отправил восьмисимвольный код на почту. "
                    "Подтверди одним «да»: открыть почту, прочитать код, ввести его "
                    "и немедленно завершить отправку заявки №123?"
                ),
            }
        )
        self.assertIn(
            "Открыть почту, ввести код и завершить отправку заявки?",
            text,
        )

    def test_successful_callback_sends_persistent_receipt(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-456",
            cwd=self.temp_dir.name,
            question="Подтверждаешь отправку заявки?",
        )
        update = {
            "callback_query": {
                "id": "callback-1",
                "data": "yes:A1B2C3D4",
                "message": {"chat": {"id": "42"}},
            }
        }
        with mock.patch.object(
            bridge, "dispatch_to_codex", return_value="A1B2C3D4"
        ), mock.patch.object(
            bridge, "answer_callback"
        ) as answer, mock.patch.object(bridge, "send_telegram") as send:
            bridge.process_update("token", "42", update)

        answer.assert_called_once_with("token", "callback-1", "Отправлено в Codex")
        send.assert_called_once_with("✅ Принято. Ответ «Да» передан в Codex.")

    def test_english_confirmation_uses_english_ui(self):
        text, keyboard = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "thread-id": "thread-123",
                "turn-id": "turn-456",
                "cwd": "/tmp/example-project",
                "last-assistant-message": (
                    "Example Corp — Platform Engineer is ready. Please confirm that "
                    "I should upload the tailored resume and submit the application?"
                ),
            }
        )
        self.assertIn("⏸ Confirmation needed", text)
        self.assertIn(
            "Complete the form, upload the tailored resume, and submit?",
            text,
        )
        self.assertEqual(keyboard["inline_keyboard"][0][0]["text"], "✅ Yes")
        self.assertEqual(keyboard["inline_keyboard"][0][1]["text"], "⛔ No")

    def test_successful_plain_reply_sends_persistent_receipt(self):
        update = {"message": {"chat": {"id": "42"}, "text": "да"}}
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-456",
            cwd=self.temp_dir.name,
            question="Подтверждаешь отправку заявки?",
        )
        with mock.patch.object(
            bridge, "dispatch_to_codex", return_value="A1B2C3D4"
        ), mock.patch.object(
            bridge, "send_telegram"
        ) as send:
            bridge.process_update("token", "42", update)

        send.assert_called_once_with("✅ Принято. Ответ «Да» передан в Codex.")

    def test_english_negative_reply_receipt_is_localized(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-456",
            cwd=self.temp_dir.name,
            question="Confirm final submission?",
        )
        update = {"message": {"chat": {"id": "42"}, "text": "no"}}
        with mock.patch.object(
            bridge, "dispatch_to_codex", return_value="A1B2C3D4"
        ), mock.patch.object(bridge, "send_telegram") as send:
            bridge.process_update("token", "42", update)

        send.assert_called_once_with("⏸ Accepted. The No response was sent to Codex.")

    def test_new_confirmation_supersedes_older_one_in_same_thread(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-old",
            cwd=self.temp_dir.name,
            question="Подтверди отправку.",
        )
        bridge.store_pending(
            "E5F6A7B8",
            thread_id="thread-123",
            turn_id="turn-new",
            cwd=self.temp_dir.name,
            question="Подтверди загрузку резюме.",
        )

        state = bridge._read_state_unlocked()
        self.assertTrue(state["pending"]["A1B2C3D4"]["consumed"])
        self.assertEqual(state["pending"]["A1B2C3D4"]["response"], "superseded")
        self.assertFalse(state["pending"]["E5F6A7B8"]["consumed"])

    def test_completed_notification_contains_result(self):
        text, keyboard = bridge.build_notification(
            {
                "type": "agent-turn-complete",
                "cwd": "/tmp/example-project",
                "last-assistant-message": "Заявка отправлена и записана в трекер.",
            }
        )
        self.assertIn("Заявка отправлена и записана в трекер.", text)
        self.assertTrue(text.startswith("✅ Готово"))
        self.assertIsNone(keyboard)

    def test_notify_suppresses_routine_completed_result(self):
        notification = {
            "type": "agent-turn-complete",
            "cwd": "/tmp/example-project",
            "last-assistant-message": '{"description":"technical suggestion"}',
        }
        with mock.patch.object(bridge, "send_telegram") as send:
            result = bridge.notify(json.dumps(notification))

        self.assertEqual(result, 0)
        send.assert_not_called()

    def test_notify_keeps_actionable_confirmation(self):
        notification = {
            "type": "agent-turn-complete",
            "thread-id": "thread-123",
            "turn-id": "turn-456",
            "cwd": "/tmp/example-project",
            "last-assistant-message": "Подтверждаешь отправку заявки?",
        }
        with mock.patch.object(bridge, "send_telegram") as send:
            result = bridge.notify(json.dumps(notification))

        self.assertEqual(result, 0)
        send.assert_called_once()
        self.assertIn("Нужен ответ", send.call_args.args[0])
        self.assertIsNotNone(send.call_args.kwargs["reply_markup"])

    def test_plain_answer_targets_newest_pending_question(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-old",
            turn_id="turn-old",
            cwd=self.temp_dir.name,
            question="Старый вопрос?",
        )
        bridge.store_pending(
            "E5F6A7B8",
            thread_id="thread-new",
            turn_id="turn-new",
            cwd=self.temp_dir.name,
            question="Новый вопрос?",
        )

        pending_id, item = bridge.resolve_pending("yes", None)
        self.assertEqual(pending_id, "E5F6A7B8")
        self.assertEqual(item["question"], "Новый вопрос?")

    def test_single_letter_mobile_answers_are_accepted(self):
        self.assertEqual(bridge.parse_text_action("y"), ("yes", None))
        self.assertEqual(bridge.parse_text_action("Y"), ("yes", None))
        self.assertEqual(bridge.parse_text_action("n"), ("no", None))
        self.assertEqual(bridge.parse_text_action("N"), ("no", None))
        self.assertEqual(bridge.parse_text_action("д"), ("yes", None))
        self.assertEqual(bridge.parse_text_action("н"), ("no", None))

    def test_dispatch_queues_visible_message_into_active_thread(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-456",
            cwd=self.temp_dir.name,
            question="Подтверждаешь отправку заявки?",
        )
        with mock.patch.object(bridge, "CODEX_BINARY", Path("/bin/echo")), mock.patch.object(
            bridge.subprocess, "run", return_value=mock.Mock(stdout="queued", stderr="")
        ) as run:
            resolved = bridge.dispatch_to_codex("yes", "A1B2C3D4")

        self.assertEqual(resolved, "A1B2C3D4")
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["queue", "--thread", "thread-123"])
        self.assertEqual(command[4], "--message")
        self.assertEqual(command[5], "Да. Продолжай последнее ожидающее действие.")
        state = bridge._read_state_unlocked()
        self.assertTrue(state["pending"]["A1B2C3D4"]["consumed"])

    def test_failed_queue_keeps_confirmation_retriable(self):
        bridge.store_pending(
            "A1B2C3D4",
            thread_id="thread-123",
            turn_id="turn-456",
            cwd=self.temp_dir.name,
            question="Подтверждаешь отправку заявки?",
        )
        failure = bridge.subprocess.CalledProcessError(1, ["codex", "queue"], stderr="busy")
        with mock.patch.object(bridge, "CODEX_BINARY", Path("/bin/echo")), mock.patch.object(
            bridge.subprocess, "run", side_effect=failure
        ):
            with self.assertRaises(RuntimeError):
                bridge.dispatch_to_codex("yes", "A1B2C3D4")

        state = bridge._read_state_unlocked()
        self.assertFalse(state["pending"]["A1B2C3D4"]["consumed"])


if __name__ == "__main__":
    unittest.main()
