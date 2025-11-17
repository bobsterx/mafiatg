"""Game state and core logic for the Mafia bot."""

import random
from typing import Dict, Optional

from config import ROLES, BOT_NAMES, SPECIAL_EVENTS


class MafiaGame:
    def __init__(self):
        self.games: Dict[int, Dict] = {}
        self.game_messages: Dict[int, int] = {}
        
    def create_game(self, chat_id: int, admin_id: int) -> Dict:
        """Створення нової гри"""
        # Вибираємо спеціальну подію (30% шанс)
        special_event = None
        if random.random() < 0.30:
            special_event = random.choice(list(SPECIAL_EVENTS.keys()))
        
        self.games[chat_id] = {
            'admin_id': admin_id,
            'players': {},
            'bots': {},
            'bot_count': 0,
            'phase': 'registration',
            'day_number': 0,
            'alive_players': set(),
            'night_actions': {},
            'votes': {},
            'vote_nominee': None,
            'vote_results': {},
            'history': [],
            'started': False,
            'last_healed': None,
            'mafia_chat_enabled': True,
            'detective_bullet_used': False,
            'detective_shot_this_night': None,
            'detective_error_target': None,
            'rope_break_save': None,
            'mafia_misfire': False,
            'perks_messages': [],
            'night_resolved': False,
            'final_voting_done': False,
            'discussion_started': False,
            'special_event': special_event,
            'special_items': {},  # user_id: item_type
            'potato_throws': {}  # user_id: target_id
        }
        return self.games[chat_id]
    
    def add_player(self, chat_id: int, user_id: int, username: str, is_bot: bool = False) -> bool:
        """Додавання гравця до гри"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        if game['phase'] != 'registration':
            return False
        
        total_players = len(game['players']) + len(game['bots'])
        if total_players >= 15:
            return False
        
        if is_bot:
            if user_id not in game['bots']:
                game['bots'][user_id] = {
                    'id': user_id,
                    'username': username,
                    'role': None,
                    'alive': True,
                    'is_bot': True
                }
                return True
        else:
            if user_id not in game['players']:
                game['players'][user_id] = {
                    'id': user_id,
                    'username': username,
                    'role': None,
                    'alive': True,
                    'is_bot': False
                }
                return True
        return False
    
    def add_bots(self, chat_id: int, count: int) -> int:
        """Додає ботів до гри"""
        if chat_id not in self.games:
            return 0
        
        game = self.games[chat_id]
        if game['phase'] != 'registration':
            return 0
        
        total_players = len(game['players']) + len(game['bots'])
        available_slots = 15 - total_players
        count = min(count, available_slots, len(BOT_NAMES))
        
        available_names = [name for name in BOT_NAMES if name not in [b['username'] for b in game['bots'].values()]]
        random.shuffle(available_names)
        
        added = 0
        for i in range(count):
            if i >= len(available_names):
                break
            bot_id = -(i + 1 + len(game['bots']))  # Негативні ID для ботів
            bot_name = available_names[i]
            if self.add_player(chat_id, bot_id, bot_name, is_bot=True):
                added += 1
        
        game['bot_count'] = len(game['bots'])
        return added
    
    def remove_player(self, chat_id: int, user_id: int) -> bool:
        """Видалення гравця з гри"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        if game['phase'] != 'registration':
            return False
        
        if user_id in game['players']:
            del game['players'][user_id]
            return True
        elif user_id in game['bots']:
            del game['bots'][user_id]
            game['bot_count'] = len(game['bots'])
            return True
        return False
    
    def get_all_players(self, chat_id: int) -> dict:
        """Отримати всіх гравців (людей + ботів)"""
        if chat_id not in self.games:
            return {}
        
        game = self.games[chat_id]
        all_players = {}
        all_players.update(game['players'])
        all_players.update(game['bots'])
        return all_players
    
    def assign_roles(self, chat_id: int) -> bool:
        """Розподіл ролей серед гравців"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        all_players = self.get_all_players(chat_id)
        players = list(all_players.keys())
        player_count = len(players)
        
        if player_count < 5:
            return False
        
        # Розподіл ролей залежно від кількості гравців
        if player_count >= 7:
            roles_to_assign = ['kishkel', 'rohalskyi', 'detective', 'fedorchak']
        else:
            roles_to_assign = ['kishkel', 'detective', 'fedorchak']
        
        # Решта - мирні жителі
        while len(roles_to_assign) < player_count:
            roles_to_assign.append('demyan')
        
        random.shuffle(players)
        random.shuffle(roles_to_assign)
        
        # Присвоюємо ролі
        for player_id, role in zip(players, roles_to_assign):
            if player_id in game['players']:
                game['players'][player_id]['role'] = role
            elif player_id in game['bots']:
                game['bots'][player_id]['role'] = role
        
        game['alive_players'] = set(players)
        game['started'] = True
        
        # Роздаємо спеціальні предмети якщо є подія
        if game['special_event']:
            event = SPECIAL_EVENTS[game['special_event']]
            for player_id in players:
                if random.random() < event['item_chance']:
                    game['special_items'][player_id] = event['special_item']
        
        return True
    
    def get_role_info(self, role_key: str) -> Dict:
        """Отримання інформації про роль"""
        return ROLES.get(role_key, ROLES['demyan'])
    
    def get_player_info(self, chat_id: int, user_id: int) -> Optional[Dict]:
        """Отримати інформацію про гравця"""
        if chat_id not in self.games:
            return None
        
        game = self.games[chat_id]
        if user_id in game['players']:
            return game['players'][user_id]
        elif user_id in game['bots']:
            return game['bots'][user_id]
        return None
    
    def is_bot(self, chat_id: int, user_id: int) -> bool:
        """Перевірка чи є гравець ботом"""
        if chat_id not in self.games:
            return False
        return user_id in self.games[chat_id]['bots']
    
    def get_mafia_members(self, chat_id: int) -> list:
        """Отримання списку живих мафіозі"""
        if chat_id not in self.games:
            return []
        
        game = self.games[chat_id]
        all_players = self.get_all_players(chat_id)
        mafia_members = []
        
        for user_id, player_info in all_players.items():
            if player_info['alive']:
                role_info = self.get_role_info(player_info['role'])
                if role_info['team'] == 'mafia':
                    mafia_members.append((user_id, player_info))
        
        return mafia_members
    
    def get_alive_citizens(self, chat_id: int) -> list:
        """Отримання списку живих мирних жителів"""
        if chat_id not in self.games:
            return []
        
        game = self.games[chat_id]
        all_players = self.get_all_players(chat_id)
        citizens = []
        
        for user_id, player_info in all_players.items():
            if player_info['alive']:
                role_info = self.get_role_info(player_info['role'])
                if role_info['team'] == 'citizens':
                    citizens.append((user_id, player_info))
        
        return citizens

mafia_game = MafiaGame()