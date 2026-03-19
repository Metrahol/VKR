import sys
import re
import json
import threading
import time
import speech_recognition as sr
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import Signal, Qt, QThread, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
import os

from settings_manager import SettingsManager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC_DIR = os.path.join(BASE_DIR, "music")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
UI_DIR = os.path.join(BASE_DIR, "ui")

from rag_retriever import get_philosopher_context, get_web_context
import resources_rc
from debate_manager import DebateManager
from show_debate_manager import ShowDebateManager
from workers import AgentWorker, SpeakerWorker
from agents import DeepSeekManager, get_opponent_system_prompt, MODERATOR_PROMPT, JURY_PROMPT, CRITIQUE_PROMPT
from database import DatabaseManager
from philosophers_data import PHILOSOPHERS_DATA
from show_widgets import BetDialog, JuryEvaluationWidget

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?…])\s+')


class SpeechThread(QThread):
    """Поток для распознавания речи с настройками для длинных фраз"""
    status_updated = Signal(str)
    text_recognized = Signal(str)
    error_occurred = Signal(str)
    finished_listening = Signal()

    def run(self):
        recognizer = sr.Recognizer()


        recognizer.energy_threshold = 300  # Чувствительность микрофона
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 1.2  # Ждем 1.2 сек тишины перед тем как закончить 
        

        try:
            with sr.Microphone() as source:
                self.status_updated.emit("Калибровка шума...")
                recognizer.adjust_for_ambient_noise(source, duration=0.8)

                self.status_updated.emit("Говорите! (Я слушаю...)")

                # phrase_time_limit=20 -> даем до 20 секунд на одну фразу
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=20)

                self.status_updated.emit("Обрабатываю...")

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


class VoiceInputDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, title="Ваш ход", label_text="Введите аргумент:", is_tutorial=False):
        super().__init__(parent)
        
        self.resize(600, 200)

        if is_tutorial:

            self.setWindowFlags(Qt.WindowType.Widget | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setModal(False)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
            self.setModal(True)

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

        # Кнопка ОТПРАВИТЬ 
        self.send_btn = QtWidgets.QPushButton("ОТПРАВИТЬ ОТВЕТ")
        self.send_btn.setObjectName("SendBtn")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setMinimumHeight(50)

        bottom_layout.addLayout(mic_layout, stretch=1)
        bottom_layout.addWidget(self.send_btn, stretch=3)

        layout.addLayout(bottom_layout)

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

class FlippableLabel(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._flipped = False

    def setFlipped(self, flipped):
        self._flipped = flipped
        self.update()

    def paintEvent(self, event):
        if not self._flipped:
            super().paintEvent(event)
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        
        # Отражаем по горизонтали
        painter.translate(self.width(), 0)
        painter.scale(-1, 1)
        
        if self.pixmap():
            # Рисуем пиксмап с учетом масштабирования (setScaledContents=True)
            if self.hasScaledContents():
                painter.drawPixmap(self.rect(), self.pixmap())
            else:
                super().paintEvent(event) # fall back if not scaled
        else:
            super().paintEvent(event)
        painter.end()

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
        vbox.setContentsMargins(30, 20, 30, 30) 
        vbox.setSpacing(10)
        

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
            vbox.setContentsMargins(30, 40, 30, 30) 
            self.action_btn.setText("ЗАРЕГИСТРИРОВАТЬСЯ")
            self.toggle_btn.setText("Уже есть аккаунт? Войти")
            self.nick_input.show()
        else:
            self.mode = "login"
            self.auth_container.setFixedSize(400, 310)
            self.title_lbl.setText("ВХОД")
            vbox.setContentsMargins(30, 20, 30, 30) 
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
        header_frame.setFixedSize(846, 70) 
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
        self.title_lbl.setStyleSheet("margin-bottom: 0px; font-size: 28px;")
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


class TutorialOverlayWidget(QtWidgets.QWidget):
    tutorial_finished = Signal()

    def __init__(self, parent=None, steps=None):
        super().__init__(parent)
        self.steps = steps or []
        self.current_step = 0

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
        self.info_box.raise_() 
        self.update() 

    def next_step(self):
        self.current_step += 1
        self.update_step()

    def finish(self):
        self.hide()
        self.tutorial_finished.emit()

    def mousePressEvent(self, event):
        self.next_step()

    def position_info_box(self, step):
        target = step.get("widget")
        direction = step.get("direction", "bottom")
        
        if not target or not target.isVisible():
            self.info_box.move((self.width() - self.info_box.width()) // 2, (self.height() - self.info_box.height()) // 2)
            self.info_box.show()
            return

        target_pos = target.mapToGlobal(QtCore.QPoint(0, 0))
        overlay_pos = self.mapFromGlobal(target_pos)
        
        tw, th = target.width(), target.height()
        iw, ih = self.info_box.width(), self.info_box.height()
        
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

        ix = max(10, min(ix, self.width() - iw - 10))
        iy = max(10, min(iy, self.height() - ih - 10))
        
        self.info_box.move(ix, iy)
        self.info_box.show()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        path = QtGui.QPainterPath()
        path.addRect(QtCore.QRectF(self.rect()))
        
        hole_rect = None
        
        if self.current_step < len(self.steps):
            step = self.steps[self.current_step]
            target = step.get("widget")
            
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
                
                hole_path = QtGui.QPainterPath()
                hole_path.addRoundedRect(hole_rect, 10, 10)
                path = path.subtracted(hole_path)

        painter.fillPath(path, QtGui.QColor(0, 0, 0, 180))
        
        if hole_rect:
            pen = QtGui.QPen(QtGui.QColor("#d4af37"))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(hole_rect, 10, 10)

class ShopScreen(QtWidgets.QWidget):
    back_requested = Signal()
    buy_requested = Signal(str, int) 
    details_requested = Signal(str) 

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
            bio_btn.setProperty("class", "back") 
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
            if col > 1:
                col = 0
                row += 1


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

        main_layout.addLayout(left_layout, 1) 
        main_layout.addWidget(scroll_area, 2) 
        
    def load_philosopher(self, name):
        data = PHILOSOPHERS_DATA.get(name)
        if not data: return
        
        pixmap = QtGui.QPixmap(data["img"])
        if not pixmap.isNull():
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


class SettingsWidget(QtWidgets.QWidget):
    """
    Экран настроек приложения.

    Стилистика: академическая (gold/sepia), идентичная главному меню.
    Содержит:
      - QComboBox  — выбор микрофона (через QMediaDevices)
      - QSlider    — громкость голоса оппонента (TTS), 0–100 %
      - QSlider    — громкость фоновой музыки, 0–100 %
      - QComboBox  — скорость речи (Медленная / Обычная / Быстрая)
      - QSpinBox   — размер шрифта субтитров, 14–24 px
      - QPushButton «Сохранить и вернуться в меню»

    Сигнал `saved` эмитируется после записи настроек в SettingsManager,
    чтобы AppController мог немедленно применить их к активным объектам
    (pygame.mixer, QAudioOutput и т.д.).
    """

    saved = Signal()  # сигнал, который AppController ловит для сохранения
    music_volume_changed = Signal(int)  # для предпрослушивания громкости музыки
    tts_volume_changed = Signal(int)    # для предпрослушивания громкости голоса
    test_voice_requested = Signal()     # сигнал для запуска тестовой фразы

    # Общий стиль контейнера и элементов
    _CONTAINER_SS = """
        QFrame#SettingsContainer {
            background-color: rgba(20, 20, 30, 245);
            border-radius: 16px;
            border: 2px solid #a67c00;
        }
    """
    _LABEL_SS = "color: #d4af37; font-size: 15px; font-weight: bold; background: transparent; border: none;"
    _VALUE_SS = "color: #ffffff; font-size: 14px; background: transparent; border: none;"
    _COMBO_SS = """
        QComboBox {
            background-color: rgba(30, 30, 46, 220);
            color: #ffffff;
            border: 2px solid #a67c00;
            border-radius: 8px;
            padding: 4px 12px;
            font-size: 16px;
            font-weight: bold;
            min-height: 42px;
        }
        QComboBox::drop-down { border-left: 1px solid #a67c00; width: 28px; }
        QComboBox QAbstractItemView {
            background: #1e1e2e;
            color: #ffffff;
            selection-background-color: #a67c00;
            selection-color: #3e2723;
            outline: none;
        }
    """
    _SLIDER_SS = """
        QSlider::groove:horizontal {
            height: 8px;
            background: rgba(80,60,20,150);
            border-radius: 4px;
        }
        QSlider::sub-page:horizontal {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #a67c00,stop:1 #d4af37);
            border-radius: 4px;
        }
        QSlider::handle:horizontal {
            background: #d4af37;
            border: 2px solid #a67c00;
            width: 18px;
            height: 18px;
            margin: -5px 0;
            border-radius: 9px;
        }
        QSlider::handle:horizontal:hover { background: #ffca28; }
    """
    _SPINBOX_SS = """
        QSpinBox {
            background-color: rgba(30, 30, 46, 220);
            color: #ffffff;
            border: 2px solid #a67c00;
            border-radius: 8px;
            padding: 4px 10px;
            font-size: 16px;
            font-weight: bold;
            min-width: 100px;
            min-height: 42px;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            width: 24px;
            background: rgba(164, 124, 0, 100);
            border: none;
        }
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {
            background: #a67c00;
        }
    """
    _SAVE_BTN_SS = """
        QPushButton {
            background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #d4af37,stop:1 #a67c00);
            color: #3e2723;
            font-size: 18px;
            font-weight: bold;
            border-radius: 10px;
            border: 2px solid #a67c00;
            padding: 12px 30px;
            outline: none;
        }
        QPushButton:hover {
            background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #edd27a,stop:1 #d4af37);
        }
        QPushButton:pressed { background-color: #a67c00; }
    """
    _TEST_BTN_SS = """
        QPushButton {
            background-color: rgba(164, 124, 0, 80);
            color: #ffca28;
            font-size: 13px;
            font-weight: bold;
            border-radius: 6px;
            border: 1px solid #a67c00;
            padding: 4px 10px;
            outline: none;
        }
        QPushButton:hover { background-color: rgba(164, 124, 0, 150); }
        QPushButton:pressed { background-color: #a67c00; color: #3e2723; }
    """

    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SettingsScreen")
        self.setStyleSheet(
            "#SettingsScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }"
        )
        self._build_ui()
        self._load_from_settings()

    # ------------------------------------------------------------------
    # Построение интерфейса
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # ── Центральный контейнер ──────────────────────────────────────
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("SettingsContainer")
        self.container.setStyleSheet(self._CONTAINER_SS)
        self.container.setFixedSize(680, 700)  # Увеличили высоту до 700, чтобы не было «нахлёста»

        outer.addWidget(self.container)

        vbox = QtWidgets.QVBoxLayout(self.container)
        vbox.setContentsMargins(50, 30, 50, 30)
        vbox.setSpacing(10)  # Уменьшили общий шаг, будем добавлять отступы вручную

        # ── Заголовок ─────────────────────────────────────────────────
        title = QtWidgets.QLabel("⚙  НАСТРОЙКИ")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size: 30px; font-weight: 900; color: #ffca28; "
            "background: transparent; border: none; "
            "font-family: 'Times New Roman', serif; letter-spacing: 3px;"
        )
        vbox.addWidget(title)

        sep = self._make_separator()
        vbox.addWidget(sep)
        vbox.addSpacing(10)

        # ── 1. Выбор микрофона ────────────────────────────────────────
        vbox.addLayout(self._make_label_row("🎙  Микрофон"))
        self.mic_combo = QtWidgets.QComboBox()
        self.mic_combo.setStyleSheet(self._COMBO_SS)
        self.mic_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._populate_microphones()
        vbox.addWidget(self.mic_combo)
        vbox.addSpacing(10)

        # ── 2. Громкость TTS ──────────────────────────────────────────
        self._tts_val_lbl = QtWidgets.QLabel("80 %")
        self._tts_val_lbl.setStyleSheet(self._VALUE_SS)
        self._tts_val_lbl.setFixedWidth(48)
        
        tts_row = self._make_label_row("🔊  Громкость голоса оппонента", self._tts_val_lbl)
        # Добавляем кнопку теста в ту же строку, что и заголовок
        self.test_btn = QtWidgets.QPushButton("▶ ТЕСТ")
        self.test_btn.setStyleSheet(self._TEST_BTN_SS)
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self.test_voice_requested.emit)
        tts_row.insertWidget(2, self.test_btn) # Вставляем перед значением %
        tts_row.insertSpacing(3, 10)
        
        vbox.addLayout(tts_row)
        self.tts_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.tts_slider.setRange(0, 100)
        self.tts_slider.setStyleSheet(self._SLIDER_SS)
        self.tts_slider.valueChanged.connect(
            lambda v: (self._tts_val_lbl.setText(f"{v} %"), self.tts_volume_changed.emit(v))
        )
        vbox.addWidget(self.tts_slider)
        vbox.addSpacing(10)

        # ── 3. Громкость музыки ───────────────────────────────────────
        self._music_val_lbl = QtWidgets.QLabel("15 %")
        self._music_val_lbl.setStyleSheet(self._VALUE_SS)
        self._music_val_lbl.setFixedWidth(48)
        vbox.addLayout(self._make_label_row("🎵  Громкость фоновой музыки", self._music_val_lbl))
        self.music_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.music_slider.setRange(0, 100)
        self.music_slider.setStyleSheet(self._SLIDER_SS)
        self.music_slider.valueChanged.connect(
            lambda v: (self._music_val_lbl.setText(f"{v} %"), self.music_volume_changed.emit(v))
        )
        vbox.addWidget(self.music_slider)
        vbox.addSpacing(10)

        # ── 4. Скорость речи ──────────────────────────────────────────
        vbox.addLayout(self._make_label_row("⏩  Скорость речи оппонента"))
        self.rate_combo = QtWidgets.QComboBox()
        self.rate_combo.addItems(["Медленная", "Обычная", "Быстрая"])
        self.rate_combo.setStyleSheet(self._COMBO_SS)
        self.rate_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        vbox.addWidget(self.rate_combo)
        vbox.addSpacing(10)

        # ── 5. Размер шрифта субтитров ────────────────────────────────
        vbox.addLayout(self._make_label_row("🔡  Размер шрифта субтитров (14–24 px)"))
        spin_row = QtWidgets.QHBoxLayout()
        self.font_spin = QtWidgets.QSpinBox()
        self.font_spin.setRange(14, 24)        # СТРОГОЕ ограничение
        self.font_spin.setSuffix(" px")
        self.font_spin.setStyleSheet(self._SPINBOX_SS)
        spin_row.addWidget(self.font_spin)
        spin_row.addStretch()
        vbox.addLayout(spin_row)

        vbox.addSpacing(20)

        # ── Кнопка «Сохранить» ────────────────────────────────────────
        self.save_btn = QtWidgets.QPushButton("💾  Сохранить и вернуться в меню")
        self.save_btn.setStyleSheet(self._SAVE_BTN_SS)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setMinimumHeight(55)
        self.save_btn.clicked.connect(self._on_save)
        vbox.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        vbox.addStretch(1)


    # ------------------------------------------------------------------
    # Вспомогательные методы построения UI
    # ------------------------------------------------------------------

    @staticmethod
    def _make_label_row(text: str, right_widget: QtWidgets.QWidget = None) -> QtWidgets.QHBoxLayout:
        """Создаёт горизонтальный ряд: иконка+текст слева, опциональный виджет справа."""
        row = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(
            "color: #d4af37; font-size: 15px; font-weight: bold; "
            "background: transparent; border: none;"
        )
        row.addWidget(lbl)
        if right_widget:
            row.addStretch()
            row.addWidget(right_widget)
        return row

    @staticmethod
    def _make_separator() -> QtWidgets.QFrame:
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid rgba(164, 124, 0, 120);")
        sep.setFixedHeight(1)
        return sep

    def _populate_microphones(self):
        """Заполняет список микрофонов через QMediaDevices."""
        self.mic_combo.clear()
        self.mic_combo.addItem("По умолчанию", userData="")
        devices = QMediaDevices.audioInputs()
        for dev in devices:
            self.mic_combo.addItem(dev.description(), userData=dev.description())

    # ------------------------------------------------------------------
    # Загрузка / сохранение настроек
    # ------------------------------------------------------------------

    def _load_from_settings(self):
        """Восстанавливает состояние всех контролов из SettingsManager."""
        # Микрофон
        saved_mic = self.settings.microphone_name
        idx = self.mic_combo.findData(saved_mic)
        self.mic_combo.setCurrentIndex(max(0, idx))

        # Слайдеры
        self.tts_slider.setValue(self.settings.tts_volume)
        self.music_slider.setValue(self.settings.music_volume)

        # Скорость речи
        rate_idx = self.rate_combo.findText(self.settings.speech_rate)
        self.rate_combo.setCurrentIndex(max(0, rate_idx))

        # Шрифт субтитров
        self.font_spin.setValue(self.settings.subtitle_font_size)

    def showEvent(self, event):
        """Каждый раз при показе экрана обновляем список устройств и значения."""
        self._populate_microphones()
        self._load_from_settings()
        super().showEvent(event)

    def _on_save(self):
        """Записывает выбранные значения в SettingsManager, затем эмитирует `saved`."""
        # Микрофон
        self.settings.microphone_name = self.mic_combo.currentData() or ""

        # Громкости
        self.settings.tts_volume   = self.tts_slider.value()
        self.settings.music_volume = self.music_slider.value()

        # Скорость речи
        self.settings.speech_rate = self.rate_combo.currentText()

        # Размер шрифта
        self.settings.subtitle_font_size = self.font_spin.value()

        # Информируем контроллер
        self.saved.emit()


class SetupWidget(QtWidgets.QWidget):
    start_requested = Signal(bool, str, str, int, int) # is_critique, raw_topic, filepath, time_limit, rounds
    start_show_requested = Signal(str, str, str, str, int) # topic, p1, p2, role, rounds
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.critique_filepath = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SetupScreen")
        self.setStyleSheet("#SetupScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }")
        
        setup_main_layout = QtWidgets.QVBoxLayout(self)
        setup_main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        setup_container = QtWidgets.QFrame()
        setup_container.setFixedSize(640, 800)
        setup_container.setStyleSheet("QFrame { background-color: rgba(20, 20, 30, 240); border-radius: 15px; border: 2px solid #a67c00; }")
        
        vbox_setup = QtWidgets.QVBoxLayout(setup_container)
        vbox_setup.setContentsMargins(40, 30, 40, 40)
        vbox_setup.setSpacing(15)
        
        setup_title = QtWidgets.QLabel("НАСТРОЙКА ПОЕДИНКА", setup_container)
        setup_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        setup_title.setStyleSheet("font-size: 34px; font-weight: bold; color: #ffca28; border: none; background: transparent; margin-bottom: 10px; min-height: 50px;")
        
        self.mode_combo = QtWidgets.QComboBox(setup_container)
        self.mode_combo.addItems(["Тренировочные дебаты", "Критика научного тезиса", "Шоу: ИИ vs ИИ"])
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

        settings_layout = QtWidgets.QFormLayout()
        settings_layout.setHorizontalSpacing(20)
        settings_layout.setVerticalSpacing(15)
        
        lbl_style = "color: #dcdcdc; font-size: 18px; font-weight: bold; background: transparent; border: none;"
        
        self.time_limit_combo = QtWidgets.QComboBox()
        self.time_limit_combo.setStyleSheet(self.mode_combo.styleSheet())
        self.time_limit_combo.addItem("30 сек", 30)
        self.time_limit_combo.addItem("60 сек", 60)
        self.time_limit_combo.addItem("120 сек", 120)
        self.time_limit_combo.addItem("5 минут", 300)
        self.time_limit_combo.addItem("Безлимит", 0)
        self.time_limit_combo.setCurrentIndex(1)
        
        self.time_lbl = QtWidgets.QLabel("Время на ответ:")
        self.time_lbl.setStyleSheet(lbl_style)
        settings_layout.addRow(self.time_lbl, self.time_limit_combo)
        
        self.rounds_spinbox = QtWidgets.QSpinBox()
        self.rounds_spinbox.setRange(1, 10)
        self.rounds_spinbox.setValue(3)
        self.rounds_spinbox.setStyleSheet("""
            QSpinBox { background-color: rgba(30,30,40,200); color: white; border: 2px solid #a67c00; border-radius: 8px; padding: 10px; font-size: 18px; font-weight: bold;}
            QSpinBox::up-button, QSpinBox::down-button { width: 30px; background: rgba(164, 124, 0, 100); border: none; }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #a67c00; }
        """)
        
        self.rounds_lbl = QtWidgets.QLabel("Кол-во раундов:")
        self.rounds_lbl.setStyleSheet(lbl_style)
        settings_layout.addRow(self.rounds_lbl, self.rounds_spinbox)

        self.confirm_start_btn = QtWidgets.QPushButton("К БАРЬЕРУ", setup_container)
        self.confirm_start_btn.setFixedSize(380, 60)
        self.confirm_start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.confirm_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_start_btn.setStyleSheet("""
            QPushButton { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00); color: #3e2723; font-size: 24px; font-weight: bold; border-radius: 10px; border: none; }
            QPushButton:hover { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37); }
            QPushButton:disabled { background-color: #555555; color: #aaaaaa; }
        """)
        self.confirm_start_btn.clicked.connect(self.on_start_clicked)
        
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
        self.setup_back_btn.clicked.connect(self.back_requested.emit)
        
        # === ШОУ-СПЕЦИФИЧНЫЕ ЭЛЕМЕНТЫ ===
        show_lbl_style = "color: #dcdcdc; font-size: 18px; font-weight: bold; background: transparent; border: none;"
        combo_style = self.mode_combo.styleSheet()

        self.show_settings_layout = QtWidgets.QFormLayout()
        self.show_settings_layout.setHorizontalSpacing(20)
        self.show_settings_layout.setVerticalSpacing(12)

        from agents import OPPONENTS_CONFIG
        opponent_names = [name for name in OPPONENTS_CONFIG.keys() if name != "Академический Рецензент"]

        self.show_p1_lbl = QtWidgets.QLabel("Оппонент 1:")
        self.show_p1_lbl.setStyleSheet(show_lbl_style)
        self.show_p1_combo = QtWidgets.QComboBox(setup_container)
        self.show_p1_combo.addItems(opponent_names)
        self.show_p1_combo.setCurrentIndex(0)
        self.show_p1_combo.setStyleSheet(combo_style)
        self.show_settings_layout.addRow(self.show_p1_lbl, self.show_p1_combo)

        self.show_p2_lbl = QtWidgets.QLabel("Оппонент 2:")
        self.show_p2_lbl.setStyleSheet(show_lbl_style)
        self.show_p2_combo = QtWidgets.QComboBox(setup_container)
        self.show_p2_combo.addItems(opponent_names)
        self.show_p2_combo.setCurrentIndex(min(1, len(opponent_names) - 1))
        self.show_p2_combo.setStyleSheet(combo_style)
        self.show_settings_layout.addRow(self.show_p2_lbl, self.show_p2_combo)

        self.show_role_lbl = QtWidgets.QLabel("Ваша роль:")
        self.show_role_lbl.setStyleSheet(show_lbl_style)
        self.show_role_combo = QtWidgets.QComboBox(setup_container)
        self.show_role_combo.addItems(["Зритель (автономные дебаты)", "Жюри (я оцениваю)"])
        self.show_role_combo.setStyleSheet(combo_style)
        self.show_settings_layout.addRow(self.show_role_lbl, self.show_role_combo)

        # ====== Кнопка перемены мест ======
        self.swap_btn = QtWidgets.QPushButton("⇅ Поменять порядок выступления")
        self.swap_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(60, 60, 80, 180);
                color: #dcdcdc; font-size: 13px;
                border: 1px solid #777; border-radius: 5px;
                padding: 5px;
            }
            QPushButton:hover { background-color: #444466; }
        """)
        self.swap_btn.clicked.connect(self._swap_show_opponents)
        self.show_settings_layout.addRow("", self.swap_btn)

        # Контейнер для шоу-элементов (скрыт по умолчанию)
        self.show_settings_widget = QtWidgets.QWidget()
        self.show_settings_widget.setLayout(self.show_settings_layout)
        self.show_settings_widget.hide()

        vbox_setup.addWidget(setup_title)
        vbox_setup.addWidget(self.mode_combo)
        vbox_setup.addWidget(self.topic_input)
        vbox_setup.addWidget(self.file_upload_wrapper)
        vbox_setup.addLayout(settings_layout)
        vbox_setup.addWidget(self.show_settings_widget)
        vbox_setup.addSpacing(20)
        vbox_setup.addWidget(self.confirm_start_btn,alignment=Qt.AlignmentFlag.AlignCenter)
        vbox_setup.addWidget(self.setup_back_btn,alignment=Qt.AlignmentFlag.AlignCenter)
    
        setup_main_layout.addWidget(setup_container)

    def _swap_show_opponents(self):
        p1_idx = self.show_p1_combo.currentIndex()
        p2_idx = self.show_p2_combo.currentIndex()
        self.show_p1_combo.setCurrentIndex(p2_idx)
        self.show_p2_combo.setCurrentIndex(p1_idx)

    @QtCore.Slot(int)
    def on_mode_changed(self, index):
        if index == 1:  # Критика
            self.file_upload_wrapper.show()
            self.topic_input.setPlaceholderText("Введите ваш тезис или загрузите файл с текстом работы...")
            self.time_lbl.hide()
            self.time_limit_combo.hide()
            self.rounds_lbl.hide()
            self.rounds_spinbox.hide()
            self.show_settings_widget.hide()
        elif index == 2:  # Шоу
            self.file_upload_wrapper.hide()
            self.topic_input.setPlaceholderText("Введите тему для шоу-дебатов...")
            self.time_lbl.hide()
            self.time_limit_combo.hide()
            self.rounds_lbl.show()
            self.rounds_spinbox.show()
            self.show_settings_widget.show()
            self.confirm_start_btn.setText("НАЧАТЬ ШОУ")
        else:  # Тренировочные
            self.file_upload_wrapper.hide()
            self.topic_input.setPlaceholderText("Введите тему дебатов...")
            self.time_lbl.show()
            self.time_limit_combo.show()
            self.rounds_lbl.show()
            self.rounds_spinbox.show()
            self.show_settings_widget.hide()
            self.confirm_start_btn.setText("К БАРЬЕРУ")

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
    def on_start_clicked(self):
        mode_index = self.mode_combo.currentIndex()
        if mode_index == 2:  # Шоу
            p1 = self.show_p1_combo.currentText()
            p2 = self.show_p2_combo.currentText()
            if p1 == p2:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Ошибка", "Нельзя выбрать одного и того же оппонента!")
                return
            raw_topic = self.topic_input.text().strip()
            role = "spectator" if self.show_role_combo.currentIndex() == 0 else "jury"
            rounds = self.rounds_spinbox.value()
            raw_topic = self.topic_input.text().strip()
            role = "spectator" if self.show_role_combo.currentIndex() == 0 else "jury"
            rounds = self.rounds_spinbox.value()
            self.start_show_requested.emit(raw_topic, p1, p2, role, rounds)
        else:
            is_critique = mode_index == 1
            raw_topic = self.topic_input.text().strip()
            filepath = getattr(self, "critique_filepath", "") or ""
            time_limit = self.time_limit_combo.currentData()
            rounds = self.rounds_spinbox.value()
            self.start_requested.emit(is_critique, raw_topic, filepath, time_limit, rounds)

    def set_loading(self, is_loading, text="Обработка..."):
        self.confirm_start_btn.setEnabled(not is_loading)
        if is_loading:
            self.confirm_start_btn.setText(text)
        else:
            self.confirm_start_btn.setText("К БАРЬЕРУ")

class AppController(QtWidgets.QMainWindow):
    request_generation = Signal(str, object, dict)
    request_speak = Signal(list)
    request_set_volume = Signal(float)  # Новый сигнал для громкости голоса
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
        "Мюррей Ротбард": "assets/rothbard.png",
        "Томас Гоббс": "assets/hobbes.png",
        "Сёрен Кьеркегор": "assets/kierkegaard.png",
        "Карл Поппер": "assets/popper.png",
        "Иеремия Бентам": "assets/bentham.png",
        "Жан-Жак Руссо": "assets/rousseau.png",
        "Дэвид Юм": "assets/hume.png",
        "Марк Аврелий": "assets/aurelius.png",
        "Артур Шопенгауэр": "assets/schopenhauer.png",
        "Людвиг Витгенштейн": "assets/wittgenstein.png",
        "Секст Эмпирик": "assets/empiricus.png"
    }


    def __init__(self):
        super().__init__()
        self.base_size = QtCore.QSize(1024, 768)
        self.initial_geometries = {}
        self.initial_font_sizes = {}
        self.is_fullscreen = False

        # Настройки — инициализируем самыми первыми
        self.settings = SettingsManager()

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
        self.clash_turns_done = 0

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
        self.request_set_volume.connect(self.speaker_worker.set_volume)
        self.request_stream_start.connect(self.speaker_worker.start_stream)
        self.request_stream_chunk.connect(self.speaker_worker.append_stream_text)
        self.request_stream_finish.connect(self.speaker_worker.finish_stream)
        self.request_set_voice.connect(self.speaker_worker.set_voice)
        self.speaker_thread.start()

    def _set_voice_for_speaker(self, speaker_name):
        voice_id = "ru-RU-SvetlanaNeural" if speaker_name == "Модератор" else "ru-RU-DmitryNeural"
        # По умолчанию берём скорость из настроек пользователя
        rate = self.settings.get_edge_tts_rate()
        pitch = "+0Hz"

        if speaker_name in PHILOSOPHERS_DATA:
            data = PHILOSOPHERS_DATA[speaker_name]
            voice_id = data.get("voice", "ru-RU-DmitryNeural")
            # Для персонажа применяем пользовательскую скорость поверх базовой
            base_rate = data.get("rate", "-15%")
            # Если пользователь выбрал «Быстрая» — ускоряем чуть относительно базы
            user_rate = self.settings.get_edge_tts_rate()
            rate = user_rate if user_rate != "-15%" else base_rate
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

        # Кнопка Настройки
        self.settings_btn = QtWidgets.QPushButton("НАСТРОЙКИ", self.main_menu)
        self.settings_btn.setGeometry((win_w - btn_w) // 2, 370 + 4*(btn_h + 15), btn_w, btn_h)
        self.settings_btn.setStyleSheet(btn.styleSheet())
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

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
        # Replace QLabel with FlippableLabel for user and opp avatars
        old_user_label = self.debate_screen.findChild(QtWidgets.QLabel, "user")
        if old_user_label:
            self.debate_screen.user = FlippableLabel(self.debate_screen)
            self.debate_screen.user.setObjectName("user")
            self.debate_screen.user.setGeometry(old_user_label.geometry())
            self.debate_screen.user.setAlignment(old_user_label.alignment())
            self.debate_screen.user.setStyleSheet(old_user_label.styleSheet())
            self.debate_screen.user.setScaledContents(True)
            old_user_label.deleteLater()

        old_opp_label = self.debate_screen.findChild(QtWidgets.QLabel, "opp")
        if old_opp_label:
            self.debate_screen.opp = FlippableLabel(self.debate_screen)
            self.debate_screen.opp.setObjectName("opp")
            self.debate_screen.opp.setGeometry(old_opp_label.geometry())
            self.debate_screen.opp.setAlignment(old_opp_label.alignment())
            self.debate_screen.opp.setStyleSheet(old_opp_label.styleSheet())
            self.debate_screen.opp.setScaledContents(True)
            old_opp_label.deleteLater()

        # 3. НАСТРОЙКИ (SETUP SCREEN)
        self.setup_screen = SetupWidget()
        self.setup_screen.back_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_menu))
        self.setup_screen.start_requested.connect(self.start_debate_with_topic)
        self.setup_screen.start_show_requested.connect(self.start_show_debate)

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

        # 8. ЭКРАН НАСТРОЕК
        self.settings_screen = SettingsWidget(self.settings)
        self.settings_screen.saved.connect(self.apply_settings)
        # Реал-тайм изменение громкости для предпросмотра
        self.settings_screen.music_volume_changed.connect(self._on_music_volume_preview)
        self.settings_screen.tts_volume_changed.connect(self._on_tts_volume_preview)
        self.settings_screen.test_voice_requested.connect(self._on_test_voice)

        # 9. ЭКРАН ОЦЕНИВАНИЯ ЖЮРИ
        self.jury_eval_screen = JuryEvaluationWidget()
        self.jury_eval_screen.evaluation_complete.connect(self._handle_show_jury_complete)
        self.jury_eval_screen.back_requested.connect(lambda: self.stacked_widget.setCurrentWidget(self.main_menu))

        # Применяем громкость TTS сразу при инициализации
        self._on_tts_volume_preview(self.settings.tts_volume)

        self.stacked_widget.addWidget(self.auth_screen)
        self.stacked_widget.addWidget(self.main_menu)
        self.stacked_widget.addWidget(self.setup_screen)
        self.stacked_widget.addWidget(self.debate_screen)
        self.stacked_widget.addWidget(self.profile_screen)
        self.stacked_widget.addWidget(self.shop_screen)
        self.stacked_widget.addWidget(self.details_screen)
        self.stacked_widget.addWidget(self.settings_screen)
        self.stacked_widget.addWidget(self.jury_eval_screen)

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
        self.profile_btn.clicked.connect(self.go_to_profile)
        self.shop_btn.clicked.connect(self.go_to_shop)
        self.tutorial_btn.clicked.connect(self.start_tutorial)
        self.settings_btn.clicked.connect(self.go_to_settings)
        self.settings_btn.clicked.connect(self.go_to_settings)

        self.stacked_widget.setCurrentWidget(self.auth_screen)

        # Фоновая музыка — берём громкость из сохранённых настроек (с коррекцией восприятия)
        self.bg_player = QMediaPlayer()
        self.bg_audio = QAudioOutput()
        # Используем степенную зависимость (квадратичную) для сбалансированного изменения
        music_vol = (self.settings.music_volume / 100.0) ** 2
        self.bg_audio.setVolume(music_vol)
        self.bg_player.setAudioOutput(self.bg_audio)
        self.bg_player.setLoops(QMediaPlayer.Loops.Infinite)

        # Фоновая предзагрузка тяжелых моделей во время нахождения в меню
        threading.Thread(target=self._preload_heavy_models, daemon=True).start()

    def _preload_heavy_models(self):
        """Асинхронно предзагружает тяжелые ML модели, чтобы избежать зависаний перед дебатами"""
        print("PRELOAD: Начинаю фоновую загрузку моделей...")
        try:

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

    # --- ЭКРАН НАСТРОЕК ---

    @QtCore.Slot()
    def go_to_settings(self):
        """Переходит на экран настроек."""
        self.stacked_widget.setCurrentWidget(self.settings_screen)

    @QtCore.Slot()
    def apply_settings(self):
        """
        Применяет настройки, сохранённые SettingsWidget, к активным объектам
        и возвращает пользователя в главное меню.
        """
        # Громкость фоновой музыки (квадратичная коррекция)
        music_vol = (self.settings.music_volume / 100.0) ** 2
        self.bg_audio.setVolume(music_vol)

        # Громкость TTS (линейная шкала для голоса, чтобы не было слишком тихо)
        tts_vol_float = self.settings.tts_volume / 100.0
        self.request_set_volume.emit(tts_vol_float)
        
        # Шрифт субтитров применяется через update_subtitles при следующем вызове
        # (стиль хранится в QLabel в debate_screen — обновляем немедленно если виджет доступен)
        if hasattr(self, 'debate_screen') and hasattr(self.debate_screen, 'subtitleLabel'):
            fsize = self.settings.subtitle_font_size
            self.debate_screen.subtitleLabel.setStyleSheet(
                f"background-color: rgba(0,0,100,0.8); color: #E0E0E0; "
                f"border-radius: 10px; padding: 15px; font-size: {fsize}px;"
            )

        # Возвращаемся в меню
        self.stacked_widget.setCurrentWidget(self.main_menu)

    @QtCore.Slot(int)
    def _on_music_volume_preview(self, value):
        """Мгновенно меняет громкость музыки при движении слайдера (с коррекцией)."""
        if hasattr(self, 'bg_audio'):
            # Квадратичная коррекция (x^2) вместо кубической — чтобы не было слишком тихо на 20-40%
            self.bg_audio.setVolume((value / 100.0) ** 2)

    @QtCore.Slot(int)
    def _on_tts_volume_preview(self, value):
        """Мгновенно меняет громкость голоса (линейно для четкости)."""
        self.request_set_volume.emit(value / 100.0)

    @QtCore.Slot()
    def _on_test_voice(self):
        """Проигрывает тестовую фразу для проверки громкости голоса."""
        voice_id = "ru-RU-DmitryNeural"
        rate = self.settings.get_edge_tts_rate()
        pitch = "+0Hz"
        self.request_speak.emit(["Проверка громкости голоса оппонента."])
        # Убеждаемся, что голос настроен правильно (на случай если это первый запуск)
        self.request_set_voice.emit(voice_id, rate, pitch)

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
            self.handle_login(email, pwd) 
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
                self.go_to_shop() 
            else:
                QtWidgets.QMessageBox.warning(self, "Ошибка", msg)

    @QtCore.Slot(str)
    def go_to_details(self, name):
        self.details_screen.load_philosopher(name)
        self.stacked_widget.setCurrentWidget(self.details_screen)

    @QtCore.Slot()
    def start_tutorial(self):
        self.stacked_widget.setCurrentWidget(self.debate_screen)
        
        self.debate_screen.topic.setText(" ТЕМА: В чём смысл жизни?")
        self.debate_screen.user.setText(self.current_user['nickname'] if self.current_user else "ИГРОК")
        
        self.debate_screen.opp.setText("")
        socrates_pixmap = QtGui.QPixmap("assets/socrates.png")
        if socrates_pixmap.isNull():
     
             socrates_pixmap = QtGui.QPixmap("assets/resized-socrates.png")
        if not socrates_pixmap.isNull():
             self.debate_screen.opp.setPixmap(socrates_pixmap)
             
        self.update_subtitles("Приветствую на Арене Разума! Я Сократ, и мы начинаем наши дебаты.")
        self.update_speaker_name("Сократ")
        
        # Ensure user avatar is not flipped for tutorial
        if hasattr(self.debate_screen.user, 'setFlipped'):
            self.debate_screen.user.setFlipped(False)

        self.tutorial_input_dialog = VoiceInputDialog(self.debate_screen, "Ваш ход", "Введите аргумент:", is_tutorial=True)

        self.tutorial_input_dialog.move((self.width() - 600) // 2, self.height() - 250)
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
                "action": lambda: self.tutorial_input_dialog.show() 
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
            
  
        self.update_subtitles("")
        self.update_speaker_name("")
        self.debate_screen.topic.setText("")
        self.stacked_widget.setCurrentWidget(self.main_menu)

    # --- МЕТОД ВЫЗОВА ОКНА ---
    def get_user_input(self, title, label):
        dialog = VoiceInputDialog(self, title, label)
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
        self.setup_screen.topic_input.clear()
        self.setup_screen.critique_filepath = None
        self.setup_screen.file_name_lbl.setText("")
        self.setup_screen.mode_combo.setCurrentIndex(0)
        self.setup_screen.show_settings_widget.hide()
        self.setup_screen.confirm_start_btn.setText("К БАРЬЕРУ")
        self.stacked_widget.setCurrentWidget(self.setup_screen)

    @QtCore.Slot(bool, str, str, int, int)
    def start_debate_with_topic(self, is_critique, raw_topic, filepath, time_limit, rounds_count):
        self.current_time_limit = time_limit
        self.current_rounds_count = rounds_count

        if is_critique:
            self.setup_screen.set_loading(True)
            QtWidgets.QApplication.processEvents()
            
            attached_text = ""
            if filepath:
                import document_parser
                attached_text = document_parser.extract_text(filepath)
                if attached_text.startswith("Ошибка"):
                    QtWidgets.QMessageBox.warning(self, "Ошибка файла", attached_text)
                    self.setup_screen.set_loading(False)
                    return
            
            self.critique_full_text = f"{raw_topic}\n\n{attached_text}".strip()
            
            if not self.critique_full_text:
                QtWidgets.QMessageBox.warning(self, "Пусто", "Введите тезис или прикрепите файл.")
                self.setup_screen.set_loading(False)
                return
            
            topic_label = raw_topic if raw_topic else "Защита научной работы"
            if len(topic_label) > 50: topic_label = topic_label[:50] + "..."
            
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
                # Снимаем отзеркаливание для обычных дебатов
                if hasattr(self.debate_screen.user, 'setFlipped'):
                    self.debate_screen.user.setFlipped(False)

            self.setup_screen.set_loading(False)
            self._finalize_debate_start(topic_label, "Академический Рецензент")
            return
            
        else:
            if not raw_topic:
                raw_topic = "Искусственный интеллект — благо или угроза?"

            self.setup_screen.set_loading(True, "Анализирую...")
            QtWidgets.QApplication.processEvents()

            self._formatter_thread = TopicFormatterThread(raw_topic, self.deepseek, parent=self)
            self._formatter_thread.topic_ready.connect(self._continue_debate_setup)
            self._formatter_thread.start()

    def _continue_debate_setup(self, formatted_topic):
        self.setup_screen.set_loading(False)

        if formatted_topic.startswith("Ошибка"):
            QtWidgets.QMessageBox.critical(self, "Ошибка", formatted_topic)
            return
        if formatted_topic == "ERROR":
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Тема не распознана.")
            return

        dialog = CustomConfirmDialog(self, formatted_topic)
        if dialog.exec():
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

            user_pixmap = QtGui.QPixmap("assets/user_avatar_no_bg.png")
            if not user_pixmap.isNull():
                self.debate_screen.user.setPixmap(user_pixmap)
                self.debate_screen.user.setScaledContents(True)
                # Снимаем отзеркаливание для обычных дебатов
                if hasattr(self.debate_screen.user, 'setFlipped'):
                    self.debate_screen.user.setFlipped(False)
            
            # Запуск дебатов
            self._finalize_debate_start(topic, opponent_name)
        else:
            feedback = self.get_user_input("Уточнение", "Что именно нужно изменить в теме?")
            if not feedback: 
                return
            
            self.setup_screen.set_loading(True, "Переписываю...")
            QtWidgets.QApplication.processEvents()
            
            current_input = f"Предыдущий вариант темы: {formatted_topic}. ПРАВКА ПОЛЬЗОВАТЕЛЯ: {feedback}"
            self._formatter_thread = TopicFormatterThread(current_input, self.deepseek, parent=self)
            self._formatter_thread.topic_ready.connect(self._continue_debate_setup)
            self._formatter_thread.start()

    def _finalize_debate_start(self, topic, opponent_name):
        self.stacked_widget.setCurrentWidget(self.debate_screen)

        self.opp_system_prompt = get_opponent_system_prompt(topic, opponent_name)
        self.deepseek.reset_stats()  
        user_name = self.current_user['nickname'] if self.current_user else "Вы"

        time_limit = getattr(self, "current_time_limit", 60)
        rounds = getattr(self, "current_rounds_count", 3)
        self.engine = DebateManager(topic, user_name, opponent_name, time_limit, rounds)
        
        self.clash_duration_sec = time_limit if time_limit > 0 else 9999
        
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
            queue = [
                {'type': 'generate_and_speak', 'prompt_func': self.engine.get_setup_prompt,
                 'speaker_name': 'Модератор', 'role': 'moderator'},
                {'type': 'speech', 'participant': self.engine.p1_name},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': f'Благодарю. Теперь слово для вступительной речи предоставляется {self.engine.p2_name}.'},
                {'type': 'speech', 'participant': self.engine.p2_name},
            ]
            
            is_first_clash = True
            for _ in range(rounds):
                queue.append({'type': 'clash_round', 'leader': self.engine.p1_name, 'is_first': is_first_clash})
                is_first_clash = False
                queue.append({'type': 'clash_round', 'leader': self.engine.p2_name, 'is_first': False})
                
            queue.extend([
                {'type': 'jury_questions'},
                {'type': 'summary_statement', 'participant': self.engine.p1_name},
                {'type': 'summary_statement', 'participant': self.engine.p2_name},
                {'type': 'final_verdict'},
                {'type': 'speak_only', 'speaker_name': 'Модератор', 'text': 'Игра окончена. Спасибо всем участникам дебатов за отличную игру. Переходим к результатам.'},
                {'type': 'end_debate'}
            ])
            self.action_queue.extend(queue)

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
            self.update_speaker_name(action['speaker_name'])
            self.update_subtitles(f"{action['speaker_name']} готовится...")
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
                self.update_speaker_name(participant_name)
                self.update_subtitles(f"{participant_name} обдумывает заключительное слово...")
                self.request_stream_start.emit()
                self.request_generation.emit(prompt, None, {
                    'speaker_name': participant_name, 'is_stream': True,
                    'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent'
                })

    def _handle_participant_speech(self, participant_name):
        if participant_name == self.engine.user_name:
            if hasattr(self, 'critique_full_text') and self.critique_full_text and self.engine.opponent_name == "Академический Рецензент":
                text = "Я готов защищать свою работу. Ознакомьтесь с ней, пожалуйста. Вот текст:\n\n" + self.critique_full_text
                self.engine._add_to_transcript(self.engine.user_name, text)
                self.update_speaker_name(self.engine.user_name)
                self.update_subtitles("Я готов защищать свою работу. Ознакомьтесь с ней, пожалуйста.")
                self.last_user_speech = text

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
                print("[RAG] Извлекаем контекст для философа...")
                rag_memory = get_philosopher_context(self.engine.opponent_name, self.last_user_speech, top_k=3)
                if hasattr(self, 'deepseek'):
                    self.deepseek.log_rag_citation(rag_memory)
                prompt =  f"\n\n[СПРАВОЧНАЯ ИНФОРМАЦИЯ ИЗ ТВОИХ ТРУДОВ]\n(Используй эти данные мягко и органично, только если они релевантны. Не цитируй дословно, если это вредит естественности диалога. Сохраняй свой живой характер.)\nКонтекст:\n{rag_memory}\n[КОНЕЦ ИНФОРМАЦИИ]" + raw_prompt 

                
            self._set_voice_for_speaker(self.engine.opponent_name)
            self.update_speaker_name(self.engine.opponent_name)
            self.update_subtitles("Размышляю...")
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
        is_show = metadata.get('is_show', False)

        # ═══ Обработка ШОУ ═══
        if is_show and callback == 'show_announce_winner':
            from agents import JURY_PROMPT
            import json
            try:
                data = json.loads(text)
                self.show_verdict_data = data
                self.verdict_data = data # Для PDF
                self._show_winner_name = data.get('winner', 'Ничья')
                
                # Запускаем цепочку оглашения (Баллы -> Победа)
                self.verdict_announcement_step = 'SCORES_MATTER'
                
                # Подменяем engine на время оглашения, чтобы работали анонсеры
                original_engine = self.engine
                self.engine = self.show_engine
                self._show_verdict_announcer_original_engine = original_engine
                
                self.update_speaker_name("Жюри")
                self._process_verdict_announcement() 
            except Exception as e:
                print(f"Error parsing show verdict: {e}")
                self._show_end_debate_screen("Ошибка при вынесении вердикта.")
            return

        if is_show and callback == 'show_handle_jury_questions':
            try:
                clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
                start_idx = clean.find('{')
                end_idx = clean.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    clean = clean[start_idx:end_idx+1]
                jq = json.loads(clean)
                # Жюри спрашивает P1
                q1 = jq.get('question_for_p1', 'Нет вопроса')
                q2 = jq.get('question_for_p2', 'Нет вопроса')
                self.show_engine._add_to_transcript('Жюри', f'Вопрос для {self.show_engine.p1_name}: {q1}')
                self.show_engine._add_to_transcript('Жюри', f'Вопрос для {self.show_engine.p2_name}: {q2}')
                # Вставляем в очередь ответы обоих
                q_actions = [
                    {'type': 'show_speak_only', 'speaker': 'Жюри', 'text': f'Вопрос для {self.show_engine.p1_name}: {q1}'},
                    {'type': 'show_generate', 'speaker': self.show_engine.p1_name, 'system': 'p1',
                     'prompt_func': 'jury_answer', 'prompt': self.show_engine.get_jury_answer_prompt(q1)},
                    {'type': 'show_speak_only', 'speaker': 'Жюри', 'text': f'Вопрос для {self.show_engine.p2_name}: {q2}'},
                    {'type': 'show_generate', 'speaker': self.show_engine.p2_name, 'system': 'p2',
                     'prompt_func': 'jury_answer', 'prompt': self.show_engine.get_jury_answer_prompt(q2)},
                ]
                self.show_action_queue = q_actions + self.show_action_queue
                self._show_process_next()
            except Exception as e:
                print(f"SHOW ERROR parsing jury questions: {e}. Raw: {text}")
                self._show_process_next()
            return

        if is_show and not callback:
            # Обычная генерация речи в шоу (вступление, клэш, заключение)
            clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            self.show_engine._add_to_transcript(speaker_name, clean_text)
            self.update_speaker_name(speaker_name)
            self.show_last_speech = clean_text
            # Озвучка обработается через stream -> on_sequence_finished
            return

        # ═══ Обычные коллбэки тренировочных дебатов ═══

        if callback == 'handle_topic_format':
            self._continue_debate_setup(text.strip())
            return
            
        if callback == 'handle_jury_questions':
            try:
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
                
                # --- ИЗМЕНЕНИЕ 2: Программный подсчёт total ---
                if 'user_scores' in self.verdict_data and 'opponent_scores' in self.verdict_data:
                    for role_key in ['user_scores', 'opponent_scores']:
                        scores = self.verdict_data[role_key]
                        matter = (scores.get('matter_argumentation', 0) + scores.get('matter_clash', 0) + 
                                  scores.get('matter_answers', 0) + scores.get('matter_consistency', 0))
                        manner = scores.get('manner_rhetoric', 0) + scores.get('manner_language', 0)
                        method = (scores.get('method_coherence', 0) + scores.get('method_targeting', 0) + 
                                  scores.get('method_questions', 0))
                        total = matter + manner + method
                        
                        scores['matter'] = matter  
                        scores['manner'] = manner
                        scores['method'] = method
                        scores['total'] = total
                        
                    u_total = self.verdict_data['user_scores']['total']
                    o_total = self.verdict_data['opponent_scores']['total']
                    
                    if abs(u_total - o_total) <= 2:
                        self.verdict_data['winner'] = "Ничья"
                    elif u_total > o_total:
                        self.verdict_data['winner'] = self.engine.user_name if hasattr(self.engine, 'user_name') and self.engine.user_name else "Пользователь"
                    else:
                        self.verdict_data['winner'] = self.engine.opponent_name if hasattr(self.engine, 'opponent_name') and self.engine.opponent_name else "Оппонент"
                # ----------------------------------------------
                
                self.update_speaker_name("Жюри")

                is_3m = metadata.get('is_3m', False)
                

                winner_name = str(self.verdict_data.get('winner', 'Ничья')).strip().lower()
                opp_name_lower = self.engine.opponent_name.lower()
                
                if opp_name_lower in winner_name:
                    is_user_win = False
                elif "ничья" in winner_name or "draw" in winner_name:
                    is_user_win = False
                else:
                    is_user_win = True
                # ------------------------------------

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
        if is_clash:
            if self.clash_timer.isActive():
                self.clash_timer.stop()
            if self.state != "CLASH_ACTIVE": return
            self.last_clash_speech = text

        self.engine._add_to_transcript(speaker_name, text)
        self.update_speaker_name(speaker_name)
        if not metadata.get('is_stream'):
            self.request_speak.emit([text])

    @QtCore.Slot()
    def on_sequence_finished(self):
        # ═══ Обработка ШОУ ═══
        if getattr(self, '_show_pending', False):
            self._show_pending = False
            if getattr(self, 'show_state', '') == 'SHOW_WAITING_VERDICT':
                self._show_launch_ai_verdict()
                return
            if getattr(self, 'show_state', '') == 'SHOW_FLOW':
                self._show_process_next()
                return

        # ═══ Обычные дебаты ═══
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
            self.update_speaker_name(self.engine.opponent_name)
            self.update_subtitles(f"{self.engine.opponent_name} обдумывает ответ...")
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
                self.request_speak.emit(["К результатам прикреплены материалы, найденные в базе знаний."])
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
            self.update_speaker_name("Жюри")
            self.update_subtitles("Жюри формулирует вопросы...")
            self.request_generation.emit(self.engine.get_jury_questions_prompt(), None, {
                'speaker_name': 'Жюри', 'callback_action': 'handle_jury_questions',
                'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
            })
            return

        if self.state == "WAITING_FOR_VERDICT_PROMPT":
            self.state = "DEBATE_FLOW"
            is_critique = (self.engine.opponent_name == "Академический Рецензент")
            
    
            print("[RAG Jury] Проверяем факты участников...")
            recent_claims = " ".join(self.engine.transcript[-4:])
            search_query = f"проверка фактов {recent_claims[:100]}"
            from rag_retriever import get_web_context
            jury_facts = get_web_context([search_query], max_results_per_query=2)
            if hasattr(self, 'deepseek'):
                self.deepseek.log_rag_citation(jury_facts)
            
            if is_critique:
                raw_prompt = self.engine.get_final_verdict_prompt()
            else:
                raw_prompt = self.engine.get_3m_verdict_prompt()
                
            prompt = raw_prompt + f"\n\n[БЛОК ПРОВЕРКИ ФАКТОВ В СЕТИ — GROUND TRUTH]\nРезультаты онлайн-поиска (учитывай их при выставлении баллов за Matter):\n{jury_facts}\n[КОНЕЦ БЛОКА ПРОВЕРКИ ФАКТОВ]"
     
            self.update_speaker_name("Жюри")
            self.update_subtitles("Жюри подводит итоги и выставляет оценки...")
            self.request_generation.emit(prompt, None, {
                'speaker_name': 'Жюри', 
                'callback_action': 'announce_winner',
                'is_3m': not is_critique,
                'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
            })
            return

        if self.state == "CLASH_INTRO":
            self._begin_clash_timer_and_turns()
            return
        elif self.state == "CLASH_ACTIVE":
            if self.clash_turns_done >= 2:
                self._on_clash_round_finished()
            else:
                self._process_clash_turn()
            return

        elif self.state in ["CLASH_ENDING", "CLASH_TRANSITION", "DEBATE_FLOW"]:
            self.process_next_action()


    def _start_clash_round(self, action):
        self.state = "CLASH_INTRO"
        self.clash_leader = action['leader']
        self.clash_responder = self.engine.p2_name if self.clash_leader == self.engine.p1_name else self.engine.p1_name
        self.current_clash_turn_holder = self.clash_leader
        self.clash_turns_done = 0
        self.update_speaker_name("Модератор")
        
        if action.get('is_first', False):
            user_opening = self.last_user_speech if hasattr(self, 'last_user_speech') else "не озвучена"
            opponent_opening = "не озвучена"
            for t in self.engine.transcript:
                if t.startswith(f"[{self.engine.opponent_name}]:"):
                    opponent_opening = t.split("]: ", 1)[-1]
                    break
            self.engine.set_positions_after_opening(user_opening, opponent_opening)
            text = f"Начинается раунд полемики. Ведущий — {self.clash_leader}."
        else:
            text = f"Право на встречный вопрос переходит к: {self.clash_leader}."
        
        self.request_speak.emit([text])

    def _begin_clash_timer_and_turns(self):
        self.state = "CLASH_ACTIVE"
        self.clash_time_left = self.clash_duration_sec
        self.clash_timer.start()
        self._process_clash_turn()

    def _process_clash_turn(self):
        """Управляет очередностью ходов внутри раунда полемики."""
        if self.state != "CLASH_ACTIVE": return
        
        # Обнуляем/запускаем таймер для НОВОГО хода
        self.clash_time_left = self.clash_duration_sec
        self.clash_timer.start()

        if self.current_clash_turn_holder == self.engine.user_name:
            # Ход игрока
            text = self.get_user_input("Полемика", "Ваш короткий ответ/вопрос (время ограничено!):")
            
            # Как только игрок нажал "Отправить", останавливаем таймер досрочно
            self.clash_timer.stop()
            
            if text:
                self.last_clash_speech = text
                self.update_speaker_name(self.engine.user_name)
                self.engine._add_to_transcript(self.engine.user_name, text)
                self.update_subtitles(text)
                self.clash_turns_done += 1
                # Смена хода
                self.current_clash_turn_holder = self.clash_responder if self.current_clash_turn_holder == self.clash_leader else self.clash_leader
                # Переходим к следующему ходу (озвучки нет, так как это игрок)
                QtCore.QTimer.singleShot(500, self.on_sequence_finished)
            else:
                # Если игрок отменил ввод - считаем пропуском или концом раунда
                self._on_clash_round_finished()
        else:
            # Ход ИИ-оппонента
            is_leader = (self.current_clash_turn_holder == self.clash_leader)
            if is_leader:
                raw_prompt = self.engine.get_clash_leader_prompt(self.last_clash_speech)
                # TODO: validated = deepseek.validate_attack_question(raw_reply, debate_manager.opponent_declared_position, debate_manager.user_declared_position)
            else:
                raw_prompt = self.engine.get_clash_responder_prompt(self.last_clash_speech)
            
            # RAG контекст
            rag_memory = get_philosopher_context(self.current_clash_turn_holder, self.last_clash_speech, top_k=2)
            
            # ЗАЩИТА ОТ RECENCY BIAS: Сначала даем цитаты, затем жесткую инструкцию по их интерпретации
            prompt = (
                f"=== БАЗА ЗНАНИЙ (ТВОИ ИСТОРИЧЕСКИЕ ТРУДЫ) ===\n"
                f"{rag_memory}\n"
                f"=============================================\n\n"
                f"[ИНСТРУКЦИЯ ПО РАБОТЕ С БАЗОЙ ЗНАНИЙ]:\n"
                f"Выше приведены твои реальные цитаты и концепции. ВНИМАНИЕ: твоя текущая стратегическая роль в этих дебатах "
                f"может прямо противоречить твоим историческим взглядам. Если это так, тебе СТРОГО ЗАПРЕЩЕНО критиковать или отрицать свои труды. "
                f"Вместо этого прояви гениальность софиста: переосмысли, искази или интерпретируй свои же концепции парадоксальным образом так, "
                f"чтобы они блестяще подтверждали твою ТЕКУЩУЮ позицию в споре.\n\n"
                f"[ТВОЯ ЗАДАЧА НА ЭТОТ ХОД]:\n"
                f"{raw_prompt}"
            )
            
            actual_speaker = self.current_clash_turn_holder
            self.clash_turns_done += 1
            # Смена хода для следующей итерации
            self.current_clash_turn_holder = self.clash_responder if self.current_clash_turn_holder == self.clash_leader else self.clash_leader

            self._set_voice_for_speaker(actual_speaker)
            self.update_speaker_name(actual_speaker)
            self.update_subtitles("Размышляю...")
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': actual_speaker, 'is_clash_turn': True, 'is_stream': True,
                'deepseek_manager': self.deepseek, 'system_prompt': self.opp_system_prompt, 'role': 'opponent',
                'use_chat_model': is_leader
            })
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
        if self.clash_timer.isActive():
            self.clash_timer.stop()
        self.debate_screen.time.setText("")
        self.state = "CLASH_ENDING"
        QtCore.QTimer.singleShot(100, self.on_sequence_finished)

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
                
                # Собираем данные вердикта для детальной отрисовки
                v_data = getattr(self, 'verdict_data', None)
                
                generate_debate_report(self.engine.transcript, self.engine.topic, winner, v_data, file_path)
                QMessageBox.information(self, "Успех", f"Отчет успешно сохранен:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить PDF:\n{e}")

    def _return_to_main_menu_from_debate(self):
        self.post_debate_overlay.deleteLater()
        self.post_debate_overlay = None
        self.stacked_widget.setCurrentWidget(self.main_menu)

    # ═══════════════════════════════════════════════════════
    # РЕЖИМ «ШОУ: ИИ vs ИИ» — ПОЛНАЯ ЛОГИКА
    # ═══════════════════════════════════════════════════════

    @QtCore.Slot(str, str, str, str, int)
    def start_show_debate(self, raw_topic, p1_name, p2_name, role, rounds):
        """Запускает режим ШОУ: форматирует тему, затем начинает шоу-дебаты."""
        self.show_role = role  # 'spectator' или 'jury'
        self.show_p1_name = p1_name
        self.show_p2_name = p2_name
        self.show_rounds = rounds

        if not raw_topic:
            raw_topic = "Искусственный интеллект — благо или угроза?"

        self.setup_screen.set_loading(True, "Анализирую тему...")
        QtWidgets.QApplication.processEvents()

        self._show_formatter_thread = TopicFormatterThread(raw_topic, self.deepseek, parent=self)
        self._show_formatter_thread.topic_ready.connect(self._show_continue_setup)
        self._show_formatter_thread.start()

    def _show_continue_setup(self, formatted_topic):
        self.setup_screen.set_loading(False)
        if formatted_topic.startswith("Ошибка") or formatted_topic == "ERROR":
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Тема не распознана. Попробуйте другую формулировку.")
            return
        if formatted_topic == "ERROR_FACT":
            QtWidgets.QMessageBox.warning(self, "Неподходящая тема", "Тема является неоспоримым фактом. Выберите дискуссионную тему.")
            return

        dialog = CustomConfirmDialog(self, formatted_topic)
        if dialog.exec():
            self._start_show_flow(formatted_topic)
        else:
            feedback = self.get_user_input("Уточнение", "Что именно нужно изменить в теме?")
            if not feedback:
                return
            self.setup_screen.set_loading(True, "Переписываю...")
            QtWidgets.QApplication.processEvents()
            current_input = f"Предыдущий вариант темы: {formatted_topic}. ПРАВКА ПОЛЬЗОВАТЕЛЯ: {feedback}"
            self._show_formatter_thread = TopicFormatterThread(current_input, self.deepseek, parent=self)
            self._show_formatter_thread.topic_ready.connect(self._show_continue_setup)
            self._show_formatter_thread.start()

    def _start_show_flow(self, topic):
        """Инициализирует ShowDebateManager и запускает полностью автономную цепочку генераций."""
        p1 = self.show_p1_name
        p2 = self.show_p2_name
        rounds = self.show_rounds

        # Первый выбранный = первый спикер
        starter = p1
        responder = p2
        starter_sys_key = 'p1'
        responder_sys_key = 'p2'

        # Переключаемся на debate_screen
        self.stacked_widget.setCurrentWidget(self.debate_screen)

        # Аватары
        p1_avatar = self.AVATAR_MAP.get(p1, "assets/kant.png")
        p2_avatar = self.AVATAR_MAP.get(p2, "assets/kant.png")
        pix1 = QtGui.QPixmap(p1_avatar)
        pix2 = QtGui.QPixmap(p2_avatar)
        if pix1.isNull(): pix1 = QtGui.QPixmap("assets/kant.png")
        if pix2.isNull(): pix2 = QtGui.QPixmap("assets/kant.png")
        
        self.debate_screen.user.setPixmap(pix1)
        self.debate_screen.user.setScaledContents(True)
        self.debate_screen.user.setFlipped(True) # Отзеркаливаем левого оппонента в шоу
        
        self.debate_screen.opp.setPixmap(pix2)
        self.debate_screen.opp.setScaledContents(True)

        # Менеджер
        self.show_engine = ShowDebateManager(topic, p1, p2, rounds)
        
        # Системные промпты по новой логике
        from agents import get_show_opponent_system_prompt
        self.show_p1_system = get_show_opponent_system_prompt(topic, p1, p2)
        self.show_p2_system = get_show_opponent_system_prompt(topic, p2, p1)
        
        self.deepseek.reset_stats()

        self.play_music("Clockwork Focus.mp3")

        if hasattr(self.debate_screen, 'subtitletopicLabel'):
            self.update_speaker_name("Модератор")
        if hasattr(self.debate_screen, 'subtitleLabel'):
            self.debate_screen.subtitleLabel.setText("Идет подготовка к шоу...")
        if hasattr(self.debate_screen, 'topic'):
            self.debate_screen.topic.setText(topic)
            self.debate_screen.topic.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.debate_screen.time.setText("")
        self.show_state = "SHOW_FLOW"
        self.show_action_queue = []
        self.show_last_speech = ""

        # Собираем action_queue для шоу
        queue = []

        # Фаза 1: Вступительные речи
        queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                      'text': f'Добро пожаловать на Шоу «ИИ vs ИИ»! Тема дебатов: {topic}. Сегодня дебатируют {p1} и {p2}. Слово для вступительной речи предоставляется {starter}.'})
        queue.append({'type': 'show_generate', 'speaker': starter, 'system': starter_sys_key,
                      'prompt_func': 'opening', 'prompt_arg': ''})
        queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                      'text': f'Благодарю. Слово для вступительной речи предоставляется {responder}.'})
        queue.append({'type': 'show_generate', 'speaker': responder, 'system': responder_sys_key,
                      'prompt_func': 'opening', 'prompt_arg': '_opponent_opening_'})
        queue.append({'type': 'show_set_positions'})

        # Фаза 2: Полемика (чередование)
        for r in range(rounds):
            if r % 2 == 0:
                attacker, defender = starter, responder
            else:
                attacker, defender = responder, starter
            queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                          'text': f'Раунд {r+1} полемики. Ведущий — {attacker}.'})
            queue.append({'type': 'show_clash_attack', 'attacker': attacker, 'defender': defender})
            queue.append({'type': 'show_clash_defend', 'attacker': attacker, 'defender': defender})

        # Фаза 3: Вопросы от жюри
        queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                      'text': 'Раунды полемики завершены. Слово жюри.'})
        queue.append({'type': 'show_jury_questions'})

        # Фаза 4: Заключительные слова
        queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                      'text': f'Заключительное слово от {starter}.'})
        queue.append({'type': 'show_generate', 'speaker': starter, 'system': starter_sys_key,
                      'prompt_func': 'summary'})
        queue.append({'type': 'show_speak_only', 'speaker': 'Модератор',
                      'text': f'Заключительное слово от {responder}.'})
        queue.append({'type': 'show_generate', 'speaker': responder, 'system': responder_sys_key,
                      'prompt_func': 'summary'})

        # Финал: ставка/жюри
        queue.append({'type': 'show_pre_verdict'})

        self.show_action_queue = queue
        self._show_process_next()

    def _show_process_next(self):
        if self.show_state != "SHOW_FLOW":
            return
        if not self.show_action_queue:
            return
        QtCore.QTimer.singleShot(500, self._show_exec_action)

    def _show_exec_action(self):
        if not self.show_action_queue:
            return
        action = self.show_action_queue.pop(0)
        t = action['type']

        if t == 'show_speak_only':
            self._set_voice_for_speaker(action['speaker'])
            self.update_speaker_name(action['speaker'])
            sentences = [s for s in SENTENCE_SPLIT_RE.split(action['text'].strip()) if s]
            # Сохраняем контекст шоу для on_sequence_finished
            self._show_pending = True
            self.request_speak.emit(sentences)

        elif t == 'show_generate':
            speaker = action['speaker']
            sys_key = action['system']  # 'p1' or 'p2'
            system_prompt = self.show_p1_system if sys_key == 'p1' else self.show_p2_system
            func = action['prompt_func']

            if func == 'opening':
                arg = action.get('prompt_arg', '')
                if arg == '_opponent_opening_':
                    # Ищем вступительную речь оппонента в стенограмме
                    opponent_name = self.show_engine.p1_name if speaker == self.show_engine.p2_name else self.show_engine.p2_name
                    opponent_opening = ''
                    for entry in self.show_engine.transcript:
                        if entry.startswith(f'[{opponent_name}]:'):
                            opponent_opening = entry.split(']: ', 1)[-1]
                            break
                    prompt = self.show_engine.get_opening_prompt(opponent_opening)
                else:
                    prompt = self.show_engine.get_opening_prompt('')
            elif func == 'summary':
                prompt = self.show_engine.get_summary_prompt()
            elif func == 'jury_answer':
                prompt = action['prompt']
            else:
                prompt = ''

            self._set_voice_for_speaker(speaker)
            self.update_speaker_name(speaker)
            self.update_subtitles(f"{speaker} размышляет...")
            self._show_pending = True
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': speaker, 'is_stream': True, 'is_show': True,
                'deepseek_manager': self.deepseek, 'system_prompt': system_prompt, 'role': 'opponent'
            })

        elif t == 'show_set_positions':
            p1_opening = ''
            p2_opening = ''
            for entry in self.show_engine.transcript:
                if entry.startswith(f'[{self.show_engine.p1_name}]:') and not p1_opening:
                    p1_opening = entry.split(']: ', 1)[-1]
                elif entry.startswith(f'[{self.show_engine.p2_name}]:') and not p2_opening:
                    p2_opening = entry.split(']: ', 1)[-1]
            self.show_engine.set_positions_after_opening(p1_opening, p2_opening)
            self._show_process_next()

        elif t == 'show_clash_attack':
            attacker = action['attacker']
            defender = action['defender']
            sys_key = 'p1' if attacker == self.show_engine.p1_name else 'p2'
            system_prompt = self.show_p1_system if sys_key == 'p1' else self.show_p2_system
            prompt = self.show_engine.get_clash_leader_prompt(self.show_last_speech, attacker, defender)

            self._set_voice_for_speaker(attacker)
            self.update_speaker_name(attacker)
            self.update_subtitles(f"{attacker} готовит вопрос...")
            self._show_pending = True
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': attacker, 'is_stream': True, 'is_show': True,
                'deepseek_manager': self.deepseek, 'system_prompt': system_prompt, 'role': 'opponent',
                'use_chat_model': True
            })

        elif t == 'show_clash_defend':
            defender = action['defender']
            sys_key = 'p1' if defender == self.show_engine.p1_name else 'p2'
            system_prompt = self.show_p1_system if sys_key == 'p1' else self.show_p2_system
            prompt = self.show_engine.get_clash_responder_prompt(self.show_last_speech)

            self._set_voice_for_speaker(defender)
            self.update_speaker_name(defender)
            self.update_subtitles(f"{defender} отвечает...")
            self._show_pending = True
            self.request_stream_start.emit()
            self.request_generation.emit(prompt, None, {
                'speaker_name': defender, 'is_stream': True, 'is_show': True,
                'deepseek_manager': self.deepseek, 'system_prompt': system_prompt, 'role': 'opponent'
            })

        elif t == 'show_jury_questions':
            self.update_speaker_name("Жюри")
            self.update_subtitles("Жюри формулирует вопросы...")
            self._show_pending = True
            self.request_generation.emit(self.show_engine.get_jury_questions_prompt(), None, {
                'speaker_name': 'Жюри', 'callback_action': 'show_handle_jury_questions',
                'is_show': True,
                'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
            })

        elif t == 'show_pre_verdict':
            self._show_pre_verdict()

    def _show_pre_verdict(self):
        """Показывает блок ставки (Зритель) или оценивание (Жюри) после всех фаз."""
        if self.show_role == 'spectator':
            # Ставка
            dialog = BetDialog(self.show_engine.p1_name, self.show_engine.p2_name, self)
            dialog.exec()
            self.show_user_bet = dialog.choice or "Ничья"
            # Запускаем ИИ-жюри
            self.update_speaker_name("Модератор")
            self.update_subtitles("Наступает момент оглашения вердикта...")
            self._show_pending = True
            self.request_speak.emit(["Наступает момент оглашения вердикта."])
            self.show_state = "SHOW_WAITING_VERDICT"
        elif self.show_role == 'jury':
            # Переходим на экран оценивания
            self.jury_eval_screen.start_evaluation(
                self.show_engine.p1_name,
                self.show_engine.p2_name,
                self.show_engine.transcript
            )
            self.stacked_widget.setCurrentWidget(self.jury_eval_screen)

    def _show_launch_ai_verdict(self):
        """Запускает ИИ-жюри для шоу (режим Зритель)."""
        self.show_state = "SHOW_FLOW"
        raw_prompt = self.show_engine.get_3m_verdict_prompt()
        self.update_speaker_name("Жюри")
        self.update_subtitles("Жюри выносит вердикт...") # БАГ 1: Показываем индикатор
        self.request_generation.emit(raw_prompt, None, {
            'speaker_name': 'Жюри',
            'callback_action': 'show_announce_winner',
            'is_show': True,
            'use_chat_model': True, # БАГ 1: Используем MODEL_CHAT для скорости
            'deepseek_manager': self.deepseek, 'system_prompt': JURY_PROMPT, 'role': 'jury'
        })

    def _handle_show_jury_complete(self, scores, winner):
        """Обработчик завершения ручного оценивания (режим Жюри)."""
        # Начислить монеты
        if self.current_user:
            self.db.add_coins(self.current_user['id'], 5)
            self.update_main_menu_info()

        # Показываем финальный экран
        self.stacked_widget.setCurrentWidget(self.debate_screen)
        self._show_end_debate_screen(f"Ваш вердикт: {winner}", 5)

    def _show_end_debate_screen(self, verdict_text, coins=0):
        """Показывает финальный оверлей шоу."""
        self.show_state = "IDLE"
        self.deepseek.print_game_stats()

        overlay = QtWidgets.QFrame(self.debate_screen)
        overlay.setGeometry(0, 0, self.debate_screen.width(), self.debate_screen.height())
        overlay.setStyleSheet("background-color: rgba(0, 0, 0, 220);")
        self.post_debate_overlay = overlay

        layout = QtWidgets.QVBoxLayout(overlay)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(25)

        title = QtWidgets.QLabel(verdict_text)
        title.setStyleSheet("color: #ffca28; font-size: 42px; font-weight: bold; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)

        if coins > 0:
            coins_lbl = QtWidgets.QLabel(f"+{coins} аргуконов 🎉")
            coins_lbl.setStyleSheet("color: #a5d6a7; font-size: 32px; font-weight: bold; background: transparent;")
            coins_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(coins_lbl)

        btn_layout = QtWidgets.QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.setSpacing(15)

        pdf_btn = QtWidgets.QPushButton("Скачать отчет (PDF)")
        pdf_btn.setFixedSize(300, 60)
        pdf_btn.setStyleSheet("QPushButton { background-color: #2196F3; color: white; font-size: 20px; font-weight: bold; border-radius: 10px; border: 2px solid #1976D2; } QPushButton:hover { background-color: #1E88E5; }")
        pdf_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pdf_btn.clicked.connect(self._show_export_pdf)
        btn_layout.addWidget(pdf_btn)

        exit_btn = QtWidgets.QPushButton("В главное меню")
        exit_btn.setFixedSize(300, 60)
        exit_btn.setStyleSheet("QPushButton { background-color: #757575; color: white; font-size: 20px; font-weight: bold; border-radius: 10px; border: 2px solid #616161; } QPushButton:hover { background-color: #616161; }")
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.clicked.connect(self._return_to_main_menu_from_debate)
        btn_layout.addWidget(exit_btn)

        layout.addLayout(btn_layout)
        overlay.show()

    def _show_export_pdf(self):
        from pdf_generator import generate_debate_report
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        file_path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчет", "show_debate_report.pdf", "PDF Files (*.pdf)")
        if file_path:
            try:
                winner = getattr(self, '_show_winner_name', 'Ничья')
                v_data = getattr(self, 'show_verdict_data', None)
                generate_debate_report(self.show_engine.transcript, self.show_engine.topic, winner, v_data, file_path)
                QMessageBox.information(self, "Успех", f"Отчет успешно сохранен:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить PDF:\n{e}")
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
                widget.setAlignment(Qt.AlignmentFlag.AlignCenter) 
                widget.move((w - new_width) // 2, int(h * 0.05))
                text_len = len(widget.text())
                base_font = 34 if self.isFullScreen() else 26
                font_size = max(20, base_font - max(0, text_len - 40) // 5)
                widget.setStyleSheet(
                    f"background: transparent; color: #E0E0E0; font-size: {font_size}px; font-weight: bold;")

            elif name == 'subtitleLabel':
                # Размер шрифта субтитров берётся из настроек пользователя (14–24 px)
                fsize = self.settings.subtitle_font_size if not self.isFullScreen() else min(25, self.settings.subtitle_font_size + 5)
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
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.showNormal()
            self.setFixedSize(self.base_size)
            self.center_window()
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.showFullScreen()

    def resize_main_menu_widgets(self):
        w, h = self.main_menu.width(), self.main_menu.height()
        base_w, base_h = self.base_size.width(), self.base_size.height()
        scale = min(w / base_w, h / base_h)

        btn_scale = scale * 1.025 if self.isFullScreen() else scale
        btn_w = int(280 * btn_scale)
        btn_h = int(65 * btn_scale)
        spacing = int(15 * btn_scale)
        center_x = (w - btn_w) // 2
        start_y = int(h * 0.40)  # Подняли ещё выше (было 0.42), чтобы кнопки были на уровне края стола

        self.main_menu.startDebateButton.setGeometry(center_x, start_y, btn_w, btn_h)
        self.shop_btn.setGeometry(center_x, start_y + (btn_h + spacing), btn_w, btn_h)
        self.profile_btn.setGeometry(center_x, start_y + 2 * (btn_h + spacing), btn_w, btn_h)
        self.tutorial_btn.setGeometry(center_x, start_y + 3 * (btn_h + spacing), btn_w, btn_h)
        self.settings_btn.setGeometry(center_x, start_y + 4 * (btn_h + spacing), btn_w, btn_h)

        font_scale = scale * 1.08 if self.isFullScreen() else scale
        btn_font_size = int(18 * font_scale)
        btn_style = f"""
            QPushButton {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00);
                color: #3e2723; font-size: {btn_font_size}px; font-weight: bold;
                border-radius: 8px; border: 2px solid #a67c00;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37);
            }}
        """
        for btn in [self.main_menu.startDebateButton, self.shop_btn,
                    self.profile_btn, self.tutorial_btn, self.settings_btn]:
            btn.setStyleSheet(btn_style)

        info_w = int(400 * scale)
        self.user_info_lbl.setGeometry(w - info_w - 20, 20, info_w, int(40 * scale))
        self.user_info_lbl.setStyleSheet(f"font-size: {int(20 * scale)}px; color: white; font-weight: bold; background: transparent;")

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
                if isinstance(obj, (QAbstractButton, QComboBox)):
                    self.sound.play()
        return False

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    
    click_filter = ClickSoundFilter()
    app.installEventFilter(click_filter)
    
    window = AppController()
    window.show()
    sys.exit(app.exec())