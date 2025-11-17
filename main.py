"""Entry point for running the Mafia Telegram bot.

Цей файл лише збирає застосунок, підключає хендлери
і запускає long polling. Увесь ігровий код винесений
в окремі модулі config.py, game_state.py та handlers.py.
"""

import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from handlers import (
    start,
    newgame_command,
    status_command,
    endgame_command,
    help_command,
    button_callback,
    check_dead_player_message,
    error_handler,
)


logger = logging.getLogger(__name__)


def main() -> None:
    """Головна функція запуску бота"""
    # Токен тепер безпечніше зчитується з змінної оточення
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN or TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("❌ TELEGRAM_BOT_TOKEN не встановлено!")
        logger.error("Вкажіть токен у змінній оточення TELEGRAM_BOT_TOKEN.")
        raise SystemExit(1)

    application = Application.builder().token(TOKEN).build()

    # Команди
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("newgame", newgame_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("endgame", endgame_command))
    application.add_handler(CommandHandler("help", help_command))

    # Кнопки
    application.add_handler(CallbackQueryHandler(button_callback))

    # Блокування повідомлень від мертвих гравців у групах
    application.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS,
        check_dead_player_message,
    ))

    # Глобальний обробник помилок
    application.add_error_handler(error_handler)

    logger.info("🎭 Бот МАФІЯ запущено!")
    logger.info("📱 Натисніть Ctrl+C для зупинки")
    logger.info("🎮 Відкрийте Telegram і напишіть /start вашому боту!")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
