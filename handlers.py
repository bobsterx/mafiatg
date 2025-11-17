"""Telegram handlers and game flow for the Mafia bot.

Повний функціонал:
- Реєстрація гравців та ботів
- Нічна фаза з таймером (45 сек)
- Денна фаза з обговоренням (60 сек)
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
    POTATO_PHRASES, SPECIAL_EVENTS, GIF_PATHS
)
from game_state import mafia_game

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ============================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================

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
    """Вибір жертви для мафії"""
    all_players = mafia_game.get_all_players(game['chat_id'])
    
    # Мафія не вбиває своїх
    bot_role = game['bots'][bot_id]['role']
    mafia_team = {'kishkel', 'rohalskyi'}
    
    targets = []
    for pid, pinfo in all_players.items():
        if pinfo['alive'] and pinfo['role'] not in mafia_team:
            targets.append(pid)
    
    if not targets:
        return None
    
    # Проста стратегія: випадковий вибір
    return random.choice(targets)


def bot_doctor_choice(game: dict, bot_id: int) -> Optional[int]:
    """Вибір цілі для лікаря"""
    all_players = mafia_game.get_all_players(game['chat_id'])
    
    targets = []
    for pid, pinfo in all_players.items():
        if pinfo['alive'] and pid != bot_id:  # Не лікуємо себе
            targets.append(pid)
    
    if not targets:
        return None
    
    # Випадковий вибір
    return random.choice(targets)


def bot_voting_choice(game: dict, bot_id: int) -> int:
    """Вибір кандидата для голосування"""
    all_players = mafia_game.get_all_players(game['chat_id'])
    
    # Боти можуть проголосувати за когось або пропустити
    if random.random() < 0.8:  # 80% шанс проголосувати
        targets = [pid for pid, pinfo in all_players.items() 
                  if pinfo['alive'] and pid != bot_id]
        if targets:
            return random.choice(targets)
    
    return 0  # Пропустити день


# ============================================
# ОБРОБКА ДІЙ БОТІВ
# ============================================

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
            
            # ВИПРАВЛЕННЯ: Прибрано смайлик ролі щоб не палити бота
            bot_name = bot_info['username']
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🤖 <b>{bot_name}</b> зробив свій вибір...",
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


async def process_bot_final_votes(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Боти голосують ЗА/ПРОТИ випадково"""
    game = mafia_game.games[chat_id]
    
    for bot_id, bot_info in game['bots'].items():
        if not bot_info['alive']:
            continue
        
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        vote = random.choice(['yes', 'no'])
        game['vote_results'][bot_id] = vote
        
        bot_name = bot_info['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{bot_name}</b> проголосував!",
            parse_mode=ParseMode.HTML
        )


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
    
    await update.message.reply_text(
        "🎮 <b>Вітаю в грі МАФІЯ!</b> 🎮\n\n"
        "🎯 Основні команди:\n"
        "   /newgame - створити нову гру\n"
        "   /join - приєднатись до гри\n"
        "   /startgame - почати гру\n"
        "   /status - перевірити статус\n"
        "   /endgame - завершити гру\n\n"
        "🤖 <i>Гра підтримує ботів!</i>\n"
        "💡 <i>Додайте бота до групи та дайте права адміністратора.</i>",
        parse_mode=ParseMode.HTML
    )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /newgame - створення нової гри"""
    chat_id = update.message.chat_id
    
    # Перевірка чи є вже активна гра
    if chat_id in mafia_game.games and mafia_game.games[chat_id]['started']:
        await update.message.reply_text(
            "⚠️ <b>Гра вже йде!</b>\n\n"
            "Використовуйте /endgame щоб завершити поточну гру.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Створення нової гри
    admin_id = update.message.from_user.id
    game = mafia_game.create_game(chat_id, admin_id)
    
    # Вибір випадкової події
    game['special_event'] = random.choice(list(SPECIAL_EVENTS.keys()))
    
    # Відправка повідомлення про гру
    await send_game_message(context, chat_id)
    
    await update.message.reply_text(
        "🎮 <b>НОВА ГРА СТВОРЕНА!</b> 🎮\n\n"
        f"🎲 Подія: <b>{SPECIAL_EVENTS[game['special_event']]['name']}</b>\n"
        f"<i>{SPECIAL_EVENTS[game['special_event']]['description']}</i>\n\n"
        "👥 Натисніть «ПРИЄДНАТИСЯ» щоб грати!\n"
        "🤖 Можна додати ботів для повної гри.",
        parse_mode=ParseMode.HTML
    )


async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /endgame - завершення гри"""
    chat_id = update.message.chat_id
    
    if chat_id not in mafia_game.games:
        await update.message.reply_text("⚠️ Немає активної гри!")
        return
    
    # Очищення гри
    mafia_game.end_game(chat_id)
    
    await update.message.reply_text(
        "🛑 <b>ГРУ ЗАВЕРШЕНО!</b> 🛑\n\n"
        "Дякую за гру! 🙏\n"
        "Можна створити нову гру командою /newgame",
        parse_mode=ParseMode.HTML
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status - статус гри"""
    chat_id = update.message.chat_id
    
    if chat_id not in mafia_game.games:
        await update.message.reply_text("⚠️ Немає активної гри!")
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    status_text = f"""
📊 <b>СТАТУС ГРИ</b>

🔄 Фаза: <b>{game['phase']}</b>
📅 День: <b>{game['day_number']}</b>
👥 Гравців: <b>{len(all_players)}</b>
🎲 Подія: <b>{SPECIAL_EVENTS.get(game['special_event'], {}).get('name', 'Немає')}</b>
"""
    
    if game['started']:
        alive_players = [p for p in all_players.values() if p['alive']]
        dead_players = [p for p in all_players.values() if not p['alive']]
        
        status_text += f"\n✅ Живих: <b>{len(alive_players)}</b>"
        if dead_players:
            status_text += f"\n💀 Мертвих: <b>{len(dead_players)}</b>"
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)


# ============================================
# КОЛБЕКИ (INLINE BUTTONS)
# ============================================

async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приєднання до гри"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    if game['started']:
        await query.answer("⚠️ Гра вже почалась!", show_alert=True)
        return
    
    if len(all_players) >= 15:
        await query.answer("⚠️ Гра повна!", show_alert=True)
        return
    
    if user_id in game['players']:
        await query.answer("⚠️ Ви вже в грі!", show_alert=True)
        return
    
    # Додавання гравця
    mafia_game.add_player(chat_id, user_id, username, is_bot=False)
    
    await query.answer(f"✅ {username} приєднався!", show_alert=True)
    await update_game_message(context, chat_id)


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


async def send_game_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Відправка повідомлення про гру"""
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
    message_text = f"""
🎮 <b>ГРА: МАФІЯ</b> 🎮{event_text}

<b>📊 Учасників ({total}/15):</b>
{players_list}
"""
    
    try:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=InlineKeyboardMarkup(announcement_keyboard),
            parse_mode=ParseMode.HTML
        )
        mafia_game.game_messages[chat_id] = message.message_id
    except Exception as e:
        logger.error(f"Помилка відправки повідомлення: {e}")


# ============================================
# ПОЧАТОК ГРИ
# ============================================

async def start_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок гри"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Гра не знайдена!", show_alert=True)
        return
    
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    if len(all_players) < 3:
        await query.answer("⚠️ Потрібно мінімум 3 гравці!", show_alert=True)
        return
    
    # Позначаємо гру як розпочату
    game['started'] = True
    game['phase'] = 'night'
    game['day_number'] = 1
    
    # Роздача ролей
    mafia_game.distribute_roles(chat_id)
    
    await query.edit_message_text(
        "🎮 <b>ГРА ПОЧАЛАСЬ!</b> 🎮\n\n"
        "🌙 Ніч опускається на село...\n"
        "🎭 Ролі роздані в особисті повідомлення!",
        parse_mode=ParseMode.HTML
    )
    
    # Відправка ролей гравцям
    await send_roles_to_players(context, chat_id)
    
    # Початок першої ночі
    await start_night(context, chat_id)


async def send_roles_to_players(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Відправка ролей гравцям"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    for user_id, player_info in all_players.items():
        role_key = player_info['role']
        role_info = mafia_game.get_role_info(role_key)
        
        role_text = f"""
🎭 <b>ВАША РОЛЬ:</b>

{role_info['emoji']} <b>{role_info['full_name']}</b>

📋 <b>Опис:</b>
{role_info['description']}

🎯 <b>Команда:</b> {'<b>🔴 МАФІЯ</b>' if role_info['team'] == 'mafia' else '<b>🔵 МИРНІ</b>'}
"""
        
        try:
            if player_info['is_bot']:
                # Для ботів просто повідомлення в чат
                pass
            else:
                # Для людей - в особисті
                await context.bot.send_message(
                    chat_id=user_id,
                    text=role_text,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Помилка відправки ролі {user_id}: {e}")


# ============================================
# НІЧНА ФАЗА
# ============================================

async def start_night(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Початок нічної фази"""
    game = mafia_game.games[chat_id]
    
    # Очищення дій попередньої ночі
    game['night_actions'] = {}
    game['perks_messages'] = []
    game['night_resolved'] = False
    
    # ВИПРАВЛЕННЯ: Відправляємо лише ОДИН GIF на початку ночі
    await send_gif(
        context,
        chat_id,
        'night',
        f"🌙 <b>Ніч {game['day_number']}...</b> 🌙\n\n"
        f"{random.choice(NIGHT_PHRASES)}\n\n"
        f"<i>Село засинає...</i>"
    )
    
    # Відправка кнопок дій живим гравцям
    await send_night_actions(context, chat_id)
    
    # Обробка дій ботів
    await process_bot_actions(context, chat_id)
    
    # Таймер на 45 секунд
    context.job_queue.run_once(night_timeout, when=45, chat_id=chat_id, name=f"night_{chat_id}")


async def send_night_actions(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Відправка кнопок для нічних дій"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    for user_id, player_info in all_players.items():
        if not player_info['alive'] or player_info['is_bot']:
            continue
        
        role_key = player_info['role']
        role_info = mafia_game.get_role_info(role_key)
        action = role_info.get('action')
        
        if not action:
            continue
        
        # Формуємо клавіатуру з цілями
        targets = []
        for target_id, target_info in all_players.items():
            if target_id != user_id and target_info['alive']:
                targets.append((target_id, target_info['username']))
        
        if not targets:
            continue
        
        keyboard = []
        for target_id, target_name in targets:
            if action == 'kill':
                keyboard.append([InlineKeyboardButton(
                    f"🔪 {target_name}",
                    callback_data=f"night_kill_{target_id}"
                )])
            elif action == 'heal':
                keyboard.append([InlineKeyboardButton(
                    f"💉 {target_name}",
                    callback_data=f"night_heal_{target_id}"
                )])
            elif action == 'check':
                keyboard.append([InlineKeyboardButton(
                    f"🔍 {target_name}",
                    callback_data=f"night_check_{target_id}"
                )])
        
        # Додаткова дія для детектива - постріл
        if action == 'check' and game.get('detective_shot_used', False) == False:
            keyboard.append([InlineKeyboardButton(
                "🔫 Постріл (один раз)",
                callback_data="night_shoot_menu"
            )])
        
        action_text = {
            'kill': "🔪 <b>ВИБЕРІТЬ ЖЕРТВУ:</b>",
            'heal': "💉 <b>ВИБЕРІТЬ КОГО ВРЯТУВАТИ:</b>",
            'check': "🔍 <b>ВИБЕРІТЬ КОГО ПЕРЕВІРИТИ:</b>"
        }
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=action_text.get(action, "<b>ВАША ДІЯ:</b>"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка відправки дій {user_id}: {e}")


async def night_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Завершення нічної фази"""
    chat_id = context.job.chat_id
    game = mafia_game.games.get(chat_id)
    
    if not game or game['phase'] != 'night':
        return
    
    # Якщо ніч вже оброблена - виходимо
    if game.get('night_resolved', False):
        return
    
    game['night_resolved'] = True
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ <b>НІЧ ЗАКІНЧИЛАСЬ!</b>\n\n"
             "📊 Обробляємо результати...",
        parse_mode=ParseMode.HTML
    )
    
    await process_night(context, chat_id)


async def process_night(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка результатів ночі"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)

    mafia_target: Optional[int] = None
    healed_target: Optional[int] = None
    check_results = []
    detective_shot: Optional[int] = None
    potato_kills = []

    # Картопля з Буковеля
    if game['special_event'] == 'bukovel':
        for thrower_id, target_id in game.get('potato_throws', {}).items():
            if random.random() < 0.20:  # 20% влучити
                potato_kills.append((thrower_id, target_id))
                game['perks_messages'].append(
                    f"🥔💥 <b>{random.choice(POTATO_PHRASES)}</b>\n"
                    f"💀 Бульба забрала життя!"
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

    # Логіка мафії
    if mafia_target:
        if mafia_target == healed_target:
            saved = True
            game['perks_messages'].append(
                f"💉 <b>Федорчак врятував {all_players[healed_target]['username']}!</b>\n"
                f"🙏 {random.choice(SAVED_PHRASES)}"
            )
        else:
            victims.add(mafia_target)

    # Логіка детектива - постріл
    if detective_shot and detective_shot != healed_target:
        victims.add(detective_shot)
        game['detective_shot_used'] = True
        game['perks_messages'].append(
            "🔫 <b>Детектив відкрив вогонь!</b>\n💀 Постріл забрав життя!"
        )

    # Картопля
    for thrower_id, target_id in potato_kills:
        victims.add(target_id)

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

    # Виправлення довгих ліній
    perks_block = ""
    if game['perks_messages']:
        perks_block = "\n\n━━━━━━━━━━━━━━\n\n" + "\n".join(game['perks_messages']) + "\n\n━━━━━━━━━━━━━━"

    # Оголошення результатів
    if victims:
        if len(victims) == 1:
            killed = next(iter(victims))
            killed_name = all_players[killed]['username']
            killed_role = mafia_game.get_role_info(all_players[killed]['role'])

            death_phrase = random.choice(DEATH_PHRASES)

            night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

💀 <b>ТРАГІЧНА НОВИНА!</b> 💀

<i>Жителі села виявили страшну знахідку...</i>

💀 <b>Загинув:</b> {killed_name}
🎭 <b>Роль:</b> {killed_role['emoji']} {killed_role['full_name']}

{death_phrase}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> (60 сек)

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

💀 <b>КРИВАВА НІЧ!</b> 💀

{victims_block}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> (60 сек)

{random.choice(DISCUSSION_PHRASES)}
"""
    elif saved:
        saved_name = all_players[healed_target]['username']
        saved_phrase = random.choice(SAVED_PHRASES)

        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

🎉 <b>ДИВО!</b> 🎉

💉 <b>Федорчак</b> врятував <b>{saved_name}</b>!

{saved_phrase}{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> (60 сек)

{random.choice(DISCUSSION_PHRASES)}
"""
    else:
        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

😌 <b>СПОКІЙНА НІЧ!</b> 😌

🕊 Всі живі!{perks_block}

🗣 <b>ЧАС ОБГОВОРЕННЯ!</b> (60 сек)

{random.choice(DISCUSSION_PHRASES)}
"""

    # ВИПРАВЛЕННЯ: Відправляємо лише ОДИН GIF замість двох
    await send_gif(context, chat_id, 'death' if victims else 'morning', night_result)

    # Перевірка перемоги
    if await check_victory(context, chat_id):
        return

    # Обговорення 60 секунд
    game['phase'] = 'discussion'
    game['discussion_started'] = True
    context.job_queue.run_once(discussion_timeout, when=60, chat_id=chat_id, name=f"discussion_{chat_id}")


# ============================================
# ДЕННА ФАЗА - ОБГОВОРЕННЯ ТА ГОЛОСУВАННЯ
# ============================================

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

    await start_voting(context, chat_id)


async def start_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Початок голосування за висунення"""
    game = mafia_game.games[chat_id]
    game['phase'] = 'voting'
    game['votes'] = {}
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_players = {uid: pinfo for uid, pinfo in all_players.items() if pinfo['alive']}
    
    # Відправка кнопок голосування
    for user_id, player_info in alive_players.items():
        if player_info['is_bot']:
            continue
        
        keyboard = []
        
        # Додаємо всіх живих гравців
        for target_id, target_info in alive_players.items():
            if target_id != user_id:
                keyboard.append([InlineKeyboardButton(
                    f"👤 {target_info['username']}",
                    callback_data=f"nominate_{chat_id}_{target_id}"
                )])
        
        # Опція пропустити день
        keyboard.append([InlineKeyboardButton(
            "🚫 Пропустити день",
            callback_data=f"nominate_{chat_id}_0"
        )])
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🗳 <b>ВИСУНЬТЕ КАНДИДАТА:</b>\n\n"
                     "Кого підозрюєте в мафії?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка відправки голосування {user_id}: {e}")
    
    # Боти голосують
    await process_bot_votes(context, chat_id)
    
    # Таймер на 30 секунд
    context.job_queue.run_once(check_nominations_complete, when=30, chat_id=chat_id, name=f"nomination_{chat_id}")


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
    game = mafia_game.games[chat_id]
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_count = sum(1 for p in all_players.values() if p['alive'])
    
    if len(game['votes']) >= alive_count:
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
            await start_night(context, chat_id)
            return
        
        # Знаходимо переможця
        max_votes = max(nominations.values())
        candidates = [uid for uid, votes in nominations.items() if votes == max_votes]
        
        if len(candidates) > 1:
            # Нічия - нікого не виключаємо
            await context.bot.send_message(
                chat_id=chat_id,
                text="🤝 <b>НІЧИЯ!</b>\n\nНіхто не має більшості. Настає ніч...",
                parse_mode=ParseMode.HTML
            )
            await asyncio.sleep(2)
            await start_night(context, chat_id)
            return
        
        nominee_id = candidates[0]
        game['vote_nominee'] = nominee_id
        
        nominee_name = all_players[nominee_id]['username']
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎯 <b>ВИСУВАЄМО НА ВИКЛЮЧЕННЯ:</b>\n\n"
                 f"👤 <b>{nominee_name}</b>\n\n"
                 f"🗳 Голосуємо ЗА або ПРОТИ виключення:",
            parse_mode=ParseMode.HTML
        )
        
        await start_final_voting(context, chat_id)


async def start_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Фінальне голосування ЗА/ПРОТИ"""
    game = mafia_game.games[chat_id]
    game['phase'] = 'final_voting'
    game['vote_results'] = {}
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_players = {uid: pinfo for uid, pinfo in all_players.items() if pinfo['alive']}
    
    nominee_name = all_players[game['vote_nominee']]['username']
    
    # Відправка кнопок фінального голосування
    for user_id, player_info in alive_players.items():
        if player_info['is_bot']:
            continue
        
        keyboard = [
            [InlineKeyboardButton("✅ ЗА виключення", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_yes")],
            [InlineKeyboardButton("❌ ПРОТИ виключення", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_no")]
        ]
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🗳 <b>ФІНАЛЬНЕ ГОЛОСУВАННЯ:</b>\n\n"
                     f"👤 Кандидат: <b>{nominee_name}</b>\n\n"
                     f"Ваше рішення:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка фінального голосування {user_id}: {e}")
    
    # Боти голосують
    await process_bot_final_votes(context, chat_id)
    
    # Таймер на 30 секунд
    context.job_queue.run_once(check_final_voting_complete, when=30, chat_id=chat_id, name=f"final_vote_{chat_id}")


async def check_final_voting_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення фінального голосування"""
    game = mafia_game.games[chat_id]
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_count = sum(1 for p in all_players.values() if p['alive'])
    
    # ВИПРАВЛЕННЯ: Додано перевірку наявності голосів і правильну логіку завершення
    if len(game['vote_results']) >= alive_count and not game.get('final_voting_done'):
        await process_final_voting(context, chat_id)


async def process_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка фінального голосування"""
    game = mafia_game.games[chat_id]
    
    # ВИПРАВЛЕННЯ: Додано перевірку щоб не обробляти два рази
    if game.get('final_voting_done'):
        return
    game['final_voting_done'] = True
    
    yes_votes = sum(1 for v in game['vote_results'].values() if v == 'yes')
    no_votes = sum(1 for v in game['vote_results'].values() if v == 'no')
    total_votes = yes_votes + no_votes
    
    nominee_id = game['vote_nominee']
    nominee_name = mafia_game.get_all_players(chat_id)[nominee_id]['username']
    
    if yes_votes > no_votes:
        # Виключення
        all_players = mafia_game.get_all_players(chat_id)
        all_players[nominee_id]['alive'] = False
        game['alive_players'].discard(nominee_id)
        
        nominee_role = mafia_game.get_role_info(all_players[nominee_id]['role'])
        
        result_text = f"""
⚖️ <b>РЕЗУЛЬТАТИ ГОЛОСУВАННЯ:</b>

👤 <b>{nominee_name}</b> ВИКЛЮЧЕНО!
🎭 Роль: {nominee_role['emoji']} {nominee_role['full_name']}

📊 Голоси: {yes_votes} ЗА, {no_votes} ПРОТИ
"""
        
        await send_gif(context, chat_id, 'death', result_text)
        
        # Перевірка перемоги
        if await check_victory(context, chat_id):
            return
            
    else:
        # Не виключено
        result_text = f"""
⚖️ <b>РЕЗУЛЬТАТИ ГОЛОСУВАННЯ:</b>

👤 <b>{nominee_name}</b> ЗАЛИШАЄТЬСЯ!

📊 Голоси: {yes_votes} ЗА, {no_votes} ПРОТИ
"""
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            parse_mode=ParseMode.HTML
        )
    
    await asyncio.sleep(3)
    await start_night(context, chat_id)


# ============================================
# НІЧНІ ДІЇ ГРАВЦІВ
# ============================================

async def night_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка нічних дій гравців"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    action = data[1]  # kill, heal, check
    target_id = int(data[2])
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night':
        await query.edit_message_text("⚠️ Ніч вже закінчилась!")
        return
    
    # Зберігаємо дію
    game['night_actions'][user_id] = {
        'action': action,
        'target': target_id
    }
    
    all_players = mafia_game.get_all_players(chat_id)
    target_name = all_players[target_id]['username']
    
    action_text = {
        'kill': f"🔪 Ви обрали жертву: {target_name}",
        'heal': f"💉 Ви вирішили врятувати: {target_name}",
        'check': f"🔍 Ви вирішили перевірити: {target_name}"
    }
    
    await query.edit_message_text(
        f"✅ <b>ВИБІР ЗРОБЛЕНО!</b>\n\n{action_text.get(action, 'Дія збережена')}",
        parse_mode=ParseMode.HTML
    )
    
    # Повідомлення в чат (без розкриття ролі)
    user_name = game['players'][user_id]['username']
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🤖 <b>{user_name}</b> зробив свій вибір...",
        parse_mode=ParseMode.HTML
    )
    
    await check_night_complete(context, chat_id)


async def check_night_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка чи всі зробили нічні дії"""
    game = mafia_game.games[chat_id]
    
    all_players = mafia_game.get_all_players(chat_id)
    alive_humans = [uid for uid, pinfo in all_players.items() 
                   if pinfo['alive'] and not pinfo['is_bot']]
    
    # Перевіряємо чи всі живі люди зробили дії
    humans_with_actions = [uid for uid in alive_humans 
                          if uid in game['night_actions']]
    
    if len(humans_with_actions) >= len(alive_humans):
        # Всі зробили дії - можна завершувати ніч
        if not game.get('night_resolved', False):
            game['night_resolved'] = True
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="✅ <b>УСІ ЗРОБИЛИ ВИБІР!</b>\n\n📊 Обробляємо результати...",
                parse_mode=ParseMode.HTML
            )
            
            await process_night(context, chat_id)


# ============================================
# ПЕРЕВІРКА ПЕРЕМОГИ
# ============================================

async def check_victory(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Перевірка умов перемоги"""
    game = mafia_game.games[chat_id]
    all_players = mafia_game.get_all_players(chat_id)
    
    alive_players = {uid: pinfo for uid, pinfo in all_players.items() if pinfo['alive']}
    
    mafia_alive = [uid for uid, pinfo in alive_players.items() 
                  if mafia_game.get_role_info(pinfo['role'])['team'] == 'mafia']
    citizens_alive = [uid for uid, pinfo in alive_players.items() 
                     if mafia_game.get_role_info(pinfo['role'])['team'] == 'citizens']
    
    if not mafia_alive:
        # Перемога мирних
        victory_text = """
🎉 <b>ПЕРЕМОГА МИРНИХ!</b> 🎉

🏆 Мафія повністю знищена!
🕊 Село врятоване!

👏 Дякую за гру!
"""
        await send_gif(context, chat_id, 'victory', victory_text)
        mafia_game.end_game(chat_id)
        return True
    
    elif len(mafia_alive) >= len(citizens_alive):
        # Перемога мафії
        victory_text = """
😈 <b>ПЕРЕМОГА МАФІЇ!</b> 😈

🔪 Мафія захопила контроль!
💀 Село підкорене!

👏 Дякую за гру!
"""
        await send_gif(context, chat_id, 'victory', victory_text)
        mafia_game.end_game(chat_id)
        return True
    
    return False


# ============================================
# СПЕЦІАЛЬНІ ПОДІЇ - КАРТОПЛЯ
# ============================================

async def potato_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка кидка картоплі"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    target_id = int(data[2])
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night':
        await query.edit_message_text("⚠️ Зараз не можна кидати картоплю!")
        return
    
    if game['special_event'] != 'bukovel':
        await query.edit_message_text("⚠️ Зараз немає картоплі!")
        return
    
    all_players = mafia_game.get_all_players(chat_id)
    
    if user_id not in all_players or not all_players[user_id]['alive']:
        await query.edit_message_text("⚠️ Ви не можете кидати картоплю!")
        return
    
    # Зберігаємо кидок
    game['potato_throws'][user_id] = target_id
    
    target_name = all_players[target_id]['username']
    
    # ВИПРАВЛЕННЯ: Прибрано розкриття ролі бота
    await query.edit_message_text(
        f"🥔 <b>КАРТОПЛЯ ВІДПРАВЛЕНА!</b>\n\n"
        f"Ціль: {target_name}\n\n"
        f"💥 Чекаємо на результат...",
        parse_mode=ParseMode.HTML
    )
    
    # Повідомлення в чат (без розкриття хто кинув)
    await context.bot.send_message(
        chat_id=chat_id,
        text="🥔 <i>Десь у темряві пролетіла картопля...</i>",
        parse_mode=ParseMode.HTML
    )


# ============================================
# РЕЄСТРАЦІЯ ОБРОБНИКІВ
# ============================================

def setup_handlers(application: Application):
    """Реєстрація всіх обробників"""
    
    # Команди
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("newgame", newgame))
    application.add_handler(CommandHandler("endgame", endgame))
    application.add_handler(CommandHandler("status", status))
    
    # Колбеки
    application.add_handler(CallbackQueryHandler(join_game_callback, pattern="^join_game$"))
    application.add_handler(CallbackQueryHandler(add_bots_menu_callback, pattern="^add_bots_menu$"))
    application.add_handler(CallbackQueryHandler(add_bots_callback, pattern="^add_bots_"))
    application.add_handler(CallbackQueryHandler(leave_game_callback, pattern="^leave_game$"))
    application.add_handler(CallbackQueryHandler(start_game_callback, pattern="^start_game$"))
    application.add_handler(CallbackQueryHandler(back_to_game_callback, pattern="^back_to_game$"))
    
    # Нічні дії
    application.add_handler(CallbackQueryHandler(night_action_callback, pattern="^night_(kill|heal|check)_"))
    application.add_handler(CallbackQueryHandler(vote_callback, pattern="^(nominate|votefor)_"))
    
    # Картопля
    application.add_handler(CallbackQueryHandler(potato_callback, pattern="^potato_"))
    
    # Блокування повідомлень від мертвих
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_dead_player_message))


async def back_to_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повернення до головного меню"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    if chat_id in mafia_game.games:
        await update_game_message(context, chat_id)
    else:
        await query.edit_message_text("🎮 <b>ГРА: МАФІЯ</b> 🎮\n\nГру завершено!")


# ============================================
# ГОЛОВНА ФУНКЦІЯ
# ============================================

if __name__ == "__main__":
    # Тут має бути ініціалізація бота
    pass