"""Telegram handlers and game flow for the Mafia bot.

Повний функціонал:
- Реєстрація гравців та ботів
- Нічна фаза з таймером (налаштовується)
- Денна фаза з обговоренням (налаштовується)
- Голосування за виключення
- Логіка ботів (мафія/лікар/мирні)
- Спеціальні події (Буковель + картопля)
- GIF анімації
- Перки (5% шанс)
"""

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
from collections import defaultdict
import asyncio
import random
from typing import Optional, List, Tuple

from config import (
    ROLES, DEATH_PHRASES, SAVED_PHRASES, MAFIA_PHRASES,
    DISCUSSION_PHRASES, MORNING_PHRASES, NIGHT_PHRASES,
    POTATO_PHRASES, SPECIAL_EVENTS, GIF_PATHS, TIMERS
)
from game_state import mafia_game

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Перевіряємо наявність GIF файлів одразу при завантаженні модуля
for gif_type, gif_path in GIF_PATHS.items():
    if not os.path.exists(gif_path):
        logger.warning(f"⚠️ GIF не знайдено: {gif_type} -> {gif_path}")


_JOB_QUEUE_WARNED = False


BOT_NIGHT_MESSAGES = [
    "🤖 Нічні тіні ворушаться у темряві...",
    "🌒 Хтось робить хід у тиші ночі...",
    "🕯 Таємничі шепоти лунають у темряві...",
    "🌫 У темряві чутно приглушені кроки...",
    "😶‍🌫️ Село занурюється в напругу ночі..."
]

PERKS_DIVIDER = "━━━━━━━━━━━━━━"


async def _announce_hidden_potato_throw(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, target_name: str
) -> None:
    """Надсилає нейтральне повідомлення про кидок картоплі без розкриття особи."""

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🥔 <i>Хтось кинув картоплю в <b>{target_name}</b>!</i>",
        parse_mode=ParseMode.HTML,
    )
BOT_ACTION_MESSAGES = {
    'kill': [
        "🔫 Мафія зробила свій вибір...",
        "🌙 Мафія обирає жертву...",
        "😈 Темна справа у розпалі..."
    ],
    'heal': [
        "💉 Лікар зробив свій вибір...",
        "🏥 Федорчак уже працює...",
        "⚕️ Швидка допомога на місці..."
    ],
    'check': [
        "🔍 Детектив шукає правду...",
        "🕵️ Детектив проводить розслідування...",
        "🔦 Хтось шукає відповіді..."
    ],
    'shoot': [
        "💥 Детектив готує зброю...",
        "⚡ Наближається постріл справедливості...",
        "🔫 Справедливість незабаром восторжествує..."
    ]
}


# ============================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================

def _get_job_queue(context: ContextTypes.DEFAULT_TYPE):
    """Повертає JobQueue або логувує підказку щодо встановлення залежності."""
    global _JOB_QUEUE_WARNED
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None and not _JOB_QUEUE_WARNED:
        logger.error(
            "⏱️ JobQueue недоступний. Встановіть додаткову залежність: "
            'pip install "python-telegram-bot[job-queue]"'
        )
        _JOB_QUEUE_WARNED = True
    return job_queue


async def _notify_missing_scheduler(context: ContextTypes.DEFAULT_TYPE, chat_id: int, game: dict):
    """Попереджає чат про відсутність таймерів (показує лише один раз)."""
    if game.get('job_queue_missing_notified'):
        return

    game['job_queue_missing_notified'] = True
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚠️ <b>Автоматичні таймери недоступні.</b>\n\n"
            "Встановіть залежність <code>python-telegram-bot[job-queue]</code>,\n"
            "щоб нічні та денні фази завершувались автоматично."
        ),
        parse_mode=ParseMode.HTML,
    )



# ============================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================

def _merge_players(game: dict) -> dict:
    """Комбінує гравців та ботів у один словник"""
    combined = dict(game['players'])
    combined.update(game['bots'])
    return combined


def _cancel_jobs(job_queue, name: str):
    """Видаляє всі задачі з певною назвою, щоб уникнути подвійного виконання"""
    if job_queue is None:
        return
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def send_gif(context: ContextTypes.DEFAULT_TYPE, chat_id: int, gif_type: str, caption: str = None):
    """Відправка GIF файлу"""
    try:
        gif_path = GIF_PATHS.get(gif_type)
        if gif_path and os.path.exists(gif_path):
            with open(gif_path, 'rb') as gif:
                await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=gif,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
                return
        # Якщо GIF не знайдено, просто текст
        if caption:
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Помилка відправки GIF: {e}")
        if caption:
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode=ParseMode.HTML
            )


async def send_potato_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Надсилає меню кидка картоплі для власників бульби"""
    game = mafia_game.games[chat_id]

    if game.get('special_event') != 'bukovel':
        return

    all_players = mafia_game.get_all_players(chat_id)
    alive_targets = [
        (uid, pinfo) for uid, pinfo in all_players.items() if pinfo['alive']
    ]

    if len(alive_targets) <= 1:
        return

    event_info = SPECIAL_EVENTS['bukovel']

    for user_id, player_info in game['players'].items():
        if not player_info['alive']:
            continue

        if mafia_game.get_player_item(chat_id, user_id) != 'potato':
            continue

        if user_id in game['potato_throws']:
            continue

        keyboard = []
        for target_id, target_info in alive_targets:
            if target_id == user_id:
                continue

            bot_mark = "🤖 " if target_info['is_bot'] else ""
            keyboard.append([InlineKeyboardButton(
                f"🥔 {bot_mark}{target_info['username']}",
                callback_data=f"potato_throw_{chat_id}_{target_id}"
            )])

        if not keyboard:
            continue

        keyboard.append([InlineKeyboardButton(
            "🚫 Приберегти картоплю",
            callback_data=f"potato_throw_{chat_id}_0"
        )])

        text = (
            "🥔 <b>БУКОВЕЛЬСЬКА КАРТОПЛЯ!</b>\n\n"
            f"{event_info['item_description']}\n"
            "🎯 Шанс вбити: 20%\n\n"
            "Виберіть ціль або збережіть картоплю."
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка надсилання меню картоплі {user_id}: {e}")


async def check_dead_player_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Блокує повідомлення від мертвих гравців"""
    if not update.message or not update.message.text:
        return
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    if chat_id in mafia_game.games:
        game = mafia_game.games[chat_id]
        if game['started'] and user_id in game['players'] and not game['players'][user_id]['alive']:
            try:
                await update.message.delete()
                await context.bot.send_message(
                    chat_id=user_id,
                    text="💀 <b>ТИ МЕРТВИЙ!</b>\n\nНе можеш писати в чат до кінця гри.\n🤐 Дотримуйся правил, мертвяк!",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Помилка видалення повідомлення мертвого: {e}")


# ============================================
# ЛОГІКА БОТІВ
# ============================================

def bot_mafia_choice(game: dict, bot_id: int) -> Optional[int]:
    """Мафія вибирає жертву випадково з живих мирних (включно з ботами)"""
    all_players = _merge_players(game)
    citizens = [
        uid for uid, pinfo in all_players.items()
        if pinfo['alive']
        and uid != bot_id
        and mafia_game.get_role_info(pinfo['role'])['team'] == 'citizens'
    ]
    return random.choice(citizens) if citizens else None


def bot_doctor_choice(game: dict, bot_id: int) -> Optional[int]:
    """Лікар лікує випадково (враховуючи всіх живих) і не себе двічі поспіль"""
    all_players = _merge_players(game)
    targets = [
        uid for uid, pinfo in all_players.items()
        if pinfo['alive'] and (uid != bot_id or game['last_healed'] != bot_id)
    ]
    return random.choice(targets) if targets else None


def bot_voting_choice(game: dict, bot_id: int) -> int:
    """Бот голосує за гравця з 2+ голосами або за випадкового живого"""
    # Підраховуємо голоси
    vote_counts = defaultdict(int)
    for voted_for in game['votes'].values():
        if voted_for != 0:
            vote_counts[voted_for] += 1
    
    # Якщо є кандидат з 2+ голосами
    candidates = [uid for uid, count in vote_counts.items() if count >= 2]
    if candidates:
        return random.choice(candidates)
    
    # Інакше випадковий живий гравець (крім себе)
    all_players = _merge_players(game)
    alive = [
        uid for uid, pinfo in all_players.items()
        if pinfo['alive'] and uid != bot_id
    ]
    return random.choice(alive) if alive else 0


async def process_bot_actions(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка дій ботів під час нічної фази"""
    game = mafia_game.games[chat_id]
    
    for bot_id, bot_info in game['bots'].items():
        if not bot_info['alive']:
            continue
        
        role_key = bot_info['role']
        role_info = mafia_game.get_role_info(role_key)
        action = role_info.get('action')
        
        if not action:
            continue
        
        target = None
        if action == 'kill':
            target = bot_mafia_choice(game, bot_id)
        elif action == 'heal':
            target = bot_doctor_choice(game, bot_id)
        # Детектив ботам не випадає
        
        if target:
            game['night_actions'][bot_id] = {
                'action': action,
                'target': target
            }
            
            await asyncio.sleep(random.uniform(1, 3))  # Імітація "думання"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=random.choice(BOT_NIGHT_MESSAGES),
            message_options = BOT_ACTION_MESSAGES.get(action)
            if message_options:
                info_text = random.choice(message_options)
            else:
                info_text = "🤖 Бот завершив свій хід..."

            await context.bot.send_message(
                chat_id=chat_id,
                text=info_text,
                parse_mode=ParseMode.HTML
            )

    # Боти також можуть кидати картоплю під час події "Буковель"
    if game.get('special_event') == 'bukovel':
        all_players = mafia_game.get_all_players(chat_id)

        for bot_id, bot_info in game['bots'].items():
            if not bot_info['alive']:
                continue

            if mafia_game.get_player_item(chat_id, bot_id) != 'potato':
                continue

            if bot_id in game['potato_throws']:
                continue

            if random.random() >= 0.5:
                continue

            alive_targets = [
                uid for uid, pinfo in all_players.items()
                if pinfo['alive'] and uid != bot_id
            ]

            if not alive_targets:
                continue

            target = random.choice(alive_targets)

            if mafia_game.use_potato(chat_id, bot_id, target):
                target_name = all_players[target]['username']
                await _announce_hidden_potato_throw(
                    context, chat_id, target_name
                bot_name = bot_info['username']
                target_name = all_players[target]['username']

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🥔 <i>Хтось кинув картоплю в <b>{target_name}</b>!</i>",
                    text=f"🤖🥔 Один із ботів кинув картоплю в <b>{target_name}</b>!",
                    text=f"🤖🥔 <b>{bot_name}</b> кинув картоплю в <b>{target_name}</b>!",
                    parse_mode=ParseMode.HTML
                )


async def process_bot_votes(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка голосів ботів за висунення кандидата"""
    game = mafia_game.games[chat_id]

    for bot_id, bot_info in game['bots'].items():
        if not bot_info['alive']:
            continue
        
        await asyncio.sleep(random.uniform(1, 2))
        
        choice = bot_voting_choice(game, bot_id)
        game['votes'][bot_id] = choice
        
        bot_name = bot_info['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{bot_name}</b> висунув кандидата!",
            parse_mode=ParseMode.HTML
        )

    await check_nominations_complete(context, chat_id)


async def process_bot_final_votes(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Боти голосують ЗА/ПРОТИ випадково"""
    game = mafia_game.games[chat_id]

    for bot_id, bot_info in game['bots'].items():
        if not bot_info['alive']:
            continue
        
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        vote = random.choice(['yes', 'no'])
        game['vote_results'][bot_id] = vote
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="🤖 Один із ботів проголосував!",
            parse_mode=ParseMode.HTML
        )

    await check_final_voting_complete(context, chat_id)


# ============================================
# КОМАНДИ /start, /newgame, /status, /endgame
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    if update.message and update.message.chat.type != 'private':
        await update.message.reply_text(
            "👋 <b>Вітаю в грі МАФІЯ!</b>\n\n"
            "📝 Щоб керувати грою, напишіть мені /start в особистих повідомленнях!\n"
            "🎮 Команди в групі:\n"
            "   /newgame - створити нову гру\n"
            "   /endgame - завершити поточну гру\n"
            "   /status - статус гри\n\n"
            "💡 <b>Важливо:</b> Спочатку напишіть боту /start в особисті повідомлення!",
            parse_mode=ParseMode.HTML
        )
        return
    
    keyboard = [
        [InlineKeyboardButton("📜 Правила гри", callback_data="menu_rules")],
        [InlineKeyboardButton("🎮 Як грати?", callback_data="menu_howto")],
        [InlineKeyboardButton("👥 Персонажі", callback_data="menu_characters")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = """
🎭 <b>МАФІЯ - Telegram Bot</b> 🎭

Вітаю в класичній грі МАФІЯ!

<b>⭐ Головні персонажі:</b>
🌾 <b>Демян</b> - Простий селянин
👑 <b>Кішкель</b> - Дон мафії (імунітет)
🔫 <b>Ігор Рогальський</b> - Мафіозі
💉 <b>Федорчак</b> - Лікар-рятівник
🔍 <b>Детектив</b> - Шукач правди (+ 1 куля)

<b>🎯 Мета:</b>
🔵 Мирні - знищити мафію
🔴 Мафія - знищити мирних

<b>🎲 Нові фічі:</b>
- 🤖 Можна додати ботів (1-10)
- 🥔 Спеціальні події (Буковель!)
- 🔫 Детектив має кулю
- 🎪 Рандомні перки (5%)

Оберіть розділ нижче! 👇
"""
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /newgame"""
    if update.message.chat.type == 'private':
        await update.message.reply_text(
            "⚠️ Ця команда працює тільки в груповому чаті!",
            parse_mode=ParseMode.HTML
        )
        return
    
    chat_id = update.message.chat_id
    admin_id = update.message.from_user.id
    
    if chat_id in mafia_game.games and mafia_game.games[chat_id]['started']:
        await update.message.reply_text(
            "⚠️ <b>Гра вже йде!</b>\n\nЗавершіть поточну гру командою /endgame",
            parse_mode=ParseMode.HTML
        )
        return
    
    game = mafia_game.create_game(chat_id, admin_id)
    
    event_text = ""
    if game['special_event']:
        event_info = SPECIAL_EVENTS[game['special_event']]
        event_text = f"\n\n🎲 <b>СПЕЦІАЛЬНА ПОДІЯ!</b>\n{event_info['emoji']} <b>{event_info['name']}</b>\n<i>{event_info['description']}</i>\n"
    
    announcement_keyboard = [
        [InlineKeyboardButton("➕ ПРИЄДНАТИСЯ", callback_data="join_game")],
        [InlineKeyboardButton("🤖 ДОДАТИ БОТІВ", callback_data="add_bots_menu")],
        [InlineKeyboardButton("🎯 ПОЧАТИ ГРУ", callback_data="start_game")],
        [InlineKeyboardButton("❌ ВИЙТИ", callback_data="leave_game")],
    ]
    
    announcement_text = f"""
🎮 <b>━━━ НОВА ГРА СТВОРЕНА! ━━━</b> 🎮

🎭 <b>МАФІЯ</b> запрошує гравців!{event_text}

<b>🎯 Правила:</b>
- Мінімум 5 учасників (гравці + боти)
- Максимум 15 учасників
- Можна додати ботів (1-10)

<b>👥 Гравці (0/15):</b>
<i>Поки що немає...</i>

<b>🤖 Ботів: 0</b>

<b>⚠️ ВАЖЛИВО:</b> Напишіть /start боту в особисті повідомлення!

👇 <b>ПРИЄДНУЙТЕСЬ!</b> 👇
"""
    
    msg = await update.message.reply_text(
        announcement_text,
        reply_markup=InlineKeyboardMarkup(announcement_keyboard),
        parse_mode=ParseMode.HTML
    )
    
    mafia_game.game_messages[chat_id] = msg.message_id


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status"""
    chat_id = update.message.chat_id
    
    if chat_id not in mafia_game.games:
        await update.message.reply_text(
            "⚠️ Активна гра не знайдена!\n\nСтворіть нову гру командою /newgame",
            parse_mode=ParseMode.HTML
        )
        return
    
    game = mafia_game.games[chat_id]
    
    phase_names = {
        'registration': '📝 Реєстрація',
        'night': f'🌙 Ніч {game["day_number"]}',
        'day': f'☀️ День {game["day_number"]}',
        'discussion': f'🗣 Обговорення дня {game["day_number"]}',
        'voting': f'🗳 Голосування дня {game["day_number"]}',
        'ended': '🏁 Гра завершена'
    }
    
    all_players = mafia_game.get_all_players(chat_id)
    status_text = f"""
📊 <b>━━━ СТАТУС ГРИ ━━━</b> 📊

<b>🎮 Фаза:</b> {phase_names.get(game['phase'], 'Невідомо')}
<b>👥 Всього учасників:</b> {len(all_players)}
<b>🤖 З них ботів:</b> {len(game['bots'])}
"""
    
    if game['started']:
        status_text += f"<b>💚 Живих:</b> {len(game['alive_players'])}\n"
        status_text += f"<b>📅 День №:</b> {game['day_number']}\n"
        status_text += f"<b>🔫 Куля детектива:</b> {'Використана' if game['detective_bullet_used'] else 'Є'}\n\n"
        
        status_text += "<b>👥 Учасники:</b>\n"
        for i, (uid, pinfo) in enumerate(all_players.items(), 1):
            status_emoji = "✅" if pinfo['alive'] else "💀"
            bot_emoji = "🤖 " if pinfo['is_bot'] else ""
            status_text += f"{i}. {status_emoji} {bot_emoji}{pinfo['username']}\n"
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)


async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /endgame"""
    if update.message.chat.type == 'private':
        await update.message.reply_text(
            "⚠️ Ця команда працює тільки в груповому чаті!",
            parse_mode=ParseMode.HTML
        )
        return
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    if chat_id not in mafia_game.games:
        await update.message.reply_text(
            "⚠️ Активна гра не знайдена!",
            parse_mode=ParseMode.HTML
        )
        return
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in ['creator', 'administrator']:
            await update.message.reply_text(
                "⚠️ Тільки адміністратори можуть завершити гру!",
                parse_mode=ParseMode.HTML
            )
            return
    except Exception:
        pass
    
    del mafia_game.games[chat_id]
    if chat_id in mafia_game.game_messages:
        del mafia_game.game_messages[chat_id]
    
    await update.message.reply_text(
        "🏁 <b>ГРУ ЗАВЕРШЕНО!</b>\n\nСтворіть нову гру командою /newgame 🎮",
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
🎮 <b>━━━ КОМАНДИ БОТА ━━━</b> 🎮

<b>📝 В ГРУПОВОМУ ЧАТІ:</b>
/newgame - Створити нову гру
/status - Статус гри
/endgame - Завершити гру (адміни)

<b>💬 В ОСОБИСТИХ ПОВІДОМЛЕННЯХ:</b>
/start - Головне меню
/help - Ця довідка

<b>🎯 ЯК ПОЧАТИ:</b>
1. Додайте бота в групу
2. Напишіть /start боту в ЛС
3. В групі /newgame
4. Приєднуйтесь через кнопку
5. Додайте ботів (опціонально)
6. Адмін запускає гру

<b>🎲 ФІЧІ:</b>
- 🤖 Боти з логікою
- 🥔 Буковель з картоплею
- 🔫 Куля детектива
- 🎪 Рандомні перки

<b>🎭 Приємної гри!</b>
"""
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


# ============================================
# РЕЄСТРАЦІЯ ГРАВЦІВ ТА БОТІВ
# ============================================

async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приєднання до гри"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Створіть гру командою /newgame!", show_alert=True)
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    if len(all_players) >= 15:
        await query.answer("⚠️ Гра повна! Максимум 15 учасників.", show_alert=True)
        return
    
    if mafia_game.add_player(chat_id, user_id, username):
        try:
            welcome_msg = f"""
✅ <b>ВИ В ГРІ!</b> 🎉

Вітаємо, <b>{username}</b>!

🎮 <b>Що далі?</b>
- Чекайте на початок
- Отримаєте роль в ЛС
- Грайте та перемагайте!

<b>👥 Гравців:</b> {len(game['players'])}
<b>🤖 Ботів:</b> {len(game['bots'])}

<i>Удачі! 🍀</i>
"""
            await context.bot.send_message(
                chat_id=user_id,
                text=welcome_msg,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Не можу надіслати повідомлення {user_id}: {e}")
            del game['players'][user_id]
            await query.answer(
                "⚠️ Напишіть мені /start в ЛС спочатку!",
                show_alert=True
            )
            return
        
        await update_game_message(context, chat_id)
        await query.answer(f"✅ {username} приєднався! 🎉")
    else:
        await query.answer("⚠️ Ви вже в грі!", show_alert=True)


async def add_bots_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню додавання ботів"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    available_slots = 15 - len(all_players)
    
    if available_slots <= 0:
        await query.answer("⚠️ Гра повна!", show_alert=True)
        return
    
    keyboard = []
    for i in [1, 2, 3, 5, 10]:
        if i <= available_slots:
            keyboard.append([InlineKeyboardButton(
                f"🤖 Додати {i} бот{'а' if i in [2, 3, 4] else 'ів' if i > 4 else ''}",
                callback_data=f"add_bots_{i}"
            )])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_game")])
    
    await query.edit_message_text(
        f"🤖 <b>ДОДАТИ БОТІВ</b>\n\n"
        f"👥 Гравців: {len(game['players'])}\n"
        f"🤖 Ботів: {len(game['bots'])}\n"
        f"📊 Вільно: {available_slots}\n\n"
        f"<b>Скільки додати?</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )


async def add_bots_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додавання ботів"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    count = int(data[2])
    chat_id = query.message.chat_id
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return
    
    added = mafia_game.add_bots(chat_id, count)
    
    if added > 0:
        await query.answer(f"✅ Додано {added} бот{'а' if added in [2, 3, 4] else 'ів'}!", show_alert=True)
        await update_game_message(context, chat_id)
        
        game = mafia_game.games[chat_id]
        bot_names = [b['username'] for b in game['bots'].values()]
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>Боти приєднались!</b>\n\n"
                 f"🎭 {', '.join(bot_names)}\n\n"
                 f"<i>Можна починати!</i>",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.answer("⚠️ Не вдалось додати ботів!", show_alert=True)


async def leave_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вихід з гри"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return
    
    if mafia_game.remove_player(chat_id, user_id):
        await update_game_message(context, chat_id)
        await query.answer(f"👋 {username} вийшов")
    else:
        await query.answer("⚠️ Ви не в грі або вона вже почалась!", show_alert=True)


async def update_game_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Оновлення повідомлення про гру"""
    if chat_id not in mafia_game.games or chat_id not in mafia_game.game_messages:
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    event_text = ""
    if game['special_event']:
        event_info = SPECIAL_EVENTS[game['special_event']]
        event_text = f"\n\n🎲 <b>{event_info['emoji']} {event_info['name']}</b>\n<i>{event_info['description']}</i>"
    
    announcement_keyboard = [
        [InlineKeyboardButton("➕ ПРИЄДНАТИСЯ", callback_data="join_game")],
        [InlineKeyboardButton("🤖 ДОДАТИ БОТІВ", callback_data="add_bots_menu")],
        [InlineKeyboardButton("🎯 ПОЧАТИ ГРУ", callback_data="start_game")],
        [InlineKeyboardButton("❌ ВИЙТИ", callback_data="leave_game")],
    ]
    
    players_list = ""
    if game['players']:
        players_list += "<b>👥 Гравці:</b>\n"
        for i, pinfo in enumerate(game['players'].values(), 1):
            players_list += f"   {i}. ✅ {pinfo['username']}\n"
    
    if game['bots']:
        players_list += f"\n<b>🤖 Боти ({len(game['bots'])}):</b>\n"
        for i, binfo in enumerate(game['bots'].values(), 1):
            players_list += f"   {i}. 🤖 {binfo['username']}\n"
    
    if not players_list:
        players_list = "<i>Поки що немає...</i>"
    
    total = len(all_players)
    updated_text = f"""
🎮 <b>ГРА: МАФІЯ</b> 🎮{event_text}

<b>📊 Учасників ({total}/15):</b>
{players_list}

{'⏳ <b>Потрібно ще ' + str(5 - total) + ' учасників</b>' if total < 5 else '🔥 <b>Можна починати!</b>'}

<b>⚠️ ВАЖЛИВО:</b> Напишіть /start боту в ЛС!

👇 <b>ПРИЄДНУЙТЕСЬ!</b> 👇
"""
    
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=mafia_game.game_messages[chat_id],
            text=updated_text,
            reply_markup=InlineKeyboardMarkup(announcement_keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Помилка оновлення повідомлення: {e}")


async def back_to_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повернення до меню гри"""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    await update_game_message(context, chat_id)


# ============================================
# ПОЧАТОК ГРИ
# ============================================

async def start_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок гри"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return

    game = mafia_game.games[chat_id]

    # Перевірка прав
    if user_id != game['admin_id']:
        try:
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            if chat_member.status not in ['creator', 'administrator']:
                await query.answer("⚠️ Тільки адміни можуть почати гру!", show_alert=True)
                return
        except Exception:
            await query.answer("⚠️ Тільки адміни можуть почати гру!", show_alert=True)
            return

    all_players = mafia_game.get_all_players(chat_id)
    if len(all_players) < 5:
        await query.answer(
            f"⚠️ Недостатньо учасників!\nПотрібно: 5\nЄ: {len(all_players)}", 
            show_alert=True
        )
        return

    if game.get('started'):
        await query.answer("⚠️ Гра вже почалась!", show_alert=True)
        return

    if not mafia_game.assign_roles(chat_id):
        await query.answer("❌ Помилка розподілу ролей!", show_alert=True)
        return

    game['phase'] = 'night'
    game['day_number'] = 1
    game['night_actions'] = {}
    game['detective_shot_this_night'] = None
    game['detective_error_target'] = None
    game['rope_break_save'] = None
    game['mafia_misfire'] = False
    game['perks_messages'] = []
    game['night_resolved'] = False

    await query.edit_message_text(
        "🎭 <b>ГРА ПОЧИНАЄТЬСЯ!</b> 🎭\n\n"
        "⏳ Розподіляємо ролі...\n"
        "📨 Перевірте особисті повідомлення!\n\n"
        "🌙 Настає перша ніч...", 
        parse_mode=ParseMode.HTML
    )

    # Надсилання ролей ТІЛЬКИ живим гравцям (не ботам)
    failed_users = []
    for uid, player_info in list(game['players'].items()):
        try:
            role_info = mafia_game.get_role_info(player_info['role'])
            team_emoji = "🔴" if role_info['team'] == 'mafia' else "🔵"
            team_name = "<b>МАФІЯ</b>" if role_info['team'] == 'mafia' else "<b>МИРНІ ЖИТЕЛІ</b>"

            role_text = f"""
🎭 <b>━━━ ВАША РОЛЬ ━━━</b> 🎭

{role_info['emoji']} <b>{role_info['full_name']}</b>

📝 <b>Опис:</b>
{role_info['description']}

{team_emoji} <b>Команда:</b> {team_name}

{'🔪 <b>Ви граєте за мафію!</b> Знищуйте мирних!' if role_info['team'] == 'mafia' else '⚔️ <b>Ви граєте за мирних!</b> Шукайте мафію!'}

⏳ Чекайте на початок ночі...
"""

            if role_info['team'] == 'mafia':
                mafia_members = mafia_game.get_mafia_members(chat_id)
                mafia_list = "\n".join([
                    f"   • {pinfo['username']} ({mafia_game.get_role_info(pinfo['role'])['name']})"
                    for m_uid, pinfo in mafia_members if m_uid != uid
                ])
                if mafia_list:
                    role_text += f"\n\n🤝 <b>Ваші союзники:</b>\n{mafia_list}"

            await context.bot.send_message(
                chat_id=uid,
                text=role_text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка надсилання ролі {uid}: {e}") 
            failed_users.append(uid)

    # Видаляємо тих, кому не вдалося надіслати
    if failed_users:
        for uid in failed_users:
            game['players'].pop(uid, None)
            game['alive_players'].discard(uid)

        all_remaining = mafia_game.get_all_players(chat_id)
        if len(all_remaining) < 5:
            game['started'] = False
            game['phase'] = 'registration'
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ <b>Гра не може бути продовжена.</b>\n\n"
                    "Деяким гравцям не вдалося надіслати ролі.\n"
                    "Після видалення залишилось < 5 учасників.\n\n"
                    "🙋 Попросіть усіх написати /start боту в ЛС та створіть нову гру."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        # Якщо гравців все ще достатньо — повертаємо гру до стадії реєстрації
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ <b>Роздачу ролей скасовано.</b>\n\n"
                "Не всім вдалося надіслати ЛС, тому ролі не розкриті.\n"
                "Гру повернуто до реєстрації — спробуйте почати її знову,"
                " коли всі гравці активують бота."
            ),
            parse_mode=ParseMode.HTML
        )

        game['started'] = False
        game['phase'] = 'registration'
        game['day_number'] = 0
        game['night_actions'] = {}
        game['votes'] = {}
        game['vote_nominee'] = None
        game['vote_results'] = {}
        game['night_resolved'] = False
        game['nominations_done'] = False
        game['final_voting_done'] = False
        game['discussion_started'] = False
        game['detective_bullet_used'] = False
        game['detective_shot_this_night'] = None
        game['perks_messages'] = []
        game['potato_throws'] = {}
        game['special_items'] = {}

        for player_info in game['players'].values():
            player_info['role'] = None
            player_info['alive'] = True

        for bot_info in game['bots'].values():
            bot_info['role'] = None
            bot_info['alive'] = True

        game['alive_players'] = set()

        await update_game_message(context, chat_id)
        return

    # Оновлюємо живих
    game['alive_players'] = {uid for uid, p in mafia_game.get_all_players(chat_id).items() if p['alive']}

    # Запускаємо першу ніч
    await night_phase(context, chat_id)


# ============================================
# НІЧНА ФАЗА
# ============================================

async def night_phase(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Нічна фаза з таймером"""
    if chat_id not in mafia_game.games:
        return

    game = mafia_game.games[chat_id]
    game['phase'] = 'night'
    game['night_actions'] = {}
    game['detective_shot_this_night'] = None
    game['perks_messages'] = []
    game['night_resolved'] = False
    game['potato_throws'] = {}

    night_phrase = random.choice(NIGHT_PHRASES)
    night_duration = TIMERS['night']
    
    await send_gif(
        context, 
        chat_id, 
        'night',
        f"🌙 <b>━━━ НІЧ {game['day_number']} ━━━</b> 🌙\n\n"
        f"{night_phrase}\n\n"
        f"⏳ У вас є {night_duration} секунд на дії...\n"
        f"🤫 {random.choice(MAFIA_PHRASES)}"
    )

    # Таймер
    job_queue = _get_job_queue(context)
    if job_queue:
        job_queue.run_once(
            night_timeout,
            when=night_duration,
            chat_id=chat_id,
            name=f"night_{chat_id}"
        )
    else:
        await _notify_missing_scheduler(context, chat_id, game)
    context.job_queue.run_once(
        night_timeout,
        when=night_duration,
        chat_id=chat_id,
        name=f"night_{chat_id}"
    )

    # Розсилка дій живим гравцям
    for user_id, player_info in game['players'].items():
        if not player_info['alive']:
            continue

        role_key = player_info['role']
        role_info = mafia_game.get_role_info(role_key)
        action = role_info.get('action')

        if not action:
            continue

        all_players = mafia_game.get_all_players(chat_id)

        # Формуємо список цілей
        if action == 'kill':
            targets = [
                (uid, pinfo) for uid, pinfo in all_players.items()
                if pinfo['alive'] and mafia_game.get_role_info(pinfo['role'])['team'] == 'citizens'
            ]
        elif action == 'heal':
            targets = [
                (uid, pinfo) for uid, pinfo in all_players.items()
                if pinfo['alive'] and (uid != user_id or game['last_healed'] != user_id)
            ]
        else:  # check
            targets = [
                (uid, pinfo) for uid, pinfo in all_players.items()
                if pinfo['alive'] and uid != user_id
            ]

        if not targets:
            continue

        keyboard = []
        for target_id, target_info in targets:
            button_emoji = {'kill': '💀', 'heal': '💉', 'check': '🔍'}.get(action, '👤')
            bot_mark = "🤖 " if target_info['is_bot'] else ""
            keyboard.append([InlineKeyboardButton(
                f"{button_emoji} {bot_mark}{target_info['username']}",
                callback_data=f"night_{action}_{chat_id}_{target_id}"
            )])

        # Детектив: кнопка стрільби
        if action == 'check' and not game['detective_bullet_used']:
            keyboard.append([InlineKeyboardButton(
                "🔫 ВИСТРІЛИТИ (1 куля)",
                callback_data=f"night_shoot_{chat_id}_menu"
            )])

        reply_markup = InlineKeyboardMarkup(keyboard)

        action_texts = {
            'kill': (
                "🔫 <b>━━━ ВИБІР ЖЕРТВИ ━━━</b> 🔫\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                f"{random.choice(MAFIA_PHRASES)}\n\n"
                "Оберіть жертву:"
            ),
            'heal': (
                "💉 <b>━━━ РОБОТА ЛІКАРЯ ━━━</b> 💉\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                "Кого врятувати?\n\n"
                + ("⚠️ Не можете лікувати себе двічі поспіль!" if game['last_healed'] == user_id else "")
            ),
            'check': (
                "🔍 <b>━━━ РОЗСЛІДУВАННЯ ━━━</b> 🔍\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                "Кого перевірити?\n\n"
                "⚠️ Дон має імунітет!\n\n"
                + ("🔫 Або використайте кулю!" if not game['detective_bullet_used'] else "❌ Куля використана")
            )
        }

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=action_texts[action],
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка надсилання нічних дій {user_id}: {e}")

    # Боти роблять свої дії автоматично
    await asyncio.sleep(2)
    await send_potato_menu(context, chat_id)
    await process_bot_actions(context, chat_id)


async def night_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Таймаут ночі: 45 секунд минуло"""
    chat_id = context.job.chat_id
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night' or game.get('night_resolved'):
        return

    game['night_resolved'] = True
    _cancel_jobs(context.job_queue, f"night_{chat_id}")
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ <b>Час ночі вичерпано!</b> Обробляємо результати...",
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(2)
    await process_night(context, chat_id)


async def night_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка нічних дій"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    action = data[1]
    chat_id = int(data[2])
    target_id = int(data[3]) if len(data) > 3 and data[3] != 'menu' else 0
    user_id = query.from_user.id
    
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night':
        await query.edit_message_text("⚠️ Нічна фаза завершилась!")
        return
    
    all_players = mafia_game.get_all_players(chat_id)
    
    # Постріл детектива
    if action == 'shoot':
        if game['detective_bullet_used']:
            await query.edit_message_text("⚠️ Куля вже використана!", parse_mode=ParseMode.HTML)
            return
        if data[3] == 'menu':
            alive = [(uid, pinfo) for uid, pinfo in all_players.items()
                     if pinfo['alive'] and uid != user_id]
            
            shoot_keyboard = []
            for tid, tinfo in alive:
                bot_mark = "🤖 " if tinfo['is_bot'] else ""
                shoot_keyboard.append([InlineKeyboardButton(
                    f"🔫 {bot_mark}{tinfo['username']}",
                    callback_data=f"night_shoot_{chat_id}_{tid}"
                )])
            shoot_keyboard.append([InlineKeyboardButton(
                "🔙 Назад до перевірки",
                callback_data=f"night_back_{chat_id}"
            )])
            
            await query.edit_message_text(
                "🔫 <b>━━━ ПОСТРІЛ ━━━</b> 🔫\n\n"
                "⚠️ <b>У вас 1 куля на всю гру!</b>\n\n"
                "💀 Якщо вистрілите - всі дізнаються\n"
                "🕯 Обирайте мудро...\n\n"
                "Ціль:",
                reply_markup=InlineKeyboardMarkup(shoot_keyboard),
                parse_mode=ParseMode.HTML
            )
            return
        else:
            game['detective_bullet_used'] = True
            game['detective_shot_this_night'] = target_id
            game['night_actions'][user_id] = {'action': 'shoot', 'target': target_id}
            
            target_name = all_players[target_id]['username']
            
            await query.edit_message_text(
                f"🔫 <b>━━━ ПОСТРІЛ! ━━━</b> 🔫\n\n"
                f"🎯 <b>Ціль:</b> {target_name}\n\n"
                f"💥 Вистріл пролунав...\n"
                f"🕯 Хтось не проснеться...\n\n"
                f"⏳ Результати вранці.\n\n"
                f"<i>Куля витрачена.</i>",
                parse_mode=ParseMode.HTML
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔫 <i>Десь у темряві пролунав постріл...</i> 💥",
                parse_mode=ParseMode.HTML
            )
            
            await check_night_complete(context, chat_id)
            return
    
    # Назад
    if action == 'back':
        await query.edit_message_text("🔄 Повертаємось...")
        return
    
    # Звичайні дії
    game['night_actions'][user_id] = {'action': action, 'target': target_id}
    
    action_names = {'kill': 'вбивство', 'heal': 'лікування', 'check': 'перевірку'}
    action_emojis = {'kill': '💀', 'heal': '💉', 'check': '🔍'}
    
    target_name = all_players[target_id]['username']
    
    await query.edit_message_text(
        f"✅ <b>ДІЮ ПІДТВЕРДЖЕНО!</b>\n\n"
        f"{action_emojis[action]} <b>Ціль:</b> {target_name}\n"
        f"🎯 <b>Дія:</b> {action_names[action]}\n\n"
        f"⏳ Чекаємо на інших...\n\n"
        f"<i>Можете закрити це повідомлення</i>",
        parse_mode=ParseMode.HTML
    )
    
    player_role = game['players'][user_id]['role']
    role_info = mafia_game.get_role_info(player_role)
    
    choice_messages = {
        'kill': f"🌙 {role_info['emoji']} <b>Мафія</b> зробила вибір... 😈",
        'heal': f"🌙 💉 <b>Федорчак</b> зробив вибір... 🏥",
        'check': f"🌙 🔍 <b>Детектив</b> зробив вибір... 🕵️"
    }
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=choice_messages[action],
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Помилка повідомлення: {e}")
    
    await check_night_complete(context, chat_id)


async def potato_throw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка кидка картоплі"""
    query = update.callback_query
    await query.answer()

    data = query.data.split('_')
    chat_id = int(data[2])
    target_id = int(data[3])
    user_id = query.from_user.id

    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night':
        await query.edit_message_text("⚠️ Нічна фаза завершилась!", parse_mode=ParseMode.HTML)
        return

    if target_id == 0:
        await query.edit_message_text(
            "🥔 Ви приберегли картоплю на іншу ніч.",
            parse_mode=ParseMode.HTML
        )
        return

    if user_id in game['potato_throws']:
        await query.edit_message_text(
            "🥔 Ви вже кидали картоплю цієї ночі!",
            parse_mode=ParseMode.HTML
        )
        return

    all_players = mafia_game.get_all_players(chat_id)
    if target_id not in all_players or not all_players[target_id]['alive']:
        await query.edit_message_text("⚠️ Ціль недоступна!", parse_mode=ParseMode.HTML)
        return

    success = mafia_game.use_potato(chat_id, user_id, target_id)
    if not success:
        await query.edit_message_text(
            "⚠️ Ви більше не маєте картоплі!",
            parse_mode=ParseMode.HTML
        )
        return

    target_name = all_players[target_id]['username']

    await query.edit_message_text(
        f"🥔 Ви кинули картоплю в <b>{target_name}</b>!\n\n⏳ Результат дізнаємось вранці...",
        parse_mode=ParseMode.HTML
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text="🥔 <i>Десь у темряві пролетіла картопля...</i>",
        parse_mode=ParseMode.HTML
    )


async def check_night_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка: всі зробили дії?"""
    game = mafia_game.games.get(chat_id)
    if not game:
        return

    all_players = mafia_game.get_all_players(chat_id)
    required = sum(
        1 for pinfo in all_players.values()
        if pinfo['alive'] and mafia_game.get_role_info(pinfo['role'])['action']
    )

    if len(game['night_actions']) >= required and not game.get('night_resolved'):
        game['night_resolved'] = True
        _cancel_jobs(context.job_queue, f"night_{chat_id}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ <b>Всі зробили вибір!</b>\n\n⏳ Обробка...",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(2)
        await process_night(context, chat_id)


# ============================================
# ОБРОБКА НОЧІ
# ============================================

async def process_night(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка результатів ночі"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)

    mafia_target: Optional[int] = None
    healed_target: Optional[int] = None
    check_results = []
    detective_shot: Optional[int] = None
    potato_results = []
    potato_kills = []
    discussion_duration = TIMERS['discussion']
    potato_actions = dict(game.get('potato_throws', {}))
    game['potato_throws'] = {}

    # Картопля з Буковеля
    if game['special_event'] == 'bukovel' and potato_actions:
        for thrower_id, target_id in potato_actions.items():
            thrower = all_players.get(thrower_id)
            target = all_players.get(target_id)

            if not target:
                continue

            potato_results.append({
                'thrower_id': thrower_id,
                'target_id': target_id,
                'thrower_name': thrower['username'] if thrower else "Гравець",
                'target_name': target['username'],
                'hit': random.random() < 0.20
            })
            thrower_name = thrower['username'] if thrower else "Гравець"
            target_name = target['username']

            if random.random() < 0.20:  # 20% влучити
                potato_kills.append((thrower_id, target_id))
                game['perks_messages'].append(
                    f"🥔💥 <b>{random.choice(POTATO_PHRASES)}</b>\n"
                    f"<b>{thrower_name}</b> влучив у <b>{target_name}</b>!"
                )
            else:
                game['perks_messages'].append(
                    f"🥔 <b>{thrower_name}</b> промахнувся по <b>{target_name}</b>!"
                )

    # Розбір нічних дій
    for user_id, action_info in game['night_actions'].items():
        action = action_info['action']
        target = action_info['target']

        if action == 'kill':
            mafia_target = target
        elif action == 'heal':
            healed_target = target
            game['last_healed'] = healed_target
        elif action == 'check':
            target_role_key = all_players[target]['role']
            role_info = mafia_game.get_role_info(target_role_key)

            detective_error = random.random() < 0.05

            if target_role_key == 'kishkel':
                is_mafia = False
            else:
                is_mafia = (role_info['team'] == 'mafia')
                if detective_error:
                    is_mafia = not is_mafia
                    game['detective_error_target'] = target

            check_results.append((user_id, target, is_mafia, detective_error))
        elif action == 'shoot':
            detective_shot = target

    victims = set()
    saved = False
    mafia_misfire = False

    # Постріл мафії
    if mafia_target is not None:
        if random.random() < 0.05:
            mafia_misfire = True
            game['perks_messages'].append(
                "🎲 <b>ПЕРК: ОСІЧКА МАФІЇ!</b>\n🔫❌ Зброя заклинила!"
            )
        else:
            victims.add(mafia_target)

    # Лікування
    if healed_target and mafia_target == healed_target and mafia_target in victims:
        victims.remove(healed_target)
        saved = True
        game['perks_messages'].append(
            f"💉 <b>{random.choice(SAVED_PHRASES)}</b>"
        )

    # Постріл детектива
    if detective_shot:
        if healed_target == detective_shot:
            game['perks_messages'].append(
                "💉 <b>Федорчак врятував від пострілу детектива!</b>"
            )
        else:
            victims.add(detective_shot)
            game['perks_messages'].append(
                "🔫 <b>Детектив відкрив вогонь!</b>\n💀 Постріл забрав життя!"
            )

    # Картопля (після інших дій)
    for result in potato_results:
        if not result['hit']:
            game['perks_messages'].append(
                f"🥔 <b>{result['thrower_name']}</b> промахнувся по <b>{result['target_name']}</b>!"
            )
            continue

        if result['target_id'] in victims:
            game['perks_messages'].append(
                f"🥔 <b>{result['thrower_name']}</b> влучив у <b>{result['target_name']}</b>,"
                " але її вже прибрали до цього!"
            )
            continue

        victims.add(result['target_id'])
        game['perks_messages'].append(
            f"🥔💥 <b>{random.choice(POTATO_PHRASES)}</b>\n"
            f"<b>{result['thrower_name']}</b> влучив у <b>{result['target_name']}</b>!"
        )

    game['mafia_misfire'] = mafia_misfire

    # Застосовуємо смерті
    for vid in victims:
        all_players[vid]['alive'] = False
        game['alive_players'].discard(vid)

    # Результати детективу
    for detective_id, target_id, is_mafia, had_error in check_results:
        if detective_id not in game['players']:
            continue
        target_name = all_players[target_id]['username']

        result_text = f"""
🔍 <b>━━━ РЕЗУЛЬТАТ РОЗСЛІДУВАННЯ ━━━</b> 🔍

<b>Перевірений:</b> {target_name}

<b>Результат:</b>
{'🔴 <b>МАФІЯ!</b> Це злочинець!' if is_mafia else '🔵 <b>МИРНИЙ!</b> Чесна людина.'}

{'⚠️ Обережно з цією інформацією!' if is_mafia else '✅ Можна довіряти.'}
"""
        if had_error:
            result_text += "\n⚠️ <i>Здається, інтуїція цього разу підвела... (5% похибка)</i>"
        try:
            await context.bot.send_message(
                chat_id=detective_id,
                text=result_text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка детективу: {e}")

    # День
    game['phase'] = 'day'
    morning_intro = random.choice(MORNING_PHRASES)

    await asyncio.sleep(2)

    perks_block = ""
    if game['perks_messages']:
        perks_block = (
            f"\n\n{PERKS_DIVIDER}\n"
            + "\n".join(game['perks_messages'])
            + f"\n{PERKS_DIVIDER}"
        )

    # Оголошення результатів
    if victims:
        if len(victims) == 1:
            killed = next(iter(victims))
            killed_name = all_players[killed]['username']
            killed_role = mafia_game.get_role_info(all_players[killed]['role'])

            death_phrase = random.choice(DEATH_PHRASES)

            night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

{morning_intro}

💀 <b>ТРАГІЧНА НОВИНА!</b> 💀

<i>Жителі села виявили страшну знахідку...</i>

💀 <b>Загинув:</b> {killed_name}
🎭 <b>Роль:</b> {killed_role['emoji']} {killed_role['full_name']}

{death_phrase}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> ({discussion_duration} сек)

{random.choice(DISCUSSION_PHRASES)}
"""
        else:
            lines = []
            for vid in victims:
                pinfo = all_players[vid]
                rinfo = mafia_game.get_role_info(pinfo['role'])
                bot_mark = "🤖 " if pinfo['is_bot'] else ""
                lines.append(f"💀 <b>{bot_mark}{pinfo['username']}</b> — {rinfo['emoji']} {rinfo['full_name']}")
            victims_block = "\n".join(lines)

            night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

{morning_intro}

💀 <b>КРИВАВА НІЧ!</b> 💀

{victims_block}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> ({discussion_duration} сек)

{random.choice(DISCUSSION_PHРАSES)}
"""
    elif saved:
        saved_name = all_players[healed_target]['username']
        saved_phrase = random.choice(SAVED_PHRASES)

        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

{morning_intro}

🎉 <b>ДИВО!</b> 🎉

💉 <b>Федорчак</b> врятував <b>{saved_name}</b>!

{saved_phrase}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> ({discussion_duration} сек)

{random.choice(DISCUSSION_PHРАSES)}
"""
    else:
        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

{morning_intro}

😌 <b>СПОКІЙНА НІЧ!</b> 😌

🕊 Всі живі!{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> ({discussion_duration} сек)

{random.choice(DISCUSSION_PHРАSES)}
"""

    await send_gif(context, chat_id, 'death' if victims else 'morning', night_result)

    # Перевірка перемоги
    if await check_victory(context, chat_id):
        return

    # Обговорення (таймер з конфігурації)
    game['phase'] = 'discussion'
    game['discussion_started'] = True
    job_queue = _get_job_queue(context)
    if job_queue:
        job_queue.run_once(
            discussion_timeout,
            when=discussion_duration,
            chat_id=chat_id,
            name=f"discussion_{chat_id}"
        )
    else:
        await _notify_missing_scheduler(context, chat_id, game)
    context.job_queue.run_once(
        discussion_timeout,
        when=discussion_duration,
        chat_id=chat_id,
        name=f"discussion_{chat_id}"
    )


async def discussion_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Завершення обговорення → голосування"""
    chat_id = context.job.chat_id
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'discussion':
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ <b>ЧАС ОБГОВОРЕННЯ ЗАКІНЧИВСЯ!</b>\n\n🗳 Починаємо голосування...",
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(1)
    await start_voting(context, chat_id)


async def final_voting_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Автоматичне завершення фінального голосування"""
    chat_id = context.job.chat_id
    game = mafia_game.games.get(chat_id)
    if not game or game.get('final_voting_done'):
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ <b>Час фінального голосування вичерпано!</b>\n\nПідраховуємо наявні голоси...",
        parse_mode=ParseMode.HTML,
    )

    await process_final_voting(context, chat_id)


# ============================================
# ГОЛОСУВАННЯ
# ============================================

async def start_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Початок голосування за виключення"""
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'discussion':
        game['phase'] = 'voting'
    
    game['phase'] = 'voting'
    game['vote_nominee'] = None
    game['votes'] = {}
    game['nominations_done'] = False
    game['final_voting_done'] = False
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_players = [(uid, pinfo) for uid, pinfo in all_players.items() if pinfo['alive']]
    
    # Надсилання кнопок живим гравцям (не ботам)
    for user_id, player_info in game['players'].items():
        if not player_info['alive']:
            continue
        
        keyboard = []
        
        for target_id, target_info in alive_players:
            if target_id != user_id:
                bot_mark = "🤖 " if target_info['is_bot'] else ""
                keyboard.append([InlineKeyboardButton(
                    f"👤 {bot_mark}{target_info['username']}",
                    callback_data=f"nominate_{chat_id}_{target_id}"
                )])
        
        keyboard.append([InlineKeyboardButton(
            "🚫 Пропустити день",
            callback_data=f"nominate_{chat_id}_0"
        )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        voting_text = f"""
🗳 <b>━━━ ВИСУНЕННЯ КАНДИДАТА ━━━</b> 🗳

<b>День {game['day_number']}</b>

Кого хочете виключити з гри?

⚠️ Після висунення буде фінальне голосування ЗА/ПРОТИ

<b>👥 Живих учасників:</b> {len(alive_players)}

<i>Висуньте підозрілого або пропустіть день!</i>
"""
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=voting_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка надсилання голосування {user_id}: {e}")
    
    await send_gif(
        context,
        chat_id,
        'vote',
        "🗳 <b>ВИСУНЕННЯ КАНДИДАТІВ!</b>\n\n"
        "📨 Перевірте особисті повідомлення!\n"
        "⏳ Голосуйте за підозрілих...\n\n"
        "<i>Кожен голос важливий!</i>"
    )
    
    # Боти голосують автоматично через 2-5 секунд
    await asyncio.sleep(random.uniform(2, 4))
    await process_bot_votes(context, chat_id)


async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка голосів"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    action = data[0]  # nominate або votefor
    chat_id = int(data[1])
    target_id = int(data[2])
    user_id = query.from_user.id
    
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'voting':
        await query.edit_message_text("⚠️ Голосування завершилось!")
        return
    
    all_players = mafia_game.get_all_players(chat_id)
    
    # Висунення кандидата
    if action == 'nominate':
        game['votes'][user_id] = target_id
        
        if target_id == 0:
            vote_text = "✅ <b>ВИ ПРОПУСТИЛИ ДЕНЬ</b>\n\n⏳ Чекаємо на інших..."
        else:
            target_name = all_players[target_id]['username']
            vote_text = f"✅ <b>ВИ ВИСУНУЛИ:</b> {target_name}\n\n⏳ Чекаємо на інших..."
        
        await query.edit_message_text(vote_text, parse_mode=ParseMode.HTML)
        
        voter_name = game['players'][user_id]['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🗳 <b>{voter_name}</b> проголосував!",
            parse_mode=ParseMode.HTML
        )
        
        await check_nominations_complete(context, chat_id)
    
    # Фінальне голосування ЗА/ПРОТИ
    elif action == 'votefor':
        vote = data[3]  # yes або no
        game['vote_results'][user_id] = vote
        
        nominee_name = all_players[game['vote_nominee']]['username']
        
        if vote == 'yes':
            vote_text = f"✅ <b>ВИ ЗА ВИКЛЮЧЕННЯ</b>\n\n👤 {nominee_name}\n\n⏳ Чекаємо..."
        else:
            vote_text = f"✅ <b>ВИ ПРОТИ ВИКЛЮЧЕННЯ</b>\n\n👤 {nominee_name}\n\n⏳ Чекаємо..."
        
        await query.edit_message_text(vote_text, parse_mode=ParseMode.HTML)
        
        voter_name = game['players'][user_id]['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🗳 <b>{voter_name}</b> проголосував!",
            parse_mode=ParseMode.HTML
        )
        
        await check_final_voting_complete(context, chat_id)


async def check_nominations_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення висунення"""
    game = mafia_game.games.get(chat_id)
    if not game or game.get('nominations_done'):
        return

    all_players = mafia_game.get_all_players(chat_id)
    alive_count = sum(1 for p in all_players.values() if p['alive'])

    if len(game['votes']) >= alive_count:
        game['nominations_done'] = True
        # Підрахунок
        nominations = defaultdict(int)
        for nominated in game['votes'].values():
            if nominated != 0:
                nominations[nominated] += 1
        
        if not nominations:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🚫 <b>ДЕНЬ ПРОПУЩЕНО!</b>\n\nНіхто не висунутий. Настає ніч...",
                parse_mode=ParseMode.HTML
            )
            await asyncio.sleep(2)
            game['phase'] = 'night'
            game['day_number'] += 1
            await night_phase(context, chat_id)
            return
        
        # Найбільш висунутий
        max_nominations = max(nominations.values())
        candidates = [uid for uid, count in nominations.items() if count == max_nominations]
        
        nominee = random.choice(candidates) if len(candidates) > 1 else candidates[0]
        
        game['vote_nominee'] = nominee
        game['vote_results'] = {}
        
        nominee_name = all_players[nominee]['username']
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📊 <b>ПІДРАХУНОК ГОЛОСІВ</b>\n\n"
                 f"👤 <b>Кандидат:</b> {nominee_name}\n"
                 f"🗳 Висунень: {max_nominations}\n\n"
                 f"⚖️ <b>ФІНАЛЬНЕ ГОЛОСУВАННЯ ЗА/ПРОТИ!</b>",
            parse_mode=ParseMode.HTML
        )
        
        await asyncio.sleep(2)
        await start_final_voting(context, chat_id)


async def start_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Фінальне голосування ЗА/ПРОТИ"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    nominee_name = all_players[game['vote_nominee']]['username']
    
    # Живі гравці (не боти)
    for user_id, player_info in game['players'].items():
        if not player_info['alive']:
            continue
        
        keyboard = [
            [InlineKeyboardButton("✅ ТАК, ВИКЛЮЧИТИ", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_yes")],
            [InlineKeyboardButton("❌ НІ, ЗАЛИШИТИ", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_no")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        voting_text = f"""
⚖️ <b>━━━ ФІНАЛЬНЕ ГОЛОСУВАННЯ ━━━</b> ⚖️

🪢 <b>Виключити з гри?</b>

👤 <b>Кандидат:</b> {nominee_name}

<b>Ваше рішення:</b>
✅ ТАК - виключити
❌ НІ - залишити

⚠️ Якщо більшість ЗА - гравця виключать!

<i>Голосуйте розумно!</i>
"""
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=voting_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка фінального голосування {user_id}: {e}")
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚖️ <b>ФІНАЛЬНЕ ГОЛОСУВАННЯ!</b>\n\n"
             f"👤 Кандидат: <b>{nominee_name}</b>\n\n"
             f"📨 Перевірте особисті повідомлення!\n"
             f"🪢 Доля гравця у ваших руках!",
        parse_mode=ParseMode.HTML
    )

    job_queue = _get_job_queue(context)
    if job_queue:
        _cancel_jobs(job_queue, f"final_vote_{chat_id}")
        job_queue.run_once(
            final_voting_timeout,
            when=TIMERS['final_vote'],
            chat_id=chat_id,
            name=f"final_vote_{chat_id}"
        )

    # Боти голосують
    await asyncio.sleep(random.uniform(2, 4))
    await process_bot_final_votes(context, chat_id)


async def check_final_voting_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення фінального голосування"""
    game = mafia_game.games[chat_id]
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_count = sum(1 for p in all_players.values() if p['alive'])
    
    if len(game['vote_results']) >= alive_count and not game.get('final_voting_done'):
        await process_final_voting(context, chat_id)


async def process_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка фінального голосування"""
    game = mafia_game.games[chat_id]

    _cancel_jobs(getattr(context, 'job_queue', None), f"final_vote_{chat_id}")

    if game.get('final_voting_done'):
        return
    game['final_voting_done'] = True
    
    yes_votes = sum(1 for v in game['vote_results'].values() if v == 'yes')
    no_votes = sum(1 for v in game['vote_results'].values() if v == 'no')
    
    all_players = mafia_game.get_all_players(chat_id)
    nominee = all_players[game['vote_nominee']]
    nominee_role = mafia_game.get_role_info(nominee['role'])
    
    # Перк: мотузка рветься (5%)
    rope_break = random.random() < 0.05
    
    if yes_votes > no_votes:
        if rope_break:
            result_text = f"""
📊 <b>РЕЗУЛЬТАТИ ГОЛОСУВАННЯ</b>

👤 <b>Кандидат:</b> {nominee['username']}

✅ <b>ЗА:</b> {yes_votes}
❌ <b>ПРОТИ:</b> {no_votes}

━━━━━━━━━━━━━━━━━━━━━━━━━━

🪢 <b>МОТУЗКА ПОРВАЛАСЬ!</b> 🪢

✨ Гравець врятований долею!
🎲 Рандомний перк спрацював!

<b>{nominee['username']}</b> залишається в грі! 🎉
"""
        else:
            all_players[game['vote_nominee']]['alive'] = False
            game['alive_players'].discard(game['vote_nominee'])
            
            result_text = f"""
📊 <b>РЕЗУЛЬТАТИ ГОЛОСУВАННЯ</b>

👤 <b>Виключений:</b> {nominee['username']}

✅ <b>ЗА:</b> {yes_votes}
❌ <b>ПРОТИ:</b> {no_votes}

━━━━━━━━━━━━━━━━━━━━━━━━━━

💀 <b>РОЛЬ РОЗКРИТА!</b>

{nominee_role['emoji']} <b>{nominee_role['full_name']}</b>

{random.choice(DEATH_PHRASES)}
"""
    else:
        result_text = f"""
📊 <b>РЕЗУЛЬТАТИ ГОЛОСУВАННЯ</b>

👤 <b>Кандидат:</b> {nominee['username']}

✅ <b>ЗА:</b> {yes_votes}
❌ <b>ПРОТИ:</b> {no_votes}

━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ <b>НЕДОСТАТНЬО ГОЛОСІВ!</b>

<b>{nominee['username']}</b> залишається в грі! ✨
"""
    
    await send_gif(
        context,
        chat_id,
        'vote',
        result_text
    )
    
    # Перевірка перемоги
    if await check_victory(context, chat_id):
        return
    
    await asyncio.sleep(3)
    
    # Наступна ніч
    game['phase'] = 'night'
    game['day_number'] += 1
    game['detective_error_target'] = None
    game['potato_throws'] = {}
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🌙 <b>Настає ніч {game['day_number']}...</b> 🌙\n\n"
             f"{random.choice(NIGHT_PHRASES)}\n\n"
             f"<i>Село засинає...</i>",
        parse_mode=ParseMode.HTML
    )
    
    await asyncio.sleep(2)
    await night_phase(context, chat_id)


# ============================================
# ПЕРЕВІРКА ПЕРЕМОГИ
# ============================================

async def check_victory(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Перевірка умов перемоги"""
    game = mafia_game.games[chat_id]
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_mafia = 0
    alive_citizens = 0
    any_humans_alive = any(player['alive'] for player in game['players'].values())

    for user_id in game['alive_players']:
        player = all_players[user_id]
        role = player['role']
        role_info = mafia_game.get_role_info(role)
        if role_info['team'] == 'mafia':
            alive_mafia += 1
        else:
            alive_citizens += 1
    
    winner = None
    
    if alive_mafia == 0:
        winner = 'citizens'
        victory_text = """
🎉🎉🎉 <b>ПЕРЕМОГА МИРНИХ ЖИТЕЛІВ!</b> 🎉🎉🎉

⚔️ Мафія знешкоджена!
🏆 Справедливість перемогла!
✨ Село врятоване!

<b>Вітаємо героїв! 🦸‍♂️</b>
"""
    elif alive_mafia >= alive_citizens or not any_humans_alive:
        winner = 'mafia'
        if not any_humans_alive and alive_mafia < alive_citizens:
            victory_text = """
💀💀💀 <b>ГРУ ЗАВЕРШЕНО!</b> 💀💀💀

🙅 Людей у грі більше не залишилось.
🤖 Лише боти продовжували б раунд, тому гра завершується.

Мафія оголошена переможцем автоматично.
"""
        else:
            victory_text = """
💀💀💀 <b>ПЕРЕМОГА МАФІЇ!</b> 💀💀💀

🔫 Мафія захопила село!
👑 Темна сторона перемогла!
😈 Злочинці тріумфують!

<b>Вітаємо мафіозі! 🎭</b>
"""
    
    if winner:
        # Розкриття ролей
        roles_text = "\n\n🎭 <b>━━━ РОЗКРИТТЯ РОЛЕЙ ━━━</b> 🎭\n\n"
        
        for user_id, player_info in all_players.items():
            role_info = mafia_game.get_role_info(player_info['role'])
            status = "💀" if not player_info['alive'] else "✅"
            team_emoji = "🔴" if role_info['team'] == 'mafia' else "🔵"
            bot_mark = "🤖 " if player_info['is_bot'] else ""
            
            roles_text += f"{status} {team_emoji} <b>{bot_mark}{player_info['username']}</b>\n"
            roles_text += f"   └ {role_info['emoji']} {role_info['full_name']}\n\n"
        
        # Статистика
        roles_text += f"\n📊 <b>Статистика:</b>\n"
        roles_text += f"   • Днів: {game['day_number']}\n"
        roles_text += f"   • Учасників: {len(all_players)}\n"
        roles_text += f"   • Ботів: {len(game['bots'])}\n"
        roles_text += f"   • Переможець: {'Мирні' if winner == 'citizens' else 'Мафія'}\n"
        roles_text += f"   • Куля детектива: {'Використана' if game['detective_bullet_used'] else 'Не використана'}\n"
        
        if game.get('special_event'):
            event_info = SPECIAL_EVENTS[game['special_event']]
            roles_text += f"   • 🎲 Подія: {event_info['name']}\n"
        
        game['phase'] = 'ended'
        
        keyboard = [
            [InlineKeyboardButton("🎮 Нова гра", callback_data="create_new_game")],
        ]
        
        await send_gif(
            context,
            chat_id,
            'win',
            victory_text + roles_text
        )
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=victory_text + roles_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except:
            pass
        
        return True
    
    return False


# ============================================
# МЕНЮ КНОПКИ
# ============================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Головний обробник кнопок"""
    query = update.callback_query
    
    # Меню
    if query.data == "menu_rules":
        await show_rules(update, context)
    elif query.data == "menu_howto":
        await show_howto(update, context)
    elif query.data == "menu_characters":
        await show_characters(update, context)
    elif query.data == "back_main":
        await start(update, context)
    # Гра
    elif query.data == "join_game":
        await join_game_callback(update, context)
    elif query.data == "leave_game":
        await leave_game_callback(update, context)
    elif query.data == "start_game":
        await start_game_callback(update, context)
    elif query.data == "create_new_game":
        await create_new_game_callback(update, context)
    elif query.data == "add_bots_menu":
        await add_bots_menu_callback(update, context)
    elif query.data.startswith("add_bots_"):
        await add_bots_callback(update, context)
    elif query.data == "back_to_game":
        await back_to_game_callback(update, context)
    # Нічні дії
    elif query.data.startswith("night_"):
        await night_action_callback(update, context)
    elif query.data.startswith("potato_throw_"):
        await potato_throw_callback(update, context)
    # Голосування
    elif query.data.startswith("nominate_") or query.data.startswith("votefor_"):
        await vote_callback(update, context)


async def show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати правила"""
    query = update.callback_query
    await query.answer()
    
    rules_text = f"""
📜 <b>━━━ ПРАВИЛА ГРИ ━━━</b> 📜

<b>🎯 Мета:</b>
🔵 Мирні - знищити мафію
🔴 Мафія - знищити мирних

<b>🌙 НІЧ:</b>
- 🔫 Мафія вбиває
- 💉 Лікар рятує
- 🔍 Детектив перевіряє/стріляє

<b>☀️ ДЕНЬ:</b>
- 📢 Результати ночі
- 🗣 Обговорення ({TIMERS['discussion']} сек)
- 🗳 Голосування за виключення

<b>⚡ ОСОБЛИВОСТІ:</b>
- 👑 Дон має імунітет
- 💉 Лікар не лікує себе двічі
- 🔫 Детектив має 1 кулю
- 🤖 Боти з логікою
- 🎲 Спеціальні події (30%)
- 🎪 Рандомні перки (5%)

<b>🥔 БУКОВЕЛЬ:</b>
- 20% шанс отримати картоплю
- Кинути = 20% вбити когось
- Незалежно від ролі

<b>🎮 УЧАСНИКИ:</b>
- Мін: 5 (гравці + боти)
- Макс: 15
- Боти: 1-10
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def show_howto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Як грати"""
    query = update.callback_query
    await query.answer()
    
    howto_text = """
🎮 <b>━━━ ЯК ГРАТИ? ━━━</b> 🎮

<b>📝 ПІДГОТОВКА:</b>
1️⃣ Додайте бота в групу
2️⃣ Напишіть /start боту в ЛС
3️⃣ В групі /newgame
4️⃣ Приєднуйтесь кнопкою
5️⃣ Додайте ботів (опціонально)
6️⃣ Адмін запускає гру

<b>🎯 ГРА:</b>
1️⃣ Отримаєте роль в ЛС
2️⃣ Ніч - виконуйте дії
3️⃣ День - обговорення
4️⃣ Голосування за виключення
5️⃣ Повтор до перемоги

<b>🤖 БОТИ:</b>
- Мафія: вбивають рандомно
- Лікар: лікує рандомно
- Мирні: голосують за того, хто має 2+ голоси

<b>💡 ПОРАДИ:</b>
- Не розкривайте роль рано
- Детектив: думайте коли стріляти
- Мафія: будьте переконливими
- Мирні: шукайте непослідовність

<b>💀 МЕРТВІ НЕ ПИШУТЬ В ЧАТ!</b>
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(howto_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def show_characters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Персонажі"""
    query = update.callback_query
    await query.answer()
    
    characters_text = """
👥 <b>━━━ ПЕРСОНАЖІ ━━━</b> 👥

🔵 <b>МИРНІ:</b>

🌾 <b>ДЕМЯН</b>
├ Простий селянин
└ Немає здібностей

💉 <b>ФЕДОРЧАК (Лікар)</b>
├ Рятує 1 гравця за ніч
└ Не лікує себе двічі поспіль

🔍 <b>ДЕТЕКТИВ КОЛОМБО</b>
├ Перевіряє АБО стріляє
├ 1 куля на гру
└ Дон має імунітет

━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 <b>МАФІЯ:</b>

👑 <b>КІШКЕЛЬ (Дон)</b>
├ Вбиває + імунітет
└ Детектив бачить як мирного

🔫 <b>ІГОР РОГАЛЬСЬКИЙ</b>
├ Вбиває з доном
└ З'являється при 7+ гравцях

━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🎲 ПЕРКИ (5%):</b>
🪢 Мотузка рветься
🔫 Осічка мафії
🔍 Помилка детектива

<b>🥔 БУКОВЕЛЬ (30%):</b>
Картопля = вбивство будь-кого!
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(characters_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def create_new_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Створення нової гри"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    await query.edit_message_text(
        "🎮 Щоб створити нову гру:\n\n"
        "<code>/newgame</code>\n\n"
        "Або натисніть команду! 👆",
        parse_mode=ParseMode.HTML
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка помилок"""
    logger.error(f"Помилка: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Виникла помилка. Спробуйте пізніше.",
                parse_mode=ParseMode.HTML
            )
    except Exception:
        pass