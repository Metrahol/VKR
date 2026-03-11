import sys
import re
import json
import threading
import time
import speech_recognition as sr
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import Signal, Qt, QThread, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC_DIR = os.path.join(BASE_DIR, "music")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
UI_DIR = os.path.join(BASE_DIR, "ui")

# Импорт твоих модулей
from rag_retriever import get_philosopher_context, get_web_context
import resources_rc
from debate_manager import DebateManager
from workers import AgentWorker, SpeakerWorker
from agents import DeepSeekManager, get_opponent_system_prompt, MODERATOR_PROMPT, JURY_PROMPT, CRITIQUE_PROMPT
from database import DatabaseManager
from philosophers_data import PHILOSOPHERS_DATA

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?…])\s+')


# ==========================================
# 1. ПОТОК РАСПОЗНАВАНИЯ (УЛУЧШЕННЫЙ)
# ==========================================
class SpeechThread(QThread):
    """Поток для распознавания речи с настройками для длинных фраз"""
    status_updated = Signal(str)
    text_recognized = Signal(str)
    error_occurred = Signal(str)
    finished_listening = Signal()

    def run(self):
        recognizer = sr.Recognizer()

        # --- ВАЖНЫЕ НАСТРОЙКИ КАЧЕСТВА ---
        recognizer.energy_threshold = 300  # Чувствительность микрофона
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 1.2  # Ждем 1.2 сек тишины перед тем как закончить (было 0.8)
        # ---------------------------------

        try:
            with sr.Microphone() as source:
                self.status_updated.emit("Калибровка шума...")
                recognizer.adjust_for_ambient_noise(source, duration=0.8)

                self.status_updated.emit("Говорите! (Я слушаю...)")

                # phrase_time_limit=20 -> даем до 20 секунд на одну фразу
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=20)

                self.status_updated.emit("Обрабатываю...")

                # Используем Google Cloud Speech (через библиотеку)
                # Совет: Говори четко. Для идеального распознавания терминов нужен OpenAI Whisper,
                # но он требует мощной видеокарты и сложной установки. Google - лучший из простых.
                text = recognizer.recognize_google(audio, language="ru-RU")

                self.text_recognized.emit(text)

        except sr.WaitTimeoutError:
            self.error_occurred.emit("Тишина... Нажмите еще раз.")
        except sr.UnknownValueError:
            self.error_occurred.emit("Не разобрал слова. Попробуйте четче.")
        except Exception as e:
            self.error_occurred.emit(f"Ошибка сети/микрофона")
        finally:
            self.finished_listening.emit()


# 1b. ПОТОК ФОРМАТИРОВАНИЯ ТЕМЫ (DeepSeek)
# ==========================================
class TopicFormatterThread(QThread):
    """Запускает DeepSeekManager.format_topic в отдельном потоке, чтобы не замораживать UI."""
    topic_ready = Signal(str)

    def __init__(self, raw_topic, deepseek_manager, parent=None):
        super().__init__(parent)
        self.raw_topic = raw_topic
        self.ds = deepseek_manager

    def run(self):
        result = self.ds.format_topic(self.raw_topic)
        self.topic_ready.emit(result)

# ==========================================
class VoiceInputDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, title="Ваш ход", label_text="Введите аргумент:", is_tutorial=False):
        super().__init__(parent)
        
        self.resize(600, 200)

        if is_tutorial:
            # Делаем обычным виджетом внутри родителя без рамки
            self.setWindowFlags(Qt.WindowType.Widget | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setModal(False)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
            self.setModal(True)

        # СТИЛЬ: Плотный темный фон, чтобы не "висело в воздухе"
        self.setStyleSheet("""
            QDialog {
                /* Темно-синяя подложка, почти непрозрачная */
                background-color: #1e1e2e; 
                border: 3px solid #d4af37; /* Золотая рамка */
                border-radius: 15px;
            }
            QLabel#Title {
                font-size: 22px; font-weight: bold; color: #ffca28; 
                background: transparent; font-family: "Segoe UI", sans-serif;
                margin-bottom: 5px;
            }
            /* Поле ввода */
            QTextEdit {
                font-size: 18px; padding: 10px; 
                border: 2px solid #5d4037; border-radius: 8px; 
                background-color: #f5f5f5; color: #1a1a1a;
            }

            /* Кнопка ОТПРАВИТЬ (Сделали широкой и зеленой) */
            QPushButton#SendBtn {
                background-color: #2e7d32; 
                color: white; 
                font-weight: bold; font-size: 18px; 
                border-radius: 8px; padding: 10px;
                border: 1px solid #1b5e20;
            }
            QPushButton#SendBtn:hover { background-color: #388e3c; }

            /* Микрофон */
            QPushButton#MicBtn {
                background-color: #ffb300; border: 2px solid #e65100; color: #3e2723;
                border-radius: 25px; font-size: 24px;
            }
            QPushButton#MicBtn:hover { background-color: #ffca28; }
            QPushButton#MicBtn:disabled { background-color: #bdbdbd; border-color: #757575; }

            QLabel#Status {
                font-size: 13px; color: #90caf9; font-weight: bold; background: transparent;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        # Заголовок
        self.lbl = QtWidgets.QLabel(label_text)
        self.lbl.setObjectName("Title")
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl)

        # Поле ввода (QTextEdit)
        self.text_input = QtWidgets.QTextEdit()
        self.text_input.setPlaceholderText("Ваша речь появится здесь...")
        layout.addWidget(self.text_input)

        # Нижняя панель
        bottom_layout = QtWidgets.QHBoxLayout()

        # Блок микрофона (слева)
        mic_layout = QtWidgets.QVBoxLayout()
        self.mic_btn = QtWidgets.QPushButton("🎤")
        self.mic_btn.setObjectName("MicBtn")
        self.mic_btn.setFixedSize(50, 50)
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.status_lbl = QtWidgets.QLabel("Нажмите для записи")
        self.status_lbl.setObjectName("Status")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        mic_layout.addWidget(self.mic_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        mic_layout.addWidget(self.status_lbl, alignment=Qt.AlignmentFlag.AlignCenter)

        # Кнопка ОТПРАВИТЬ (справа, большая)
        self.send_btn = QtWidgets.QPushButton("ОТПРАВИТЬ ОТВЕТ")
        self.send_btn.setObjectName("SendBtn")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setMinimumHeight(50)

        # Добавляем в лайаут (убрали кнопку отмены!)
        bottom_layout.addLayout(mic_layout, stretch=1)
        bottom_layout.addWidget(self.send_btn, stretch=3)

        layout.addLayout(bottom_layout)

        # Логика
        self.send_btn.clicked.connect(self.accept)
        self.mic_btn.clicked.connect(self.start_listen)

        self.speech_thread = None

    def start_listen(self):
        """Запуск слушания"""
        self.mic_btn.setEnabled(False)
        self.speech_thread = SpeechThread()
        self.speech_thread.status_updated.connect(self.update_status)
        self.speech_thread.text_recognized.connect(self.append_text)
        self.speech_thread.error_occurred.connect(self.show_error)
        self.speech_thread.finished_listening.connect(self.reset_mic_ui)
        self.speech_thread.start()

    @QtCore.Slot(str)
    def update_status(self, text):
        self.status_lbl.setText(text)
        if "Говорите" in text:
            self.mic_btn.setText("🔴")

    @QtCore.Slot(str)
    def append_text(self, text):
        current = self.text_input.toPlainText()
        new_text = f"{current} {text}" if current else text
        self.text_input.setText(new_text)
        # Автопрокрутка вниз
        cursor = self.text_input.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.text_input.setTextCursor(cursor)
        self.status_lbl.setText("Успешно!")

    @QtCore.Slot(str)
    def show_error(self, err_msg):
        self.status_lbl.setText("Не расслышал")

    @QtCore.Slot()
    def reset_mic_ui(self):
        self.mic_btn.setEnabled(True)
        self.mic_btn.setText("🎤")
        QtCore.QTimer.singleShot(2000, lambda: self.status_lbl.setText("Нажмите для записи"))

    def get_text(self):
        return self.text_input.toPlainText()


# ==========================================
# 2.5 КАСТОМНЫЕ ДИАЛОГИ (ДЛЯ ТЕМЫ И ОППОНЕНТА)
# ==========================================

class CustomConfirmDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, topic=""):
        super().__init__(parent)
        self.setWindowTitle("Подтверждение темы")
        self.setMinimumWidth(600)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e2e; border: 2px solid #5d4037; border-radius: 10px; }
            QLabel { color: #E0E0E0; font-size: 18px; font-weight: bold; }
            QPushButton { background-color: #2c3e50; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; padding: 10px; border: 2px solid #34495e; outline: none; }
            QPushButton:focus { outline: none; }
            QPushButton:hover { background-color: #34495e; border: 2px solid #E0E0E0;}
            QPushButton#YesBtn { background-color: #2e7d32; border: 2px solid #1b5e20; }
            QPushButton#YesBtn:hover { background-color: #388e3c; }
            QPushButton#NoBtn { background-color: #c62828; border: 2px solid #b71c1c; }
            QPushButton#NoBtn:hover { background-color: #d32f2f; }
        """)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        lbl_title = QtWidgets.QLabel("Сформулированная тема:")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_title)
        
        lbl_topic = QtWidgets.QLabel(f"\"{topic}\"")
        lbl_topic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_topic.setWordWrap(True)
        lbl_topic.setStyleSheet("color: #FFD700; font-size: 20px; font-style: italic;")
        layout.addWidget(lbl_topic)
        
        lbl_ask = QtWidgets.QLabel("Утверждаем?")
        lbl_ask.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_ask)
        
        btn_layout = QtWidgets.QHBoxLayout()
        
        yes_btn = QtWidgets.QPushButton("Да, к барьеру!")
        yes_btn.setObjectName("YesBtn")
        yes_btn.setFixedSize(250, 50)
        yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yes_btn.clicked.connect(self.accept)
        
        no_btn = QtWidgets.QPushButton("Нет, хочу изменить")
        no_btn.setObjectName("NoBtn")
        no_btn.setFixedSize(250, 50)
        no_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        no_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(no_btn)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(yes_btn)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addLayout(btn_layout)

class OpponentSelectionDialog(QtWidgets.QDialog):
    """Кастомное окно выбора оппонента (Frameless)"""
    def __init__(self, parent=None, available_opps=[]):
        super().__init__(parent)
        self.setModal(True)
        self.setFixedSize(450, 300)
        
        # Убираем системную рамку
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.container = QtWidgets.QFrame(self)
        self.container.setObjectName("Container")
        main_layout.addWidget(self.container)
        
        self.container.setStyleSheet("""
            QFrame#Container {
                background-color: #1e1e2e; 
                border: 3px solid #d4af37; 
                border-radius: 15px;
            }
            QLabel { color: #ffca28; font-size: 24px; font-weight: bold; background: transparent; }
            
            QComboBox {
                background-color: #282837;
                color: white;
                border: 2px solid #5d4037;
                border-radius: 8px;
                padding: 10px;
                font-size: 18px;
                min-width: 300px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #282837;
                color: white;
                selection-background-color: #d4af37;
                selection-color: #3e2723;
                border: 1px solid #d4af37;
            }

            QPushButton {
                font-size: 18px; font-weight: bold; border-radius: 8px; padding: 12px; min-width: 140px;
            }
            QPushButton#OkBtn { background-color: #2e7d32; color: white; border: 2px solid #1b5e20; }
            QPushButton#OkBtn:hover { background-color: #388e3c; }
            
            QPushButton#CancelBtn { background-color: #c62828; color: white; border: 2px solid #b71c1c; }
            QPushButton#CancelBtn:hover { background-color: #d32f2f; }
        """)

        layout = QtWidgets.QVBoxLayout(self.container)
        layout.setContentsMargins(30, 35, 30, 30)
        layout.setSpacing(25)

        title_lbl = QtWidgets.QLabel("ВЫБОР ОППОНЕНТА")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(available_opps)
        self.combo.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.combo, alignment=Qt.AlignmentFlag.AlignCenter)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(15)
        
        self.cancel_btn = QtWidgets.QPushButton("ОТМЕНА")
        self.cancel_btn.setObjectName("CancelBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.reject)
        
        self.ok_btn = QtWidgets.QPushButton("К БОЮ!")
        self.ok_btn.setObjectName("OkBtn")
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        
        layout.addLayout(btn_layout)

    def get_selected(self):
        return self.combo.currentText()

# ==========================================
# 2.6 КАСТОМНЫЙ ДИАЛОГ УСПЕХА
# ==========================================
class CustomSuccessDialog(QtWidgets.QDialog):
    def __init__(self, message_text, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Основной контейнер
        self.main_frame = QtWidgets.QFrame(self)
        self.main_frame.setObjectName("MainFrame")
        self.main_frame.setFixedSize(450, 250)
        self.main_frame.setStyleSheet("""
            #MainFrame {
                background-color: #1A1A24;
                border: 1px solid #D4AF37;
                border-radius: 15px;
            }
            QLabel#TitleLabel {
                color: #2ECC71;
                font-size: 24px;
                font-weight: bold;
                background: transparent;
                border: none;
            }
            QLabel#MessageLabel {
                color: #E0E0E0;
                font-size: 18px;
                background: transparent;
                border: none;
            }
            QPushButton#OkButton {
                background-color: #D4AF37;
                color: #1A1A24;
                font-size: 18px;
                font-weight: bold;
                border-radius: 8px;
                padding: 10px 30px;
                border: none;
            }
            QPushButton#OkButton:hover {
                background-color: #F1C40F;
            }
        """)

        # Тень
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QtGui.QColor(0, 0, 0, 200))
        shadow.setOffset(0, 5)
        self.main_frame.setGraphicsEffect(shadow)

        layout = QtWidgets.QVBoxLayout(self.main_frame)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # Заголовок
        title_lbl = QtWidgets.QLabel("УСПЕХ", self.main_frame)
        title_lbl.setObjectName("TitleLabel")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Сообщение
        msg_lbl = QtWidgets.QLabel(message_text, self.main_frame)
        msg_lbl.setObjectName("MessageLabel")
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_lbl.setWordWrap(True)

        # Кнопка
        self.ok_btn = QtWidgets.QPushButton("ОТЛИЧНО", self.main_frame)
        self.ok_btn.setObjectName("OkButton")
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self.accept)

        layout.addWidget(title_lbl)
        layout.addWidget(msg_lbl, 1)
        layout.addWidget(self.ok_btn, 0, Qt.AlignmentFlag.AlignCenter)

        # Центрирование содержимого
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.addWidget(self.main_frame)
        
    def showEvent(self, event):
        # Центрируем относительно родителя вручную, если это возможно
        if self.parentWidget():
            parent_geo = self.parentWidget().geometry()
            self.move(parent_geo.center() - self.rect().center())
        super().showEvent(event)


# ==========================================
# 3. ЭКРАН АВТОРИЗАЦИИ
# ==========================================
class AuthScreen(QtWidgets.QWidget):
    login_requested = Signal(str, str)
    register_requested = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("AuthScreen")
        self.setStyleSheet("""
            #AuthScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }
            QLabel { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
            QLabel.title { font-size: 32px; font-weight: bold; color: #ffca28; margin-bottom: 10px; min-height: 45px; }
            QLabel.error { color: #ef5350; font-weight: bold; }
            QLineEdit { padding: 5px 15px; font-size: 18px; border: 2px solid #555; border-radius: 8px; background-color: rgba(30,30,40,200); color: white; }
            QPushButton { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00); color: #3e2723; font-size: 16px; font-weight: bold; border-radius: 8px; border: none; padding: 10px; }
            QPushButton:hover { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37); }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Контейнер по центру
        self.auth_container = QtWidgets.QFrame()
        self.auth_container.setFixedSize(400, 310)
        self.auth_container.setStyleSheet("QFrame { background-color: rgba(20, 20, 30, 240); border-radius: 15px; border: 2px solid #a67c00; }")
        
        vbox = QtWidgets.QVBoxLayout(self.auth_container)
        vbox.setContentsMargins(30, 20, 30, 30) # Сверху уменьшил, но добавим спейсер ниже
        vbox.setSpacing(10)
        
        # Спейсер сверху для гибкой настройки отступа надписи
        self.top_spacer = vbox.addSpacing(10) 
        
        
        self.title_lbl = QtWidgets.QLabel("ВХОД", self.auth_container)
        self.title_lbl.setProperty("class", "title")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("background: transparent; border: none;")
        
        self.error_lbl = QtWidgets.QLabel("", self.auth_container)
        self.error_lbl.setProperty("class", "error")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_lbl.setStyleSheet("background: transparent; border: none;")
        
        self.email_input = QtWidgets.QLineEdit(self.auth_container)
        self.email_input.setPlaceholderText("E-mail")
        
        self.nick_input = QtWidgets.QLineEdit(self.auth_container)
        self.nick_input.setPlaceholderText("Никнейм (только для регистрации)")
        self.nick_input.hide()
        
        self.pass_input = QtWidgets.QLineEdit(self.auth_container)
        self.pass_input.setPlaceholderText("Пароль")
        self.pass_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        
        self.action_btn = QtWidgets.QPushButton("ВОЙТИ", self.auth_container)
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.action_btn.clicked.connect(self.handle_action)
        
        self.toggle_btn = QtWidgets.QPushButton("Нет аккаунта? Зарегистрируйтесь", self.auth_container)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.toggle_btn.setStyleSheet("QPushButton { background: transparent; border: none; color: #90caf9; font-size: 14px; text-decoration: underline; } QPushButton:hover { color: #ffffff; }")
        self.toggle_btn.clicked.connect(self.toggle_mode)
        
        vbox.addWidget(self.title_lbl)
        vbox.addWidget(self.error_lbl)
        vbox.addWidget(self.email_input)
        vbox.addWidget(self.nick_input)
        vbox.addWidget(self.pass_input)
        vbox.addSpacing(5)
        vbox.addWidget(self.action_btn)
        vbox.addWidget(self.toggle_btn)
        
        main_layout.addWidget(self.auth_container)
        
        self.mode = "login" # 'login' or 'register'

    def toggle_mode(self):
        vbox = self.auth_container.layout()
        if self.mode == "login":
            self.mode = "register"
            self.auth_container.setFixedSize(400, 380)
            self.title_lbl.setText("РЕГИСТРАЦИЯ")
            vbox.setContentsMargins(30, 40, 30, 30) # ТУТ: отступ сверху (40) для Регистрации
            self.action_btn.setText("ЗАРЕГИСТРИРОВАТЬСЯ")
            self.toggle_btn.setText("Уже есть аккаунт? Войти")
            self.nick_input.show()
        else:
            self.mode = "login"
            self.auth_container.setFixedSize(400, 310)
            self.title_lbl.setText("ВХОД")
            vbox.setContentsMargins(30, 20, 30, 30) # ТУТ: отступ сверху (20) для Входа
            self.action_btn.setText("ВОЙТИ")
            self.toggle_btn.setText("Нет аккаунта? Зарегистрируйтесь")
            self.nick_input.hide()
        self.error_lbl.setText("")

    def handle_action(self):
        email = self.email_input.text().strip()
        pwd = self.pass_input.text().strip()
        nickname = self.nick_input.text().strip()
        
        if not email or not pwd:
            self.show_error("Заполните email и пароль")
            return
            
        if self.mode == "register":
            if not nickname:
                self.show_error("Заполните никнейм")
                return
            self.register_requested.emit(email, nickname, pwd)
        else:
            self.login_requested.emit(email, pwd)
            
    def show_error(self, msg):
        self.error_lbl.setText(msg)

# ==========================================
# 4. ЭКРАН ПРОФИЛЯ
# ==========================================
class ProfileScreen(QtWidgets.QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ProfileScreen")
        self.setStyleSheet("""
            #ProfileScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }
            QLabel { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
            QLabel.title { font-size: 36px; font-weight: bold; color: #ffca28; margin-bottom: 20px; }
            QLabel.stat_title { font-size: 15px; color: #b0bec5; font-weight: bold; }
            QLabel.stat_value { font-size: 24px; color: #ffffff; font-weight: bold; }
            QFrame.card { background-color: rgba(20, 20, 30, 240); border-radius: 12px; border: 1px solid #a67c00; }
            QPushButton { background-color: rgba(20,20,30,240); border: 1px solid #a67c00; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; padding: 10px; }
            QPushButton:hover { background-color: #382a00; }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Центральный контейнер (модальное окно)
        self.modal_container = QtWidgets.QFrame()
        self.modal_container.setFixedSize(850, 560)
        self.modal_container.setObjectName("ModalContainer")
        self.modal_container.setStyleSheet("""
            #ModalContainer { 
                background-color: rgba(20, 20, 30, 240); 
                border-radius: 15px; 
                border: 2px solid #a67c00; 
            }
        """)
        
        modal_layout = QtWidgets.QVBoxLayout(self.modal_container)
        modal_layout.setContentsMargins(0, 0, 0, 30)
        modal_layout.setSpacing(0)
        
        # HEADER ПРОФИЛЯ
        header_frame = QtWidgets.QFrame()
        header_frame.setFixedSize(846, 70) # Чуть уже контейнера из-за border
        header_frame.setStyleSheet("""
            QFrame { 
                background-color: #1e1e2e; 
                border-top-left-radius: 13px; 
                border-top-right-radius: 13px;
                border-bottom: 1px solid #a67c00;
            }
        """)
        header_layout = QtWidgets.QHBoxLayout(header_frame)
        
        self.title_lbl = QtWidgets.QLabel("ПРОФИЛЬ ИГРОКА")
        self.title_lbl.setProperty("class", "title")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("margin-bottom: 0px; font-size: 28px;") # Тюнинг для хедера
        header_layout.addWidget(self.title_lbl)
        
        modal_layout.addWidget(header_frame)
        modal_layout.addSpacing(20)
        
        # Контентная область ниже хедера
        content_layout = QtWidgets.QVBoxLayout()
        content_layout.setContentsMargins(30, 0, 30, 0)
        content_layout.setSpacing(20)
        modal_layout.addLayout(content_layout)

        # Сетка карточек статистики
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(20)
        
        # Функция-помощник для создания карточек
        def make_card(title_text):
            card = QtWidgets.QFrame()
            card.setProperty("class", "card")
            card.setFixedSize(260, 100)
            l = QtWidgets.QVBoxLayout(card)
            
            t = QtWidgets.QLabel(title_text)
            t.setProperty("class", "stat_title")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            v = QtWidgets.QLabel("-")
            v.setProperty("class", "stat_value")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            l.addWidget(t)
            l.addWidget(v)
            return card, v

        self.cards = {}
        titles = [
            ("coins", "💰 Баланс (аргуконы)", 0, 0),
            ("games", "⚔️ Всего матчей", 0, 1),
            ("winrate", "🏆 Винрейт", 0, 2),
            ("wins_losses", "✅ Побед / Поражений", 1, 0),
            ("winstreak", "🔥 Максимальная серия", 1, 1),
            ("favorite", "❤️ Любимый оппонент", 1, 2),
            ("playtime", "⏳ Общее время", 2, 0),
            ("words", "🗣️ Слов сказано", 2, 1),
            ("chars", "📝 Символов написано", 2, 2)
        ]

        for key, text, row, col in titles:
            card, l_val = make_card(text)
            self.cards[key] = l_val
            grid.addWidget(card, row, col)

        content_layout.addLayout(grid)

        # Кнопка назад
        self.back_btn = QtWidgets.QPushButton("НАЗАД В МЕНЮ")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.setFixedSize(250, 50)
        self.back_btn.clicked.connect(self.back_requested.emit)
        
        bottom_layout = QtWidgets.QHBoxLayout()
        bottom_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(self.back_btn)
        
        content_layout.addSpacing(20)
        content_layout.addLayout(bottom_layout)
        
        main_layout.addWidget(self.modal_container)

    def update_stats(self, profile):
        if not profile: return
        
        g = profile.get('total_games', 0)
        w = profile.get('wins', 0)
        l = profile.get('losses', 0)
        wr = f"{(w/g*100):.1f}%" if g > 0 else "0%"
        
        playtime = profile.get('total_playtime_seconds', 0)
        mins = playtime // 60
        secs = playtime % 60
        time_str = f"{mins}м {secs}с" if mins > 0 else f"{secs}с"

        self.title_lbl.setText(f"ПРОФИЛЬ: {profile.get('nickname', 'Игрок')}".upper())

        self.cards["coins"].setText(str(profile.get('coins', 0)))
        self.cards["games"].setText(str(g))
        self.cards["winrate"].setText(wr)
        self.cards["wins_losses"].setText(f"{w} / {l}")
        self.cards["winstreak"].setText(str(profile.get('max_win_streak', 0)))
        
        fav = profile.get('favorite_opponent')
        self.cards["favorite"].setText(fav if fav else "Нет данных")
        
        self.cards["playtime"].setText(time_str)
        self.cards["words"].setText(str(profile.get('total_words_spoken', 0)))
        self.cards["chars"].setText(str(profile.get('total_chars_spoken', 0)))

# ==========================================
# 5. ТУТОРИАЛ (ИНТЕРАКТИВНОЕ ОБУЧЕНИЕ)
# ==========================================
class TutorialOverlayWidget(QtWidgets.QWidget):
    tutorial_finished = Signal()

    def __init__(self, parent=None, steps=None):
        super().__init__(parent)
        self.steps = steps or []
        self.current_step = 0
        
        # Виджет на весь экран поверх всего
        if parent:
            self.resize(parent.size())
            
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        # Информационное табло
        self.info_box = QtWidgets.QFrame(self)
        self.info_box.setStyleSheet("""
            QFrame { background-color: #1e1e2e; border: 2px solid #d4af37; border-radius: 10px; }
            QLabel { color: white; font-size: 16px; font-family: 'Segoe UI'; font-weight: normal; border: none; }
            QPushButton { background-color: #d4af37; color: #1e1e2e; font-weight: bold; border-radius: 5px; padding: 5px 15px; border: none; outline: none; }
            QPushButton:hover { background-color: #edd27a; }
            QPushButton:focus { outline: none; border: none; }
        """)
        self.info_box.hide()
        
        vbox = QtWidgets.QVBoxLayout(self.info_box)
        self.text_lbl = QtWidgets.QLabel("")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        vbox.addWidget(self.text_lbl)
        
        self.next_btn = QtWidgets.QPushButton("Далее")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self.next_step)
        
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.next_btn)
        vbox.addLayout(btn_layout)

    def start(self):
        if not self.steps:
            self.finish()
            return
        self.current_step = 0
        self.show()
        self.raise_()
        self.update_step()

    def update_step(self):
        if self.current_step >= len(self.steps):
            self.finish()
            return
            
        step = self.steps[self.current_step]
        
        # Выполняем экшен (например, показать скрытый попап) если он есть
        action = step.get("action")
        if action and callable(action):
            action()
            
        self.text_lbl.setText(step["text"])
        
        if self.current_step == len(self.steps) - 1:
            self.next_btn.setText("К барьеру!")
        else:
            self.next_btn.setText("Далее")
            
        self.info_box.adjustSize()
        self.position_info_box(step)
        self.info_box.raise_() # Поднимаем инфо-бокс поверх всего, включая VoiceInputDialog
        self.update() # Вызов paintEvent

    def next_step(self):
        self.current_step += 1
        self.update_step()

    def finish(self):
        self.hide()
        self.tutorial_finished.emit()

    def mousePressEvent(self, event):
        # Позволяем переключать шаги кликом в любое место
        self.next_step()

    def position_info_box(self, step):
        target = step.get("widget")
        direction = step.get("direction", "bottom")
        
        if not target or not target.isVisible():
            # Если нет виджета, центрируем окошко
            self.info_box.move((self.width() - self.info_box.width()) // 2, (self.height() - self.info_box.height()) // 2)
            self.info_box.show()
            return

        # Находим координаты виджета относительно главного окна
        target_pos = target.mapToGlobal(QtCore.QPoint(0, 0))
        overlay_pos = self.mapFromGlobal(target_pos)
        
        tw, th = target.width(), target.height()
        iw, ih = self.info_box.width(), self.info_box.height()
        
        # Расчет позиций
        x, y = overlay_pos.x(), overlay_pos.y()
        offset = 20
        
        if direction == "bottom":
            ix = x + (tw - iw) // 2
            iy = y + th + offset
        elif direction == "top":
            ix = x + (tw - iw) // 2
            iy = y - ih - offset
        elif direction == "left":
            ix = x - iw - offset
            iy = y + (th - ih) // 2
        elif direction == "right":
            ix = x + tw + offset
            iy = y + (th - ih) // 2
        else:
            ix, iy = x, y
            
        # Защита от выхода за экран
        ix = max(10, min(ix, self.width() - iw - 10))
        iy = max(10, min(iy, self.height() - ih - 10))
        
        self.info_box.move(ix, iy)
        self.info_box.show()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        # Определяем основной путь на весь экран
        path = QtGui.QPainterPath()
        path.addRect(QtCore.QRectF(self.rect()))
        
        hole_rect = None
        
        if self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            target = step.get("widget")
            
            # Если есть целевой виджет, вырезаем из пути дырку
            if target and target.isVisible():
                target_pos = target.mapToGlobal(QtCore.QPoint(0, 0))
                overlay_pos = self.mapFromGlobal(target_pos)
                
                pad = 5
                width = target.width()
                height = target.height()
                x = overlay_pos.x()
                y = overlay_pos.y()
                
                if target.objectName() == "opp":
                     pad = -10
                elif target.objectName() == "topic":
                     y += 10
                     height -= 20
                     pad = 0
                
                hole_rect = QtCore.QRectF(x - pad, y - pad, width + 2*pad, height + 2*pad)
                
                # Создаем путь для дырки и вычитаем его из основного пути
                hole_path = QtGui.QPainterPath()
                hole_path.addRoundedRect(hole_rect, 10, 10)
                path = path.subtracted(hole_path)

        # 1. Заливаем полученный "дырявый" путь полупрозрачным черным
        painter.fillPath(path, QtGui.QColor(0, 0, 0, 180))
        
        # 2. Рисуем золотую рамку вокруг дырки, если она есть
        if hole_rect:
            pen = QtGui.QPen(QtGui.QColor("#d4af37"))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(hole_rect, 10, 10)

# ==========================================
# 6. ЭКРАН МАГАЗИНА
# ==========================================
class ShopScreen(QtWidgets.QWidget):
    back_requested = Signal()
    buy_requested = Signal(str, int) # opponent_name, price
    details_requested = Signal(str) # opponent_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ShopScreen")
        self.setStyleSheet("""
            #ShopScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }
            QLabel { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
            QLabel.title { font-size: 36px; font-weight: bold; color: #ffca28; margin-bottom: 5px; }
            QLabel.balance { font-size: 20px; color: #a5d6a7; font-weight: bold; margin-bottom: 20px; }
            QFrame.item { background-color: rgba(20, 20, 30, 240); border-radius: 12px; border: 1px solid #a67c00; }
            QLabel.item_name { font-size: 22px; color: white; font-weight: bold; }
            QLabel.item_price { font-size: 18px; color: #ffca28; font-weight: bold; }
            QPushButton.buy { background-color: #2e7d32; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; padding: 10px; border: none; }
            QPushButton.buy:hover { background-color: #388e3c; }
            QPushButton.buy:disabled { background-color: #555555; color: #aaaaaa; }
            QPushButton.back { background-color: rgba(20,20,30,240); border: 1px solid #a67c00; color: white; font-size: 16px; font-weight: bold; border-radius: 8px; padding: 10px; }
            QPushButton.back:hover { background-color: #382a00; }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # State
        self.current_category = "Все"
        
        # TOP BAR
        top_bar = QtWidgets.QFrame()
        top_bar.setFixedHeight(80)
        top_bar.setStyleSheet("QFrame { background-color: rgba(0, 0, 0, 180); border-bottom: 2px solid #a67c00; }")
        top_bar_layout = QtWidgets.QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(30, 0, 30, 0)
        
        # Кнопка назад в топбаре
        self.back_btn = QtWidgets.QPushButton("⬅ НАЗАД")
        self.back_btn.setProperty("class", "back")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.setFixedSize(140, 40)
        self.back_btn.clicked.connect(self.back_requested.emit)
        
        # Заголовок и баланс по центру
        center_info = QtWidgets.QWidget()
        center_vbox = QtWidgets.QVBoxLayout(center_info)
        center_vbox.setSpacing(2)
        center_vbox.setContentsMargins(0, 10, 0, 10)
        
        self.title_lbl = QtWidgets.QLabel("МАГАЗИН ОППОНЕНТОВ")
        self.title_lbl.setProperty("class", "title")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet("font-size: 24px; margin-bottom: 0px;")
        
        self.balance_lbl = QtWidgets.QLabel("Ваш баланс: 0 аргуконов")
        self.balance_lbl.setProperty("class", "balance")
        self.balance_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.balance_lbl.setStyleSheet("font-size: 16px; margin-bottom: 0px; color: #a5d6a7;")
        
        center_vbox.addWidget(self.title_lbl)
        center_vbox.addWidget(self.balance_lbl)
        
        # Пустой виджет справа для симметрии
        spacer_right = QtWidgets.QWidget()
        spacer_right.setFixedSize(140, 40)
        
        top_bar_layout.addWidget(self.back_btn)
        top_bar_layout.addWidget(center_info, 1)
        top_bar_layout.addWidget(spacer_right)
        
        main_layout.addWidget(top_bar)
        
        # Category Filters 
        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.setContentsMargins(30, 20, 30, 0)
        filter_layout.setSpacing(10)
        filter_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        categories = ["Все", "Философы", "Ученые", "Писатели", "Политики", "Религиоведы", "Разное"]
        self.filter_buttons = []
        
        for cat in categories:
            btn = QtWidgets.QPushButton(cat)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCheckable(True)
            if cat == "Все":
                btn.setChecked(True)
                
            btn.setStyleSheet("""
                QPushButton { 
                    background-color: rgba(30, 30, 46, 200); 
                    border: 1px solid #a67c00; 
                    border-radius: 22px; 
                    padding: 12px 25px; 
                    color: #dcdcdc; 
                    font-size: 18px;
                    font-weight: bold; 
                }
                QPushButton:hover { background-color: rgba(212, 175, 55, 0.2); }
                QPushButton:checked { background-color: #d4af37; color: #1e1e2e; border: 1px solid #ffffff; }
            """)
            btn.clicked.connect(lambda checked, c=cat: self.on_category_selected(c))
            filter_layout.addWidget(btn)
            self.filter_buttons.append(btn)
            
        main_layout.addLayout(filter_layout)

        # Контентная область
        content_vbox = QtWidgets.QVBoxLayout()
        content_vbox.setContentsMargins(30, 20, 30, 30)
        

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; } QWidget#scroll_content { background: transparent; }")
        
        scroll_content = QtWidgets.QWidget()
        scroll_content.setObjectName("scroll_content")
        self.grid = QtWidgets.QGridLayout(scroll_content)
        self.grid.setSpacing(15)
        
        scroll_area.setWidget(scroll_content)
        content_vbox.addWidget(scroll_area)
        
        main_layout.addLayout(content_vbox)


        
        # Каталог магазина берем из philosophers_data
        self.catalog = PHILOSOPHERS_DATA
        self.coins = 0
        self.unlocked_list = []

    def on_category_selected(self, category):
        self.current_category = category
        for btn in self.filter_buttons:
            if btn.text() == category:
                btn.setChecked(True)
            else:
                btn.setChecked(False)
        self.update_shop(self.coins, self.unlocked_list)

    def update_shop(self, coins, unlocked_list):
        self.coins = coins
        self.unlocked_list = unlocked_list
        self.balance_lbl.setText(f"Ваш баланс: {coins} аргуконов")
        
        # Очистка сетки
        while self.grid.count():
            child = self.grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        row, col = 0, 0
        for name, data in self.catalog.items():
            item_cat = data.get("category", "Разное")
            if self.current_category != "Все" and item_cat != self.current_category:
                continue
            card = QtWidgets.QFrame()
            card.setProperty("class", "item")
            card.setFixedSize(350, 460)
            
            l = QtWidgets.QVBoxLayout(card)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setSpacing(10)
            
            # Аватар
            img_lbl = QtWidgets.QLabel()
            pixmap = QtGui.QPixmap(data["img"])
            if not pixmap.isNull():
                img_lbl.setPixmap(pixmap.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            n_lbl = QtWidgets.QLabel(name)
            n_lbl.setProperty("class", "item_name")
            n_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            price_text = "Базовый персонаж" if data['price'] == 0 else f"💰 {data['price']} аргуконов"
            p_lbl = QtWidgets.QLabel(price_text)
            p_lbl.setProperty("class", "item_price")
            p_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            buy_btn = QtWidgets.QPushButton()
            buy_btn.setProperty("class", "buy")
            buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            buy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            
            is_unlocked = data.get('is_default', False) or name in unlocked_list
            if is_unlocked:
                buy_btn.setText("КУПЛЕНО")
                buy_btn.setEnabled(False)
                if data['price'] > 0:
                    p_lbl.setText("✅ Куплено")
            elif coins < data['price']:
                buy_btn.setText("НЕ ХВАТАЕТ")
                buy_btn.setEnabled(False)
            else:
                buy_btn.setText("КУПИТЬ")
                buy_btn.clicked.connect(lambda checked=False, n=name, p=data['price']: self.buy_requested.emit(n, p))
            
            bio_btn = QtWidgets.QPushButton("БИОГРАФИЯ")
            bio_btn.setProperty("class", "back") # Имитируем стиль кнопки "Назад" (прозрачный + бордер)
            bio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            bio_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            bio_btn.clicked.connect(lambda checked=False, n=name: self.details_requested.emit(n))

            btn_layout = QtWidgets.QHBoxLayout()
            btn_layout.addWidget(buy_btn)
            btn_layout.addWidget(bio_btn)
            
            l.addWidget(img_lbl)
            l.addWidget(n_lbl)
            l.addWidget(p_lbl)
            l.addLayout(btn_layout)
            
            self.grid.addWidget(card, row, col)
            col += 1
            # Adjust column count based on available space if desired, sticking to 2-3 looks good
            if col > 1:
                col = 0
                row += 1

# ==========================================
# 5.5. ЭКРАН ДЕТАЛЕЙ ФИЛОСОФА (БИОГРАФИЯ)
# ==========================================
class PhilosopherDetailsScreen(QtWidgets.QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("PhilosopherDetailsScreen")
        self.setStyleSheet("""
            #PhilosopherDetailsScreen { background-color: #1e1e2e; }
            QLabel { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
            QLabel.name { font-size: 42px; font-weight: bold; color: #ffca28; margin-bottom: 0px; }
            QLabel.vital_stats { font-size: 18px; color: #a0a0a0; margin-bottom: 20px; font-style: italic; }
            QLabel.header2 { font-size: 24px; font-weight: bold; color: #d4af37; margin-top: 20px; margin-bottom: 10px; }
            QLabel.body_text { font-size: 18px; line-height: 1.5; color: #dcdcdc; }
            QFrame.quote_block { background-color: rgba(212, 175, 55, 0.1); border-left: 4px solid #d4af37; padding: 15px; margin-top: 20px; }
            QLabel.quote_text { font-size: 20px; font-style: italic; color: #ffca28; }
            QPushButton.back { background-color: rgba(30,30,46,240); border: 2px solid #a67c00; color: white; font-size: 18px; font-weight: bold; border-radius: 8px; padding: 10px 20px; }
            QPushButton.back:hover { background-color: #382a00; }
        """)

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(40)

        # Левая колонка - Картинка и кнопка Назад
        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        
        self.img_lbl = QtWidgets.QLabel()
        self.img_lbl.setFixedSize(350, 450)
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setStyleSheet("border: 2px solid #a67c00; border-radius: 15px; background-color: #151520;")
        
        self.back_btn = QtWidgets.QPushButton("⬅ НАЗАД В МАГАЗИН")
        self.back_btn.setProperty("class", "back")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.back_btn.clicked.connect(self.back_requested.emit)

        left_layout.addWidget(self.img_lbl)
        left_layout.addSpacing(30)
        left_layout.addWidget(self.back_btn)
        left_layout.addStretch()

        # Правая колонка - Текст
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; } QWidget#scroll_content { background: transparent; }")
        
        scroll_content = QtWidgets.QWidget()
        scroll_content.setObjectName("scroll_content")
        self.right_layout = QtWidgets.QVBoxLayout(scroll_content)
        self.right_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.right_layout.setSpacing(10)

        self.name_lbl = QtWidgets.QLabel("")
        self.name_lbl.setProperty("class", "name")
        self.name_lbl.setWordWrap(True)
        
        self.vital_lbl = QtWidgets.QLabel("")
        self.vital_lbl.setProperty("class", "vital_stats")
        self.vital_lbl.setWordWrap(True)
        
        bio_header = QtWidgets.QLabel("Краткая биография")
        bio_header.setProperty("class", "header2")
        
        self.bio_lbl = QtWidgets.QLabel("")
        self.bio_lbl.setProperty("class", "body_text")
        self.bio_lbl.setWordWrap(True)
        
        theses_header = QtWidgets.QLabel("Основные тезисы")
        theses_header.setProperty("class", "header2")
        
        self.theses_lbl = QtWidgets.QLabel("")
        self.theses_lbl.setProperty("class", "body_text")
        self.theses_lbl.setWordWrap(True)

        self.quote_frame = QtWidgets.QFrame()
        self.quote_frame.setProperty("class", "quote_block")
        quote_layout = QtWidgets.QVBoxLayout(self.quote_frame)
        self.quote_lbl = QtWidgets.QLabel("")
        self.quote_lbl.setProperty("class", "quote_text")
        self.quote_lbl.setWordWrap(True)
        quote_layout.addWidget(self.quote_lbl)
        
        self.right_layout.addWidget(self.name_lbl)
        self.right_layout.addWidget(self.vital_lbl)
        self.right_layout.addWidget(self.quote_frame)
        self.right_layout.addWidget(bio_header)
        self.right_layout.addWidget(self.bio_lbl)
        self.right_layout.addWidget(theses_header)
        self.right_layout.addWidget(self.theses_lbl)
        self.right_layout.addStretch()

        scroll_area.setWidget(scroll_content)

        main_layout.addLayout(left_layout, 1) # Картинка занимает 1 долю ширины
        main_layout.addWidget(scroll_area, 2) # Текст занимает 2 доли ширины
        
    def load_philosopher(self, name):
        data = PHILOSOPHERS_DATA.get(name)
        if not data: return
        
        # Обновляем картинку
        pixmap = QtGui.QPixmap(data["img"])
        if not pixmap.isNull():
            # Заполняем область метки, сохраняя пропорции, можно также обрезать
            scaled = pixmap.scaled(350, 450, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self.img_lbl.setPixmap(scaled)
            
        self.name_lbl.setText(data["name"])
        self.vital_lbl.setText(data["vital_stats"])
        self.bio_lbl.setText(data["short_biography"])
        
        theses_text = "<ul>"
        for t in data["key_theses"]:
            theses_text += f"<li>{t}</li><br>"
        theses_text += "</ul>"
        self.theses_lbl.setText(theses_text)
        
        self.quote_lbl.setText(f"«{data['quote']}»")

# ==========================================
# 6. ГЛАВНЫЙ КОНТРОЛЛЕР
# ==========================================
class AppController(QtWidgets.QMainWindow):
    request_generation = Signal(str, object, dict)
    request_speak = Signal(list)
    request_stream_start = Signal()
    request_stream_chunk = Signal(str)
    request_stream_finish = Signal()
    request_set_voice = Signal(str, str, str)

    AVATAR_MAP = {
        "Иммануил Кант": "assets/kant.png",
        "Сократ": "assets/socrates.png",
        "Фридрих Ницше": "assets/nietzsche.png",
        "Чарльз Дарвин": "assets/darwin.png",
        "Стив Джобс": "assets/jobs.png",
        "Макиавелли": "assets/machiavelli.png",
        "Никола Тесла": "assets/tesla.png",
        "Федор Достоевский": "assets/dostoevsky.png",
        "Карл Маркс": "assets/karl marks.png",
        "Диоген": "assets/diogen.png",
        "Фома Аквинский": "assets/foma.png",
        "Лев Толстой": "assets/lev.png",
        "Альберт Эйнштейн": "assets/einstein.png",
        "Исаак Ньютон": "assets/newton.png",
        "Джордж Вашингтон": "assets/washington.png",
        "Аль-Газали": "assets/al ghazali.png",
        "Маймонид": "assets/maimonides.png",
        "Владимир Ленин": "assets/lenin.png",
        "Иосиф Сталин": "assets/stalin.png",
        "Гай Юлий Цезарь": "assets/caesar.png",
        "Уильям Оккам": "assets/ockham.png",
        "Зигмунд Фрейд": "assets/freud.png",
        "Мюррей Ротбард": "assets/rothbard.png"
    }


    def __init__(self):
        super().__init__()
        self.base_size = QtCore.QSize(1024, 768)
        self.initial_geometries = {}
        self.initial_font_sizes = {}
        self.is_fullscreen = False

        self.engine = None
        self.opp_model = None
        self.opp_system_prompt = None
        self.deepseek = DeepSeekManager()
        self.action_queue = []
        self.state = "IDLE"
        self.critique_filepath = None
        self.critique_full_text = None
        
        self.db = DatabaseManager()
        self.current_user = None

        self.clash_timer = QtCore.QTimer(self)
        self.clash_duration_sec = 60
        self.clash_time_left = self.clash_duration_sec
        self.clash_leader = None
        self.clash_responder = None
        self.current_clash_turn_holder = None
        self.last_clash_speech = ""
        self.clash_ending_pending = False

        self.jury_phase_step = None
        self.pending_jury_questions = None
        self.verdict_announcement_step = None
        self.verdict_data = None
        self.summary_phase_started = False

        self.setup_threads_and_workers()
        self.clash_timer.setInterval(1000)
        self.clash_timer.timeout.connect(self.on_clash_timer_tick)

        self.stacked_widget = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        self.load_screens_and_connect_signals()

        self.setWindowTitle("Интеллектуальный Гладиатор")
        self.resize(self.base_size)
        self.center_window()
        # Запрещаем ручное изменение размера и кнопку разворота
        self.setFixedSize(self.base_size)

    def setup_threads_and_workers(self):
        self.agent_thread = QtCore.QThread(self)
        self.agent_worker = AgentWorker()
        self.agent_worker.moveToThread(self.agent_thread)
        self.request_generation.connect(self.agent_worker.generate, Qt.ConnectionType.QueuedConnection)
        self.agent_worker.generation_chunk.connect(self.on_generation_chunk, Qt.ConnectionType.QueuedConnection)
        self.agent_worker.generation_complete.connect(self.on_generation_complete, Qt.ConnectionType.QueuedConnection)
        self.agent_thread.start()

        self.speaker_thread = QtCore.QThread(self)
        self.speaker_worker = SpeakerWorker()
        self.speaker_worker.moveToThread(self.speaker_thread)
        self.speaker_thread.started.connect(self.speaker_worker.initialize_engine)
        self.request_speak.connect(self.speaker_worker.speak_sequence)
        self.speaker_worker.speech_started.connect(self.update_subtitles)
        self.speaker_worker.sequence_finished.connect(self.on_sequence_finished)
        self.request_stream_start.connect(self.speaker_worker.start_stream)
        self.request_stream_chunk.connect(self.speaker_worker.append_stream_text)
        self.request_stream_finish.connect(self.speaker_worker.finish_stream)
        self.request_set_voice.connect(self.speaker_worker.set_voice)
        self.speaker_thread.start()

    def _set_voice_for_speaker(self, speaker_name):
        # Default fallback is male DmitryNeural, Moderator can use Svetlana.
        voice_id = "ru-RU-SvetlanaNeural" if speaker_name == "Модератор" else "ru-RU-DmitryNeural"
        rate = "-15%"
        pitch = "+0Hz"
        
        if speaker_name in PHILOSOPHERS_DATA:
            data = PHILOSOPHERS_DATA[speaker_name]
            voice_id = data.get("voice", "ru-RU-DmitryNeural")
            rate = data.get("rate", "-15%")
            pitch = data.get("pitch", "+0Hz")
            
        self.request_set_voice.emit(voice_id, rate, pitch)

    def load_screens_and_connect_signals(self):
        loader = QUiLoader()

        # 1. МЕНЮ
        self.main_menu = loader.load(os.path.join(UI_DIR, "main_menu.ui"), self)
        self.main_menu.setObjectName("MainMenuWidget")
        self.main_menu.setStyleSheet(
            "QWidget#MainMenuWidget { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }")

        win_w = self.base_size.width()
        win_h = self.base_size.height()

        btn = self.main_menu.startDebateButton
        btn_w, btn_h = 280, 65
        btn.setGeometry((win_w - btn_w) // 2, 370, btn_w, btn_h)
        btn.setText("ВЫЙТИ НА АРЕНУ")
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00);
                color: #3e2723; font-size: 18px; font-weight: bold; border-radius: 8px; border: 2px solid #a67c00;
            }
            QPushButton:hover { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37); }
        """)

        # Кнопка Магазин
        self.shop_btn = QtWidgets.QPushButton("МАГАЗИН", self.main_menu)
        self.shop_btn.setGeometry((win_w - btn_w) // 2, 370 + btn_h + 15, btn_w, btn_h)
        self.shop_btn.setStyleSheet(btn.styleSheet())
        self.shop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.shop_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        
        # Кнопка Профиль
        self.profile_btn = QtWidgets.QPushButton("МОЙ ПРОФИЛЬ", self.main_menu)
        self.profile_btn.setGeometry((win_w - btn_w) // 2, 370 + 2*(btn_h + 15), btn_w, btn_h)
        self.profile_btn.setStyleSheet(btn.styleSheet())
        self.profile_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.profile_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        
        # Кнопка Обучение (Как играть)
        self.tutorial_btn = QtWidgets.QPushButton("ОБУЧЕНИЕ", self.main_menu)
        self.tutorial_btn.setGeometry((win_w - btn_w) // 2, 370 + 3*(btn_h + 15), btn_w, btn_h)
        self.tutorial_btn.setStyleSheet(btn.styleSheet())
        self.tutorial_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tutorial_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        # Инфо пользователя (Никнейм и Монеты) - Правый верхний угол
        self.user_info_lbl = QtWidgets.QLabel("", self.main_menu)
        self.user_info_lbl.setGeometry(win_w - 420, 20, 400, 40)
        self.user_info_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.user_info_lbl.setStyleSheet("font-size: 20px; color: white; font-weight: bold; background: transparent;")

        for label in self.main_menu.findChildren(QtWidgets.QLabel):
            if "Дебаты" in label.text() or "ИНТЕЛЛЕКТУАЛЬНЫЙ" in label.text():
                label.setGeometry((win_w - 600) // 2 - 15, 70, 600, 200)
                label.setText("ИНТЕЛЛЕКТУАЛЬНЫЙ\nГЛАДИАТОР")
                label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                label.setStyleSheet("""
                    QLabel {
                        color: #3e2723; background-color: rgba(245, 245, 220, 230); 
                        border: 2px solid #a67c00; border-radius: 12px; font-size: 42px; font-weight: 900;
                        font-family: "Times New Roman", serif; padding: 10px;
                    } 
                """)

        # 2. ДЕБАТЫ
        self.debate_screen = loader.load(os.path.join(UI_DIR, "debate_screen.ui"), self)

        # 3. НАСТРОЙКИ (SETUP SCREEN)
        self.setup_screen = QtWidgets.QWidget()
        self.setup_screen.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setup_screen.setObjectName("SetupScreen")
        self.setup_screen.setStyleSheet("#SetupScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }")
        
        setup_main_layout = QtWidgets.QVBoxLayout(self.setup_screen)
        setup_main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        setup_container = QtWidgets.QFrame()
        setup_container.setFixedSize(640, 500)
        setup_container.setStyleSheet("QFrame { background-color: rgba(20, 20, 30, 240); border-radius: 15px; border: 2px solid #a67c00; }")
        
        vbox_setup = QtWidgets.QVBoxLayout(setup_container)
        vbox_setup.setContentsMargins(40, 30, 40, 40)
        vbox_setup.setSpacing(15)
        
        setup_title = QtWidgets.QLabel("НАСТРОЙКА ПОЕДИНКА", setup_container)
        setup_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        setup_title.setStyleSheet("font-size: 34px; font-weight: bold; color: #ffca28; border: none; background: transparent; margin-bottom: 10px; min-height: 50px;")
        
        # Выбор режима
        self.mode_combo = QtWidgets.QComboBox(setup_container)
        self.mode_combo.addItems(["Тренировочные дебаты", "Критика научного тезиса"])
        self.mode_combo.setStyleSheet("""
            QComboBox { background-color: rgba(30,30,40,200); color: white; border: 2px solid #a67c00; border-radius: 8px; padding: 10px; font-size: 18px; font-weight: bold;}
            QComboBox::drop-down { border-left: 2px solid #a67c00; }
            QComboBox QAbstractItemView { background: #1e1e2e; color: white; selection-background-color: #a67c00; outline: none; }
        """)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)

        self.topic_input = QtWidgets.QLineEdit(setup_container)
        self.topic_input.setPlaceholderText("Введите тему дебатов...")
        self.topic_input.setStyleSheet("QLineEdit { padding: 10px 20px; font-size: 20px; border: 2px solid #555; border-radius: 8px; background-color: rgba(30,30,40,200); color: white; }")
        
        self.file_upload_wrapper = QtWidgets.QWidget(setup_container)
        fu_layout = QtWidgets.QHBoxLayout(self.file_upload_wrapper)
        fu_layout.setContentsMargins(0, 0, 0, 0)
        
        self.file_upload_btn = QtWidgets.QPushButton("📎 Прикрепить файл (TXT/PDF/DOCX)")
        self.file_upload_btn.setStyleSheet("QPushButton { background-color: #2c3e50; color: white; font-size: 16px; font-weight:bold; padding: 10px; border-radius: 8px; } QPushButton:hover { background-color: #34495e; }")
        self.file_upload_btn.clicked.connect(self.upload_critique_file)
        self.file_upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_name_lbl = QtWidgets.QLabel("")
        self.file_name_lbl.setStyleSheet("color: #a0a0a0; font-size: 16px; font-style: italic;")
        
        fu_layout.addWidget(self.file_upload_btn)
        fu_layout.addWidget(self.file_name_lbl)
        self.file_upload_wrapper.hide()

        self.confirm_start_btn = QtWidgets.QPushButton("К БАРЬЕРУ", setup_container)
        self.confirm_start_btn.setFixedSize(380, 60)
        self.confirm_start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.confirm_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_start_btn.setStyleSheet("""
            QPushButton { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00); color: #3e2723; font-size: 24px; font-weight: bold; border-radius: 10px; border: none; }
            QPushButton:hover { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37); }
        """)
        
        self.setup_back_btn = QtWidgets.QPushButton("Отмена", setup_container)
        self.setup_back_btn.setFixedSize(300, 45)
        self.setup_back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setup_back_btn.setStyleSheet("""
            QPushButton { 
                background-color: rgba(60, 60, 80, 200); 
                color: #B0B0B0; font-size: 14px; font-weight: bold; 
                border-radius: 8px; border: 1px solid #555;
            } 
            QPushButton:hover { background-color: #c62828; color: white; border-color: #ef5350; }
        """)
        self.setup_back_btn.clicked.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_menu))
        
        vbox_setup.addWidget(setup_title)
        vbox_setup.addWidget(self.mode_combo)
        vbox_setup.addWidget(self.topic_input)
        vbox_setup.addWidget(self.file_upload_wrapper)
        vbox_setup.addSpacing(20)
        vbox_setup.addWidget(self.confirm_start_btn,alignment=Qt.AlignmentFlag.AlignCenter)
        vbox_setup.addWidget(self.setup_back_btn,alignment=Qt.AlignmentFlag.AlignCenter)
    
        setup_main_layout.addWidget(setup_container)

        # 4. ЭКРАН АВТОРИЗАЦИИ
        self.auth_screen = AuthScreen()
        self.auth_screen.login_requested.connect(self.handle_login)
        self.auth_screen.register_requested.connect(self.handle_register)

        # 5. ЭКРАН ПРОФИЛЯ
        self.profile_screen = ProfileScreen()
        self.profile_screen.back_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_menu))

        # 6. ЭКРАН МАГАЗИНА
        self.shop_screen = ShopScreen()
        self.shop_screen.back_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_menu))
        self.shop_screen.buy_requested.connect(self.handle_buy)
        self.shop_screen.details_requested.connect(self.go_to_details)

        # 7. ЭКРАН БИОГРАФИИ
        self.details_screen = PhilosopherDetailsScreen()
        self.details_screen.back_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.shop_screen))

        self.stacked_widget.addWidget(self.auth_screen)
        self.stacked_widget.addWidget(self.main_menu)
        self.stacked_widget.addWidget(self.setup_screen)
        self.stacked_widget.addWidget(self.debate_screen)
        self.stacked_widget.addWidget(self.profile_screen)
        self.stacked_widget.addWidget(self.shop_screen)
        self.stacked_widget.addWidget(self.details_screen)

        widgets_to_capture = ['user', 'opp', 'left', 'right', 'time', 'topic', 'subtitleLabel', 'subtitletopicLabel']
        for name in widgets_to_capture:
            widget = getattr(self.debate_screen, name, None)
            if widget:
                self.initial_geometries[name] = widget.geometry()
                if name in ['subtitleLabel', 'subtitletopicLabel']:
                    self.initial_font_sizes[name] = widget.font().pointSize()

        self.debate_screen.subtitleLabel.setWordWrap(True)
        self.debate_screen.subtitleLabel.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.debate_screen.installEventFilter(self)
        self.main_menu.installEventFilter(self)

        self.main_menu.startDebateButton.clicked.connect(self.go_to_setup_screen)
        self.confirm_start_btn.clicked.connect(self.start_debate_with_topic)
        self.profile_btn.clicked.connect(self.go_to_profile)
        self.shop_btn.clicked.connect(self.go_to_shop)
        self.tutorial_btn.clicked.connect(self.start_tutorial)

        self.stacked_widget.setCurrentWidget(self.auth_screen)

        # Фоновая музыка
        self.bg_player = QMediaPlayer()
        self.bg_audio = QAudioOutput()
        self.bg_audio.setVolume(0.15) # Тихая музыка
        self.bg_player.setAudioOutput(self.bg_audio)
        self.bg_player.setLoops(QMediaPlayer.Loops.Infinite)

        # Фоновая предзагрузка тяжелых моделей во время нахождения в меню
        threading.Thread(target=self._preload_heavy_models, daemon=True).start()

    def _preload_heavy_models(self):
        """Асинхронно предзагружает тяжелые ML модели, чтобы избежать зависаний перед дебатами"""
        print("PRELOAD: Начинаю фоновую загрузку моделей...")
        try:
            # DeepSeek клиент уже инициализирован в __init__
            print("PRELOAD: DeepSeek клиент готов.")
            
            from rag_retriever import get_chroma_client, get_embedding_function
            get_chroma_client()
            get_embedding_function()
            print("PRELOAD: Векторная БД (ChromaDB) и локальная ML модель загружены.")
            
            import edge_tts
            print("PRELOAD: Модуль озвучки (edge-tts) загружен.")
        except Exception as e:
            print(f"PRELOAD: Ошибка при предзагрузке: {e}")

    def play_music(self, track_name):
        path = os.path.join(MUSIC_DIR, track_name)
        url = QUrl.fromLocalFile(path)
        if self.bg_player.source() != url:
            self.bg_player.setSource(url)
            self.bg_player.play()

    # --- ЛОГИКА АВТОРИЗАЦИИ ---
    @QtCore.Slot(str, str)
    def handle_login(self, email, pwd):
        ok, result = self.db.authenticate_user(email, pwd)
        if ok:
            self.current_user = result
            self.update_main_menu_info()
            self.stacked_widget.setCurrentWidget(self.main_menu)
            self.play_music("Sunbeams in the Stacks.mp3")
        else:
            self.auth_screen.show_error(result)
            
    @QtCore.Slot(str, str, str)
    def handle_register(self, email, nickname, pwd):
        ok, result = self.db.register_user(email, nickname, pwd)
        if ok:
            self.handle_login(email, pwd) # Автологин
        else:
            self.auth_screen.show_error(result)

    def update_main_menu_info(self):
        if self.current_user:
            profile = self.db.get_user_profile(self.current_user['id'])
            coins = profile['coins'] if profile else 0
            self.user_info_lbl.setText(f"👤 {self.current_user['nickname']} | 💰 {coins} аргуконов")

    @QtCore.Slot()
    def go_to_profile(self):
        if self.current_user:
            profile = self.db.get_user_profile(self.current_user['id'])
            self.profile_screen.update_stats(profile)
            self.stacked_widget.setCurrentWidget(self.profile_screen)

    @QtCore.Slot()
    def go_to_shop(self):
        if self.current_user:
            profile = self.db.get_user_profile(self.current_user['id'])
            coins = profile['coins'] if profile else 0
            unlocked = self.db.get_unlocked_opponents(self.current_user['id'])
            self.shop_screen.update_shop(coins, unlocked)
            self.stacked_widget.setCurrentWidget(self.shop_screen)

    @QtCore.Slot(str, int)
    def handle_buy(self, name, price):
        if self.current_user:
            ok, msg = self.db.unlock_opponent(self.current_user['id'], name, price)
            if ok:
                dialog = CustomSuccessDialog(f"{name} теперь доступен в дебатах!", self)
                dialog.exec()
                self.update_main_menu_info()
                self.go_to_shop() # Перерисовка магазина (кнопки КУПИТЬ -> РАЗБЛОКИРОВАНО)
            else:
                QtWidgets.QMessageBox.warning(self, "Ошибка", msg)

    @QtCore.Slot(str)
    def go_to_details(self, name):
        self.details_screen.load_philosopher(name)
        self.stacked_widget.setCurrentWidget(self.details_screen)

    @QtCore.Slot()
    def start_tutorial(self):
        # Переключаемся на экран дебатов для туториала
        self.stacked_widget.setCurrentWidget(self.debate_screen)
        
        # Настраиваем фейковые данные для туториала
        self.debate_screen.topic.setText(" ТЕМА: В чём смысл жизни?")
        self.debate_screen.user.setText(self.current_user['nickname'] if self.current_user else "ИГРОК")
        
        # Загружаем Изображение Сократа, а не текст (иначе затрет картинку)
        self.debate_screen.opp.setText("")
        socrates_pixmap = QtGui.QPixmap("assets/socrates.png")
        if socrates_pixmap.isNull():
             # Fallback if resources.qrc is not used or path differs
             socrates_pixmap = QtGui.QPixmap("assets/resized-socrates.png")
        if not socrates_pixmap.isNull():
             self.debate_screen.opp.setPixmap(socrates_pixmap)
             
        self.update_subtitles("Приветствую на Арене Разума! Я Сократ, и мы начинаем наши дебаты.")
        self.update_speaker_name("Сократ")
        
        # Чтобы диалог ввода можно было подсветить, нужно его создать
        # Для туториала передаем is_tutorial=True (он будет прозрачен для кликов)
        self.tutorial_input_dialog = VoiceInputDialog(self.debate_screen, "Ваш ход", "Введите аргумент:", is_tutorial=True)
        # Важно: переместим его в удобное место поверх debate_screen
        self.tutorial_input_dialog.move((self.width() - 600) // 2, self.height() - 250)
        # СКРЫВАЕМ его до тех пор, пока мы не дойдем до шага с вводом
        self.tutorial_input_dialog.hide()

        steps = [
            {
                "widget": self.debate_screen.topic,
                "text": "<b>Тема дебатов.</b><br><br>Здесь отображается тезис, вокруг которого строится вся аргументация матча.",
                "direction": "bottom"
            },
            {
                "widget": self.debate_screen.opp,
                "text": "<b>Это ваш оппонент.</b><br><br>У каждого философа свой уникальный характер и стиль аргументации. Будьте готовы к сложным логическим ловушкам!",
                "direction": "bottom"
            },
            {
                "widget": None,
                "text": "<div style='font-size: 15px;'><b>Структура дебатов (Часть 1)</b><br><br>"
                        "<b>Раунд 1: Вступительное слово</b><br>Вам нужно заявить свою позицию и заложить логический фундамент.<br><br>"
                        "<b>Раунд 2: Перекрестная полемика (Clash)</b><br>Попытайтесь разрушить аргументы противника и защитить свои. Сначала атакует Игрок, затем ИИ.</div>",
                "direction": "bottom"
            },
            {
                "widget": None,
                "text": "<div style='font-size: 15px;'><b>Структура дебатов (Часть 2)</b><br><br>"
                        "<b>Раунд 3: Допрос Жюри (Кульминация)</b><br>Нейросеть-Жюри анализирует первые раунды и задает каверзный вопрос обоим участникам.<br><br>"
                        "<b>Раунд 4: Заключительное слово (Эндшпиль)</b><br>Красивая выжимка и итог. Новые аргументы приводить запрещено!<br><br>"
                        "<b>Финал: Вердикт Судьи</b><br>ИИ оценивает структуру и выносит решение!</div>",
                "direction": "bottom"
            },
            {
                "widget": self.debate_screen.subtitleLabel,
                "text": "<b>Арена разума.</b><br><br>Здесь будет появляться история ваших дебатов. Внимательно читайте тезисы противника, чтобы найти в них слабые места.",
                "direction": "top"
            },
            {
                "widget": self.tutorial_input_dialog.text_input,
                "text": "<b>Ваше оружие.</b><br><br>Введите сюда свой аргумент или контраргумент. Постарайтесь быть лаконичным и убедительным.",
                "direction": "top",
                "action": lambda: self.tutorial_input_dialog.show() # Показываем диалог
            },
            {
                "widget": self.tutorial_input_dialog.mic_btn,
                "text": "<b>Полное погружение!</b><br><br>Используйте этот микрофон, чтобы диктовать свои аргументы голосом, как настоящий оратор.",
                "direction": "top"
            },
            {
                "widget": self.tutorial_input_dialog.send_btn,
                "text": "<b>Конец хода.</b><br><br>Отправьте свой ответ, когда будете готовы. В конце дебатов независимое ИИ-жюри вынесет свой вердикт и наградит вас аргуконами!",
                "direction": "top"
            }
        ]
        
        self.tutorial_overlay = TutorialOverlayWidget(self, steps=steps)
        self.tutorial_overlay.tutorial_finished.connect(self.on_tutorial_finished)
        self.tutorial_overlay.start()

    @QtCore.Slot()
    def on_tutorial_finished(self):
        if hasattr(self, 'tutorial_input_dialog') and self.tutorial_input_dialog:
            self.tutorial_input_dialog.close()
            self.tutorial_input_dialog = None
            
        if hasattr(self, 'tutorial_overlay') and self.tutorial_overlay:
            self.tutorial_overlay.deleteLater()
            self.tutorial_overlay = None
            
        # Очищаем поле сабов
        self.update_subtitles("")
        self.update_speaker_name("")
        self.debate_screen.topic.setText("")
        self.stacked_widget.setCurrentWidget(self.main_menu)

    # --- МЕТОД ВЫЗОВА ОКНА ---
    def get_user_input(self, title, label):
        dialog = VoiceInputDialog(self, title, label)
        # Смещаем диалог ниже центра
        rect = self.geometry()
        x = rect.x() + (rect.width() - dialog.width()) // 2
        y = rect.y() + int(rect.height() * 0.5)
        dialog.move(x, y)
        if dialog.exec():
            text = dialog.get_text()
            return text.strip() if text.strip() else "..."
        return None

    @QtCore.Slot()
    def go_to_setup_screen(self):
        self.topic_input.clear()
        self.stacked_widget.setCurrentWidget(self.setup_screen)

    @QtCore.Slot(int)
    def on_mode_changed(self, index):
        if index == 1: # Критика
            self.file_upload_wrapper.show()
            self.topic_input.setPlaceholderText("Введите ваш тезис или загрузите файл с текстом работы...")
        else:
            self.file_upload_wrapper.hide()
            self.topic_input.setPlaceholderText("Введите тему дебатов...")

    @QtCore.Slot()
    def upload_critique_file(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите файл", "", "Supported files (*.txt *.pdf *.docx)"
        )
        if filepath:
            self.critique_filepath = filepath
            import os
            self.file_name_lbl.setText(os.path.basename(filepath))

    @QtCore.Slot()
    def start_debate_with_topic(self):
        raw_topic = self.topic_input.text().strip()
        is_critique = hasattr(self, 'mode_combo') and self.mode_combo.currentIndex() == 1

        if is_critique:
            self.confirm_start_btn.setText("Обработка...")
            self.confirm_start_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()
            
            attached_text = ""
            if hasattr(self, 'critique_filepath') and self.critique_filepath:
                import document_parser
                attached_text = document_parser.extract_text(self.critique_filepath)
                if attached_text.startswith("Ошибка"):
                    QtWidgets.QMessageBox.warning(self, "Ошибка файла", attached_text)
                    self.confirm_start_btn.setText("К БАРЬЕРУ")
                    self.confirm_start_btn.setEnabled(True)
                    return
            
            self.critique_full_text = f"{raw_topic}\n\n{attached_text}".strip()
            
            if not self.critique_full_text:
                QtWidgets.QMessageBox.warning(self, "Пусто", "Введите тезис или прикрепите файл.")
                self.confirm_start_btn.setText("К БАРЬЕРУ")
                self.confirm_start_btn.setEnabled(True)
                return
            
            topic_label = raw_topic if raw_topic else "Защита научной работы"
            if len(topic_label) > 50: topic_label = topic_label[:50] + "..."
            
            # Пропускаем стандартного оппонента, используем Рецензента
            file_name = "assets/science.png"
            pixmap = QtGui.QPixmap(file_name)
            if pixmap.isNull():
                pixmap = QtGui.QPixmap("assets/kant.png")
            self.debate_screen.opp.setPixmap(pixmap)
            self.debate_screen.opp.setScaledContents(True)

            user_pixmap = QtGui.QPixmap("assets/user_avatar_no_bg.png")
            if not user_pixmap.isNull():
                self.debate_screen.user.setPixmap(user_pixmap)
                self.debate_screen.user.setScaledContents(True)

            self.confirm_start_btn.setText("К БАРЬЕРУ")
            self.confirm_start_btn.setEnabled(True)
            self._finalize_debate_start(topic_label, "Академический Рецензент")
            return
            
        else:
            if not raw_topic:
                raw_topic = "Искусственный интеллект — благо или угроза?"

            self.confirm_start_btn.setText("Анализирую...")
            self.confirm_start_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()

            # Запускаем AI-форматирование через DeepSeek в отдельном потоке
            self._formatter_thread = TopicFormatterThread(raw_topic, self.deepseek, parent=self)
            self._formatter_thread.topic_ready.connect(self._continue_debate_setup)
            self._formatter_thread.start()

    def _continue_debate_setup(self, formatted_topic):
        self.confirm_start_btn.setText("К БАРЬЕРУ")
        self.confirm_start_btn.setEnabled(True)

        if formatted_topic.startswith("Ошибка"):
            QtWidgets.QMessageBox.critical(self, "Ошибка", formatted_topic)
            return
        if formatted_topic == "ERROR":
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Тема не распознана.")
            return

        dialog = CustomConfirmDialog(self, formatted_topic)
        if dialog.exec():
            # Пользователь подтвердил тему, выбираем оппонента
            topic = formatted_topic
            unlocked_opps = self.db.get_unlocked_opponents(self.current_user['id']) if self.current_user else list(self.AVATAR_MAP.keys())
            available_opps = [opp for opp in self.AVATAR_MAP.keys() if opp in unlocked_opps]
            
            opp_dialog = OpponentSelectionDialog(self, available_opps)
            if not opp_dialog.exec():
                return
            
            opponent_name = opp_dialog.get_selected()

            file_name = self.AVATAR_MAP.get(opponent_name, "assets/kant.png")
            pixmap = QtGui.QPixmap(file_name)
            if pixmap.isNull():
                pixmap = QtGui.QPixmap("assets/kant.png")

            self.debate_screen.opp.setPixmap(pixmap)
            self.debate_screen.opp.setScaledContents(True)

            # Пользовательский аватар
            user_pixmap = QtGui.QPixmap("assets/user_avatar_no_bg.png")
            if not user_pixmap.isNull():
                self.debate_screen.user.setPixmap(user_pixmap)
                self.debate_screen.user.setScaledContents(True)
            
            # Запуск дебатов
            self._finalize_debate_start(topic, opponent_name)
        else:
            # Пользователь отказался, просим уточнение
            feedback = self.get_user_input("Уточнение", "Что именно нужно изменить в теме?")
            if not feedback: 
                return
            
            # Повторный цикл с учетом фидбека
            self.confirm_start_btn.setText("Переписываю...")
            self.confirm_start_btn.setEnabled(False)
            QtWidgets.QApplication.processEvents()
            
            current_input = f"Предыдущий вариант темы: {formatted_topic}. ПРАВКА ПОЛЬЗОВАТЕЛЯ: {feedback}"
            self._formatter_thread = TopicFormatterThread(current_input, self.deepseek, parent=self)
            self._formatter_thread.topic_ready.connect(self._continue_debate_setup)
            self._formatter_thread.start()

    def _finalize_debate_start(self, topic, opponent_name):
        self.stacked_widget.setCurrentWidget(self.debate_screen)

        self.opp_system_prompt = get_opponent_system_prompt(topic, opponent_name)
        self.deepseek.reset_stats()  # Сбрасываем счетчики перед новой игрой
        user_name = self.current_user['nickname'] if self.current_user else "Вы"
        self.engine = DebateManager(topic, user_name, opponent_name)
        
        # Включаем музыку дебатов
        self.play_music("Clockwork Focus.mp3")

        if hasattr(self.debate_screen, 'subtitletopicLabel'):
            self.update_speaker_name("Модератор")
        if hasattr(self.debate_screen, 'subtitleLabel'):
            if opponent_name == "Академический Рецензент":
                self.debate_screen.subtitleLabel.setText("Идет подготовка к защите...")
            else:
                self.debate_screen.subtitleLabel.setText("Идет подготовка к дебатам...") # Показываем текст ожидания
        if hasattr(self.debate_screen, 'topic'):
            self.debate_screen.topic.setText(topic)
            self.debate_screen.topic.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter) # Центруем заголовок

        self.action_queue.clear()
        self.debate_screen.time.setText("")
        self.state = "DEBATE_FLOW"

        if opponent_name == "Академический Рецензент":
            self.action_queue.extend([
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': 'Начинается защита научной работы. Слово предоставляется автору.'},
                {'type': 'speech', 'participant': self.engine.user_name},
                {'type': 'speech', 'participant': self.engine.opponent_name},
                {'type': 'speech', 'participant': self.engine.user_name},
                {'type': 'speech', 'participant': self.engine.opponent_name},
                {'type': 'speech', 'participant': self.engine.user_name},
                {'type': 'speech', 'participant': self.engine.opponent_name},
                {'type': 'speech', 'participant': self.engine.user_name},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': 'Защита завершена. Слово предоставляется комиссии для вынесения вердикта.'},
                {'type': 'final_verdict'},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': 'Игра окончена. Спасибо всем участникам дебатов за отличную игру. Переходим к результатам.'},
                {'type': 'end_debate'}
            ])
        else:
            self.action_queue.extend([
                {'type': 'generate_and_speak', 'prompt_func': self.engine.get_setup_prompt,
                 'speaker_name': 'Модератор', 'role': 'moderator'},
                {'type': 'speech', 'participant': self.engine.p1_name},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': f'Благодарю. Теперь слово для вступительной речи предоставляется {self.engine.p2_name}.'},
                {'type': 'speech', 'participant': self.engine.p2_name},
                {'type': 'clash_round', 'leader': self.engine.p1_name},
                {'type': 'clash_round', 'leader': self.engine.p2_name},
                {'type': 'jury_questions'},
                {'type': 'summary_statement', 'participant': self.engine.p1_name},
                {'type': 'summary_statement', 'participant': self.engine.p2_name},
                {'type': 'final_verdict'},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': 'Игра окончена. Спасибо всем участникам дебатов за отличную игру. Переходим к результатам.'},
                {'type': 'end_debate'}
            ])

        self.process_next_action()

    def process_next_action(self):
        if self.state not in ["DEBATE_FLOW", "CLASH_TRANSITION", "CLASH_ENDING"]: return
        if not self.action_queue:
            self.state = "IDLE"
            return
        QtCore.QTimer.singleShot(500, self._process_action_from_queue)

    def _process_action_from_queue(self):
        if not self.action_queue: return
        action = self.action_queue.pop(0)
        action_type = action['type']

        if action_type == 'generate_and_speak':
            prompt = action['prompt_func'](**action.get('prompt_args', {}))
            self._set_voice_for_speaker(action['speaker_name'])
            role = action.get('role', 'moderator')
            system_prompt = MODERATOR_PROMPT if role == 'moderator' else self.opp_system_prompt
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': action['speaker_name'], 'is_stream': True,
                'deepseek_manager': self.deepseek, 'system_prompt': system_prompt, 'role': role
            })
        elif action_type == 'speak_only':
            self.update_speaker_name(action['speaker_name'])
            sentences = [s for s in SENTENCE_SPLIT_RE.split(action['text'].strip()) if s]
            self.request_speak.emit(sentences)
        elif action_type == 'speech':
            self._handle_participant_speech(action['participant'])
        elif action_type == 'clash_round':
            self._start_clash_round(action)
        elif action_type == 'jury_questions':
            self.update_speaker_name("Модератор")
            self.request_speak.emit(["Раунды полемики завершены. Слово жюри."])
            self.state = "WAITING_FOR_JURY_PROMPT"
        elif action_type == 'summary_statement':
            self._handle_summary_statement(action['participant'])
        elif action_type == 'final_verdict':
            self.update_speaker_name("Модератор")
            self.request_speak.emit(["Наступает момент оглашения вердикта."])
            self.state = "WAITING_FOR_VERDICT_PROMPT"
        elif action_type == 'end_debate':
            self._handle_end_debate()
        elif action_type == '_get_summary_speech':
            participant_name = action['participant']
            if participant_name == self.engine.user_name:
                text = self.get_user_input("Итог", "Ваше заключительное слово:")
                if text:
                    self.engine._add_to_transcript(self.engine.user_name, text)
                    self.update_speaker_name(self.engine.user_name)
                    self.update_subtitles(text)
                    QtCore.QTimer.singleShot(1000, self.process_next_action)
            else:
                prompt = self.engine.get_summary_prompt(for_opponent=True)
                self._set_voice_for_speaker(participant_name)
                self.request_stream_start.emit()
                self.request_generation.emit(prompt, None, {
                    'speaker_name': participant_name, 'is_stream': True,
                    'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent'
                })

    def _handle_participant_speech(self, participant_name):
        if participant_name == self.engine.user_name:
            if hasattr(self, 'critique_full_text') and self.critique_full_text and self.engine.opponent_name == "Академический Рецензент":
                # Авто-инжект в режиме критики, чтобы пользователь не вводил текст вторично
                text = "Я готов защищать свою работу. Ознакомьтесь с ней, пожалуйста. Вот текст:\n\n" + self.critique_full_text
                self.engine._add_to_transcript(self.engine.user_name, text)
                self.update_speaker_name(self.engine.user_name)
                self.update_subtitles("Я готов защищать свою работу. Ознакомьтесь с ней, пожалуйста.")
                self.last_user_speech = text
                
                # Очищаем чтобы не использовать повторно
                self.critique_full_text = None 
                
                QtCore.QTimer.singleShot(2000, self.process_next_action)
            else:
                label = "Ваш ответ рецензенту:" if self.engine.opponent_name == "Академический Рецензент" else "Ваше вступительное слово:"
                text = self.get_user_input("Ваш ход", label)
                if text:
                    self.engine._add_to_transcript(self.engine.user_name, text)
                    self.update_speaker_name(self.engine.user_name)
                    self.update_subtitles(text)
                    self.last_user_speech = text
                    QtCore.QTimer.singleShot(1000, self.process_next_action)
        else:
            if self.engine.opponent_name == "Академический Рецензент":
                prompt = self.engine.get_critique_prompt()
            else:
                raw_prompt = self.engine.get_opponent_opening_prompt(self.last_user_speech)
                # --- HYBRID RAG INJECTION BEGIN ---
                print("[RAG] Извлекаем контекст для философа...")
                rag_memory = get_philosopher_context(self.engine.opponent_name, self.last_user_speech, top_k=3)
                if hasattr(self, 'deepseek'):
                    self.deepseek.log_rag_citation(rag_memory)
                prompt = raw_prompt + f"\n\n[СПРАВОЧНАЯ ИНФОРМАЦИЯ ИЗ ТВОИХ ТРУДОВ]\n(Используй эти данные мягко и органично, только если они релевантны. Не цитируй дословно, если это вредит естественности диалога. Сохраняй свой живой характер.)\nКонтекст:\n{rag_memory}\n[КОНЕЦ ИНФОРМАЦИИ]"
                # --- HYBRID RAG INJECTION END ---
                
            self._set_voice_for_speaker(self.engine.opponent_name)
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': self.engine.opponent_name, 'is_stream': True,
                'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent'
            })

    @QtCore.Slot(str, dict)
    def on_generation_chunk(self, text, metadata):
        self.request_stream_chunk.emit(text)

    @QtCore.Slot(str, dict)
    def on_generation_complete(self, text, metadata):
        if metadata.get('is_stream'):
            self.request_stream_finish.emit()
            
        callback = metadata.get('callback_action')
        speaker_name = metadata.get('speaker_name', 'Система')

        if callback == 'handle_topic_format':
            self._continue_debate_setup(text.strip())
            return
            
        if callback == 'handle_jury_questions':
            try:
                # Надежное извлечение JSON
                clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
                start_idx = clean.find('{')
                end_idx = clean.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    clean = clean[start_idx:end_idx+1]
                
                self.pending_jury_questions = json.loads(clean)
                self.jury_phase_step = 'ASKING_USER'
                self.update_speaker_name("Жюри")
                self.request_speak.emit(
                    [f"Вопрос для {self.engine.user_name}:", self.pending_jury_questions.get('question_for_user', 'К сожалению, вопрос не сгенерирован.')])
            except Exception as e:
                print(f"ERROR parsing jury questions: {e}. Raw text: {text}")
                self.process_next_action()
            return

        if callback == 'announce_winner':
            try:
                clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
                start_idx = clean.find('{')
                end_idx = clean.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    clean = clean[start_idx:end_idx+1]
                self.verdict_data = json.loads(clean)
                self.update_speaker_name("Жюри")

                is_3m = metadata.get('is_3m', False)
                
                # --- НАДЕЖНАЯ ПРОВЕРКА ПОБЕДИТЕЛЯ ---
                winner_name = str(self.verdict_data.get('winner', 'Ничья')).strip().lower()
                opp_name_lower = self.engine.opponent_name.lower()
                
                # Проверяем "от противного": 
                # Если имя оппонента есть в строке победителя — мы проиграли
                if opp_name_lower in winner_name:
                    is_user_win = False
                # Если в строке слово ничья/draw — это не победа
                elif "ничья" in winner_name or "draw" in winner_name:
                    is_user_win = False
                else:
                    # Во всех остальных случаях (Пользователь, Игрок, Имя игрока) - победил юзер!
                    is_user_win = True
                # ------------------------------------

                # Сохраняем статистику в базу данных
                if self.current_user and self.engine:
                    playtime = 300 
                    words = 200
                    chars = 1500

                    self.is_user_win = is_user_win
                    self.coins_reward = self.db.record_match_result(
                        self.current_user['id'],
                        self.engine.opponent_name,
                        is_user_win,
                        playtime, words, chars
                    )
                    self.update_main_menu_info() 
                else:
                    self.is_user_win = is_user_win
                    self.coins_reward = 0

                if is_3m:
                    self.verdict_announcement_step = 'SCORES_MATTER'
                    self.request_speak.emit(["Коллегия судей завершила подсчет баллов по системе 3M."])
                else:
                    self.verdict_announcement_step = 'REASONING'
                    verdict_text = f"Победитель: {self.verdict_data.get('winner', 'Ничья')}!"
                    self.engine._add_to_transcript("Жюри", verdict_text)
                    self.request_speak.emit([verdict_text])
            except Exception as e:
                print(f"ERROR in announce_winner: {e}")
                self.process_next_action()
            return

        is_clash = metadata.get('is_clash_turn', False)
        if is_clash and self.state != "CLASH_ACTIVE": return

        self.engine._add_to_transcript(speaker_name, text)
        self.update_speaker_name(speaker_name)
        if self.state == "CLASH_ACTIVE": self.last_clash_speech = text
        if not metadata.get('is_stream'):
            self.request_speak.emit([text])

    @QtCore.Slot()
    def on_sequence_finished(self):
        if self.clash_ending_pending:
            self.clash_ending_pending = False
            self._on_clash_round_finished()
            return

        if self.jury_phase_step == 'ASKING_USER':
            text = self.get_user_input("Жюри", "Ответ на вопрос жюри:")
            if text:
                self.engine._add_to_transcript(self.engine.user_name, text)
                self.update_subtitles(text)
            self.jury_phase_step = 'ASKING_OPPONENT'
            self.update_speaker_name("Жюри")
            self.request_speak.emit([
                "Спасибо за Ваш ответ.", 
                f"Теперь вопрос для {self.engine.opponent_name}:", 
                self.pending_jury_questions.get('question_for_opponent')
            ])
            return

        if self.jury_phase_step == 'ASKING_OPPONENT':
            q = self.pending_jury_questions.get('question_for_opponent', "")
            prompt = self.engine.get_jury_answer_prompt(q, for_opponent=True)
            self.jury_phase_step = 'WAITING_FOR_OPPONENT_ANSWER'
            self.request_generation.emit(prompt, None, {
                'speaker_name': self.engine.opponent_name,
                'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent'
            })
            return

        if self.jury_phase_step == 'WAITING_FOR_OPPONENT_ANSWER':
            self.jury_phase_step = None
            self.state = "DEBATE_FLOW"
            self.process_next_action()
            return

        if self.verdict_announcement_step == 'REASONING':
            reasoning = self.verdict_data.get('reasoning', '')
            self.engine._add_to_transcript("Жюри", f"Обоснование: {reasoning}")
            self.request_speak.emit(["Обоснование:", reasoning])
            self.verdict_announcement_step = 'FEEDBACK'
            return

        if self.verdict_announcement_step == 'FEEDBACK':
            feedback = self.verdict_data.get('feedback_for_user', '')
            self.engine._add_to_transcript("Жюри", f"Фидбэк: {feedback}")
            self.request_speak.emit(["Фидбэк:", feedback])
            self.verdict_announcement_step = 'RAG_CITATIONS'
            return

        # --- 3M SEQUENCE ---
        if self.verdict_announcement_step == 'SCORES_MATTER':
            u = self.verdict_data.get('user_scores', {}).get('matter', 0)
            o = self.verdict_data.get('opponent_scores', {}).get('matter', 0)
            text = f"Содержание и аргументация. {self.engine.user_name}: {u} баллов. {self.engine.opponent_name}: {o} баллов."
            self.request_speak.emit([text])
            self.verdict_announcement_step = 'SCORES_MANNER'
            return

        if self.verdict_announcement_step == 'SCORES_MANNER':
            u = self.verdict_data.get('user_scores', {}).get('manner', 0)
            o = self.verdict_data.get('opponent_scores', {}).get('manner', 0)
            text = f"Подача и риторика. {self.engine.user_name}: {u} баллов. {self.engine.opponent_name}: {o} баллов."
            self.request_speak.emit([text])
            self.verdict_announcement_step = 'SCORES_METHOD'
            return

        if self.verdict_announcement_step == 'SCORES_METHOD':
            u = self.verdict_data.get('user_scores', {}).get('method', 0)
            o = self.verdict_data.get('opponent_scores', {}).get('method', 0)
            text = f"Стратегия и структура. {self.engine.user_name}: {u} баллов. {self.engine.opponent_name}: {o} баллов."
            self.request_speak.emit([text])
            self.verdict_announcement_step = 'SCORES_TOTAL'
            return

        if self.verdict_announcement_step == 'SCORES_TOTAL':
            u = self.verdict_data.get('user_scores', {}).get('total', 0)
            o = self.verdict_data.get('opponent_scores', {}).get('total', 0)
            text = f"Итоговый счет. {u} против {o}."
            self.request_speak.emit([text])
            self.verdict_announcement_step = 'WINNER_ANNOUNCE'
            return

        if self.verdict_announcement_step == 'WINNER_ANNOUNCE':
            winner = self.verdict_data.get('winner', 'Ничья')
            text = f"На основании этих данных победителем объявляется: {winner}."
            self.engine._add_to_transcript("Жюри", text)
            self.request_speak.emit([text])
            self.verdict_announcement_step = '3M_EXPLANATION_MATTER'
            return

        if self.verdict_announcement_step == '3M_EXPLANATION_MATTER':
            expl = self.verdict_data.get('explanation_matter', '')
            self.engine._add_to_transcript("Жюри", f"Объяснение (Matter): {expl}")
            self.request_speak.emit(["Комментарий судей по содержанию:", expl])
            self.verdict_announcement_step = '3M_EXPLANATION_MANNER'
            return

        if self.verdict_announcement_step == '3M_EXPLANATION_MANNER':
            expl = self.verdict_data.get('explanation_manner', '')
            self.engine._add_to_transcript("Жюри", f"Объяснение (Manner): {expl}")
            self.request_speak.emit(["Комментарий судей по подаче:", expl])
            self.verdict_announcement_step = '3M_EXPLANATION_METHOD'
            return

        if self.verdict_announcement_step == '3M_EXPLANATION_METHOD':
            expl = self.verdict_data.get('explanation_method', '')
            self.engine._add_to_transcript("Жюри", f"Объяснение (Method): {expl}")
            self.request_speak.emit(["Комментарий судей по стратегии:", expl])
            self.verdict_announcement_step = 'RAG_CITATIONS'
            return

        if self.verdict_announcement_step == 'RAG_CITATIONS':
            self.verdict_announcement_step = 'DONE'
            if hasattr(self, 'deepseek') and self.deepseek.rag_citations:
                citations_text = "\n\n".join(self.deepseek.rag_citations)
                self.engine._add_to_transcript("Система", f"Использованные материалы RAG:\n{citations_text}")
                self.update_speaker_name("Система")
                # Эмитим текст для субтитров и очень краткую озвучку
                self.request_speak.emit(["К результатам прикреплены материалы, найденные в базе знаний."])
                # Дополнительно выводим текст в субтитры
                self.update_subtitles(f"Использованные материалы RAG:\n{citations_text[:250]}...")
            else:
                self.process_next_action()
            return

        if self.verdict_announcement_step == 'DONE':
            self.verdict_announcement_step = None
            self.state = "DEBATE_FLOW"
            self.process_next_action()
            return

        if self.state == "WAITING_FOR_JURY_PROMPT":
            self.state = "DEBATE_FLOW"
            self.request_generation.emit(self.engine.get_jury_questions_prompt(), None, {
                'speaker_name': 'Жюри', 'callback_action': 'handle_jury_questions',
                'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
            })
            return

        if self.state == "WAITING_FOR_VERDICT_PROMPT":
            self.state = "DEBATE_FLOW"
            is_critique = (self.engine.opponent_name == "Академический Рецензент")
            
            # --- HYBRID RAG JURY INJECTION BEGIN ---
            print("[RAG Jury] Проверяем факты участников...")
            recent_claims = " ".join(self.engine.transcript[-4:])
            search_query = f"проверка фактов {recent_claims[:100]}"
            jury_facts = get_web_context([search_query], max_results_per_query=2)
            if hasattr(self, 'deepseek'):
                self.deepseek.log_rag_citation(jury_facts)
            
            if is_critique:
                raw_prompt = self.engine.get_final_verdict_prompt()
            else:
                raw_prompt = self.engine.get_3m_verdict_prompt()
                
            prompt = raw_prompt + f"\n\n[БЛОК ПРОВЕРКИ ФАКТОВ В СЕТИ — GROUND TRUTH]\nРезультаты онлайн-поиска (учитывай их при выставлении баллов за Matter):\n{jury_facts}\n[КОНЕЦ БЛОКА ПРОВЕРКИ ФАКТОВ]"
            # --- HYBRID RAG JURY INJECTION END ---

            self.request_generation.emit(prompt, None, {
                'speaker_name': 'Жюри', 
                'callback_action': 'announce_winner',
                'is_3m': not is_critique,
                'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
            })
            return

        if self.state == "CLASH_INTRO":
            self._begin_clash_timer_and_turns()
        elif self.state == "CLASH_ACTIVE":
            self._process_clash_turn()
        elif self.state in ["CLASH_ENDING", "CLASH_TRANSITION", "DEBATE_FLOW"]:
            self.process_next_action()

    def _start_clash_round(self, action):
        self.state = "CLASH_INTRO"
        self.clash_leader = action['leader']
        self.clash_responder = self.engine.p2_name if self.clash_leader == self.engine.p1_name else self.engine.p1_name
        self.current_clash_turn_holder = self.clash_leader
        self.update_speaker_name("Модератор")
        self.request_speak.emit([f"Раунд полемики. Ведущий — {self.clash_leader}."])

    def _begin_clash_timer_and_turns(self):
        self.state = "CLASH_ACTIVE"
        self.clash_time_left = self.clash_duration_sec
        self.clash_timer.start()
        self._process_clash_turn()

    def _process_clash_turn(self):
        if not self.clash_timer.isActive() and not self.clash_ending_pending: return

        if self.current_clash_turn_holder == self.engine.user_name:
            text = self.get_user_input("Полемика", "Ваш короткий ответ/вопрос:")
            if text:
                self.last_clash_speech = text
                self.update_speaker_name(self.engine.user_name)
                self.engine._add_to_transcript(self.engine.user_name, text)
                self.update_subtitles(text)
                QtCore.QTimer.singleShot(500, self.on_sequence_finished)
        else:
            is_leader = (self.current_clash_turn_holder == self.clash_leader)
            if is_leader:
                raw_prompt = self.engine.get_clash_leader_prompt(self.last_clash_speech)
            else:
                raw_prompt = self.engine.get_clash_responder_prompt(self.last_clash_speech)
                
            # --- HYBRID RAG INJECTION BEGIN ---
            print(f"[RAG Clash] Извлекаем контекст для: {self.current_clash_turn_holder}")
            rag_memory = get_philosopher_context(self.current_clash_turn_holder, self.last_clash_speech, top_k=2)
            prompt =  f"\n\n[СПРАВОЧНАЯ ИНФОРМАЦИЯ ИЗ ТВОИХ ТРУДОВ]\n(Используй эти данные мягко и органично, только если они релевантны. Не цитируй дословно, если это вредит естественности диалога. Сохраняй свой живой характер. О)\nКонтекст:\n{rag_memory}\n[КОНЕЦ ИНФОРМАЦИИ И не забывай что задавать тебе вопрос или отвечать не на жти цитаты, а на позицию игрока!!!  Она дальше вот идёт]" + raw_prompt
            # --- HYBRID RAG INJECTION END ---

            self._set_voice_for_speaker(self.current_clash_turn_holder)
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': self.current_clash_turn_holder, 'is_clash_turn': True, 'is_stream': True,
                'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent'
            })

        self.current_clash_turn_holder = self.clash_responder if self.current_clash_turn_holder == self.clash_leader else self.clash_leader

    def on_clash_timer_tick(self):
        self.clash_time_left -= 1
        m, s = divmod(self.clash_time_left, 60)
        self.debate_screen.time.setText(f"{m:02d}:{s:02d}")
        if self.clash_time_left <= 0:
            self.clash_timer.stop()
            if self.speaker_worker.is_busy():
                self.clash_ending_pending = True
            else:
                self._on_clash_round_finished()

    def _on_clash_round_finished(self):
        self.state = "CLASH_ENDING"
        self.update_speaker_name("Модератор")
        self.request_speak.emit(["Время раунда истекло."])

    def _handle_summary_statement(self, participant_name):
        intro = f"Заключение от {participant_name}."
        self.summary_phase_started = True
        self.update_speaker_name("Модератор")
        self.request_speak.emit([intro])
        self.action_queue.insert(0, {'type': '_get_summary_speech', 'participant': participant_name})

    @QtCore.Slot(str)
    def update_subtitles(self, text):
        self.debate_screen.subtitleLabel.setText(text)

    def update_speaker_name(self, name):
        self._set_voice_for_speaker(name)
        self.debate_screen.subtitletopicLabel.setText(name)
        self.debate_screen.subtitletopicLabel.adjustSize()
        
        target = self.debate_screen.subtitleLabel
        widget = self.debate_screen.subtitletopicLabel
        widget.move(target.x() + (target.width() - widget.width()) // 2, target.y() - widget.height() - 5)

    def center_window(self):
        rect = self.frameGeometry()
        rect.moveCenter(self.screen().availableGeometry().center())
        self.move(rect.topLeft())

    def eventFilter(self, watched_object, event):
        if event.type() == QtCore.QEvent.Type.Resize:
            if watched_object == self.debate_screen:
                self.resize_debate_widgets()
            elif hasattr(self, 'main_menu') and watched_object == self.main_menu:
                self.resize_main_menu_widgets()
        return super().eventFilter(watched_object, event)

    def _handle_end_debate(self):
        self.state = "IDLE"
        self.update_speaker_name("Система")
        self.debate_screen.subtitleLabel.setText("Дебаты завершены!")
        # Выводим статистику токенов за раунд
        self.deepseek.print_game_stats()
        self.show_post_debate_screen(getattr(self, 'is_user_win', False), getattr(self, 'coins_reward', 0))

    def show_post_debate_screen(self, is_win, coins_reward):
        self.post_debate_overlay = QtWidgets.QFrame(self.debate_screen)
        self.post_debate_overlay.setGeometry(0, 0, self.debate_screen.width(), self.debate_screen.height())
        self.post_debate_overlay.setStyleSheet("background-color: rgba(0, 0, 0, 220);")
        
        layout = QtWidgets.QVBoxLayout(self.post_debate_overlay)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(25)
        
        title = QtWidgets.QLabel("ПОБЕДА!" if is_win else "ПОРАЖЕНИЕ...")
        color = "#4CAF50" if is_win else "#F44336"
        title.setStyleSheet(f"color: {color}; font-size: 72px; font-weight: bold; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        if coins_reward > 0:
            coins_lbl = QtWidgets.QLabel(f"+{coins_reward} монет")
            coins_lbl.setStyleSheet("color: #FFD700; font-size: 40px; font-weight: bold; background: transparent;")
            coins_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(coins_lbl)
        
        btn_layout = QtWidgets.QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.setSpacing(15)
        
        pdf_btn = QtWidgets.QPushButton("Скачать отчет (PDF)")
        pdf_btn.setFixedSize(300, 60)
        pdf_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; font-size: 20px; font-weight: bold; border-radius: 10px; border: 2px solid #1976D2; } QPushButton:hover { background-color: #1E88E5; }")
        pdf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pdf_btn.clicked.connect(self._export_pdf)
        btn_layout.addWidget(pdf_btn)
        
        exit_btn = QtWidgets.QPushButton("В главное меню")
        exit_btn.setFixedSize(300, 60)
        exit_btn.setStyleSheet("QPushButton { background-color: #757575; color: white; font-size: 20px; font-weight: bold; border-radius: 10px; border: 2px solid #616161; } QPushButton:hover { background-color: #616161; }")
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.clicked.connect(self._return_to_main_menu_from_debate)
        btn_layout.addWidget(exit_btn)
        
        layout.addLayout(btn_layout)
        self.post_debate_overlay.show()
        
    def _export_pdf(self):
        from pdf_generator import generate_debate_report
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        
        file_path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет", "debate_report.pdf", "PDF Files (*.pdf)")
        if file_path:
            try:
                winner = self.engine.user_name if getattr(self, 'is_user_win', False) else self.engine.opponent_name
                generate_debate_report(self.engine.transcript, self.engine.topic, winner, file_path)
                QMessageBox.information(self, "Успех", f"Отчет успешно сохранен:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить PDF:\n{e}")

    def _return_to_main_menu_from_debate(self):
        self.post_debate_overlay.deleteLater()
        self.post_debate_overlay = None
        self.stacked_widget.setCurrentWidget(self.main_menu)
        self.play_music("Sunbeams in the Stacks.mp3")
        if self.current_user:
            profile = self.db.get_user_profile(self.current_user['id'])
            if profile:
                self.update_main_menu_info()

    def resize_debate_widgets(self):
        if not self.initial_geometries: return
        w, h = self.debate_screen.width(), self.debate_screen.height()
        base_w, base_h = self.base_size.width(), self.base_size.height()
        w_diff, h_diff = w - base_w, h - base_h

        scale_factor = 1.3 if self.isFullScreen() else 1.0
        xopp_factor = 0.9
        pers_up_factor = -0.55 if self.isFullScreen() else 0.3

        for name in ['user', 'opp', 'left', 'right', 'time', 'topic', 'subtitleLabel', 'subtitletopicLabel']:
            if name not in self.initial_geometries: continue
            widget = getattr(self.debate_screen, name)
            base_geom = self.initial_geometries[name]

            if name in ['user', 'opp', 'left', 'right', 'time']:
                widget.setFixedSize(int(base_geom.width() * scale_factor), int(base_geom.height() * scale_factor))
                new_x, new_y = base_geom.x(), base_geom.y()
                if name == 'right':
                    new_x += int(w_diff * 0.85)
                elif name == 'time':
                    new_x += int(w_diff * 0.5)
                if name == 'opp':
                    new_y += int(h_diff * pers_up_factor)
                    new_x += int(w_diff * xopp_factor)
                elif name == "user":
                    new_y += int(h_diff * pers_up_factor)
                elif name in ['left', 'right']:
                    new_y += int(h_diff * -0.3)
                widget.move(new_x, new_y)

            elif name == 'topic':
                new_width = int(w * 0.9)
                widget.setFixedSize(new_width, int(h * 0.15))
                widget.setWordWrap(True)
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter) # Обязательно выравниваем по центру при ресайзе
                widget.move((w - new_width) // 2, int(h * 0.05))
                text_len = len(widget.text())
                base_font = 34 if self.isFullScreen() else 26
                font_size = max(20, base_font - max(0, text_len - 40) // 5)
                widget.setStyleSheet(
                    f"background: transparent; color: #E0E0E0; font-size: {font_size}px; font-weight: bold;")

            elif name == 'subtitleLabel':
                fsize = 25 if self.isFullScreen() else 18
                widget.setStyleSheet(
                    f"background-color: rgba(0,0,100,0.8); color: #E0E0E0; border-radius: 10px; padding: 15px; font-size: {fsize}px;")
                new_w = int(w * (0.6 if self.isFullScreen() else 0.5))
                new_h = int(base_geom.height() * (1.2 if self.isFullScreen() else 1.0))
                widget.resize(new_w, new_h)
                widget.move((w - new_w) // 2, h - new_h - int(h * 0.1))

            elif name == 'subtitletopicLabel':
                fsize = 28 if self.isFullScreen() else 24
                widget.setStyleSheet(f"background: transparent; color: #E0E0E0; font-size: {fsize}px;")
                widget.adjustSize()
                target = self.debate_screen.subtitleLabel
                widget.move(target.x() + (target.width() - widget.width()) // 2, target.y() - widget.height() - 5)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F11:
            self.toggle_fullscreen()
        elif event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            # Возвращаем окновый режим, запираем размер обратно
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.showNormal()
            self.setFixedSize(self.base_size)
            self.center_window()
        else:
            # Снимаем блокировку размера и выходим в полный экран
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.showFullScreen()

    def resize_main_menu_widgets(self):
        """Re-positions main menu buttons proportionally when window is resized/fullscreened."""
        w, h = self.main_menu.width(), self.main_menu.height()
        base_w, base_h = self.base_size.width(), self.base_size.height()
        scale = min(w / base_w, h / base_h)

        # Кнопки — в fullscreen чуть крупнее (+2.5%)
        btn_scale = scale * 1.025 if self.isFullScreen() else scale
        btn_w = int(280 * btn_scale)
        btn_h = int(65 * btn_scale)
        spacing = int(15 * btn_scale)
        center_x = (w - btn_w) // 2
        start_y = int(h * 0.48)

        self.main_menu.startDebateButton.setGeometry(center_x, start_y, btn_w, btn_h)
        self.shop_btn.setGeometry(center_x, start_y + (btn_h + spacing), btn_w, btn_h)
        self.profile_btn.setGeometry(center_x, start_y + 2 * (btn_h + spacing), btn_w, btn_h)
        self.tutorial_btn.setGeometry(center_x, start_y + 3 * (btn_h + spacing), btn_w, btn_h)

        # Шрифт кнопок — в fullscreen дополнительный бонус +8%
        font_scale = scale * 1.08 if self.isFullScreen() else scale
        btn_font_size = int(18 * font_scale)
        btn_style = f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00);
                color: #3e2723; font-size: {btn_font_size}px; font-weight: bold;
                border-radius: 8px; border: 2px solid #a67c00;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37);
            }}
        """
        for btn in [self.main_menu.startDebateButton, self.shop_btn,
                    self.profile_btn, self.tutorial_btn]:
            btn.setStyleSheet(btn_style)

        # Баланс — правый верхний угол
        info_w = int(400 * scale)
        self.user_info_lbl.setGeometry(w - info_w - 20, 20, info_w, int(40 * scale))
        self.user_info_lbl.setStyleSheet(f"font-size: {int(20 * scale)}px; color: white; font-weight: bold; background: transparent;")

        # Заголовок тоже перецентруем
        for label in self.main_menu.findChildren(QtWidgets.QLabel):
            if "ГЛАДИАТОР" in label.text() or "ИНТЕЛЛЕКТУАЛЬНЫЙ" in label.text():
                lbl_w = min(int(600 * scale), int(w * 0.75))
                font_size = int(42 * scale)
                label.setGeometry((w - lbl_w) // 2 - 15, int(h * 0.09), lbl_w, int(200 * scale))
                label.setStyleSheet(f"""
                    QLabel {{
                        color: #3e2723; background-color: rgba(245, 245, 220, 230); 
                        border: 2px solid #a67c00; border-radius: 12px; font-size: {font_size}px; font-weight: 900;
                        font-family: "Times New Roman", serif; padding: 10px;
                    }} 
                """)

    def closeEvent(self, ev):
        self.speaker_worker.shutdown()
        self.agent_thread.quit()
        self.speaker_thread.quit()
        ev.accept()


class ClickSoundFilter(QtCore.QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QSoundEffect
        from PySide6.QtCore import QUrl
        self.sound = QSoundEffect(self)
        self.sound.setSource(QUrl.fromLocalFile(os.path.join(MUSIC_DIR, "short-click-of-a-computer-mouse.wav")))
        self.sound.setVolume(0.3)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            from PySide6.QtWidgets import QAbstractButton, QComboBox
            if getattr(obj, "isEnabled", lambda: False)():
                # Проигрываем звук, если кликаем по кнопке или выпадашке (но не по полю ввода)
                if isinstance(obj, (QAbstractButton, QComboBox)):
                    self.sound.play()
        return False

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    
    # Глобальный перехватчик кликов мыши
    click_filter = ClickSoundFilter()
    app.installEventFilter(click_filter)
    
    window = AppController()
    window.show()
    sys.exit(app.exec())