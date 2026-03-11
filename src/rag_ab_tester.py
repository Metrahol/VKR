import sys
import random
import os
from dotenv import load_dotenv
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QComboBox, QTextEdit, 
                               QPushButton, QFrame, QMessageBox, QScrollArea)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

# Важно: предполагается, что эти модули лежат в той же папке
from rag_retriever import get_philosopher_context

# Убедись, что импортируешь свои промпты из основного файла (например, из agents.py)
from agents import BASE_OPPONENT_PROMPT, OPPONENTS_CONFIG, DeepSeekManager

class Worker(QThread):
    finished = Signal(str, str, str, str)
    error = Signal(str)

    def __init__(self, philosopher, user_input, deepseek_manager):
        super().__init__()
        self.philosopher = philosopher
        self.user_input = user_input
        self.ds = deepseek_manager

    def run(self):
        try:
            # 1. Извлекаем контекст из RAG
            rag_context = get_philosopher_context(self.philosopher, self.user_input, top_k=3)
            
            # 2. Достаем реальный конфиг персонажа
            config = OPPONENTS_CONFIG.get(self.philosopher)
            
            # 3. Собираем настоящий системный промпт (State Machine)
            system_instruction = BASE_OPPONENT_PROMPT.format(
                name=self.philosopher,
                topic="Философская дискуссия",
                style=config["style"],
                concepts=config["concepts"],
                greeting=config["greeting"]
            )
            
            # 4. Формируем запросы пользователя с указанием фазы
            phase_directive = "\n\n[СИСТЕМНАЯ ДИРЕКТИВА: ТЕКУЩАЯ ФАЗА — 2 (РЕЖИМ ЗАЩИТЫ). Твоя очередь отвечать на аргумент.]"
            
            # Вариант БЕЗ RAG
            prompt_no_rag = f"Аргумент оппонента: {self.user_input}" + phase_directive
            
            # Вариант С RAG
            prompt_with_rag = f"""Аргумент оппонента: {self.user_input}

[СИСТЕМНОЕ СООБЩЕНИЕ: Ниже приведены отрывки из твоих трудов, найденные через RAG. ИСПОЛЬЗУЙ ИХ ТОЛЬКО КАК СЫРОЙ МАТЕРИАЛ. Извлеки из них философскую суть и аргументы, но СТРОГО пропусти их через твой текущий стиль дебатов (академичность, отсутствие прямых оскорблений). НЕ цитируй дословно и НЕ перенимай истеричный или архаичный тон оригинального текста. Адаптируй эти идеи для твоей контратаки.]

{rag_context}

[СИСТЕМНАЯ ДИРЕКТИВА: ТЕКУЩАЯ ФАЗА — 2 (РЕЖИМ ЗАЩИТЫ). Твоя очередь отвечать на аргумент.]"""

            # 5. Делаем запросы через DeepSeek
            response_no_rag = self.ds.generate_opponent(system_instruction, prompt_no_rag, stream=False)
            response_with_rag = self.ds.generate_opponent(system_instruction, prompt_with_rag, stream=False)

            # 6. Перемешиваем (Слепой тест)
            is_rag_a = random.choice([True, False])
            
            if is_rag_a:
                text_a = response_with_rag
                text_b = response_no_rag
                truth = "RAG был вариантом А"
            else:
                text_a = response_no_rag
                text_b = response_with_rag
                truth = "RAG был вариантом Б"
                
            self.finished.emit(text_a, text_b, truth, rag_context)
            
        except Exception as e:
            self.error.emit(str(e))


class BlindTesterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RAG A/B Blind Tester")
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #FFFFFF;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QComboBox, QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #333;
                border-radius: 5px;
                padding: 5px;
                font-size: 14px;
            }
            QPushButton {
                background-color: #007ACC;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0098FF;
            }
            QPushButton:disabled {
                background-color: #555;
            }
            QLabel {
                font-size: 14px;
            }
        """)

        self.rag_wins = 0
        self.base_wins = 0
        self.current_truth = ""

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Header
        header_label = QLabel("A/B Тестирование: RAG vs Base DeepSeek")
        header_label.setFont(QFont("Arial", 20, QFont.Bold))
        header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(header_label)
        
        # Stats
        self.stats_label = QLabel(f"Счетчик побед - RAG: {self.rag_wins} | Base DeepSeek: {self.base_wins}")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stats_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        main_layout.addWidget(self.stats_label)

        # Controls Layout
        controls_layout = QHBoxLayout()
        
        self.philo_combo = QComboBox()
        self.philo_combo.addItems([
            "Фома Аквинский", "Джордж Вашингтон", "Чарльз Дарвин",
            "Федор Достоевский", "Иммануил Кант", "Владимир Ленин",
            "Маймонид", "Макиавелли", "Карл Маркс",
            "Фридрих Ницше", "Иосиф Сталин", "Никола Тесла", "Лев Толстой",
            "Гай Юлий Цезарь", "Уильям Оккам", "Зигмунд Фрейд", "Мюррей Ротбард"
        ])
        controls_layout.addWidget(QLabel("Выберите Философа:"))
        controls_layout.addWidget(self.philo_combo)
        
        main_layout.addLayout(controls_layout)

        # Input
        main_layout.addWidget(QLabel("Ваш вопрос/тезис (User Input):"))
        self.input_box = QTextEdit()
        self.input_box.setMaximumHeight(80)
        self.input_box.setPlaceholderText("Например: В чем смысл жизни? Почему религия - это опиум для народа?")
        main_layout.addWidget(self.input_box)

        # Generate Button
        self.gen_btn = QPushButton("Сгенерировать два ответа (вслепую)")
        self.gen_btn.clicked.connect(self.generate_responses)
        main_layout.addWidget(self.gen_btn)

        # Responses Layout
        resp_layout = QHBoxLayout()
        
        # Opcion A
        layout_a = QVBoxLayout()
        layout_a.addWidget(QLabel("Вариант А:"))
        self.text_a = QTextEdit()
        self.text_a.setReadOnly(True)
        layout_a.addWidget(self.text_a)
        
        self.vote_a_btn = QPushButton("Голосовать за А")
        self.vote_a_btn.setEnabled(False)
        self.vote_a_btn.clicked.connect(lambda: self.vote("A"))
        layout_a.addWidget(self.vote_a_btn)
        
        resp_layout.addLayout(layout_a)

        # Opcion B
        layout_b = QVBoxLayout()
        layout_b.addWidget(QLabel("Вариант Б:"))
        self.text_b = QTextEdit()
        self.text_b.setReadOnly(True)
        layout_b.addWidget(self.text_b)
        
        self.vote_b_btn = QPushButton("Голосовать за Б")
        self.vote_b_btn.setEnabled(False)
        self.vote_b_btn.clicked.connect(lambda: self.vote("B"))
        layout_b.addWidget(self.vote_b_btn)
        
        resp_layout.addLayout(layout_b)
        
        main_layout.addLayout(resp_layout)
        
        # Context Display (hidden until voted)
        self.context_label = QLabel("Справочный контекст, который использовал RAG:")
        self.context_label.hide()
        main_layout.addWidget(self.context_label)
        
        self.context_box = QTextEdit()
        self.context_box.setReadOnly(True)
        self.context_box.setMaximumHeight(100)
        self.context_box.hide()
        main_layout.addWidget(self.context_box)

    def generate_responses(self):
        user_text = self.input_box.toPlainText().strip()
        if not user_text:
            QMessageBox.warning(self, "Ошибка", "Введите сообщение для философа!")
            return
            
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("Генерация... Ждите...")
        self.text_a.clear()
        self.text_b.clear()
        self.context_box.clear()
        self.context_label.hide()
        self.context_box.hide()
        self.vote_a_btn.setEnabled(False)
        self.vote_b_btn.setEnabled(False)
        
        philo = self.philo_combo.currentText()
        
        self.worker = Worker(philo, user_text, self.ds)
        self.worker.finished.connect(self.on_generation_finished)
        self.worker.error.connect(self.on_generation_error)
        self.worker.start()

    def on_generation_finished(self, text_a, text_b, truth, context):
        self.text_a.setText(text_a)
        self.text_b.setText(text_b)
        self.current_truth = truth
        self.context_box.setText(context)
        
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText("Сгенерировать два ответа (вслепую)")
        self.vote_a_btn.setEnabled(True)
        self.vote_b_btn.setEnabled(True)

    def on_generation_error(self, error_msg):
        QMessageBox.critical(self, "Ошибка АПИ", f"Произошла ошибка при генерации:\n{error_msg}")
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText("Сгенерировать два ответа (вслепую)")

    def vote(self, choice):
        self.vote_a_btn.setEnabled(False)
        self.vote_b_btn.setEnabled(False)
        
        # Кто победил?
        rag_won = False
        if choice == "A" and "RAG был вариантом А" in self.current_truth:
            rag_won = True
        elif choice == "B" and "RAG был вариантом Б" in self.current_truth:
            rag_won = True
            
        if rag_won:
            self.rag_wins += 1
            result_msg = f"Правильно! Вы выбрали вариант с RAG.\n({self.current_truth})"
        else:
            self.base_wins += 1
            result_msg = f"Вы выбрали вариант БЕЗ RAG (Голая база DeepSeek).\n({self.current_truth})"
            
        self.stats_label.setText(f"Счетчик побед - RAG: {self.rag_wins} | Base DeepSeek: {self.base_wins}")
        
        # Показываем контекст
        self.context_label.show()
        self.context_box.show()
        
        QMessageBox.information(self, "Результат слепого теста", result_msg)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    load_dotenv()
    
    # Инициализируем DeepSeek менеджер
    ds_manager = DeepSeekManager()
    
    window = BlindTesterApp()
    window.ds = ds_manager  # Передаем менеджер в окно
    window.show()
    sys.exit(app.exec())
