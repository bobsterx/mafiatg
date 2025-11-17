"""Telegram handlers and game flow for the Mafia bot.

У цьому файлі зібрані всі обробники команд, кнопок,
нічних дій, голосувань і допоміжні функції.
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
from typing import Optional

from config import ROLES, DEATH_PHRASES, SAVED_PHRASES, MAFIA_PHRASES, DISCUSSION_PHRASES
from game_state import mafia_game

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


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
                    text="💀 <b>ТИ МЕРТВИЙ!</b>\n\nНе можеш писати в чат до кінця гри.\n🤐 Дотримуйся правил!",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Помилка видалення повідомлення мертвого гравця: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - показує головне меню"""
    if update.message and update.message.chat.type != 'private':
        await update.message.reply_text(
            "👋 <b>Вітаю в грі МАФІЯ!</b>\n\n"
            "📝 Щоб керувати грою, напишіть мені /start в особистих повідомленнях!\n"
            "🎮 Команди в групі:\n"
            "   /newgame - створити нову гру\n"
            "   /endgame - завершити поточну гру\n"
            "   /status - статус гри\n\n"
            "💡 <b>Важливо:</b> Спочатку напишіть боту в особисті повідомлення /start, "
            "щоб він міг надсилати вам повідомлення!",
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
- Детектив може вистрілити (1 раз)
- Рандомні перки (5% шанс)
- Мотузка може порватись
- Помилка детектива можлива

Оберіть розділ нижче! 👇
"""
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /newgame - створення гри в групі"""
    if update.message.chat.type == 'private':
        await update.message.reply_text(
            "⚠️ Ця команда працює тільки в груповому чаті!\n\n"
            "Додайте бота в групу і створіть гру там.",
            parse_mode=ParseMode.HTML
        )
        return
    
    chat_id = update.message.chat_id
    admin_id = update.message.from_user.id
    
    if chat_id in mafia_game.games and mafia_game.games[chat_id]['started']:
        await update.message.reply_text(
            "⚠️ <b>Гра вже йде!</b>\n\n"
            "Завершіть поточну гру командою /endgame",
            parse_mode=ParseMode.HTML
        )
        return
    
    mafia_game.create_game(chat_id, admin_id)
    
    announcement_keyboard = [
        [InlineKeyboardButton("➕ ПРИЄДНАТИСЯ ДО ГРИ", callback_data="join_game")],
        [InlineKeyboardButton("🎯 ПОЧАТИ ГРУ", callback_data="start_game")],
        [InlineKeyboardButton("❌ ВИЙТИ З ГРИ", callback_data="leave_game")],
    ]
    
    announcement_text = """
🎮🎮🎮 <b>НОВА ГРА СТВОРЕНА!</b> 🎮🎮🎮

🎭 <b>МАФІЯ</b> запрошує гравців!

<b>🎯 Правила:</b>
- Мінімум 5 гравців
- Максимум 15 гравців
- Гра триває до перемоги однієї з команд

<b>🎮 Персонажі:</b>
🌾 Демян - мирний житель
👑 Кішкель - дон мафії
🔫 Ігор Рогальський - мафіозі
💉 Федорчак - лікар
🔍 Детектив - шукач правди + стрілець

<b>🎲 Нові можливості:</b>
- Детектив має 1 кулю на гру
- Рандомні перки (5% шанс)
- Помилка детектива можлива
- Мотузка може порватись

<b>👥 Гравці (0/15):</b>
<i>Поки що немає...</i>

<b>⚠️ ВАЖЛИВО:</b> Напишіть боту /start в особистих повідомленнях!

👇 <b>ПРИЄДНУЙТЕСЬ ЗАРАЗ!</b> 👇
"""
    
    msg = await update.message.reply_text(
        announcement_text,
        reply_markup=InlineKeyboardMarkup(announcement_keyboard),
        parse_mode=ParseMode.HTML
    )
    
    mafia_game.game_messages[chat_id] = msg.message_id

async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приєднання до гри через кнопку"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Створіть гру командою /newgame!", show_alert=True)
        return
    
    game = mafia_game.games[chat_id]
    
    if len(game['players']) >= 15:
        await query.answer("⚠️ Гра повна! Максимум 15 гравців.", show_alert=True)
        return
    
    if mafia_game.add_player(chat_id, user_id, username):
        player_count = len(game['players'])
        
        try:
            welcome_msg = f"""
✅ <b>ВИ В ГРІ!</b> 🎉

Вітаємо, <b>{username}</b>!

Ви успішно приєдналися до гри МАФІЯ!

🎮 <b>Що далі?</b>
- Чекайте на початок гри
- Отримаєте свою роль в особисті повідомлення
- Грайте та перемагайте!

<b>👥 Гравців зараз:</b> {player_count}
{'⏳ Потрібно ще ' + str(5 - player_count) + ' гравців' if player_count < 5 else '✅ Можна починати!'}

<i>Удачі в грі! 🍀</i>
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
                "⚠️ Я не можу надіслати вам повідомлення!\n"
                "Напишіть мені /start в особистих повідомленнях спочатку!",
                show_alert=True
            )
            return
        
        await update_game_message(context, chat_id)
        await query.answer(f"✅ {username} приєднався! 🎉")
    else:
        await query.answer("⚠️ Ви вже в грі!", show_alert=True)

async def leave_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вихід з гри через кнопку"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Активна гра не знайдена!", show_alert=True)
        return
    
    if mafia_game.remove_player(chat_id, user_id):
        await update_game_message(context, chat_id)
        await query.answer(f"👋 {username} вийшов з гри")
    else:
        await query.answer("⚠️ Ви не в грі або гра вже почалась!", show_alert=True)

async def update_game_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Оновлення повідомлення про гру"""
    if chat_id not in mafia_game.games or chat_id not in mafia_game.game_messages:
        return
    
    game = mafia_game.games[chat_id]
    player_count = len(game['players'])
    
    announcement_keyboard = [
        [InlineKeyboardButton("➕ ПРИЄДНАТИСЯ ДО ГРИ", callback_data="join_game")],
        [InlineKeyboardButton("🎯 ПОЧАТИ ГРУ", callback_data="start_game")],
        [InlineKeyboardButton("❌ ВИЙТИ З ГРИ", callback_data="leave_game")],
    ]
    
    players_list = "\n".join([
        f"{i}. ✅ <b>{pinfo['username']}</b>" 
        for i, pinfo in enumerate(game['players'].values(), 1)
    ]) if game['players'] else "<i>Поки що немає...</i>"
    
    updated_text = f"""
🎮 <b>ГРА: МАФІЯ</b> 🎮

<b>👥 Гравці ({player_count}/15):</b>
{players_list}

{'⏳ <b>Потрібно ще ' + str(5 - player_count) + ' гравців для старту</b>' if player_count < 5 else '🔥 <b>Можна починати гру!</b>'}

<b>⚠️ ВАЖЛИВО:</b> Напишіть боту /start в особистих повідомленнях!

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

async def start_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок гри через кнопку"""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if chat_id not in mafia_game.games:
        await query.answer("⚠️ Створіть гру командою /newgame!", show_alert=True)
        return

    game = mafia_game.games[chat_id]

    # Перевірка прав на запуск гри
    if user_id != game['admin_id']:
        try:
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            if chat_member.status not in ['creator', 'administrator']:
                await query.answer("⚠️ Тільки адміністратори можуть почати гру!", show_alert=True)
                return
        except Exception:
            await query.answer("⚠️ Тільки адміністратори можуть почати гру!", show_alert=True)
            return

    if len(game['players']) < 5:
        await query.answer(
            f"⚠️ Недостатньо гравців!\nПотрібно: 5\nЄ зараз: {len(game['players'])}", 
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
        "⏳ Ролі розподіляються...\n"
        "📨 Перевірте особисті повідомлення!\n\n"
        "🌙 Настає перша ніч...", 
        parse_mode=ParseMode.HTML
    )

    # Надсилання ролей гравцям з перевіркою
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

{'🔪 <b>Ви граєте за мафію!</b> Ваша мета - знищити мирних жителів.' if role_info['team'] == 'mafia' else '⚔️ <b>Ви граєте за мирних!</b> Ваша мета - знайти і виключити всю мафію.'}

⏳ Чекайте на початок нічної фази...
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

    # Видаляємо гравців, яким не вдалося надіслати роль
    if failed_users:
        for uid in failed_users:
            game['players'].pop(uid, None)
            game['alive_players'].discard(uid)

        if len(game['players']) < 5:
            game['started'] = False
            game['phase'] = 'registration'
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ <b>Гра не може бути продовжена.</b>\n\n"
                    "Деяким гравцям не вдалося надіслати ролі (вони могли заблокувати бота)."
                    "\nПісля видалення цих гравців залишилось менше 5 учасників.\n\n"
                    "🙋 Попросіть усіх гравців написати боту /start в особисті повідомлення "
                    "та створіть нову гру командою /newgame."
                ),
                parse_mode=ParseMode.HTML
            )
            return

    # Оновлюємо список живих гравців
    game['alive_players'] = {uid for uid, p in game['players'].items() if p['alive']}

    # Запускаємо першу ніч
    await night_phase(context, chat_id)

async def night_phase(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Нічна фаза: надсилання дій ролям та запуск таймера (45 секунд)."""
    if chat_id not in mafia_game.games:
        return

    game = mafia_game.games[chat_id]
    game['phase'] = 'night'
    game['night_actions'] = {}
    game['detective_shot_this_night'] = None
    game['perks_messages'] = []
    game['night_resolved'] = False

    night_phrase = random.choice(MAFIA_PHRASES)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🌙 <b>━━━ НІЧ {game['day_number']} ━━━</b> 🌙\n\n"
            f"{night_phrase}\n\n"
            f"⏳ Гравці виконують свої дії...\n"
            f"🤫 Тиша в селі..."
        ),
        parse_mode=ParseMode.HTML
    )

    # Таймаут ночі: якщо хтось не зробить дію, ніч завершиться автоматично
    context.job_queue.run_once(night_timeout, when=45, chat_id=chat_id)

    # Розсилка дій за ролями
    for user_id, player_info in game['players'].items():
        if not player_info['alive']:
            continue

        role_key = player_info['role']
        role_info = mafia_game.get_role_info(role_key)
        action = role_info.get('action')

        if not action:
            continue

        # Формуємо список цілей залежно від ролі
        if action == 'kill':
            targets = [
                (uid, pinfo) for uid, pinfo in game['players'].items()
                if pinfo['alive'] and mafia_game.get_role_info(pinfo['role'])['team'] == 'citizens'
            ]
        elif action == 'heal':
            targets = [
                (uid, pinfo) for uid, pinfo in game['players'].items()
                if pinfo['alive'] and (uid != user_id or game['last_healed'] != user_id)
            ]
        else:  # check
            targets = [
                (uid, pinfo) for uid, pinfo in game['players'].items()
                if pinfo['alive'] and uid != user_id
            ]

        if not targets:
            continue

        keyboard = []
        for target_id, target_info in targets:
            button_emoji = {
                'kill': '💀',
                'heal': '💉',
                'check': '🔍'
            }.get(action, '👤')

            keyboard.append([InlineKeyboardButton(
                f"{button_emoji} {target_info['username']}",
                callback_data=f"night_{action}_{chat_id}_{target_id}"
            )])

        # Для детектива додаємо кнопку стрільби
        if action == 'check' and not game['detective_bullet_used']:
            keyboard.append([InlineKeyboardButton(
                "🔫 ВИСТРІЛИТИ (1 куля на гру)",
                callback_data=f"night_shoot_{chat_id}_menu"
            )])

        reply_markup = InlineKeyboardMarkup(keyboard)

        action_texts = {
            'kill': (
                "🔫 <b>━━━ ВИБІР ЖЕРТВИ ━━━</b> 🔫\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                f"{random.choice(MAFIA_PHRASES)}\n\n"
                "Оберіть гравця для вбивства:"
            ),
            'heal': (
                "💉 <b>━━━ РОБОТА ЛІКАРЯ ━━━</b> 💉\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                "Кого ви хочете врятувати цієї ночі?\n\n"
                + ("⚠️ Ви не можете лікувати себе два рази поспіль!" if game['last_healed'] == user_id else "")
            ),
            'check': (
                "🔍 <b>━━━ РОЗСЛІДУВАННЯ ━━━</b> 🔍\n\n"
                f"🌙 Ніч {game['day_number']}\n\n"
                "Кого ви хочете перевірити?\n\n"
                "⚠️ Пам'ятайте: Дон має імунітет!\n\n"
                + ("🔫 Або можете використати кулю!" if not game['detective_bullet_used'] else "❌ Куля вже використана")
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
            logger.error(f"Помилка надсилання нічних дій {{user_id}}: {{e}}")


async def night_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Таймаут ночі (45 секунд): якщо не всі зробили дію, ніч все одно завершується."""
    chat_id = context.job.chat_id
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night' or game.get('night_resolved'):
        return

    game['night_resolved'] = True
    await context.bot.send_message(
        chat_id=chat_id,
        text="⏰ <b>Час ночі вичерпано.</b> Обробляємо результати...", 
        parse_mode=ParseMode.HTML
    )
    await process_night(context, chat_id)
async def night_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка нічних дій гравців"""
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    action = data[1]
    chat_id = int(data[2])
    target_id = int(data[3]) if len(data) > 3 and data[3].isdigit() else 0
    user_id = query.from_user.id
    
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'night':
        await query.edit_message_text("⚠️ Нічна фаза вже завершилась!")
        return
    
    # Обробка стрільби детектива
    if action == 'shoot':
        if data[3] == 'menu':
            # Показуємо меню вибору цілі для стрільби
            alive_players = [(uid, pinfo) for uid, pinfo in game['players'].items() 
                            if pinfo['alive'] and uid != user_id]
            
            shoot_keyboard = []
            for tid, tinfo in alive_players:
                shoot_keyboard.append([InlineKeyboardButton(
                    f"🔫 {tinfo['username']}",
                    callback_data=f"night_shoot_{chat_id}_{tid}"
                )])
            shoot_keyboard.append([InlineKeyboardButton(
                "🔙 Назад до перевірки",
                callback_data=f"night_back_{chat_id}"
            )])
            
            await query.edit_message_text(
                "🔫 <b>━━━ ВИБІР ЦІЛІ ДЛЯ СТРІЛЬБИ ━━━</b> 🔫\n\n"
                "⚠️ <b>У вас тільки одна куля на всю гру!</b>\n\n"
                "💀 Якщо вистрілите - всі дізнаються вранці\n"
                "🕯 Детектив відкриває вогонь...\n\n"
                "Оберіть ціль мудро:",
                reply_markup=InlineKeyboardMarkup(shoot_keyboard),
                parse_mode=ParseMode.HTML
            )
            return
        else:
            # Виконуємо постріл
            game['detective_bullet_used'] = True
            game['detective_shot_this_night'] = target_id
            game['night_actions'][user_id] = {
                'action': 'shoot',
                'target': target_id
            }
            
            target_name = game['players'][target_id]['username']
            
            await query.edit_message_text(
                f"🔫 <b>━━━ ПОСТРІЛ ЗДІЙСНЕНО! ━━━</b> 🔫\n\n"
                f"🎯 <b>Ціль:</b> {target_name}\n\n"
                f"💥 Вистріл пролунав у темряві...\n"
                f"🕯 Хтось сьогодні не проснеться...\n\n"
                f"⏳ Результати вранці.\n\n"
                f"<i>Куля витрачена. Більше не можете стріляти.</i>",
                parse_mode=ParseMode.HTML
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔫 <i>Десь у темряві пролунав постріл...</i> 💥",
                parse_mode=ParseMode.HTML
            )
            
            await check_night_complete(context, chat_id)
            return
    
    # Обробка кнопки "Назад"
    if action == 'back':
        await night_phase(context, chat_id)
        await query.edit_message_text("🔄 Повертаємось до вибору дії...")
        return
    
    # Звичайні дії (вбивство, лікування, перевірка)
    game['night_actions'][user_id] = {
        'action': action,
        'target': target_id
    }
    
    action_names = {
        'kill': 'вбивство',
        'heal': 'лікування',
        'check': 'перевірку'
    }
    
    action_emojis = {
        'kill': '💀',
        'heal': '💉',
        'check': '🔍'
    }
    
    target_name = game['players'][target_id]['username']
    
    confirmation_text = f"""
✅ <b>ДІЮ ПІДТВЕРДЖЕНО!</b>

{action_emojis[action]} <b>Ціль:</b> {target_name}
🎯 <b>Дія:</b> {action_names[action]}

⏳ Чекаємо на дії інших гравців...

<i>Ви можете закрити це повідомлення</i>
"""
    
    await query.edit_message_text(confirmation_text, parse_mode=ParseMode.HTML)
    
    player_role = game['players'][user_id]['role']
    role_info = mafia_game.get_role_info(player_role)
    
    choice_messages = {
        'kill': f"🌙 {role_info['emoji']} <b>Мафія</b> зробила свій вибір... 😈",
        'heal': f"🌙 💉 <b>Федорчак</b> зробив свій вибір... 🏥",
        'check': f"🌙 🔍 <b>Детектив</b> зробив свій вибір... 🕵️"
    }
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=choice_messages[action],
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Помилка надсилання повідомлення про вибір: {e}")
    
    await check_night_complete(context, chat_id)

async def check_night_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення нічної фази"""
    game = mafia_game.games[chat_id]
    
    required_actions = 0
    for player_info in game['players'].values():
        if player_info['alive']:
            role_info = mafia_game.get_role_info(player_info['role'])
            if role_info['action']:
                required_actions += 1
    
    if len(game['night_actions']) >= required_actions and not game.get('night_resolved'):
        game['night_resolved'] = True
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ <b>Всі зробили свій вибір!</b>\n\n⏳ Обробка нічних подій...",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(2)
        await process_night(context, chat_id)


async def process_night(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка результатів нічної фази з урахуванням перків, лікування і пострілу детектива."""
    game = mafia_game.games[chat_id]

    mafia_target: Optional[int] = None
    healed_target: Optional[int] = None
    check_results = []
    detective_shot: Optional[int] = None
    shot_happened = False

    # Розбираємо всі нічні дії
    for user_id, action_info in game['night_actions'].items():
        action = action_info['action']
        target = action_info['target']

        if action == 'kill':
            mafia_target = target
        elif action == 'heal':
            healed_target = target
            game['last_healed'] = healed_target
        elif action == 'check':
            target_role_key = game['players'][target]['role']
            role_info = mafia_game.get_role_info(target_role_key)

            # Перк: Помилка детектива (5% шанс)
            detective_error = random.random() < 0.05

            # Дон має імунітет до перевірки
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
            shot_happened = True

    # Хто помирає цієї ночі
    victims = set()
    saved = False
    mafia_misfire = False

    # Постріл мафії
    if mafia_target is not None:
        # Перк: осічка мафії (5%)
        if random.random() < 0.05:
            mafia_misfire = True
            game['perks_messages'].append(
                "🎲 <b>ПЕРК: ОСІЧКА МАФІЇ!</b>\n🔫❌ Зброя заклинила, жертва врятована!"
            )
        else:
            victims.add(mafia_target)

    # Лікар рятує від мафії
    if healed_target is not None and mafia_target is not None and mafia_target == healed_target and mafia_target in victims:
        victims.remove(healed_target)
        saved = True
        game['perks_messages'].append("💉 <b>Лікар врятував жертву мафії!</b>")

    # Лікування від пострілу детектива
    if detective_shot is not None:
        if healed_target is not None and healed_target == detective_shot:
            saved = True
            game['perks_messages'].append("💉 <b>Федорчак врятував того, в кого стріляв детектив!</b>")
            detective_shot = None  # Куля не вбила
        else:
            # Детектив все ж когось вбиває
            if detective_shot is not None:
                if detective_shot in victims:
                    # Мафія і детектив в одну ціль
                    game['perks_messages'].append(
                        "🔫 <b>Детектив відкрив вогонь!</b>\n💀 Постріл і вбивство в одну ціль!"
                    )
                else:
                    # Додаткова жертва
                    game['perks_messages'].append(
                        "🔫 <b>Детектив відкрив вогонь!</b>\n💀 Постріл забрав ще одне життя!"
                    )
                victims.add(detective_shot)

    # Зберігаємо факт осічки в статистику гри
    game['mafia_misfire'] = mafia_misfire

    # Застосовуємо смерті
    for vid in victims:
        game['players'][vid]['alive'] = False
        game['alive_players'].discard(vid)

    # Надсилання результатів детективу
    for detective_id, target_id, is_mafia, had_error in check_results:
        target_name = game['players'][target_id]['username']

        result_text = f"""
🔍 <b>━━━ РЕЗУЛЬТАТ РОЗСЛІДУВАННЯ ━━━</b> 🔍

<b>Перевірений гравець:</b> {target_name}

<b>Результат:</b>
{'🔴 <b>МАФІЯ!</b> Це злочинець!' if is_mafia else '🔵 <b>МИРНИЙ ЖИТЕЛЬ!</b> Чесна людина.'}

{'⚠️ <b>Будьте обережні з цією інформацією!</b>' if is_mafia else '✅ Цій людині можна довіряти.'}

<i>Використовуйте цю інформацію розумно!</i>
"""
        try:
            await context.bot.send_message(
                chat_id=detective_id,
                text=result_text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка надсилання результату детективу: {e}")

    # День починається
    game['phase'] = 'day'

    await context.bot.send_message(
        chat_id=chat_id,
        text="🌅 <i>Перші промені сонця пробиваються крізь хмари...</i>\n"
             "🐓 <i>Співають півні...</i>\n"
             "🏘 <i>Село прокидається...</i>",
        parse_mode=ParseMode.HTML
    )
    await asyncio.sleep(2)

    # Формуємо блок з перками
    perks_block = ""
    if game['perks_messages']:
        perks_block = (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            + "\n".join(game['perks_messages'])
            + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    # Оголошення результатів ночі
    if victims:
        # Якщо декілька жертв – виводимо всіх
        if len(victims) == 1:
            killed = next(iter(victims))
            killed_name = game['players'][killed]['username']
            killed_role = mafia_game.get_role_info(game['players'][killed]['role'])

            # Перевірка чи детектив помилився саме по цій жертві
            detective_mistake_msg = ""
            if game['detective_error_target'] == killed:
                detective_mistake_msg = (
                    "\n\n🔍❌ <b>ПОМИЛКА ДЕТЕКТИВА!</b>\n"
                    "Тієї ночі детектив перевіряв цю людину і помилився!\n"
                    "💀 <b>Смерть на совісті детектива...</b>"
                )

            death_phrase = random.choice(DEATH_PHRASES)

            night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

💀 <b>ТРАГІЧНА НОВИНА!</b> 💀

<i>Жителі села виявили страшну знахідку...</i>

💀 <b>Загинув:</b> {killed_name}
🎭 <b>Роль:</b> {killed_role['emoji']} {killed_role['full_name']}

{death_phrase}{detective_mistake_msg}{perks_block}

🗣 <b>ЧАС ДЛЯ ОБГОВОРЕННЯ!</b> (60 секунд)

{random.choice(DISCUSSION_PHRASES)}

<i>Обговорюйте, аналізуйте, шукайте винних!</i>
"""
        else:
            lines = []
            for vid in victims:
                pinfo = game['players'][vid]
                rinfo = mafia_game.get_role_info(pinfo['role'])
                lines.append(f"💀 <b>{pinfo['username']}</b> — {rinfo['emoji']} {rinfo['full_name']}")
            victims_block = "\n".join(lines)

            night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

💀 <b>КРИВАВА НІЧ!</b> 💀

<i>Цієї ночі було декілька жертв...</i>

{victims_block}{perks_block}

🗣 <b>ЧАС ДЛЯ ОБГОВОРЕННЯ!</b> (60 секунд)

{random.choice(DISCUSSION_PHRASES)}

<i>Ситуація загострюється. Шукайте мафію!</i>
"""
    elif saved:
        # Хтось був врятований
        saved_name = game['players'][healed_target]['username'] if healed_target is not None else "Невідомий"
        saved_phrase = random.choice(SAVED_PHRASES)

        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

🎉 <b>ДИВО СТАЛОСЯ!</b> 🎉

<i>Цієї ночі планувалось вбивство...</i>

💉 Але <b>Федорчак</b> був на чеку!

✨ <b>{saved_name}</b> врятовано! ✨

{saved_phrase}

<i>Лікар зробив свою роботу бездоганно!</i>{perks_block}

🗣 <b>ЧАС ДЛЯ ОБГОВОРЕННЯ!</b> (60 секунд)

{random.choice(DISCUSSION_PHRASES)}

<i>Хто ж намагався вбити? Шукайте підозрілих!</i>
"""
    else:
        # Спокійна ніч
        night_result = f"""
☀️ <b>━━━━━ РАНОК ДНЯ {game['day_number']} ━━━━━</b> ☀️

😌 <b>СПОКІЙНА НІЧ!</b> 😌

<i>Всі жителі села прокинулись живими та здоровими!</i>

🕊 Цієї ночі ніхто не постраждав 🕊

✨ <i>Можливо мафія передумала?
Або просто сталось диво?</i> ✨{perks_block}

🗣 <b>ЧАС ДЛЯ ОБГОВОРЕННЯ!</b> (60 секунд)

{random.choice(DISCUSSION_PHRASES)}

<i>Хоча нікого не вбили, мафія все ще серед нас!</i>
"""

    await context.bot.send_message(
        chat_id=chat_id,
        text=night_result,
        parse_mode=ParseMode.HTML
    )

    # Перевірка перемоги після ночі
    if await check_victory(context, chat_id):
        return

    # Через 60 секунд після оголошення результатів ночі починається голосування
    context.job_queue.run_once(
        discussion_timeout,
        when=60,
        chat_id=chat_id
    )


async def start_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Початок голосування - вибір кандидата"""
    game = mafia_game.games.get(chat_id)
    if not game or game['phase'] != 'day':
        return
    
    game['final_voting_done'] = False
    
    game['phase'] = 'voting'
    game['vote_nominee'] = None
    game['votes'] = {}
    
    alive_players = [(uid, pinfo) for uid, pinfo in game['players'].items() if pinfo['alive']]
    
    # Надсилання кнопок вибору кандидата
    for user_id, player_info in alive_players:
        keyboard = []
        
        for target_id, target_info in alive_players:
            if target_id != user_id:
                keyboard.append([InlineKeyboardButton(
                    f"👤 {target_info['username']}",
                    callback_data=f"nominate_{chat_id}_{target_id}"
                )])
        
        keyboard.append([InlineKeyboardButton(
            "🚫 Нікого не висувати",
            callback_data=f"nominate_{chat_id}_0"
        )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        voting_text = f"""
🗳 <b>━━━ ВИСУНЕННЯ КАНДИДАТА ━━━</b> 🗳

<b>День {game['day_number']}</b>

Оберіть кого висунути на повішення:

⚠️ Після цього буде голосування ЗА/ПРОТИ

<b>👥 Живих гравців:</b> {len(alive_players)}

<i>Висуньте підозрілого!</i>
"""
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=voting_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка висунення для {user_id}: {e}")
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="🗳 <b>ВИСУНЕННЯ КАНДИДАТІВ!</b>\n\n"
             "📨 Перевірте особисті повідомлення!\n"
             "⏳ Висувайте підозрілих...\n\n"
             "<i>Кожен голос важливий!</i>",
        parse_mode=ParseMode.HTML
    )

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
        await query.edit_message_text("⚠️ Голосування вже завершилось!")
        return
    
    # Висунення кандидата
    if action == 'nominate':
        game['votes'][user_id] = target_id
        
        if target_id == 0:
            vote_text = "✅ <b>ВИ НЕ ВИСУНУЛИ КАНДИДАТА</b>\n\n⏳ Чекаємо на інших..."
        else:
            target_name = game['players'][target_id]['username']
            vote_text = f"✅ <b>ВИ ВИСУНУЛИ:</b> {target_name}\n\n⏳ Чекаємо на інших..."
        
        await query.edit_message_text(vote_text, parse_mode=ParseMode.HTML)
        
        voter_name = game['players'][user_id]['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🗳 <b>{voter_name}</b> висунув кандидата!",
            parse_mode=ParseMode.HTML
        )
        
        await check_nominations_complete(context, chat_id)
    
    # Голосування ЗА/ПРОТИ
    elif action == 'votefor':
        vote = data[3]  # yes або no
        game['vote_results'][user_id] = vote
        
        nominee_name = game['players'][game['vote_nominee']]['username']
        
        if vote == 'yes':
            vote_text = f"✅ <b>ВИ ПРОГОЛОСУВАЛИ ЗА ПОВІШЕННЯ</b>\n\n👤 Кандидат: {nominee_name}\n\n⏳ Чекаємо на інших..."
        else:
            vote_text = f"✅ <b>ВИ ПРОГОЛОСУВАЛИ ПРОТИ ПОВІШЕННЯ</b>\n\n👤 Кандидат: {nominee_name}\n\n⏳ Чекаємо на інших..."
        
        await query.edit_message_text(vote_text, parse_mode=ParseMode.HTML)
        
        voter_name = game['players'][user_id]['username']
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🗳 <b>{voter_name}</b> проголосував!",
            parse_mode=ParseMode.HTML
        )
        
        await check_final_voting_complete(context, chat_id)

async def check_nominations_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення висунення кандидатів"""
    game = mafia_game.games[chat_id]
    
    alive_count = len(game['alive_players'])
    
    if len(game['votes']) >= alive_count:
        # Підрахунок висунень
        nominations = defaultdict(int)
        for nominated in game['votes'].values():
            if nominated != 0:
                nominations[nominated] += 1
        
        if not nominations:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🚫 <b>НІХТО НЕ ВИСУНУТИЙ!</b>\n\nПродовжуємо гру без виключення...",
                parse_mode=ParseMode.HTML
            )
            await asyncio.sleep(2)
            game['phase'] = 'night'
            game['day_number'] += 1
            await night_phase(context, chat_id)
            return
        
        # Знаходимо найбільш висунутого
        max_nominations = max(nominations.values())
        candidates = [uid for uid, count in nominations.items() if count == max_nominations]
        
        if len(candidates) > 1:
            nominee = random.choice(candidates)
        else:
            nominee = candidates[0]
        
        game['vote_nominee'] = nominee
        game['vote_results'] = {}
        
        nominee_name = game['players'][nominee]['username']
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📊 <b>ПІДРАХУНОК ВИСУНЕНЬ</b>\n\n"
                 f"👤 <b>Кандидат на повішення:</b> {nominee_name}\n"
                 f"🗳 Отримано висунень: {max_nominations}\n\n"
                 f"⚖️ <b>ПОЧИНАЄМО ГОЛОСУВАННЯ ЗА/ПРОТИ!</b>",
            parse_mode=ParseMode.HTML
        )
        
        await asyncio.sleep(2)
        await start_final_voting(context, chat_id)

async def start_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Фінальне голосування ЗА/ПРОТИ повішення"""
    game = mafia_game.games[chat_id]
    nominee_name = game['players'][game['vote_nominee']]['username']
    
    alive_players = [(uid, pinfo) for uid, pinfo in game['players'].items() if pinfo['alive']]
    
    for user_id, player_info in alive_players:
        keyboard = [
            [InlineKeyboardButton("✅ ТАК, ПОВІСИТИ", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_yes")],
            [InlineKeyboardButton("❌ НІ, ЗАХИСТИТИ", callback_data=f"votefor_{chat_id}_{game['vote_nominee']}_no")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        voting_text = f"""
⚖️ <b>━━━ ФІНАЛЬНЕ ГОЛОСУВАННЯ ━━━</b> ⚖️

🪢 <b>Вішаємо чи ні?</b>

👤 <b>Кандидат:</b> {nominee_name}

<b>Ваше рішення:</b>
✅ ТАК - повісити
❌ НІ - захистити

⚠️ Якщо більшість ЗА - гравця виключать!

<i>Голосуйте мудро!</i>
"""
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=voting_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Помилка голосування для {user_id}: {e}")
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚖️ <b>ГОЛОСУВАННЯ ЗА/ПРОТИ!</b>\n\n"
             f"👤 Кандидат: <b>{nominee_name}</b>\n\n"
             f"📨 Перевірте особисті повідомлення!\n"
             f"🪢 Доля гравця у ваших руках!",
        parse_mode=ParseMode.HTML
    )


async def check_final_voting_complete(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Перевірка завершення фінального голосування"""
    game = mafia_game.games[chat_id]
    
    alive_count = len(game['alive_players'])
    
    if len(game['vote_results']) >= alive_count and not game.get('final_voting_done'):
        await process_final_voting(context, chat_id)


async def process_final_voting(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Обробка результатів фінального голосування"""
    game = mafia_game.games[chat_id]

    if game.get('final_voting_done'):
        return
    game['final_voting_done'] = True
    
    yes_votes = sum(1 for v in game['vote_results'].values() if v == 'yes')
    no_votes = sum(1 for v in game['vote_results'].values() if v == 'no')
    
    # Визначаємо результат
    if yes_votes > no_votes:
        result_text = "✅ <b>Гравця буде повішено!</b>"
        
        if game['rope_break_save']:
            result_text += "\n\n🪢 <b>Але мотузка порвалась!</b>"
            game['rope_break_save'] = False
        else:
            game['players'][game['vote_nominee']]['alive'] = False
            game['alive_players'].discard(game['vote_nominee'])
            result_text += f"\n\n💀 <b>{game['players'][game['vote_nominee']]['username']}</b> був виключений з гри!"
    else:
        result_text = "❌ <b>Гравець залишається в живих!</b>"
    
    results_text = f"""
<b>Результати голосування:</b>

🧍 Гравець на шибениці: <b>{game['players'][game['vote_nominee']]['username']}</b>

✅ <b>ЗА повішення:</b> {yes_votes} голосів
❌ <b>ПРОТИ:</b> {no_votes} голосів

━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=results_text,
        parse_mode=ParseMode.HTML
    )
    
    await asyncio.sleep(2)
    
    # Наступна ніч
    game['phase'] = 'night'
    game['day_number'] += 1
    game['detective_error_target'] = None  # Скидання помилки детектива
    
    await asyncio.sleep(2)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🌙 <b>Настає ніч {game['day_number']}...</b> 🌙\n\n"
             f"{random.choice(MAFIA_PHRASES)}\n\n"
             f"<i>Село засинає, але хтось не спить...</i>",
        parse_mode=ParseMode.HTML
    )
    
    await night_phase(context, chat_id)
async def check_victory(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Перевірка умов перемоги"""
    game = mafia_game.games[chat_id]
    
    alive_mafia = 0
    alive_citizens = 0
    
    for user_id in game['alive_players']:
        role = game['players'][user_id]['role']
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
    elif alive_mafia >= alive_citizens:
        winner = 'mafia'
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
        
        for user_id, player_info in game['players'].items():
            role_info = mafia_game.get_role_info(player_info['role'])
            status = "💀" if not player_info['alive'] else "✅"
            team_emoji = "🔴" if role_info['team'] == 'mafia' else "🔵"
            
            roles_text += f"{status} {team_emoji} <b>{player_info['username']}</b>\n"
            roles_text += f"   └ {role_info['emoji']} {role_info['full_name']}\n\n"
        
        # Статистика
        total_days = game['day_number']
        roles_text += f"\n📊 <b>Статистика гри:</b>\n"
        roles_text += f"   • Днів пройдено: {total_days}\n"
        roles_text += f"   • Гравців було: {len(game['players'])}\n"
        roles_text += f"   • Переможець: {('Мирні жителі' if winner == 'citizens' else 'Мафія')}\n"
        roles_text += f"   • Куля детектива: {('Використана' if game['detective_bullet_used'] else 'Не використана')}\n"
        
        # Статистика перків
        if game['rope_break_save']:
            roles_text += f"   • 🪢 Мотузка рвалась!\n"
        if game['detective_error_target']:
            roles_text += f"   • 🔍 Детектив помилявся!\n"
        if game['mafia_misfire']:
            roles_text += f"   • 🔫 У мафії була осічка!\n"
        
        game['phase'] = 'ended'
        
        keyboard = [
            [InlineKeyboardButton("🎮 Нова гра", callback_data="create_new_game")],
        ]
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=victory_text + roles_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return True
    
    return False

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /status - показує статус поточної гри"""
    chat_id = update.message.chat_id
    
    if chat_id not in mafia_game.games:
        await update.message.reply_text(
            "⚠️ Активна гра не знайдена!\n\nСтворіть нову гру командою /newgame",
            parse_mode=ParseMode.HTML
        )
        return
    
    game = mafia_game.games[chat_id]
    
    phase_names = {
        'registration': '📝 Реєстрація гравців',
        'night': f'🌙 Ніч {game["day_number"]}',
        'day': f'☀️ День {game["day_number"]}',
        'voting': f'🗳 Голосування дня {game["day_number"]}',
        'ended': '🏁 Гра завершена'
    }
    
    status_text = f"""
📊 <b>━━━ СТАТУС ГРИ ━━━</b> 📊

<b>🎮 Фаза:</b> {phase_names.get(game['phase'], 'Невідомо')}
<b>👥 Гравців всього:</b> {len(game['players'])}
"""
    
    if game['started']:
        status_text += f"<b>💚 Живих гравців:</b> {len(game['alive_players'])}\n"
        status_text += f"<b>📅 День №:</b> {game['day_number']}\n"
        status_text += f"<b>🔫 Куля детектива:</b> {('Використана' if game['detective_bullet_used'] else 'Є')}\n\n"
        
        status_text += "<b>👥 Список гравців:</b>\n"
        for i, (user_id, player_info) in enumerate(game['players'].items(), 1):
            status_emoji = "✅" if player_info['alive'] else "💀"
            status_text += f"{i}. {status_emoji} {player_info['username']}\n"
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)

async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /endgame - завершення гри"""
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
    
    # Перевірка прав
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
        "🏁 <b>ГРУ ЗАВЕРШЕНО!</b>\n\n"
        "Створіть нову гру командою /newgame 🎮",
        parse_mode=ParseMode.HTML
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Головний обробник кнопок"""
    query = update.callback_query
    
    # Меню кнопки
    if query.data == "menu_rules":
        await show_rules(update, context)
    elif query.data == "menu_howto":
        await show_howto(update, context)
    elif query.data == "menu_characters":
        await show_characters(update, context)
    elif query.data == "back_main":
        await start(update, context)
    # Ігрові кнопки
    elif query.data == "join_game":
        await join_game_callback(update, context)
    elif query.data == "leave_game":
        await leave_game_callback(update, context)
    elif query.data == "start_game":
        await start_game_callback(update, context)
    elif query.data == "create_new_game":
        await create_new_game_callback(update, context)
    # Нічні дії
    elif query.data.startswith("night_"):
        await night_action_callback(update, context)
    # Голосування
    elif query.data.startswith("nominate_") or query.data.startswith("votefor_"):
        await vote_callback(update, context)

async def show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати правила гри"""
    query = update.callback_query
    await query.answer()
    
    rules_text = """
📜 <b>━━━ ПРАВИЛА ГРИ В МАФІЮ ━━━</b> 📜

<b>🎯 Мета гри:</b>
🔵 <b>Мирні жителі</b> - знайти і виключити всю мафію
🔴 <b>Мафія</b> - знищити мирних до рівності чисел

<b>🌙 НІЧНА ФАЗА:</b>
- 🔫 Мафія вибирає жертву для вбивства
- 💉 Федорчак (Лікар) обирає кого врятувати
- 🔍 Детектив перевіряє підозрілого або стріляє (1 куля)

<b>☀️ ДЕННА ФАЗА:</b>
- 📢 Оголошення результатів ночі
- 🗣 Обговорення (30 секунд)
- 🗳 Висунення кандидата на повішення
- ⚖️ Голосування ЗА/ПРОТИ повішення

<b>⚡ ОСОБЛИВОСТІ:</b>
- 👑 Кішкель (Дон) має імунітет до перевірки детектива
- 💉 Федорчак не може лікувати себе два рази поспіль
- 🔫 Детектив має 1 кулю на всю гру
- 🎭 Всі дії виконуються через особисті повідомлення
- 🏆 Гра триває до перемоги однієї з команд
- 🤝 Мафія знає одне одного, мирні - ні
- 💀 Мертві не можуть писати в чат!

<b>🎲 РАНДОМНІ ПЕРКИ (5% шанс):</b>
- 🪢 Мотузка може порватись
- 🔫 Осічка у мафії
- 🔍 Помилка детектива
- 💉 Подвійне лікування

<b>🎮 КІЛЬКІСТЬ ГРАВЦІВ:</b>
- Мінімум: 5 гравців
- Максимум: 15 гравців
- При 5-6: Дон + Лікар + Детектив + Дем'яни
- При 7+: Дон + Мафіозі + Лікар + Детектив + Дем'яни
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def show_howto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати інструкцію як грати"""
    query = update.callback_query
    await query.answer()
    
    howto_text = """
🎮 <b>━━━ ЯК ГРАТИ? ━━━</b> 🎮

<b>📝 ПІДГОТОВКА:</b>
1️⃣ Додайте бота в групу
2️⃣ Напишіть боту /start в особистих повідомленнях
3️⃣ В групі напишіть /newgame
4️⃣ Натисніть "➕ ПРИЄДНАТИСЯ ДО ГРИ"
5️⃣ Чекайте поки зберуться мінімум 5 гравців

<b>🎯 ПОЧАТОК ГРИ:</b>
1️⃣ Адміністратор натискає "🎯 ПОЧАТИ ГРУ"
2️⃣ Бот надішле кожному його роль в особисті повідомлення
3️⃣ Запам'ятайте свою роль і команду!

<b>🌙 НІЧНА ФАЗА:</b>
- Бот надішле вам кнопки з можливими діями
- Мафія обирає жертву
- Лікар обирає кого врятувати
- Детектив обирає: перевірити або вистрілити
- Дем'яни (мирні) просто чекають ранку

<b>☀️ ДЕННА ФАЗА:</b>
- Бот оголосить результати ночі
- 30 секунд на обговорення в групі
- Бот надішле кнопки для висунення кандидата
- Потім голосування ЗА/ПРОТИ повішення
- Якщо більшість ЗА - гравця виключають

<b>🔫 КУЛЯ ДЕТЕКТИВА:</b>
- Детектив може вистрілити замість перевірки
- Тільки 1 куля на всю гру
- Всі дізнаються вранці про постріл
- Використовуйте мудро!

<b>🎲 ПЕРКИ:</b>
- 5% шанс що мотузка порветься
- 5% шанс помилки детектива
- 5% шанс осічки мафії
- Перки можуть змінити хід гри!

<b>💀 ВАЖЛИВО:</b>
- Якщо ви мертві - НЕ ПИШІТЬ В ЧАТ!
- Бот автоматично видалить ваші повідомлення
- Дотримуйтесь правил гри!

<b>💡 ПОРАДИ:</b>
- Не розкривайте свою роль передчасно
- Детектив: думайте коли використати кулю
- Лікар: захищайте ключових гравців
- Мирні: шукайте непослідовність
- Мафія: будьте переконливими

<b>🏆 ПЕРЕМОГА:</b>
- Мирні виграють коли вся мафія виключена
- Мафія виграє коли їх кількість ≥ мирних
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(howto_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def show_characters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати детальну інформацію про персонажів"""
    query = update.callback_query
    await query.answer()
    
    characters_text = """
👥 <b>━━━ ПЕРСОНАЖІ ━━━</b> 👥

🔵 <b>МИРНІ ЖИТЕЛІ:</b>

🌾 <b>ДЕМЯН (Мирний житель)</b>
├ Команда: Мирні жителі
├ Здібності: Немає
└ Мета: Знайти мафію голосуванням

💉 <b>ФЕДОРЧАК (Лікар)</b>
├ Команда: Мирні жителі
├ Здібності: Може врятувати 1 гравця за ніч
├ Обмеження: Не може лікувати себе двічі поспіль
└ Мета: Рятувати життя і знайти мафію

🔍 <b>ДЕТЕКТИВ КОЛОМБО</b>
├ Команда: Мирні жителі
├ Здібності: Перевірка гравця АБО постріл (1 куля)
├ Особливість: Дон має імунітет до перевірки
├ Куля: 1 на всю гру, всі дізнаються про постріл
└ Мета: Викрити мафію або вистрілити в неї

━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 <b>МАФІЯ:</b>

👑 <b>КІШКЕЛЬ (Дон мафії)</b>
├ Команда: Мафія
├ Здібності: Вбиває + імунітет до детектива
├ Особливість: Детектив бачить його як мирного
└ Мета: Знищити всіх мирних

🔫 <b>ІГОР РОГАЛЬСЬКИЙ (Мафіозі)</b>
├ Команда: Мафія
├ Здібності: Вбиває разом з доном
├ Особливість: З'являється при 7+ гравцях
└ Мета: Допомагати дону знищити мирних

━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🎲 РАНДОМНІ ПЕРКИ (5% шанс):</b>

🪢 <b>Мотузка рветься</b>
└ Засуджений на повішення виживає

🔫 <b>Осічка мафії</b>
└ Зброя заклинює, жертва виживає

🔍 <b>Помилка детектива</b>
└ Перевірка показує неправильний результат

💉 <b>Подвійне лікування</b>
└ Лікар може двічі лікувати себе

━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>💡 ВАЖЛИВО:</b>
- Мафія знає одне одного
- Мирні не знають ролей один одного
- Ролі розподіляються випадково
- Кожна роль важлива для команди!
- Перки додають несподіваності!
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(characters_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def create_new_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Створення нової гри після завершення попередньої"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    await query.edit_message_text(
        "🎮 Щоб створити нову гру, напишіть в чаті:\n\n"
        "<code>/newgame</code>\n\n"
        "Або натисніть на команду вище! 👆",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help - допомога"""
    help_text = """
🎮 <b>━━━ КОМАНДИ БОТА ━━━</b> 🎮

<b>📝 В ГРУПОВОМУ ЧАТІ:</b>
/newgame - Створити нову гру
/status - Переглянути статус гри
/endgame - Завершити поточну гру (тільки адміни)

<b>💬 В ОСОБИСТИХ ПОВІДОМЛЕННЯХ:</b>
/start - Головне меню
/help - Ця довідка

<b>🎯 ЯК ПОЧАТИ ГРАТИ:</b>
1. Додайте бота в групу
2. Напишіть боту /start в особисті повідомлення
3. В групі напишіть /newgame
4. Натисніть кнопку "Приєднатися"
5. Чекайте на 5+ гравців
6. Адмін натискає "Почати гру"

<b>🎲 НОВІ ФІЧІ:</b>
- Детектив може вистрілити (1 куля)
- Рандомні перки (5% шанс)
- Голосування ЗА/ПРОТИ повішення
- Блокування повідомлень мертвих

<b>💡 ПІДКАЗКИ:</b>
- Завжди пишіть боту /start перед грою
- Не блокуйте бота
- Мертві НЕ МОЖУТЬ писати в чат
- Слідкуйте за повідомленнями в ЛС
- Грайте чесно і насолоджуйтесь!

<b>🆘 ПРОБЛЕМИ?</b>
- Не приходять повідомлення → Напишіть /start боту
- Гра не починається → Перевірте кількість гравців
- Застрягла гра → Адмін може написати /endgame

<b>🎭 Приємної гри в МАФІЮ!</b>
"""
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка помилок"""
    logger.error(f"Помилка: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Виникла помилка. Спробуйте пізніше або зверніться до адміністратора.",
                parse_mode=ParseMode.HTML
            )
    except Exception:
        pass

