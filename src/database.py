import sqlite3
import bcrypt
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class DatabaseManager:
    """
    DAO (Data Access Object) паттерн для работы с базой данных дебатов.
    Инкапсулирует в себе всю логику SQL, предоставляя приложению чистый интерфейс.
    В будущем легко заменяется на сетевые запросы к REST API.
    """
    
    def __init__(self, db_path=os.path.join(BASE_DIR, "data", "database.db")):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        # Включаем возвращение результатов в виде словарей (row_factory) 
        # для более удобного доступа по ключам (row['coins']).
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Создает таблицы, если они еще не существуют."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    nickname TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_email_verified BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            ''')
            
            # Таблица статистики и экономики
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id INTEGER PRIMARY KEY,
                    coins INTEGER DEFAULT 0,
                    total_games INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    current_win_streak INTEGER DEFAULT 0,
                    max_win_streak INTEGER DEFAULT 0,
                    favorite_opponent TEXT,
                    nemesis TEXT,
                    total_playtime_seconds INTEGER DEFAULT 0,
                    total_words_spoken INTEGER DEFAULT 0,
                    total_chars_spoken INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            
            # Таблица разблокированных оппонентов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS unlocked_opponents (
                    user_id INTEGER,
                    opponent_name TEXT,
                    PRIMARY KEY (user_id, opponent_name),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            
            conn.commit()

    # --- АВТОРИЗАЦИЯ И РЕГИСТРАЦИЯ ---

    def register_user(self, email, nickname, password):
        """
        Регистрирует нового пользователя. 
        Возвращает (True, user_id) при успехе или (False, error_msg) при ошибке.
        """
        # Генерируем соль и хешируем пароль
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 1. Создаем юзера
                cursor.execute(
                    "INSERT INTO users (email, nickname, password_hash) VALUES (?, ?, ?)",
                    (email, nickname, hashed.decode('utf-8'))
                )
                user_id = cursor.lastrowid
                
                # 2. Создаем начальную статистику для юзера
                cursor.execute("INSERT INTO user_stats (user_id) VALUES (?)", (user_id,))
                
                # 3. Базовые оппоненты открыты всем по умолчанию
                default_opponents = ["Иммануил Кант", "Сократ"]
                for opp in default_opponents:
                    cursor.execute(
                        "INSERT INTO unlocked_opponents (user_id, opponent_name) VALUES (?, ?)", 
                        (user_id, opp)
                    )
                
                conn.commit()
                return True, user_id
        except sqlite3.IntegrityError:
            return False, "Пользователь с таким email уже существует."
        except Exception as e:
            return False, str(e)

    def authenticate_user(self, email, password):
        """
        Проверяет пару email/password.
        Возвращает (True, user_dict) при успехе, (False, error_msg) при ошибке.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            user = cursor.fetchone()
            
            if not user:
                return False, "Неверный email или пароль."
            
            # Сравниваем введенный пароль с хешем в БД
            stored_hash = user['password_hash'].encode('utf-8')
            if bcrypt.checkpw(password.encode('utf-8'), stored_hash):
                # Обновляем время последнего входа
                cursor.execute(
                    "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", 
                    (user['id'],)
                )
                conn.commit()
                return True, dict(user)
            else:
                return False, "Неверный email или пароль."

    # --- ЭКОНОМИКА И СТАТИСТИКА ---

    def get_user_profile(self, user_id):
        """Возвращает полную сводку профиля пользователя (учетка + стата)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.email, u.nickname, u.created_at, u.last_login, s.*
                FROM users u
                JOIN user_stats s ON u.id = s.user_id
                WHERE u.id = ?
            ''', (user_id,))
            profile = cursor.fetchone()
            return dict(profile) if profile else None

    def record_match_result(self, user_id, opponent_name, is_win, playtime_seconds, words_spoken, chars_spoken):
        """
        Обновляет сложную статистику после окончания дебатов.
        Вся бизнес-логика скрыта здесь (DAO паттерн).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_stats WHERE user_id = ?", (user_id,))
            stats = dict(cursor.fetchone())
            
            # Подсчет базовой статы
            new_total_games = stats['total_games'] + 1
            new_wins = stats['wins'] + (1 if is_win else 0)
            new_losses = stats['losses'] + (0 if is_win else 1)
            
            # Экономика (награда)
            coins_reward = 50 if is_win else 10
            new_coins = stats['coins'] + coins_reward
            
            # Win Streak
            current_streak = stats['current_win_streak'] + 1 if is_win else 0
            max_streak = max(stats['max_win_streak'], current_streak)
            
            # Объем данных
            new_playtime = stats['total_playtime_seconds'] + playtime_seconds
            new_words = stats['total_words_spoken'] + words_spoken
            new_chars = stats['total_chars_spoken'] + chars_spoken
            
            # TODO: Вычисление любимого оппонента и "Немезиды" (потребует историю матчей, 
            # пока оставляем статичным или обновим в следующей итерации для идеальности)
            
            cursor.execute('''
                UPDATE user_stats 
                SET coins = ?, total_games = ?, wins = ?, losses = ?,
                    current_win_streak = ?, max_win_streak = ?,
                    total_playtime_seconds = ?, total_words_spoken = ?, total_chars_spoken = ?
                WHERE user_id = ?
            ''', (
                new_coins, new_total_games, new_wins, new_losses,
                current_streak, max_streak,
                new_playtime, new_words, new_chars,
                user_id
            ))
            conn.commit()
            return coins_reward

    # --- МАГАЗИН ---
    
    def get_unlocked_opponents(self, user_id):
        """Возвращает список имен открытых противников."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT opponent_name FROM unlocked_opponents WHERE user_id = ?", (user_id,))
            return [row['opponent_name'] for row in cursor.fetchall()]

    def unlock_opponent(self, user_id, opponent_name, price):
        """
        Попытка купить оппонента.
        Транзакция (ACID): либо спишем деньги и откроем, либо выкинем ошибку (если не хватает денег).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Проверяем баланс
            cursor.execute("SELECT coins FROM user_stats WHERE user_id = ?", (user_id,))
            coins = cursor.fetchone()['coins']
            
            if coins < price:
                return False, "Недостаточно монет!"
                
            # Проверяем, не открыт ли он уже
            cursor.execute(
                "SELECT 1 FROM unlocked_opponents WHERE user_id = ? AND opponent_name = ?", 
                (user_id, opponent_name)
            )
            if cursor.fetchone():
                return False, "Оппонент уже разблокирован!"
                
            # Списываем монеты и даем доступ
            cursor.execute("UPDATE user_stats SET coins = coins - ? WHERE user_id = ?", (price, user_id))
            cursor.execute(
                "INSERT INTO unlocked_opponents (user_id, opponent_name) VALUES (?, ?)", 
                (user_id, opponent_name)
            )
            conn.commit()
            return True, "Успешная покупка!"

# Для быстрого локального теста из консоли:
if __name__ == "__main__":
    db = DatabaseManager("test_vkr.db")
    print("БД и таблицы успешно созданы.")
    
    # Тест регистрации
    ok, msg = db.register_user("test@vkr.com", "Дипломант", "qwe123rty")
    print(f"Регистрация: {ok} -> {msg}")
    
    # Тест логина
    ok, user = db.authenticate_user("test@vkr.com", "qwe123rty")
    print(f"Вход (верный пароль): {ok} -> Никнейм: {user.get('nickname', '') if ok else msg}")
    
    ok, user = db.authenticate_user("test@vkr.com", "wrong")
    print(f"Вход (неверный пароль): {ok} -> {msg}")
    
    # Тест статы
    if ok:
        prof = db.get_user_profile(user['id'])
        print("\nПрофиль до игры:", prof)
        
        db.record_match_result(user['id'], "Стив Джобс", True, 300, 150, 1000)
        
        prof_after = db.get_user_profile(user['id'])
        print("\nПрофиль после победы:", prof_after)
