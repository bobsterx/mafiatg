"""Game state and core logic for the Mafia bot."""

import random
from typing import Dict

from config import ROLES


class MafiaGame:
    def __init__(self):
        self.games: Dict[int, Dict] = {}
        self.game_messages: Dict[int, int] = {}
        
    def create_game(self, chat_id: int, admin_id: int) -> Dict:
        """Створення нової гри"""
        self.games[chat_id] = {
            'admin_id': admin_id,
            'players': {},
            'phase': 'registration',
            'day_number': 0,
            'alive_players': set(),
            'night_actions': {},
            'votes': {},
            'vote_nominee': None,  # Поточний кандидат на повішення
            'vote_results': {},  # Результати голосування за/проти
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
            'final_voting_done': False
        }
        return self.games[chat_id]
    
    def add_player(self, chat_id: int, user_id: int, username: str) -> bool:
        """Додавання гравця до гри"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        if game['phase'] != 'registration':
            return False
        
        if user_id not in game['players']:
            game['players'][user_id] = {
                'id': user_id,
                'username': username,
                'role': None,
                'alive': True,
                'vote_count': 0
            }
            return True
        return False
    
    def remove_player(self, chat_id: int, user_id: int) -> bool:
        """Видалення гравця з гри"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        if game['phase'] != 'registration' or user_id not in game['players']:
            return False
        
        del game['players'][user_id]
        return True
    
    def assign_roles(self, chat_id: int) -> bool:
        """Розподіл ролей серед гравців"""
        if chat_id not in self.games:
            return False
        
        game = self.games[chat_id]
        players = list(game['players'].keys())
        player_count = len(players)
        
        if player_count < 5:
            return False
        
        if player_count >= 7:
            roles_to_assign = ['kishkel', 'rohalskyi', 'detective', 'fedorchak']
        else:
            roles_to_assign = ['kishkel', 'detective', 'fedorchak']
        
        while len(roles_to_assign) < player_count:
            roles_to_assign.append('demyan')
        
        random.shuffle(players)
        random.shuffle(roles_to_assign)
        
        for player_id, role in zip(players, roles_to_assign):
            game['players'][player_id]['role'] = role
        
        game['alive_players'] = set(players)
        game['started'] = True
        return True
    
    def get_role_info(self, role_key: str) -> Dict:
        """Отримання інформації про роль"""
        return ROLES.get(role_key, ROLES['demyan'])
    
    def get_mafia_members(self, chat_id: int) -> list:
        """Отримання списку живих мафіозі"""
        if chat_id not in self.games:
            return []
        
        game = self.games[chat_id]
        mafia_members = []
        
        for user_id, player_info in game['players'].items():
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
        citizens = []
        
        for user_id, player_info in game['players'].items():
            if player_info['alive']:
                role_info = self.get_role_info(player_info['role'])
                if role_info['team'] == 'citizens':
                    citizens.append((user_id, player_info))
        
        return citizens

mafia_game = MafiaGame()

