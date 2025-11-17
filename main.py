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

# Виправлено імпорти - тепер відповідають дійсним функціям в handlers.py
from handlers import (
    start,
    newgame,
    status,
    endgame,
    join_game_callback,
    add_bots_menu_callback,
    add_bots_callback,
    leave_game_callback,
    start_game_callback,
    back_to_game_callback,
    night_action_callback,
    vote_callback,
    potato_callback,
    check_dead_player_message,
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

    if application.job_queue is None:
        logger.warning("⏱️ JobQueue недоступний — таймери гри не зможуть працювати.")
        logger.warning('Встановіть залежність: pip install "python-telegram-bot[job-queue]"')
        logger.warning("Бот продовжує роботу, але фази потрібно завершувати вручну.")

    # Реєстрація команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("newgame", newgame))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("endgame", endgame))
    
    # Реєстрація колбеків
    application.add_handler(CallbackQueryHandler(join_game_callback, pattern="^join_game$"))
    application.add_handler(CallbackQueryHandler(add_bots_menu_callback, pattern="^add_bots_menu$"))
    application.add_handler(CallbackQueryHandler(add_bots_callback, pattern="^add_bots_"))
    application.add_handler(CallbackQueryHandler(leave_game_callback, pattern="^leave_game$"))
    application.add_handler(CallbackQueryHandler(start_game_callback, pattern="^start_game$"))
    application.add_handler(CallbackQueryHandler(back_to_game_callback, pattern="^back_to_game$"))
    
    # Нічні дії та голосування
    application.add_handler(CallbackQueryHandler(night_action_callback, pattern="^night_(kill|heal|check)_"))
    application.add_handler(CallbackQueryHandler(vote_callback, pattern="^(nominate|votefor)_"))
    application.add_handler(CallbackQueryHandler(potato_callback, pattern="^potato_"))
    
    # Блокування повідомлень від мертвих
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_dead_player_message))

    # Запуск бота
    logger.info("🚀 Запуск бота Mafia...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()