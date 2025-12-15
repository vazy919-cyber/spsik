import telebot
from telebot import types
import sqlite3
import json
from datetime import datetime, date
import os
import sys
import logging
import time
#import schedule
import threading
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация бота
bot = telebot.TeleBot(str(os.getenv('BOT_TOKEN')))

# Список супер-администраторов (user_id)
SUPER_ADMINS = [1310818613, 5054882870]
# Список разрешенных пользователей
ALLOWED_USER_IDS = [1310818613, 5054882870,5115418851]
DB_PATH = os.getenv('DB_PATH', 'attendance_bot.db')

# ФУНКЦИЯ ПРОВЕРКИ ДОСТУПА
def is_user_allowed(user_id):
    """Проверить, есть ли у пользователя доступ к боту"""
    return user_id in ALLOWED_USER_IDS

class Database:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Таблица пользователей
        c.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY,
                     fio TEXT,
                     username TEXT,
                     registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        # Таблица отсутствий
        c.execute('''CREATE TABLE IF NOT EXISTS absences
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     absence_type TEXT,
                     reason TEXT,
                     date TEXT,
                     group_chat_id INTEGER,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY(user_id) REFERENCES users(user_id))''')

        # Таблица состояния
        c.execute('''CREATE TABLE IF NOT EXISTS bot_state
                    (key TEXT PRIMARY KEY,
                     value TEXT)''')

        # Таблица администратора
        c.execute('''CREATE TABLE IF NOT EXISTS admin_settings
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     admin_id INTEGER UNIQUE,
                     report_time TEXT DEFAULT '09:00')''')

        # Таблица для хранения username -> user_id
        c.execute('''CREATE TABLE IF NOT EXISTS usernames
                     (username TEXT PRIMARY KEY, user_id INTEGER)''')

        # Таблица для ожидающих подтверждения причин
        c.execute('''CREATE TABLE IF NOT EXISTS pending_absences
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER,
                     reason TEXT,
                     date TEXT,
                     group_chat_id INTEGER,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        # Таблица для хранения состояний пользователей
        c.execute('''CREATE TABLE IF NOT EXISTS user_states
                    (user_id INTEGER PRIMARY KEY,
                     state TEXT,
                     data TEXT,
                     updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        # Таблица для отслеживания текущих отсутствующих (Болею/Отпуск)
        c.execute('''CREATE TABLE IF NOT EXISTS active_absences
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER UNIQUE,
                     absence_type TEXT,
                     started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     message_id INTEGER,
                     chat_id INTEGER,
                     group_chat_id INTEGER)''')

        # Таблица групп
        c.execute('''CREATE TABLE IF NOT EXISTS groups
                    (chat_id INTEGER PRIMARY KEY,
                     name TEXT,
                     verified INTEGER DEFAULT 0,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        # Таблица администраторов групп
        c.execute('''CREATE TABLE IF NOT EXISTS group_admins
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     chat_id INTEGER,
                     admin_id INTEGER,
                     activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     FOREIGN KEY(chat_id) REFERENCES groups(chat_id))''')

        # Таблица ожидающих привязок групп
        c.execute('''CREATE TABLE IF NOT EXISTS pending_binds
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     chat_id INTEGER,
                     requester_id INTEGER,
                     group_name TEXT,
                     requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     status TEXT DEFAULT 'pending')''')

        # Таблица ключей активации
        c.execute('''CREATE TABLE IF NOT EXISTS activation_keys
                    (key TEXT PRIMARY KEY,
                     chat_id INTEGER,
                     target_admin_id INTEGER,
                     used INTEGER DEFAULT 0,
                     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     used_at TIMESTAMP)''')

        conn.commit()

        # Миграция - добавляем колонки если их нет
        self._migrate_db(c)
        conn.commit()
        conn.close()

    def _migrate_db(self, cursor):
        """Миграция БД - добавление недостающих колонок"""
        try:
            # Проверяем наличие таблицы и её колонок для active_absences
            cursor.execute("PRAGMA table_info(active_absences)")
            aa_columns = [col[1] for col in cursor.fetchall()]

            if 'chat_id' not in aa_columns:
                cursor.execute("ALTER TABLE active_absences ADD COLUMN chat_id INTEGER")
                logging.info("✅ Добавлена колонка chat_id в таблицу active_absences")

            if 'message_id' not in aa_columns:
                cursor.execute("ALTER TABLE active_absences ADD COLUMN message_id INTEGER")
                logging.info("✅ Добавлена колонка message_id в таблицу active_absences")

            if 'group_chat_id' not in aa_columns:
                cursor.execute("ALTER TABLE active_absences ADD COLUMN group_chat_id INTEGER")
                logging.info("✅ Добавлена колонка group_chat_id в таблицу active_absences")

            # Для absences
            cursor.execute("PRAGMA table_info(absences)")
            abs_columns = [col[1] for col in cursor.fetchall()]

            if 'group_chat_id' not in abs_columns:
                cursor.execute("ALTER TABLE absences ADD COLUMN group_chat_id INTEGER")
                logging.info("✅ Добавлена колонка group_chat_id в таблицу absences")

            # Для pending_binds
            cursor.execute("PRAGMA table_info(pending_binds)")
            pb_columns = [col[1] for col in cursor.fetchall()]

            if 'group_name' not in pb_columns:
                cursor.execute("ALTER TABLE pending_binds ADD COLUMN group_name TEXT")
                logging.info("✅ Добавлена колонка group_name в таблицу pending_binds")

            # Для pending_absences
            cursor.execute("PRAGMA table_info(pending_absences)")
            pa_columns = [col[1] for col in cursor.fetchall()]

            if 'group_chat_id' not in pa_columns:
                cursor.execute("ALTER TABLE pending_absences ADD COLUMN group_chat_id INTEGER")
                logging.info("✅ Добавлена колонка group_chat_id в таблицу pending_absences")

        except Exception as e:
            logging.info(f"Миграция: {e} (возможно колонки уже существуют)")

    # УПРАВЛЕНИЕ СОСТОЯНИЯМИ
    def set_user_state(self, user_id, state, data=None):
        """Установить состояние пользователя"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        data_json = json.dumps(data) if data else None
        c.execute('''REPLACE INTO user_states (user_id, state, data)
                     VALUES (?, ?, ?)''', (user_id, state, data_json))
        conn.commit()
        conn.close()

    def get_user_state(self, user_id):
        """Получить состояние пользователя"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT state, data FROM user_states WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            data = json.loads(result[1]) if result[1] else None
            return result[0], data
        return None, None

    def clear_user_state(self, user_id):
        """Очистить состояние пользователя"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def get_last_update_id(self):
        """Получить последний обработанный update_id"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM bot_state WHERE key = 'last_update_id'")
        result = c.fetchone()
        conn.close()
        return int(result[0]) if result else 0

    def save_last_update_id(self, update_id):
        """Сохранить последний update_id"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO bot_state (key, value) VALUES ('last_update_id', ?)",
                 (str(update_id),))
        conn.commit()
        conn.close()

    def register_user(self, user_id, fio):
        """Зарегистрировать пользователя с ФИО"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''REPLACE INTO users (user_id, fio)
                         VALUES (?, ?)''', (user_id, fio))
            conn.commit()
            print(f"✅ База: user_id={user_id}, fio={fio}")
        except Exception as e:
            print(f"❌ Ошибка базы: {e}")
            raise
        finally:
            conn.close()

    def get_user_fio(self, user_id):
        """Получить ФИО пользователя"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT fio FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else None

    def add_absence(self, user_id, absence_type, reason="", group_chat_id=None):
        """Добавить запись об отсутствии"""
        today = date.today().isoformat()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Удаляем старую запись на сегодня (если есть)
        c.execute('''DELETE FROM absences
                     WHERE user_id = ? AND date = ? AND group_chat_id = ?''',
                     (user_id, today, group_chat_id))

        # Добавляем новую запись
        c.execute('''INSERT INTO absences
                     (user_id, absence_type, reason, date, group_chat_id)
                     VALUES (?, ?, ?, ?, ?)''',
                     (user_id, absence_type, reason, today, group_chat_id))

        conn.commit()
        conn.close()

    def get_today_absences(self, group_chat_id=None):
        """Получить отсутствия за сегодня для конкретной группы"""
        today = date.today().isoformat()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Основной запрос для обычных отсутствий
        absences_query = '''SELECT u.fio, a.absence_type, a.reason, a.user_id
                          FROM absences a
                          LEFT JOIN users u ON a.user_id = u.user_id
                          WHERE a.date = ?'''
        absences_params = [today]

        # Добавляем фильтр по группе если указан
        if group_chat_id:
            absences_query += " AND a.group_chat_id = ?"
            absences_params.append(group_chat_id)

        # Запрос для активных отсутствий (Болею/Отпуск)
        active_query = '''SELECT u.fio, aa.absence_type, aa.absence_type, aa.user_id
                        FROM active_absences aa
                        LEFT JOIN users u ON aa.user_id = u.user_id'''
        active_params = []

        if group_chat_id:
            active_query += " WHERE aa.group_chat_id = ?"
            active_params.append(group_chat_id)
        else:
            # Если группа не указана (отчет в ЛС админа), показываем всех активных
            active_query += " WHERE aa.group_chat_id IS NOT NULL"

        # Объединяем запросы
        full_query = f"{absences_query} UNION ALL {active_query}"
        full_params = absences_params + active_params

        c.execute(full_query, full_params)
        result = c.fetchall()

        # Форматируем причину и тип отсутствия для активных отсутствий
        formatted_result = []
        for fio, absence_type, reason, user_id in result:
            if reason == absence_type:  # Это активное отсутствие
                reason = format_reason_for_report(reason)
                absence_type = 'уважительно'
            formatted_result.append((fio, absence_type, reason, user_id))

        conn.close()
        return formatted_result

    def set_admin(self, admin_id):
        """Добавить администратора"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("INSERT OR IGNORE INTO admin_settings (admin_id) VALUES (?)", (admin_id,))
            conn.commit()
        except Exception as e:
            print(f"Ошибка добавления админа: {e}")
        finally:
            conn.close()

    def get_admin_ids(self):
        """Получить список всех администраторов"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT admin_id FROM admin_settings")
        result = [row[0] for row in c.fetchall()]
        conn.close()
        logging.info(f"🔍 Получены администраторы из БД: {result} (всего: {len(result)})")
        return result

    def get_group_admins(self, chat_id):
        """Получить список администраторов конкретной группы"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT admin_id FROM group_admins WHERE chat_id = ?", (chat_id,))
        result = [row[0] for row in c.fetchall()]
        conn.close()
        logging.info(f"🔍 Администраторы группы {chat_id}: {result} (всего: {len(result)})")
        return result

    def add_group_admin(self, chat_id, admin_id):
        """Добавить администратора к группе"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''INSERT INTO group_admins
                         (chat_id, admin_id)
                         VALUES (?, ?)''', (chat_id, admin_id))
            conn.commit()
            logging.info(f"✅ Администратор {admin_id} добавлен к группе {chat_id}")
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка добавления администратора {admin_id} к группе {chat_id}: {e}")
            return False
        finally:
            conn.close()

    def get_admin_groups(self, admin_id):
        """Получить все группы, где администратор имеет доступ"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''SELECT g.chat_id, g.name
                        FROM group_admins ga
                        LEFT JOIN groups g ON ga.chat_id = g.chat_id
                        WHERE ga.admin_id = ?''', (admin_id,))
            groups = c.fetchall()
            logging.info(f"🔍 Найдено {len(groups)} групп для администратора {admin_id}")
            return groups
        finally:
            conn.close()

    def remove_group_admin(self, chat_id, admin_id):
        """Удалить администратора из группы"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''DELETE FROM group_admins WHERE chat_id = ? AND admin_id = ?''', (chat_id, admin_id))
            conn.commit()
            logging.info(f"✅ Администратор {admin_id} удален из группы {chat_id}")
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка удаления администратора {admin_id} из группы {chat_id}: {e}")
            return False
        finally:
            conn.close()

    def get_all_group_admins(self):
        """Получить всех администраторов групп с информацией о группах"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''SELECT g.chat_id, g.name, ga.admin_id
                        FROM group_admins ga
                        LEFT JOIN groups g ON ga.chat_id = g.chat_id
                        ORDER BY g.name, ga.admin_id''')
            result = c.fetchall()
            return result
        finally:
            conn.close()

    def remove_admin(self, admin_id):
        """Удалить администратора"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM admin_settings WHERE admin_id = ?", (admin_id,))
        conn.commit()
        conn.close()

    def update_username(self, username, user_id):
        """Обновить username -> user_id"""
        if username:
            logging.info(f"Обновляем username: {username.lower()} для user_id: {user_id}")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("REPLACE INTO usernames (username, user_id) VALUES (?, ?)", (username.lower(), user_id))
            conn.commit()
            conn.close()

    def get_user_id_by_username(self, username):
        """Получить user_id по username"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id FROM usernames WHERE username = ?", (username.lower(),))
        result = c.fetchone()
        conn.close()
        return result[0] if result else None

    def add_pending_absence(self, user_id, reason, group_chat_id=None):
        """Добавить ожидающую подтверждения причину"""
        today = date.today().isoformat()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO pending_absences (user_id, reason, date, group_chat_id)
                     VALUES (?, ?, ?, ?)''', (user_id, reason, today, group_chat_id))
        conn.commit()
        conn.close()
        return c.lastrowid

    def get_pending_absence(self, pending_id):
        """Получить ожидающую причину по ID"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT pa.id, pa.user_id, pa.reason, pa.date, pa.group_chat_id, pa.created_at, u.fio
                     FROM pending_absences pa
                     LEFT JOIN users u ON pa.user_id = u.user_id
                     WHERE pa.id = ?''', (pending_id,))
        result = c.fetchone()
        conn.close()
        return result

    def delete_pending_absence(self, pending_id):
        """Удалить ожидающую причину"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''DELETE FROM pending_absences WHERE id = ?''', (pending_id,))
        conn.commit()
        conn.close()

    def add_active_absence(self, user_id, absence_type, message_id=None, chat_id=None, group_chat_id=None):
        """Добавить пользователя в список текущих отсутствующих (Болею/Отпуск)"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''REPLACE INTO active_absences
                        (user_id, absence_type, message_id, chat_id, group_chat_id)
                        VALUES (?, ?, ?, ?, ?)''',
                        (user_id, absence_type, message_id, chat_id, group_chat_id))
            conn.commit()
        finally:
            conn.close()

    def remove_active_absence(self, user_id):
        """Удалить пользователя из списка текущих отсутствующих"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''DELETE FROM active_absences WHERE user_id = ?''', (user_id,))
            conn.commit()
        finally:
            conn.close()

    def remove_absence_from_today(self, user_id):
        """Удалить отсутствие пользователя из сегодняшних отсутствий"""
        today = date.today().isoformat()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('''DELETE FROM absences WHERE user_id = ? AND date = ?''', (user_id, today))
            conn.commit()
        finally:
            conn.close()

    def get_active_absence(self, user_id):
        """Получить информацию об активном отсутствии пользователя"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT id, user_id, absence_type, message_id, chat_id, group_chat_id FROM active_absences WHERE user_id = ?''', (user_id,))
        result = c.fetchone()
        conn.close()
        return result

    def get_all_active_absences(self):
        """Получить всех людей в списке отсутствующих"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT aa.user_id, aa.absence_type, u.fio
                    FROM active_absences aa
                    LEFT JOIN users u ON aa.user_id = u.user_id''')
        result = c.fetchall()
        conn.close()
        return result

# Инициализация базы данных
db = Database()

def create_attendance_keyboard():
    """Создать клавиатуру для отметки отсутствия"""
    keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    btn1 = types.KeyboardButton('❌ Отсутствую')
    btn2 = types.KeyboardButton('📊 Получить отчёт')
    keyboard.add(btn1, btn2)
    return keyboard

def create_reason_inline_keyboard():
    """Создать INLINE клавиатуру с причинами отсутствия"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton('🤒 Болею', callback_data='reason_boleyu')
    btn2 = types.InlineKeyboardButton('📋 Приказ', callback_data='reason_prikaz')
    btn3 = types.InlineKeyboardButton('🏠 Деж. по общаге', callback_data='reason_obshaga')
    btn4 = types.InlineKeyboardButton('🏫 Деж. по колледжу', callback_data='reason_college')
    btn5 = types.InlineKeyboardButton('🎖️ Военкомат', callback_data='reason_voenkomat')
    btn6 = types.InlineKeyboardButton('😎 Отпуск', callback_data='reason_otpusk')
    btn7 = types.InlineKeyboardButton('📝 Другое', callback_data='reason_other')
    btn8 = types.InlineKeyboardButton('❌ Отмена', callback_data='reason_cancel')

    keyboard.add(btn1, btn2)
    keyboard.add(btn3, btn4)
    keyboard.add(btn5, btn6)
    keyboard.add(btn7,btn8)
    return keyboard

def create_private_keyboard():
    """Создать клавиатуру для ЛС"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('📊 Получить отчёт')
    btn2 = types.KeyboardButton('📝 Регистрация')
    btn3 = types.KeyboardButton('ℹ️ Информация')
    keyboard.add(btn1, btn2)
    keyboard.add(btn3)
    return keyboard

def create_admin_decision_keyboard(pending_id):
    """Создать клавиатуру для решения администратора"""
    keyboard = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton('✅ Уважительно', callback_data=f'approve_respectful_{pending_id}')
    btn2 = types.InlineKeyboardButton('❌ Неуважительно', callback_data=f'approve_disrespectful_{pending_id}')
    keyboard.add(btn1, btn2)
    return keyboard

def create_cancel_inline_keyboard():
    """Создать инлайн клавиатуру только с отменой"""
    keyboard = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton('❌ Отмена', callback_data='reason_cancel')
    keyboard.add(btn)
    return keyboard

def create_exit_absence_keyboard():
    """Создать инлайн клавиатуру с кнопкой выхода из отсутствия"""
    keyboard = types.InlineKeyboardMarkup()
    btn = types.InlineKeyboardButton('🚪 Выхожу', callback_data='exit_absence')
    keyboard.add(btn)
    return keyboard

def send_absence_notification_to_private(user_id, absence_type, username, fio, group_chat_id=None):
    """Отправить уведомление об отсутствии в личные сообщения и администраторам группы"""
    try:
        keyboard = create_exit_absence_keyboard()

        pm_message = bot.send_message(
            user_id,
            f"📢 Вы отмечены как отсутствующий:\n\n"
            f"{absence_type}\n\n"
            f"Нажмите кнопку ниже, когда вернётесь/выздоровеете.",
            reply_markup=keyboard
        )

        # Сохраняем информацию о сообщении с ID группы
        db.add_active_absence(
            user_id,
            absence_type,
            pm_message.message_id,
            user_id,
            group_chat_id=group_chat_id  # Сохраняем реальный group_chat_id
        )
        logging.info(f"📬 Сообщение с кнопкой выхода отправлено @{username}")

        # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ АДМИНИСТРАТОРАМ ГРУППЫ
        if group_chat_id:
            send_notification_to_group_admins(
                group_chat_id=group_chat_id,
                user_id=user_id,
                fio=fio,
                absence_type=absence_type,
                event_type="added"  # Пользователь добавился в активное отсутствие
            )

        return True

    except Exception as e:
        error_msg = str(e)
        logging.warning(f"⚠️ Ошибка отправки ЛС пользователю @{username}: {error_msg}")
        return False


def send_notification_to_group_admins(group_chat_id, user_id, fio, absence_type, event_type="added"):
    """Отправить уведомление администраторам группы о статусе отсутствия пользователя"""
    try:
        admin_ids = db.get_group_admins(group_chat_id)

        if not admin_ids:
            logging.info(f"ℹ️ Нет администраторов для группы {group_chat_id}, уведомления не отправляются")
            return

        if event_type == "added":
            title = "🔔 НОВОЕ ОТСУТСТВИЕ"
            event_text = f"добавлен в список отсутствующих"
        elif event_type == "removed":
            title = "✅ ВОЗВРАЩЕНИЕ"
            event_text = f"вышел из списка отсутствующих"
        else:
            title = "📢 УВЕДОМЛЕНИЕ"
            event_text = "изменил статус отсутствия"

        message_text = (
            f"{title}\n\n"
            f"👤 {fio}\n"
            f"📋 {event_text}\n"
            f"📌 Тип: {absence_type}"
        )

        logging.info(f"📢 Отправляем уведомления администраторам группы {group_chat_id}. "
                    f"Всего админов: {len(admin_ids)}, Admin IDs: {admin_ids}. "
                    f"Пользователь: {fio}, Событие: {event_text}")

        success_count = 0
        failed_count = 0

        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    message_text
                )
                logging.info(f"✅ Уведомление {event_type} отправлено администратору {admin_id}")
                success_count += 1
            except Exception as e:
                logging.error(f"❌ Ошибка отправки уведомления администратору {admin_id}: {e}")
                failed_count += 1

        logging.info(f"📊 Уведомления отправлены: успешно {success_count}/{len(admin_ids)} администраторам, ошибок: {failed_count}")

    except Exception as e:
        logging.error(f"❌ Ошибка при отправке уведомлений администраторам группы {group_chat_id}: {e}")

def create_admin_keyboard(user_id=None):
    """Создать клавиатуру для администратора в ЛС"""
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('📊 Получить отчёт')
    btn2 = types.KeyboardButton('📝 Регистрация')
    btn3 = types.KeyboardButton('ℹ️ Информация')
    # btn4 = types.KeyboardButton('📋 Текущие болеющие/в отпуске')  # Закомментировано - будет использовано в будущем
    keyboard.add(btn1, btn2)
    keyboard.add(btn3)
    # keyboard.add(btn3, btn4)  # Закомментировано - раскомментируйте когда понадобится

    # Добавляем кнопку удаления админов только для супер-админов
    if user_id in SUPER_ADMINS:
        btn5 = types.KeyboardButton('🗑️ Удалить админа из группы')
        keyboard.add(btn5)

    return keyboard

# ===== ОБРАБОТЧИКИ КОМАНД =====

@bot.message_handler(commands=['start'])
def handle_start(message):
    """Обработка /start для ЛС и групп

    Супер-админы: 1310818613, 5054882870, 5115418851
    Они могут генерировать ключи активации для групп"""
    user_id = message.from_user.id

    logging.info(f"Получена команда /start от {message.from_user.username} в {message.chat.type}")

    # Очищаем состояние пользователя при старте
    db.clear_user_state(user_id)

    if message.chat.type == 'private':
        # ЛИЧНЫЕ СООБЩЕНИЯ
        keyboard = create_private_keyboard()
        admin_keyboard = create_admin_keyboard(user_id)

        # Проверяем:是ли пользователь супер-админом ИЛИ администратором хотя бы одной группы
        is_super_admin = is_user_allowed(user_id)
        user_groups = db.get_admin_groups(user_id)
        is_group_admin = len(user_groups) > 0

        logging.info(f"👤 Пользователь @{message.from_user.username}: суперадмин={is_super_admin}, администратор групп={is_group_admin}, групп={len(user_groups)}")

        # Если суперадмин или администратор группы - показываем расширенную клавиатуру
        if is_super_admin or is_group_admin:
            try:
                bot.send_message(
                    message.chat.id,
                    "👋 Бот для учёта отсутствующих\n\n"
                    "Нажмите кнопку 'ℹ️ Информация' для инструкции по использованию.",
                    reply_markup=admin_keyboard
                )
                logging.info(f"✅ Администратору @{message.from_user.username} показана расширенная клавиатура")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения в ЛС: {e}")
        else:
            # Обычный пользователь - показываем обычную клавиатуру
            try:
                bot.send_message(
                    message.chat.id,
                    "👋 Бот для учёта отсутствующих\n\n"
                    "Нажмите кнопку ниже для взаимодействия с ботом.",
                    reply_markup=keyboard
                )
                logging.info(f"✅ Пользователю @{message.from_user.username} показана обычная клавиатура")
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения в ЛС: {e}")
    else:
        # ГРУППЫ
        keyboard = create_attendance_keyboard()
        try:
            bot.send_message(
                message.chat.id,
                "🎯 **Панель управления отсутствующими**\n\n"
                "Используйте кнопки ниже для отметки:\n"
                "• ❌ Отсутствую - отметить отсутствие\n"
                "• 📊 Получить отчёт - посмотреть список\n\n"
                "📋 *Команды:* /keyboard /help /list /report",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения в группу: {e}")

@bot.message_handler(commands=['help'])
def handle_help(message):
    """Обработка /help для ЛС и групп"""
    if message.chat.type == 'private' and not is_user_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ Доступ запрещен")
        return

    if message.chat.type == 'private':
        try:
            handle_start(message)
        except Exception as e:
            logging.error(f"Ошибка в handle_help для ЛС: {e}")
    else:
        try:
            bot.send_message(
                message.chat.id,
                "ℹ️ **Помощь по боту:**\n\n"
                "📝 *Как отметить отсутствие:*\n"
                "1. Нажмите кнопку '❌ Отсутствую'\n"
                "2. Выберите причину из списка\n"
                "3. Готово! Вы в списке\n\n"
                "📊 *Команды:*\n"
                "/keyboard - показать кнопки\n"
                "/list - список отсутствующих\n"
                "/help - эта справка",
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Ошибка отправки помощи в группу: {e}")

@bot.message_handler(commands=['keyboard'])
def handle_keyboard(message):
    """Показать клавиатуру (только в группах)"""
    if message.chat.type in ['group', 'supergroup']:
        keyboard = create_attendance_keyboard()
        try:
            bot.send_message(
                message.chat.id,
                "🎯 Панель кнопок:",
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Ошибка отправки клавиатуры: {e}")

@bot.message_handler(commands=['list'])
def handle_list(message):
    """Показать список (только в группах)"""
    if message.chat.type in ['group', 'supergroup']:
        try:
            send_today_report_to_chat(message.chat.id)
        except Exception as e:
            logging.error(f"Ошибка отправки списка: {e}")

# ===== ОБРАБОТЧИК КОМАНДЫ BIND_GROUP =====
@bot.message_handler(func=lambda message: True, content_types=['new_chat_members'])
def handle_new_chat_member(message):
    """Обработчик добавления бота в группу"""
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.send_message(
                message.chat.id,
                "👋 Привет! Я бот для учета посещаемости.\n\n"
                "!!!ОЧЕНЬ ВАЖНО - ПЕРЕД НАЧАЛОМ ЛЮБОЙ РАБОТЫ С БОТОМ!!! \n "
                "ДЛЯ КОРРЕКТНОГО ДОБАВЛЕНИЯ БОТА В ГРУППУ И ЕГО РАБОТЫ НУЖНО ВСЕМ(особенно тому кто его добавил или будет администрировать группу) НАЖАТЬ В ЛИЧНЫХ СООБЩЕНИЯХ С БОТОМ КНОПКУ start ИЛИ НАПИСАТЬ /start" \
                "а также ВСЕМ УЧАСТНИКАМ ЧАТА НАПИСАТЬ ЛЮБОЕ СООБЩЕНИЕ В ЧАТ"
                "Чтобы привязать бота, выполните команду:\n"
                "`/start_bind [название группы]`\n\n"
                "Пример: `/start_bind Группа 101`"
            )
            return

@bot.message_handler(commands=['start_bind', 'bind_group'])
def handle_bind_group(message):
    """Обработчик команды привязки группы"""
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "⛔ Эта команда доступна только в группах")
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or f"ID: {user_id}"

    # Извлекаем название группы из параметра команды
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message,
            "❌ Неверный формат команды\n\n"
            "✅ Правильно:\n"
            "/start_bind [название группы]\n\n"
            "Пример:\n"
            "/start_bind Группа 101"
        )
        return

    group_name = parts[1].strip()
    logging.info(f"📋 Привязка группы: название='{group_name}', chat_id={chat_id}")

    # Проверяем, что пользователь является администратором группы
    try:
        member = bot.get_chat_member(chat_id, user_id)
        if member.status not in ['administrator', 'creator']:
            bot.reply_to(message, "⛔ Только администраторы группы могут использовать эту команду")
            return
    except Exception as e:
        logging.error(f"Ошибка проверки прав администратора: {e}")
        bot.reply_to(message, "❌ Ошибка проверки прав администратора")
        return

    # Добавляем запрос в pending_binds
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''INSERT INTO pending_binds (chat_id, requester_id, group_name)
                     VALUES (?, ?, ?)''', (chat_id, user_id, group_name))
        conn.commit()
        pending_id = c.lastrowid
    except Exception as e:
        logging.error(f"Ошибка сохранения запроса на привязку: {e}")
        bot.reply_to(message, "❌ Ошибка обработки запроса")
        return
    finally:
        conn.close()

    # Отправляем уведомление супер-админам
    for admin_id in ALLOWED_USER_IDS:
        try:
            bot.send_message(
                admin_id,
                f"📢 Новый запрос на привязку группы:\n\n"
                f"👤 Запросил: @{username} (ID: {user_id})\n"
                f"💬 Группа: {group_name}\n"
                f"🆔 ID группы: {chat_id}\n\n"
                f"Для подтверждения используйте команду /gen_key {chat_id} @{username}"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

    # Отправляем подтверждение в группу
    bot.reply_to(message,
        f"✅ Запрос на привязку группы '{group_name}' отправлен администраторам бота.\n"
        f"Ожидайте подтверждения в личных сообщениях."
    )

def generate_activation_key(length=16):
    """Генерация случайного ключа активации"""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

@bot.message_handler(commands=['gen_key'])
def handle_gen_key(message):
    """Обработчик генерации ключа активации"""
    if message.chat.type != 'private':
        bot.reply_to(message, "⛔ Эта команда доступна только в личных сообщениях")
        return

    user_id = message.from_user.id
    if user_id not in ALLOWED_USER_IDS:
        bot.reply_to(message, "⛔ Только супер-администраторы могут генерировать ключи")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message,
            "❌ Неверный формат команды\n\n"
            "✅ Правильно:\n"
            "/gen_key <ID_группы> <@username_админа>\n\n"
            "Пример:\n"
            "/gen_key -123456789 @admin_user")
        return

    try:
        chat_id = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ ID группы должен быть числом")
        return

    target_username = parts[2].strip('@')

    # Получаем user_id целевого пользователя
    target_user_id = db.get_user_id_by_username(target_username)
    if not target_user_id:
        bot.reply_to(message, f"❌ Пользователь @{target_username} не найден в базе")
        return

    # Генерируем уникальный ключ
    key = generate_activation_key()

    # Сохраняем ключ в базу
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        logging.info(f"📝 Сохраняем ключ: key={key}, chat_id={chat_id} (тип: {type(chat_id)}), target_admin_id={target_user_id}")
        c.execute('''INSERT INTO activation_keys
                    (key, chat_id, target_admin_id)
                    VALUES (?, ?, ?)''',
                    (key, chat_id, target_user_id))
        conn.commit()
        logging.info(f"✅ Ключ {key} сохранён в БД для группы {chat_id} и пользователя {target_user_id}")
    except Exception as e:
        logging.error(f"Ошибка сохранения ключа активации: {e}")
        bot.reply_to(message, "❌ Ошибка генерации ключа")
        return
    finally:
        conn.close()

    # Отправляем ключ целевому пользователю
    try:
        bot.send_message(
            target_user_id,
            f"🔑 Ключ активации для группы {chat_id}:\n\n"
            f"`{key}`\n\n"
            f"Для активации введите команду:\n"
            f"`/activate_key {key}`"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки ключа пользователю {target_user_id}: {e}")
        bot.reply_to(message, f"❌ Не удалось отправить ключ пользователю @{target_username}")
        return

    bot.reply_to(message, f"✅ Ключ активации для @{target_username} успешно сгенерирован и отправлен")

@bot.message_handler(commands=['activate_key'])
def handle_activate_key(message):
    """Обработчик активации ключа"""
    if message.chat.type != 'private':
        bot.reply_to(message, "⛔ Эта команда доступна только в личных сообщениях")
        return

    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message,
            "❌ Неверный формат команды\n\n"
            "✅ Правильно:\n"
            "`/activate_key <ключ_активации>`\n\n"
            "Пример:\n"
            "`/activate_key AbCdEfGh12345678`")
        return

    key = parts[1].strip()

    # Проверяем ключ в базе
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        logging.info(f"🔍 Ищем ключ: {key}")
        c.execute('''SELECT chat_id, target_admin_id, used
                    FROM activation_keys
                    WHERE key = ?''', (key,))
        key_data = c.fetchone()

        if not key_data:
            logging.warning(f"⚠️ Ключ {key} не найден в базе")
            bot.reply_to(message, "❌ Неверный ключ активации")
            return

        chat_id, target_admin_id, used = key_data
        logging.info(f"✅ Ключ найден: chat_id={chat_id} (тип: {type(chat_id)}), target_admin_id={target_admin_id}, used={used}")

        if used:
            logging.warning(f"⚠️ Ключ {key} уже использован")
            bot.reply_to(message, "❌ Этот ключ уже был использован")
            return

        if target_admin_id != user_id:
            logging.warning(f"⚠️ Ключ {key} предназначен для {target_admin_id}, а активирует {user_id}")
            bot.reply_to(message, "⛔ Этот ключ не предназначен для вас")
            return

        logging.info(f"✅ Проверки пройдены. Активируем админа {user_id} для группы {chat_id}")

        # Обновляем статус ключа
        c.execute('''UPDATE activation_keys
                    SET used = 1, used_at = CURRENT_TIMESTAMP
                    WHERE key = ?''', (key,))
        logging.info(f"✅ Ключ {key} отмечен как использованный")

        # Получаем название группы из pending_binds
        c.execute('''SELECT group_name FROM pending_binds WHERE chat_id = ? ORDER BY requested_at DESC LIMIT 1''', (chat_id,))
        group_name_result = c.fetchone()

        if group_name_result and group_name_result[0]:
            group_name = group_name_result[0]
            logging.info(f"📋 Найдено название из /start_bind: '{group_name}'")
        else:
            logging.warning(f"⚠️ Название группы из /start_bind не найдено для chat_id={chat_id}")
            group_name = "название группы не указано"

        logging.info(f"📋 Используем название группы: '{group_name}'")

        # Добавляем или обновляем группу (с названием ИЗ /start_bind, а НЕ из Telegram)
        c.execute('''INSERT OR REPLACE INTO groups
                    (chat_id, name, verified)
                    VALUES (?, ?, 1)''', (chat_id, group_name))
        logging.info(f"✅ Группа {chat_id} сохранена с названием: '{group_name}' (из команды /start_bind, а не из Telegram)")

        conn.commit()
        logging.info(f"✅ Предварительные изменения закоммичены в БД")
        conn.close()

        # Добавляем администратора группы (используя метод класса с отдельным подключением)
        logging.info(f"📝 Добавляем администратора {target_admin_id} к группе {chat_id}...")
        success = db.add_group_admin(chat_id, target_admin_id)

        if success:
            # Проверяем, что админ действительно добавлен
            group_admins = db.get_group_admins(chat_id)
            if target_admin_id in group_admins:
                logging.info(f"✅ Администратор {target_admin_id} успешно добавлен и подтвержден в группе {chat_id}")
            else:
                logging.error(f"❌ ОШИБКА: Администратор {target_admin_id} добавлен, но не найден при проверке в группе {chat_id}!")
        else:
            logging.error(f"❌ Не удалось добавить администратора {target_admin_id} к группе {chat_id}")

        # Отправляем подтверждение
        bot.reply_to(message,
            f"✅ Вы успешно активированы как администратор!\n\n"
            f"💬 Группа: {group_name}\n"
            f"🆔 ID группы: {chat_id}\n\n"
            f"Теперь вы можете управлять отметками в этой группе."
        )

        # Уведомляем супер-админов
        for admin_id in ALLOWED_USER_IDS:
            try:
                bot.send_message(
                    admin_id,
                    f"📢 Ключ активации использован:\n\n"
                    f"👤 Активировал: @{message.from_user.username or user_id}\n"
                    f"💬 Группа: {group_name}\n"
                    f"🆔 ID группы: {chat_id}"
                )
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

    except Exception as e:
        logging.error(f"Ошибка активации ключа: {e}")
        bot.reply_to(message, "❌ Ошибка активации ключа")
    finally:
        conn.close()

# ===== КОМАНДЫ ТОЛЬКО ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ =====

@bot.message_handler(commands=['set_fio'])
def handle_set_fio(message):
    """Регистрация ФИО (только в ЛС)"""
    if message.chat.type == 'private':
        if not is_user_allowed(message.from_user.id):
            bot.reply_to(message, "⛔ У вас нет прав для регистрации пользователей")
            return
        process_set_fio_command(message)

@bot.message_handler(commands=['set_admin'])
def handle_set_admin(message):
    """Назначение администратора (только в ЛС)"""
    if message.chat.type == 'private':
        if not is_user_allowed(message.from_user.id):
            bot.reply_to(message, "⛔ У вас нет прав для назначения администратора")
            return

        db.set_admin(message.from_user.id)
        try:
            bot.reply_to(message,
                "✅ **Вы добавлены в список администраторов!**\n\n"
                "Теперь вы будете получать:\n"
                "• Ежедневные отчёты в 9:00\n"
                "• Списки отсутствующих\n"
                "• Запросы на подтверждение причин\n\n"
                "Ваш ID: `{}`".format(message.from_user.id),
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Ошибка отправки назначения админа: {e}")

@bot.message_handler(commands=['report'])
def handle_report(message):
    """Получить отчёт (работает везде)"""
    try:
        if message.chat.type == 'private':
            # Для личных сообщений проверяем группы администратора
            admin_id = message.from_user.id
            admin_username = message.from_user.username or f"ID: {admin_id}"
            groups = db.get_admin_groups(admin_id)

            logging.info(f"📊 Администратор @{admin_username} запросил отчёты. Всего групп: {len(groups)}")

            if groups:
                # Если администратор привязан к группам - отправляем отчеты по всем группам
                logging.info(f"📨 Отправляем отчёты администратору @{admin_username} по {len(groups)} группам")

                for chat_id, group_name in groups:
                    try:
                        # Получаем отчет для группы
                        report = get_group_report(chat_id)
                        if report:
                            bot.send_message(
                                message.chat.id,
                                f"📊 Отчёт для группы {group_name or chat_id}:\n\n{report}",
                                parse_mode='Markdown'
                            )
                            logging.info(f"✅ Отчёт отправлен администратору @{admin_username} для группы {chat_id} ({group_name})")
                        else:
                            bot.send_message(
                                message.chat.id,
                                f"ℹ️ В группе {group_name or chat_id} отсутствующих нет"
                            )
                            logging.info(f"ℹ️ Отчёт пуст для группы {chat_id} ({group_name})")
                    except Exception as e:
                        logging.error(f"❌ Ошибка отправки отчёта администратору @{admin_username} для группы {chat_id}: {e}")
                        bot.send_message(
                            message.chat.id,
                            f"❌ Ошибка при получении отчёта для группы {group_name or chat_id}"
                        )
                logging.info(f"✅ Все отчёты отправлены администратору @{admin_username}")
            else:
                # Если не админ групп - отправляем сообщение об отсутствии привязок
                logging.warning(f"⚠️ Администратор @{admin_username} не привязан ни к одной группе")
                bot.send_message(
                    message.chat.id,
                    "ℹ️ Вы не привязаны ни к одной группе как администратор"
                )
        else:
            # Для групп - обычный отчет
            logging.info(f"📊 Запрос отчёта из группы {message.chat.id}")
            send_today_report_to_chat(message.chat.id)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки отчёта: {e}")

# ===== ОБРАБОТЧИК ДЛЯ РЕГИСТРАЦИИ USERNAME ОТ ЛЮБОГО СООБЩЕНИЯ =====

@bot.message_handler(func=lambda message:
                     message.chat.type in ['group', 'supergroup'] and
                     message.text and
                     not message.text.startswith('/') and
                     message.text not in ['❌ Отсутствую', '📊 Получить отчёт'] and
                     db.get_user_state(message.from_user.id)[0] is None,
                     content_types=['text'])
def register_user_from_message(message):
    """Регистрировать username пользователя от любого сообщения в группе"""
    if message.from_user and message.from_user.username:
        db.update_username(message.from_user.username, message.from_user.id)
        logging.info(f"Username зарегистрирован: @{message.from_user.username} (ID: {message.from_user.id})")

# ===== ОСНОВНЫЕ ОБРАБОТЧИКИ КНОПОК =====

@bot.message_handler(func=lambda message: message.text == '❌ Отсутствую')
def handle_absence(message):
    """Обработчик кнопки отсутствия"""
    user_id = message.from_user.id
    username = message.from_user.username or f"ID: {user_id}"
    logging.info(f"📌 Кнопка 'Отсутствую' нажата пользователем @{username} в группе {message.chat.id}")

    if message.chat.type in ['group', 'supergroup']:
        # Устанавливаем состояние и показываем инлайн клавиатуру причин
        db.set_user_state(user_id, 'waiting_for_reason')
        keyboard = create_reason_inline_keyboard()

        try:
            bot.send_message(
                message.chat.id,
                "📋 Выберите причину отсутствия:",
                reply_markup=keyboard
            )
            logging.info(f"✅ Клавиатура причин отправлена пользователю @{username}")
        except Exception as e:
            logging.error(f"❌ Ошибка отправки клавиатуры причин пользователю @{username}: {e}")

# ===== ОБРАБОТЧИКИ INLINE КНОПОК =====

@bot.callback_query_handler(func=lambda call: call.data.startswith('reason_'))
def handle_reason_selection(call):
    """Обработчик выбора причины через inline-кнопки"""
    try:
        user_id = call.from_user.id
        username = call.from_user.username or f"ID: {user_id}"
        reason_type = call.data

        logging.info(f"🔘 Пользователь @{username} выбрал причину: {reason_type}")

        # Проверяем, что пользователь в правильном состоянии
        state, _ = db.get_user_state(user_id)
        valid_states = ['waiting_for_reason', 'waiting_for_custom_reason']

        if state not in valid_states:
            logging.warning(f"⚠️ Пользователь @{username} не в нужном состоянии (state: {state})")
            bot.answer_callback_query(call.id, "❌ Сначала нажмите '❌ Отсутствую'")
            return

        if reason_type == 'reason_cancel':
            # Отмена - очищаем состояние
            db.clear_user_state(user_id)
            bot.edit_message_text(
                "❌ Действие отменено",
                call.message.chat.id,
                call.message.message_id
            )
            logging.info(f"❌ Пользователь @{username} отменил выбор причины")
            bot.answer_callback_query(call.id)
            return

        elif reason_type == 'reason_other':
            # Для "Другого" просим ввести причину
            db.set_user_state(user_id, 'waiting_for_custom_reason')
            keyboard = create_cancel_inline_keyboard()

            bot.edit_message_text(
                "📝 Опишите причину отсутствия:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=keyboard
            )
            logging.info(f"📝 Пользователю @{username} отправлена просьба описать причину (Другое)")
        else:
            # Для автоматических причин сразу добавляем
            reason_map = {
                'reason_boleyu': '🤒 Болею',
                'reason_prikaz': '📋 Приказ на весь день',
                'reason_obshaga': '🏠 Деж. по общаге',
                'reason_college': '🏫 Деж. по колледжу',
                'reason_voenkomat': '🎖️ Военкомат',
                'reason_otpusk': '😎 Отпуск'
            }

            reason_text = reason_map.get(reason_type, '')
            logging.info(f"✅ Пользователь @{username} выбрал причину: {reason_text}")

            # Проверяем, есть ли уже активное отсутствие (Болею/Отпуск)
            active_absence = db.get_active_absence(user_id)
            if active_absence:
                # Если уже есть активное отсутствие, не даем добавиться на другую причину
                if reason_type not in ['reason_boleyu', 'reason_otpusk']:
                    logging.warning(f"⚠️ Пользователь @{username} уже отмечен как отсутствующий ({active_absence[2]})")
                    bot.edit_message_text(
                        f"❌ Вы уже в списке отсутствующих: {active_absence[2]}\n\n"
                        f"Нажмите 'Выхожу' в личном сообщении, чтобы вернуться.",
                        call.message.chat.id,
                        call.message.message_id
                    )
                    db.clear_user_state(user_id)
                    bot.answer_callback_query(call.id, "❌ Вы уже отмечены как отсутствующий")
                    return

            is_active_type = reason_type in ['reason_boleyu', 'reason_otpusk']
            group_chat_id = call.message.chat.id if call.message.chat.type in ['group', 'supergroup'] else None

            # Удаляем старые записи об отсутствии этого пользователя на сегодня
            db.remove_absence_from_today(user_id)
            # Удаляем активное отсутствие если было
            db.remove_active_absence(user_id)

            if not is_active_type:
                db.add_absence(user_id, 'уважительно', reason_text, group_chat_id)
            db.clear_user_state(user_id)
            logging.info(f"💾 Отсутствие записано для @{username}: {reason_text}")

            # Обновляем сообщение с подтверждением
            if reason_type == 'reason_boleyu':
                confirmation_message = (
                    f"✅ Записал: {reason_text}\n"
                    f"Статус: уважительно\n"
                    f"❗ Когда выздоровеешь, нажми кнопку \"Выхожу\" в личных сообщениях с ботом\n"
                    f"❓ Если у тебя нет этой кнопки, то нажми кнопку start в личных сообщениях с ботом и отметься повторно"
                )
            else:
                confirmation_message = f"✅ Записал: {reason_text}\nСтатус: уважительно"

            bot.edit_message_text(
                confirmation_message,
                call.message.chat.id,
                call.message.message_id
            )

            # Если это "Болею" или "Отпуск", отправляем в ЛС (там уже добавится в активный список)
            if reason_type in ['reason_boleyu', 'reason_otpusk']:
                logging.info(f"⏳ Пользователь @{username} будет добавлен в список активных отсутствующих через ЛС: {reason_text}")

                # Получаем ФИО пользователя
                fio = db.get_user_fio(user_id) or (f"@{username}" if username and username != f"ID: {user_id}" else f"ID: {user_id}")

                # Пытаемся отправить уведомление в личные сообщения И администраторам группы
                pm_sent = send_absence_notification_to_private(user_id, reason_text, username, fio, group_chat_id=group_chat_id)

                # Если не удалось отправить в ЛС, отправляем инструкцию в группу
                if not pm_sent:
                    logging.info(f"📬 Не удалось отправить ЛС, отправляем инструкцию в группу для @{username}")
                    try:
                        instruction_text = (
                            f"👤 {fio}\n"
                            f"📋 Отметил: {reason_text}\n\n"
                            f"⚠️ *Внимание:*\n"
                            f"Если вы не получили уведомление в личке, напишите /start боту.\n"
                            f"Это нужно сделать один раз, чтобы получить кнопку выхода."
                        )
                        bot.send_message(
                            call.message.chat.id,
                            instruction_text,
                            parse_mode='Markdown'
                        )
                        logging.info(f"✅ Инструкция отправлена в группу для @{username}")
                    except Exception as e:
                        logging.error(f"❌ Ошибка отправки инструкции в группу: {e}")

        bot.answer_callback_query(call.id)

    except Exception as e:
        logging.error(f"❌ Ошибка обработки выбора причины: {e}")
        bot.answer_callback_query(call.id, "Ошибка обработки")

# ===== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ДЛЯ ПРИЧИНЫ "ДРУГОЕ" =====

@bot.message_handler(func=lambda message:
                     message.chat.type in ['group', 'supergroup'] and
                     message.text != '📊 Получить отчёт' and
                     db.get_user_state(message.from_user.id)[0] == 'waiting_for_custom_reason')
def handle_custom_reason_input(message):
    """Обработчик ввода пользовательской причины"""
    user_id = message.from_user.id
    username = message.from_user.username or f"ID: {user_id}"
    reason = message.text
    group_chat_id = message.chat.id  # ID группы, где указана причина

    logging.info(f"📝 Пользователь @{username} ввёл причину: {reason} в группе {group_chat_id}")

    # Добавляем в ожидающие подтверждения
    pending_id = db.add_pending_absence(user_id, reason, group_chat_id)
    db.clear_user_state(user_id)
    logging.info(f"⏳ Причина добавлена в очередь на подтверждение. ID запроса: {pending_id}, группа: {group_chat_id}")

    # Отправляем админам конкретной группы на подтверждение
    admin_ids = db.get_group_admins(group_chat_id)
    if admin_ids:
        fio = db.get_user_fio(user_id) or f"ID: {user_id}"
        keyboard = create_admin_decision_keyboard(pending_id)

        logging.info(f"📨 Отправляем запрос на подтверждение причины администраторам группы {group_chat_id}. Причина: '{reason}', ФИО: {fio}, Всего админов: {len(admin_ids)}, Admin IDs: {admin_ids}")

        success_count = 0
        failed_count = 0

        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    f"📢 Запрос на подтверждение причины:\n\n"
                    f"👤 {fio}\n"
                    f"📝 Причина: {reason}\n\n"
                    f"Выберите тип отсутствия:",
                    reply_markup=keyboard
                )
                logging.info(f"✅ Запрос на подтверждение (ID {pending_id}) успешно отправлен администратору {admin_id} группы {group_chat_id}")
                success_count += 1
            except Exception as e:
                logging.error(f"❌ Ошибка отправки запроса (ID {pending_id}) администратору {admin_id}: {e}")
                failed_count += 1

        logging.info(f"✅ Запрос на подтверждение причины отправлен: успешно {success_count}/{len(admin_ids)} администраторам группы {group_chat_id}, ошибок: {failed_count}")
    else:
        logging.error(f"❌ Администраторы для группы {group_chat_id} не назначены. Запрос не может быть обработан")

    bot.send_message(
        message.chat.id,
        f"📨 Ваша причина отправлена на подтверждение администратору.\n"
        f"Причина: {reason}"
    )
    logging.info(f"✅ Пользователю @{username} отправлено уведомление об отправке причины на подтверждение")

# ===== ОБРАБОТЧИКИ КНОПОК В ЛС =====

@bot.message_handler(func=lambda message: message.text == '📊 Получить отчёт')
def handle_get_report(message):
    """Обработчик кнопки отчёта"""
    try:
        user_id = message.from_user.id
        username = message.from_user.username or f"ID: {user_id}"
        logging.info(f"📊 Нажата кнопка 'Получить отчёт' пользователем @{username} в {message.chat.type}")

        # Если это личное сообщение и пользователь - администратор группы
        if message.chat.type == 'private':
            admin_groups = db.get_admin_groups(user_id)
            logging.info(f"📊 Получены группы администратора: {admin_groups}")
            if admin_groups:
                # Отправляем отчет для каждой группы администратора
                for chat_id, group_name in admin_groups:
                    logging.info(f"📊 Отправляем отчет администратору {user_id} для группы {chat_id} (название из БД: '{group_name}')")
                    send_today_report_to_chat(message.chat.id, group_chat_id=chat_id)
            else:
                # Если администратор не привязан ни к какой группе, показываем все отсутствия
                logging.info(f"📊 Администратор {user_id} не привязан к группам, показываем все отсутствия")
                send_today_report_to_chat(message.chat.id, group_chat_id=None)
        else:
            # Отправляем отчет для группы
            send_today_report_to_chat(message.chat.id)
    except Exception as e:
        logging.error(f"❌ Ошибка обработки отчёта: {e}")
        bot.reply_to(message, "❌ Ошибка при получении отчёта.")

@bot.message_handler(func=lambda message: message.text == '📋 Текущие болеющие/в отпуске')
def handle_active_list_button(message):
    """Обработчик кнопки списка текущих отсутствующих"""
    if message.chat.type != 'private':
        bot.reply_to(message, "⛔ Эта кнопка доступна только в личном сообщении")
        return

    if not is_user_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ Доступ запрещен")
        return

    try:
        active_absences = db.get_all_active_absences()

        if not active_absences:
            bot.send_message(message.chat.id, "✅ Нет активных отсутствий (Болею/Отпуск)")
            return

        text = "📋 **Текущие отсутствующие (Болею/Отпуск):**\n\n"
        for user_id, absence_type, fio in active_absences:
            display_name = fio if fio else f"ID: {user_id}"
            text += f"• {display_name} - {absence_type}\n"

        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Ошибка отправки списка активных: {e}")
        bot.reply_to(message, "❌ Ошибка при получении списка")

@bot.message_handler(func=lambda message: message.text == '📝 Регистрация')
def handle_private_registration(message):
    if message.chat.type == 'private':
        user_id = message.from_user.id
        # Разрешаем супер-админов и администраторов групп
        is_super_admin = is_user_allowed(user_id)
        user_groups = db.get_admin_groups(user_id)
        is_group_admin = len(user_groups) > 0

        if not (is_super_admin or is_group_admin):
            bot.reply_to(message, "⛔ У вас нет прав для регистрации пользователей")
            return

        try:
            bot.send_message(
                message.chat.id,
                "📝 **Регистрация участников**\n\n"
                "Используйте команду для регистрации:\n"
                "`/set_fio @username ФИО`\n\n"
                "Примеры:\n"
                "`/set_fio @kapec919 Капец Сергей`\n"
                "`/set_fio 1424283030 Иванов Иван`",
                parse_mode='Markdown'
            )
            logging.info(f"✅ Справка по регистрации отправлена пользователю {user_id}")
        except Exception as e:
            logging.error(f"Ошибка отправки подсказки регистрации: {e}")

@bot.message_handler(func=lambda message: message.text == 'ℹ️ Информация')
def handle_private_info(message):
    if message.chat.type == 'private':
        user_id = message.from_user.id
        # Разрешаем супер-админов и администраторов групп
        is_super_admin = is_user_allowed(user_id)
        user_groups = db.get_admin_groups(user_id)
        is_group_admin = len(user_groups) > 0

        if not (is_super_admin or is_group_admin):
            bot.reply_to(message, "⛔ Доступ запрещен")
            return

        try:
            # Информация для админов групп
            if is_group_admin:
                groups_list = "\n".join([f"• {name}" for chat_id, name in user_groups])
                info_text = (
                    "ℹ️ **Инструкция для администратора группы**\n\n"
                    "🎯 **Как использовать бота:**\n\n"
                    "1️⃣ **Регистрация участников**\n"
                    "Используйте кнопку 'Регистрация' или команду:\n"
                    "`/set_fio @username ФИО`\n"
                    "Пример: `/set_fio @kapec919 Капец Сергей`\n\n"
                    "2️⃣ **Отчёт по отсутствиям**\n"
                    "Нажмите кнопку 'Получить отчёт' или `/report` в группе для списка отсутствующих\n\n"
                    "3️⃣ **Подтверждение причин**\n"
                    "Когда участник указывает причину 'Другое', вы получите уведомление с просьбой подтвердить: уважительная или неуважительная\n\n"
                    "4️⃣ **Типы отсутствий**\n"
                    "• 🤒 Болею - уважительная\n"
                    "• 😎 Отпуск - уважительная\n"
                    "• 📋 Приказ - уважительная\n"
                    "• 🏠 Дежурство - уважительная\n"
                    "• 🏫 Дежурство по колледжу - уважительная\n"
                    "• 🎖️ Военкомат - уважительная\n"
                    "• Другое - вы решаете (уважительная/неуважительная)\n\n"
                    "5️⃣ **Текущие отсутствующие**\n"
                    "Нажмите 'Текущие болеющие/в отпуске' для быстрого просмотра\n\n"
                    "📋 **Ваши группы:**\n" +
                    groups_list
                )
            else:
                # Информация для супер-админов
                info_text = (
                    "ℹ️ **Справка для супер-администратора**\n\n"
                    "Вам доступны все функции управления ботом."
                )

            bot.send_message(message.chat.id, info_text, parse_mode='Markdown')
            logging.info(f"✅ Информация отправлена пользователю {user_id}")
        except Exception as e:
            logging.error(f"Ошибка в информации в ЛС: {e}")

@bot.message_handler(func=lambda message: message.text == '🗑️ Удалить админа из группы')
def handle_remove_group_admin(message):
    """Обработчик кнопки удаления админа из группы"""
    if message.chat.type != 'private':
        bot.reply_to(message, "⛔ Эта кнопка доступна только в личном сообщении")
        return

    if message.from_user.id not in SUPER_ADMINS:
        bot.reply_to(message, "⛔ Эта функция доступна только супер-администраторам")
        return

    try:
        # Получаем всех администраторов групп
        all_admins = db.get_all_group_admins()

        if not all_admins:
            bot.send_message(message.chat.id, "ℹ️ Нет администраторов групп для удаления")
            return

        # Формируем список
        text = "🗑️ **Администраторы групп:**\n\n"
        for i, (chat_id, group_name, admin_id) in enumerate(all_admins, 1):
            admin_fio = db.get_user_fio(admin_id) or f"ID: {admin_id}"
            text += f"`{i}. {group_name} - {admin_fio} (ID: {admin_id})`\n"

        text += f"\n📝 Введите номер администратора для удаления (1-{len(all_admins)}):"

        # Сохраняем список в состояние
        db.set_user_state(message.from_user.id, 'waiting_for_admin_removal', {'admins': all_admins})

        bot.send_message(message.chat.id, text, parse_mode='Markdown')
        logging.info(f"📋 Супер-админ {message.from_user.id} запросил список администраторов групп")
    except Exception as e:
        logging.error(f"❌ Ошибка при получении списка администраторов: {e}")
        bot.reply_to(message, "❌ Ошибка при получении списка администраторов")

@bot.message_handler(func=lambda message:
                     message.chat.type == 'private' and
                     db.get_user_state(message.from_user.id)[0] == 'waiting_for_admin_removal')
def handle_admin_removal_input(message):
    """Обработчик ввода номера администратора для удаления"""
    try:
        user_id = message.from_user.id
        state, data = db.get_user_state(user_id)

        if state != 'waiting_for_admin_removal' or not data:
            bot.reply_to(message, "❌ Сессия истекла. Используйте кнопку заново.")
            return

        try:
            choice = int(message.text) - 1
        except ValueError:
            bot.reply_to(message, "❌ Введите номер (цифру)")
            return

        admins = data.get('admins', [])
        if choice < 0 or choice >= len(admins):
            bot.reply_to(message, f"❌ Выберите номер от 1 до {len(admins)}")
            return

        chat_id, group_name, admin_id = admins[choice]
        admin_fio = db.get_user_fio(admin_id) or f"ID: {admin_id}"

        # Удаляем администратора
        if db.remove_group_admin(chat_id, admin_id):
            db.clear_user_state(user_id)
            bot.send_message(
                message.chat.id,
                f"✅ Администратор удален!\n\n"
                f"📋 Группа: {group_name}\n"
                f"👤 Администратор: {admin_fio}"
            )
            logging.info(f"🗑️ Супер-админ {user_id} удалил администратора {admin_id} из группы {chat_id} ({group_name})")
        else:
            bot.reply_to(message, "❌ Ошибка при удалении администратора")
    except Exception as e:
        logging.error(f"❌ Ошибка обработки удаления администратора: {e}")
        bot.reply_to(message, "❌ Ошибка обработки")

# ===== ОБРАБОТЧИК ВЫХОДА ИЗ ОТСУТСТВИЯ =====

@bot.callback_query_handler(func=lambda call: call.data == 'exit_absence')
def handle_exit_absence(call):
    """Обработчик нажатия кнопки 'Выхожу'"""
    try:
        user_id = call.from_user.id
        fio = db.get_user_fio(user_id) or f"ID: {user_id}"

        # Получаем информацию об отсутствии
        absence_info = db.get_active_absence(user_id)

        if not absence_info:
            bot.answer_callback_query(call.id, "❌ Вы не в списке отсутствующих")
            return

        absence_type = absence_info[2]  # получаем тип отсутствия (Болею/Отпуск)

        # Удаляем из активного списка отсутствующих
        db.remove_active_absence(user_id)

        # Удаляем из записей отсутствий на сегодня
        db.remove_absence_from_today(user_id)

        # Обновляем сообщение с подтверждением
        bot.edit_message_text(
            f"✅ Вернулся грызть гранит науки!\n\n"
            f"Вы удалены из списка отсутствующих.\n"
            f"{absence_type} окончена.",
            call.message.chat.id,
            call.message.message_id
        )

        # Уведомляем администраторов группы
        group_chat_id = absence_info[5]
        if group_chat_id:
            admin_ids = db.get_group_admins(group_chat_id)
        else:
            admin_ids = []

        logging.info(f"📢 Отправляем уведомление о возвращении администраторам группы {group_chat_id}. ФИО: {fio}, Причина: {absence_type}, Всего админов: {len(admin_ids)}, Admin IDs: {admin_ids}")

        success_count = 0
        failed_count = 0

        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    f"📢 Уведомление о возвращении:\n\n"
                    f"👤 {fio}\n"
                    f"📋 Причина: {absence_type}\n"
                    f"✅ Вышел из списка отсутствующих"
                )
                logging.info(f"✅ Уведомление о возвращении успешно отправлено администратору {admin_id}")
                success_count += 1
            except Exception as e:
                logging.error(f"❌ Ошибка отправки уведомления администратору {admin_id}: {e}")
                failed_count += 1

        logging.info(f"✅ Уведомление о возвращении отправлено: успешно {success_count}/{len(admin_ids)} администраторам, ошибок: {failed_count}")

        bot.answer_callback_query(call.id, "✅ Вы удалены из списка отсутствующих")

    except Exception as e:
        logging.error(f"Ошибка обработки выхода из отсутствия: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

# ===== ОБРАБОТЧИК РЕШЕНИЙ АДМИНИСТРАТОРА =====

@bot.callback_query_handler(func=lambda call: call.data.startswith('approve_'))
def handle_admin_decision(call):
    """Обработчик решения администратора"""
    try:
        admin_id = call.from_user.id
        data_parts = call.data.split('_')
        decision = data_parts[1]  # respectful или disrespectful
        pending_id = int(data_parts[2])

        logging.info(f"📋 Администратор {admin_id} обрабатывает запрос на подтверждение. Решение: {decision}, ID запроса: {pending_id}")

        # Получаем данные о ожидающей причине с проверкой группы
        pending_data = db.get_pending_absence(pending_id)
        if not pending_data:
            logging.warning(f"⚠️ Запрос {pending_id} уже обработан или не найден")
            bot.answer_callback_query(call.id, "Запрос уже обработан")
            return

        user_id = pending_data[1]
        reason = pending_data[2]
        date_str = pending_data[3]
        group_chat_id = pending_data[4]  # Получаем ID группы (индекс 4)
        fio = pending_data[6] or f"ID: {user_id}"  # Индекс 6 - результат JOIN с users

        logging.info(f"✅ Получены данные запроса: ID={pending_data[0]}, user_id={user_id}, reason='{reason}', group_chat_id={group_chat_id}, fio={fio}")
        logging.info(f"📋 Структура: [id={pending_data[0]}, user_id={pending_data[1]}, reason={pending_data[2]}, date={pending_data[3]}, group_chat_id={pending_data[4]}, created_at={pending_data[5]}, fio={pending_data[6]}]")

        # Проверяем, является ли администратор администратором этой группы
        if group_chat_id is None:
            logging.error(f"❌ group_chat_id is None для pending_id {pending_id}")
            bot.answer_callback_query(call.id, "❌ Ошибка: не указана группа для этой причины")
            return

        group_admins = db.get_group_admins(group_chat_id)
        logging.info(f"📋 Admin {admin_id} checking against group {group_chat_id} admins: {group_admins}")

        if admin_id not in group_admins:
            logging.warning(f"❌ Администратор {admin_id} не является админом группы {group_chat_id}")
            bot.answer_callback_query(call.id, "❌ У вас нет прав на подтверждение причин для этой группы")
            return

        # Определяем тип отсутствия
        absence_type = 'уважительно' if decision == 'respectful' else 'неуважительно'

        # Добавляем в основную таблицу
        db.add_absence(user_id, absence_type, reason, group_chat_id)
        logging.info(f"✅ Запись об отсутствии добавлена: {fio}, тип: {absence_type}, причина: {reason}, группа: {group_chat_id}")

        # Удаляем из ожидающих
        db.delete_pending_absence(pending_id)

        # Обновляем сообщение админу
        bot.edit_message_text(
            f"✅ Причина подтверждена:\n\n"
            f"👤 {fio}\n"
            f"📝 Причина: {reason}\n"
            f"📋 Статус: {absence_type}\n"
            f"💬 Группа: {group_chat_id}",
            call.message.chat.id,
            call.message.message_id
        )

        # Уведомляем пользователя
        try:
            bot.send_message(
                user_id,
                f"✅ Ваша причина подтверждена администратором:\n"
                f"Причина: {reason}\n"
                f"Статус: {absence_type}\n"
                f"Группа: {group_chat_id}"
            )
            logging.info(f"✅ Пользователю {user_id} ({fio}) отправлено уведомление об одобрении")
        except Exception as e:
            logging.warning(f"⚠️ Не удалось отправить уведомление пользователю {user_id}: {e}")

        bot.answer_callback_query(call.id, f"Статус установлен: {absence_type}")
        logging.info(f"✅ Запрос {pending_id} успешно обработан администратором {admin_id}")

    except Exception as e:
        logging.error(f"❌ Ошибка обработки решения админа: {e}")
        bot.answer_callback_query(call.id, "Ошибка обработки")

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def process_set_fio_command(message):
    """Обработать команду установки ФИО"""
    try:
        parts = message.text.split(' ', 2)
        if len(parts) >= 3:
            target = parts[1].strip('@')
            fio = parts[2]

            print(f"🔍 Регистрация: target='{target}', fio='{fio}'")

            user_id = None

            if target.isdigit():
                user_id = int(target)
                print(f"✅ Используем прямой ID: {user_id}")
            else:
                print(f"🔍 Ищем user_id для username: {target}")
                user_id = db.get_user_id_by_username(target)
                print(f"🔍 Результат поиска: {user_id}")

                if user_id is None:
                    bot.reply_to(message,
                        f"❌ Пользователь @{target} не найден.\n\n"
                        f"Пользователь должен сначала написать любое сообщение в группе с ботом."
                    )
                    return

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''REPLACE INTO users (user_id, fio)
                         VALUES (?, ?)''', (user_id, fio))
            conn.commit()
            conn.close()

            print(f"✅ Успешно зарегистрирован: {fio} (ID: {user_id})")
            bot.reply_to(message, f"✅ Зарегистрирован: {fio} (ID: {user_id})")

        else:
            bot.reply_to(message,
                "❌ Неверный формат!\n\n"
                "✅ Правильно:\n"
                "• `/set_fio @username Фамилия Имя`\n"
                "• `/set_fio 123456789 Фамилия Имя`\n\n"
                "Для username пользователь должен сначала написать в группе.",
                parse_mode='Markdown'
            )

    except Exception as e:
        print(f"💥 Ошибка регистрации: {e}")
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['update_group_name'])
def handle_update_group_name(message):
    """Обновить название группы в БД"""
    user_id = message.from_user.id

    # Проверяем, является ли пользователь супер-админом
    if user_id not in SUPER_ADMINS:
        bot.reply_to(message, "⛔ Доступ запрещен. Эта команда доступна только супер-администраторам.")
        return

    try:
        parts = message.text.split(' ', 2)
        if len(parts) < 3:
            bot.reply_to(message,
                "❌ Неверный формат!\n\n"
                "✅ Правильно:\n"
                "• `/update_group_name <ID_группы> <новое_название>`\n\n"
                "Пример:\n"
                "/update_group_name -123456789 1229",
                parse_mode='Markdown'
            )
            return

        try:
            chat_id = int(parts[1])
        except ValueError:
            bot.reply_to(message, "❌ ID группы должен быть числом")
            return

        new_name = parts[2]

        # Обновляем название в БД
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''UPDATE groups SET name = ? WHERE chat_id = ?''', (new_name, chat_id))
        conn.commit()
        conn.close()

        logging.info(f"✅ Супер-админ {user_id} обновил название группы {chat_id} на '{new_name}'")

        bot.reply_to(message,
            f"✅ Название группы обновлено!\n\n"
            f"📋 ID группы: {chat_id}\n"
            f"📝 Новое название: {new_name}"
        )

    except Exception as e:
        logging.error(f"❌ Ошибка обновления названия группы: {e}")
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['delete'])
def handle_delete_absence(message):
    """Обработчик команды /delete для удаления отсутствия"""
    user_id = message.from_user.id

    # Проверяем, является ли пользователь админом
    if not is_user_allowed(user_id):
        bot.reply_to(message, "⛔ Доступ запрещен. Эта команда доступна только администраторам.")
        return

    try:
        parts = message.text.split(' ', 1)
        if len(parts) < 2:
            bot.reply_to(message,
                "❌ Неверный формат!\n\n"
                "✅ Правильно:\n"
                "• `/delete @username`\n\n"
                "Удалит отсутствие пользователя из списка отсутствующих на сегодня.",
                parse_mode='Markdown'
            )
            return

        target = parts[1].strip('@')

        # Ищем user_id по username
        target_user_id = db.get_user_id_by_username(target)

        if target_user_id is None:
            bot.reply_to(message,
                f"❌ Пользователь @{target} не найден.\n\n"
                f"Пользователь должен сначала написать любое сообщение в группе с ботом."
            )
            return

        # Получаем ФИО удаляемого пользователя
        target_fio = db.get_user_fio(target_user_id) or f"ID: {target_user_id}"

        # Удаляем отсутствие из таблицы absences (сегодняшние отсутствия)
        db.remove_absence_from_today(target_user_id)

        # Удаляем из активных отсутствующих если там есть
        db.remove_active_absence(target_user_id)

        logging.info(f"🗑️ Администратор {user_id} удалил отсутствие пользователя @{target} ({target_fio})")

        bot.reply_to(message,
            f"✅ Отсутствие удалено!\n\n"
            f"👤 {target_fio} (@{target})\n"
            f"удален из списка отсутствующих на сегодня."
        )

    except Exception as e:
        logging.error(f"❌ Ошибка удаления отсутствия: {e}")
        bot.reply_to(message, f"❌ Ошибка: {e}")

def format_absence_type(absence_type):
    """Форматировать тип отсутствия для отчета"""
    if absence_type == 'уважительно':
        return 'уважительная'
    elif absence_type == 'неуважительно':
        return 'неуважительная'
    return absence_type

def format_reason_for_report(reason):
    """Форматировать причину для отчета (убрать смайлики)"""
    reason_mapping = {
        '🤒 Болею': 'болеет',
        '📋 Приказ на весь день': 'приказ',
        '🏠 Деж. по общаге': 'дежурство по общаге',
        '🏫 Деж. по колледжу': 'дежурство по колледжу',
        '🎖️ Военкомат': 'военкомат',
        '😎 Отпуск' : 'отпуск'
    }
    # Если причина есть в маппинге, используем короткую версию, иначе оставляем как есть
    if reason in reason_mapping:
        return reason_mapping[reason]
    elif reason == 'болею':
        return 'болеет'
    elif reason == 'отпуск':
        return 'в отпуске'
    else:
        return reason

def get_group_report(chat_id):
    """Сформировать отчет по группе"""
    try:
        absences = db.get_today_absences(chat_id)
        if not absences:
            return None

        today_formatted = date.today().strftime('%d.%m')
        message = f"На {today_formatted} отсутствуют:\n\n"
        absences = sorted(absences, key=lambda x: (x[0] or f"ID: {x[3]}").lower())

        for i, (fio, absence_type, reason, user_id) in enumerate(absences, 1):
            display_name = fio if fio else f"ID: {user_id}"
            formatted_reason = format_reason_for_report(reason)
            formatted_type = format_absence_type(absence_type)
            message += f"{i}. {display_name}\n({formatted_reason}/ {formatted_type})\n\n"

        return message.strip()
    except Exception as e:
        logging.error(f"Ошибка формирования отчёта для группы {chat_id}: {e}")
        return None

def send_today_report_to_chat(chat_id, group_chat_id=None):
    """Отправить отчёт об отсутствующих в указанный чат"""
    try:
        logging.info(f"📊 Начинаем подготовку отчёта для чата {chat_id}")
        # Если группа не передана явно, определяем по типу chat_id
        if group_chat_id is None:
            group_chat_id = chat_id if chat_id < 0 else None
        absences = db.get_today_absences(group_chat_id)

        logging.info(f"📊 Получено {len(absences)} отсутствующих для отчёта")

        if not absences:
            bot.send_message(chat_id, "✅ На сегодня отсутствующих нет")
            logging.info(f"✅ Отправлен пустой отчёт в чат {chat_id}")
            return

        # Получаем название группы для отчета в личном сообщении админа
        group_name = ""
        if group_chat_id and group_chat_id < 0:  # Это ID группы
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT name FROM groups WHERE chat_id = ?", (group_chat_id,))
                result = c.fetchone()
                conn.close()
                if result and result[0]:
                    group_name = result[0]
                    logging.info(f"📋 Найдено название группы {group_chat_id}: '{group_name}'")
                else:
                    logging.warning(f"⚠️ Название группы {group_chat_id} не найдено в БД")
            except Exception as e:
                logging.warning(f"⚠️ Ошибка получения названия группы {group_chat_id}: {e}")

        # Новый формат отчета
        today_formatted = date.today().strftime('%d.%m')
        if group_name:
            message = f"📋 **{group_name}**\nНа {today_formatted} отсутствуют:\n\n"
        else:
            message = f"На {today_formatted} отсутствуют:\n\n"

        # Сортируем по ФИО в алфавитном порядке
        absences = sorted(absences, key=lambda x: (x[0] or f"ID: {x[3]}").lower())

        # Логируем информацию о болеющих и отпускных в отчёте
        ill_count = 0
        vacation_count = 0
        other_count = 0

        for i, (fio, absence_type, reason, user_id) in enumerate(absences, 1):
            display_name = fio if fio else f"ID: {user_id}"
            formatted_reason = format_reason_for_report(reason)
            formatted_type = format_absence_type(absence_type)

            message += f"{i}. {display_name}\n({formatted_reason}/ {formatted_type})\n\n"

            # Подсчитываем по типам для логирования
            if 'более' in formatted_reason.lower() or 'болею' in formatted_reason.lower():
                ill_count += 1
            elif 'отпуск' in formatted_reason.lower():
                vacation_count += 1
            else:
                other_count += 1

        bot.send_message(chat_id, message, parse_mode='Markdown')
        if group_name:
            logging.info(f"📤 Отчёт для группы '{group_name}' отправлен в чат {chat_id}. Всего: {len(absences)} (болеют: {ill_count}, в отпуске: {vacation_count}, другое: {other_count})")
        else:
            logging.info(f"📤 Отчёт отправлен в чат {chat_id}. Всего: {len(absences)} (болеют: {ill_count}, в отпуске: {vacation_count}, другое: {other_count})")

    except Exception as e:
        logging.error(f"❌ Ошибка отправки отчёта в чат {chat_id}: {e}")
        try:
            bot.send_message(chat_id, "❌ Ошибка при получении списка отсутствующих")
        except:
            logging.error(f"❌ Не удалось отправить сообщение об ошибке в чат {chat_id}")



def run_bot_with_restart():
    """Запуск бота с авто-перезапуском"""
    import requests

    # Временно отключено удаление webhook из-за проблем с подключением
    # try:
    #     token = os.getenv('BOT_TOKEN')
    #     response = requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook")
    #     print(f"✅ Webhook удалён: {response.json()}")
    #     time.sleep(2)
    # except Exception as e:
    #     print(f"⚠️ Не удалось удалить webhook: {e}")

    restart_count = 0

    while True:
        try:
            print(f"🟢 Запуск бота (попытка {restart_count + 1})...")
            restart_count += 1

            bot.polling(none_stop=True, interval=0, timeout=20)

        except Exception as e:
            print(f"🔴 Бот упал: {e}")
            print("🔄 Перезапуск через 10 секунд...")
            time.sleep(10)

# Запуск бота
if __name__ == '__main__':
    print("🤖 Telegram Bot - Русская версия")
    print("🟢 Запускаем планировщик отчётов...")



    print("🟢 Запускаем бота...")
    run_bot_with_restart()


