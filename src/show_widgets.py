"""
UI-виджеты для режима «Шоу: ИИ vs ИИ».
- ShowSetupWidget: экран настройки с выбором двух оппонентов, темы, роли
- BetWidget: диалог ставки перед вердиктом (режим Зритель)
- JuryEvaluationWidget: пошаговое оценивание по 3M (режим Жюри)
"""
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Signal, Qt
from agents import OPPONENTS_CONFIG


# ═══════════════════════════════════════════════════════
# Общие стили
# ═══════════════════════════════════════════════════════

_CONTAINER_STYLE = """
    QFrame { background-color: rgba(20, 20, 30, 240); border-radius: 15px; border: 2px solid #a67c00; }
"""
_TITLE_STYLE = """
    font-size: 34px; font-weight: bold; color: #ffca28;
    border: none; background: transparent; margin-bottom: 10px; min-height: 50px;
"""
_COMBO_STYLE = """
    QComboBox { background-color: rgba(30,30,40,200); color: white; border: 2px solid #a67c00;
        border-radius: 8px; padding: 10px; font-size: 18px; font-weight: bold;}
    QComboBox::drop-down { border-left: 2px solid #a67c00; }
    QComboBox QAbstractItemView { background: #1e1e2e; color: white;
        selection-background-color: #a67c00; outline: none; }
"""
_INPUT_STYLE = """
    QLineEdit { padding: 10px 20px; font-size: 20px; border: 2px solid #555;
        border-radius: 8px; background-color: rgba(30,30,40,200); color: white; }
"""
_GOLD_BTN_STYLE = """
    QPushButton { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d4af37, stop:1 #a67c00);
        color: #3e2723; font-size: 24px; font-weight: bold; border-radius: 10px; border: none; }
    QPushButton:hover { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #edd27a, stop:1 #d4af37); }
    QPushButton:disabled { background-color: #555555; color: #aaaaaa; }
"""
_CANCEL_BTN_STYLE = """
    QPushButton {
        background-color: rgba(60, 60, 80, 200);
        color: #B0B0B0; font-size: 14px; font-weight: bold;
        border-radius: 8px; border: 1px solid #555;
    }
    QPushButton:hover { background-color: #c62828; color: white; border-color: #ef5350; }
"""
_LABEL_STYLE = "color: #dcdcdc; font-size: 18px; font-weight: bold; background: transparent; border: none;"
_SPINBOX_STYLE = """
    QSpinBox { background-color: rgba(30,30,40,200); color: white; border: 2px solid #a67c00;
        border-radius: 8px; padding: 10px; font-size: 18px; font-weight: bold;}
    QSpinBox::up-button, QSpinBox::down-button { width: 30px; background: rgba(164, 124, 0, 100); border: none; }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #a67c00; }
"""


def _get_opponent_names():
    """Возвращает список имён оппонентов из OPPONENTS_CONFIG (без Рецензента)."""
    return [name for name in OPPONENTS_CONFIG.keys() if name != "Академический Рецензент"]


# ═══════════════════════════════════════════════════════
# ShowSetupWidget — Экран настройки шоу
# ═══════════════════════════════════════════════════════

class ShowSetupWidget(QtWidgets.QWidget):
    """Экран настройки режима «Шоу: ИИ vs ИИ»."""
    start_show_requested = Signal(str, str, str, str, int)  # topic, p1, p2, role, rounds
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ShowSetupScreen")
        self.setStyleSheet("#ShowSetupScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QtWidgets.QFrame()
        container.setFixedSize(640, 650)
        container.setStyleSheet(_CONTAINER_STYLE)

        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(40, 30, 40, 40)
        vbox.setSpacing(12)

        # Заголовок
        title = QtWidgets.QLabel("ШОУ: ИИ vs ИИ", container)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(_TITLE_STYLE)
        vbox.addWidget(title)

        # Тема
        self.topic_input = QtWidgets.QLineEdit(container)
        self.topic_input.setPlaceholderText("Введите тему дебатов...")
        self.topic_input.setStyleSheet(_INPUT_STYLE)
        vbox.addWidget(self.topic_input)

        # Два выбора оппонентов
        opponents = _get_opponent_names()

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)

        lbl1 = QtWidgets.QLabel("Оппонент 1:")
        lbl1.setStyleSheet(_LABEL_STYLE)
        self.p1_combo = QtWidgets.QComboBox(container)
        self.p1_combo.addItems(opponents)
        self.p1_combo.setCurrentIndex(0)
        self.p1_combo.setStyleSheet(_COMBO_STYLE)
        form.addRow(lbl1, self.p1_combo)

        lbl2 = QtWidgets.QLabel("Оппонент 2:")
        lbl2.setStyleSheet(_LABEL_STYLE)
        self.p2_combo = QtWidgets.QComboBox(container)
        self.p2_combo.addItems(opponents)
        self.p2_combo.setCurrentIndex(min(1, len(opponents) - 1))
        self.p2_combo.setStyleSheet(_COMBO_STYLE)
        form.addRow(lbl2, self.p2_combo)

        # Роль пользователя
        lbl_role = QtWidgets.QLabel("Ваша роль:")
        lbl_role.setStyleSheet(_LABEL_STYLE)
        self.role_combo = QtWidgets.QComboBox(container)
        self.role_combo.addItems(["Зритель (автономные дебаты)", "Жюри (я оцениваю)"])
        self.role_combo.setStyleSheet(_COMBO_STYLE)
        form.addRow(lbl_role, self.role_combo)

        # Количество раундов
        lbl_rounds = QtWidgets.QLabel("Кол-во раундов:")
        lbl_rounds.setStyleSheet(_LABEL_STYLE)
        self.rounds_spinbox = QtWidgets.QSpinBox(container)
        self.rounds_spinbox.setRange(1, 10)
        self.rounds_spinbox.setValue(3)
        self.rounds_spinbox.setStyleSheet(_SPINBOX_STYLE)
        form.addRow(lbl_rounds, self.rounds_spinbox)

        vbox.addLayout(form)
        vbox.addSpacing(15)

        # Кнопка старт
        self.start_btn = QtWidgets.QPushButton("НАЧАТЬ ШОУ", container)
        self.start_btn.setFixedSize(380, 60)
        self.start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setStyleSheet(_GOLD_BTN_STYLE)
        self.start_btn.clicked.connect(self._on_start)
        vbox.addWidget(self.start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Кнопка отмена
        cancel_btn = QtWidgets.QPushButton("Отмена", container)
        cancel_btn.setFixedSize(300, 45)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_btn.setStyleSheet(_CANCEL_BTN_STYLE)
        cancel_btn.clicked.connect(self.back_requested.emit)
        vbox.addWidget(cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        main_layout.addWidget(container)

    def _on_start(self):
        p1 = self.p1_combo.currentText()
        p2 = self.p2_combo.currentText()
        if p1 == p2:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Нельзя выбрать одного и того же оппонента!")
            return
        topic = self.topic_input.text().strip()
        if not topic:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Введите тему дебатов!")
            return
        role = "spectator" if self.role_combo.currentIndex() == 0 else "jury"
        rounds = self.rounds_spinbox.value()
        self.start_show_requested.emit(topic, p1, p2, role, rounds)

    def set_loading(self, is_loading, text="Обработка..."):
        self.start_btn.setEnabled(not is_loading)
        self.start_btn.setText(text if is_loading else "НАЧАТЬ ШОУ")


# ═══════════════════════════════════════════════════════
# BetWidget — Диалог ставки (режим Зритель)
# ═══════════════════════════════════════════════════════

class BetDialog(QtWidgets.QDialog):
    """Диалог ставки: пользователь выбирает, кто победил."""

    def __init__(self, p1_name, p2_name, parent=None):
        super().__init__(parent)
        self.choice = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)

        frame = QtWidgets.QFrame(self)
        frame.setFixedSize(550, 300)
        frame.setStyleSheet("""
            QFrame { background-color: #1A1A24; border: 2px solid #D4AF37; border-radius: 15px; }
            QLabel { color: #E0E0E0; font-size: 20px; background: transparent; border: none; }
        """)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QtGui.QColor(0, 0, 0, 200))
        shadow.setOffset(0, 5)
        frame.setGraphicsEffect(shadow)

        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QtWidgets.QLabel("🎲 СДЕЛАЙ СТАВКУ", frame)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #ffca28;")
        layout.addWidget(title)

        question = QtWidgets.QLabel("Кто, по-твоему, победил в этих дебатах?", frame)
        question.setAlignment(Qt.AlignmentFlag.AlignCenter)
        question.setWordWrap(True)
        layout.addWidget(question)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(15)

        btn_style = """
            QPushButton {
                background-color: rgba(40, 40, 60, 230); color: white;
                font-size: 16px; font-weight: bold; border-radius: 10px;
                border: 2px solid #a67c00; padding: 12px 8px;
            }
            QPushButton:hover { background-color: #a67c00; color: #1A1A24; }
        """

        for text, value in [(p1_name, p1_name), ("Ничья", "Ничья"), (p2_name, p2_name)]:
            btn = QtWidgets.QPushButton(text, frame)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(btn_style)
            btn.setMinimumHeight(50)
            btn.clicked.connect(lambda checked, v=value: self._select(v))
            btn_layout.addWidget(btn)

        layout.addLayout(btn_layout)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(frame)

    def _select(self, value):
        self.choice = value
        self.accept()

    def showEvent(self, event):
        if self.parentWidget():
            parent_geo = self.parentWidget().geometry()
            self.move(parent_geo.center() - self.rect().center())
        super().showEvent(event)


# ═══════════════════════════════════════════════════════
# JuryEvaluationWidget — Пошаговое оценивание по 3M
# ═══════════════════════════════════════════════════════

# Конфигурация подкритериев: (ключ, название, макс_балл, подсказка)
_MATTER_CRITERIA = [
    ("argumentation", "1a. Аргументация во вступлении", 10,
     "0-2: только мнения без обоснования\n"
     "3-5: 1 тезис с причиной, без подкрепления\n"
     "6-8: 2 тезиса + подкрепление (пример/аналогия/довод)\n"
     "9-10: 3+ подкреплённых тезиса, разные аспекты темы"),
    ("clash", "1b. Работа с аргументами оппонента", 15,
     "0-3: проигнорировал 80%+ аргументов\n"
     "4-7: адресовал 40-60%, но без объяснения «почему»\n"
     "8-11: адресовал 60-80%, 2+ раза объяснил ошибку\n"
     "12-15: адресовал 80%+, перевернул аргумент в свою пользу"),
    ("answers", "1c. Качество ответов на вопросы", 10,
     "0-2: 2+ уклонения от вопроса\n"
     "3-5: ответы расплывчатые\n"
     "6-8: 70%+ прямых ответов с обоснованием\n"
     "9-10: каждый вопрос — прямой ответ + подкрепление"),
    ("consistency", "1d. Логическая целостность", 5,
     "0-1: самострел/признание правоты оппонента\n"
     "2-3: возможная двусмысленность\n"
     "4-5: нет противоречий, стабильные определения"),
]

_MANNER_CRITERIA = [
    ("rhetoric", "2a. Риторические приёмы", 15,
     "0-3: 0 приёмов\n"
     "4-7: 1-2 приёма\n"
     "8-11: 2-3 приёма\n"
     "12-15: 3+ приёмов, уместных и усиливающих аргумент"),
    ("language", "2b. Лексическое разнообразие", 10,
     "0-2: повторы 5+ раз, нет терминологии\n"
     "3-5: повторы 3-4 раза, базовая терминология\n"
     "6-8: мало повторов, предметная терминология\n"
     "9-10: богатая лексика, переформулировки в каждой реплике"),
    ("questions", "2c. Качество вопросов в полемике", 15,
     "0-3: общие, легко парируемые\n"
     "4-7: конкретные, но по мелочам\n"
     "8-11: 1+ вопрос вынудил уклонение/слабый ответ\n"
     "12-15: 1+ вопрос вскрыл фундаментальное противоречие"),
]

_METHOD_CRITERIA = [
    ("coherence", "3a. Связность между фазами", 10,
     "0-2: заключение не связано со вступлением\n"
     "3-5: пересказ без учёта полемики\n"
     "6-8: резюме вступления + ссылки на полемику\n"
     "9-10: + явно назван неотвеченный довод"),
    ("targeting", "3b. Выбор точек атаки", 10,
     "0-2: соломенное чучело / атака того, чего не говорили\n"
     "3-5: по мелочам, не по фундаменту\n"
     "6-8: давление на 1-2 ключевых тезиса\n"
     "9-10: нашёл слабое место и давил несколько раундов"),
]


class JuryEvaluationWidget(QtWidgets.QWidget):
    """Пошаговый виджет оценивания дебатов по 3M. Заменяет debate_screen после фазы 4."""
    evaluation_complete = Signal(dict, str)  # scores_dict, winner_name
    back_requested = Signal()

    _STEP_TITLES = ["MATTER — Содержание", "MANNER — Подача", "METHOD — Стратегия", "ИТОГИ"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("JuryEvalScreen")
        self.setStyleSheet("""
            #JuryEvalScreen { border-image: url(assets/main_menu_bg.png) 0 0 0 0 stretch stretch; }
            QLabel { color: #E0E0E0; font-family: 'Segoe UI', sans-serif; }
        """)

        self.p1_name = ""
        self.p2_name = ""
        self.transcript_text = ""
        self.current_step = 0
        self.sliders = {}  # key -> (p1_slider, p2_slider)

        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Top bar
        top_bar = QtWidgets.QFrame()
        top_bar.setFixedHeight(60)
        top_bar.setStyleSheet("QFrame { background-color: rgba(0,0,0,180); border-bottom: 2px solid #a67c00; }")
        top_layout = QtWidgets.QHBoxLayout(top_bar)
        top_layout.setContentsMargins(20, 0, 20, 0)

        self.step_title = QtWidgets.QLabel("Шаг 1: MATTER")
        self.step_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #ffca28;")
        self.step_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_layout.addWidget(self.step_title)
        main_layout.addWidget(top_bar)

        # Scroll area for content
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { background: rgba(30,30,40,150); width: 10px; border-radius: 5px; }
            QScrollBar::handle:vertical { background: #a67c00; border-radius: 5px; min-height: 30px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(40, 20, 40, 20)
        self.content_layout.setSpacing(10)
        scroll.setWidget(self.content_widget)
        main_layout.addWidget(scroll, 1)

        # Transcript collapsible
        self.transcript_toggle = QtWidgets.QPushButton("📜 Показать/скрыть стенограмму")
        self.transcript_toggle.setStyleSheet("""
            QPushButton { background-color: rgba(40,40,60,200); color: #a5d6a7; font-size: 14px;
                font-weight: bold; border-radius: 8px; padding: 8px; border: 1px solid #555; }
            QPushButton:hover { background-color: rgba(60,60,80,200); }
        """)
        self.transcript_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transcript_toggle.clicked.connect(self._toggle_transcript)
        self.content_layout.addWidget(self.transcript_toggle)

        self.transcript_box = QtWidgets.QTextEdit()
        self.transcript_box.setReadOnly(True)
        self.transcript_box.setMaximumHeight(300)
        self.transcript_box.setStyleSheet("""
            QTextEdit { background-color: rgba(20,20,30,230); color: #ccc; font-size: 13px;
                border: 1px solid #555; border-radius: 8px; padding: 10px; }
        """)
        self.transcript_box.hide()
        self.content_layout.addWidget(self.transcript_box)

        # Criteria area (filled dynamically)
        self.criteria_container = QtWidgets.QWidget()
        self.criteria_layout = QtWidgets.QVBoxLayout(self.criteria_container)
        self.criteria_layout.setSpacing(8)
        self.content_layout.addWidget(self.criteria_container)

        # Results area (for step 4)
        self.results_container = QtWidgets.QWidget()
        self.results_layout = QtWidgets.QVBoxLayout(self.results_container)
        self.results_container.hide()
        self.content_layout.addWidget(self.results_container)

        self.content_layout.addStretch()

        # Navigation buttons
        nav_bar = QtWidgets.QFrame()
        nav_bar.setFixedHeight(70)
        nav_bar.setStyleSheet("QFrame { background-color: rgba(0,0,0,180); border-top: 2px solid #a67c00; }")
        nav_layout = QtWidgets.QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(30, 10, 30, 10)

        self.prev_btn = QtWidgets.QPushButton("← Назад")
        self.prev_btn.setStyleSheet(_CANCEL_BTN_STYLE)
        self.prev_btn.setFixedSize(160, 45)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.clicked.connect(self._go_prev)

        self.next_btn = QtWidgets.QPushButton("Далее →")
        self.next_btn.setStyleSheet(_GOLD_BTN_STYLE.replace("font-size: 24px", "font-size: 18px"))
        self.next_btn.setFixedSize(200, 45)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.clicked.connect(self._go_next)

        nav_layout.addWidget(self.prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self.next_btn)
        main_layout.addWidget(nav_bar)

    def start_evaluation(self, p1_name, p2_name, transcript_lines):
        """Запускает процесс оценивания."""
        self.p1_name = p1_name
        self.p2_name = p2_name
        self.transcript_text = "\n".join(transcript_lines)
        self.transcript_box.setPlainText(self.transcript_text)
        self.current_step = 0
        self.sliders.clear()
        self._show_step(0)

    def _toggle_transcript(self):
        self.transcript_box.setVisible(not self.transcript_box.isVisible())

    def _show_step(self, step_idx):
        self.current_step = step_idx
        # Clear criteria
        while self.criteria_layout.count():
            child = self.criteria_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.results_container.hide()
        self.criteria_container.show()

        if step_idx < 3:
            title_text = f"Шаг {step_idx + 1}: {self._STEP_TITLES[step_idx]}"
            self.step_title.setText(title_text)

            criteria_list = [_MATTER_CRITERIA, _MANNER_CRITERIA, _METHOD_CRITERIA][step_idx]
            self._build_criteria_sliders(criteria_list)

            self.prev_btn.setVisible(step_idx > 0)
            self.next_btn.setText("Далее →")
        else:
            # Summary step
            self.step_title.setText("Шаг 4: ИТОГИ")
            self.criteria_container.hide()
            self.results_container.show()
            self._build_results()
            self.prev_btn.setVisible(True)
            self.next_btn.setText("ЗАВЕРШИТЬ ✓")

    def _build_criteria_sliders(self, criteria_list):
        """Создаёт слайдеры для обоих оппонентов по списку критериев."""
        for key, label, max_val, hint_text in criteria_list:
            # Criterion header
            header = QtWidgets.QLabel(f"<b>{label}</b> (0–{max_val})")
            header.setStyleSheet("font-size: 17px; color: #ffca28; margin-top: 10px; border: none;")
            self.criteria_layout.addWidget(header)

            # Hint
            hint = QtWidgets.QLabel(hint_text)
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 12px; color: #888; margin-bottom: 5px; border: none;")
            self.criteria_layout.addWidget(hint)

            # P1 slider row
            p1_slider = self._make_slider_row(self.p1_name, key + "_p1", max_val)
            self.criteria_layout.addWidget(p1_slider)

            # P2 slider row
            p2_slider = self._make_slider_row(self.p2_name, key + "_p2", max_val)
            self.criteria_layout.addWidget(p2_slider)

            # Separator
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            sep.setStyleSheet("background-color: rgba(164,124,0,80); max-height: 1px;")
            self.criteria_layout.addWidget(sep)

    def _make_slider_row(self, name, slider_key, max_val):
        """Создаёт строку с именем оппонента, слайдером и числовым значением."""
        row = QtWidgets.QWidget()
        row.setStyleSheet("background: transparent;")
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(10, 2, 10, 2)

        lbl_name = QtWidgets.QLabel(f"{name}:")
        lbl_name.setFixedWidth(200)
        lbl_name.setStyleSheet("font-size: 15px; color: #ccc; border: none;")

        slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, max_val)
        slider.setValue(0)
        slider.setStyleSheet("""
            QSlider::groove:horizontal { background: rgba(60,60,80,200); height: 8px; border-radius: 4px; }
            QSlider::handle:horizontal { background: #d4af37; width: 18px; margin: -6px 0; border-radius: 9px; }
            QSlider::sub-page:horizontal { background: #a67c00; border-radius: 4px; }
        """)

        val_lbl = QtWidgets.QLabel("0")
        val_lbl.setFixedWidth(30)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: white; border: none;")

        slider.valueChanged.connect(lambda v, lbl=val_lbl: lbl.setText(str(v)))

        # Store reference
        self.sliders[slider_key] = slider

        h.addWidget(lbl_name)
        h.addWidget(slider, 1)
        h.addWidget(val_lbl)

        return row

    def _collect_scores(self):
        """Собирает все баллы в словарь."""
        scores = {"p1": {}, "p2": {}}
        for key, slider in self.sliders.items():
            if key.endswith("_p1"):
                criterion = key[:-3]
                scores["p1"][criterion] = slider.value()
            elif key.endswith("_p2"):
                criterion = key[:-3]
                scores["p2"][criterion] = slider.value()
        return scores

    def _build_results(self):
        """Строит итоговую таблицу баллов."""
        # Clear results
        while self.results_layout.count():
            child = self.results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        scores = self._collect_scores()

        # Compute totals
        p1_total = sum(scores["p1"].values())
        p2_total = sum(scores["p2"].values())

        # Build table
        all_criteria = _MATTER_CRITERIA + _MANNER_CRITERIA + _METHOD_CRITERIA
        table = QtWidgets.QTableWidget(len(all_criteria) + 1, 3)
        table.setHorizontalHeaderLabels(["Критерий", self.p1_name, self.p2_name])
        table.horizontalHeader().setStyleSheet("color: #ffca28; font-weight: bold; font-size: 14px;")
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setStyleSheet("""
            QTableWidget { background-color: rgba(20,20,30,230); color: #E0E0E0;
                gridline-color: rgba(164,124,0,80); font-size: 14px; border: 1px solid #a67c00; border-radius: 8px; }
            QHeaderView::section { background-color: rgba(30,30,46,230); border: 1px solid #a67c00; padding: 5px; }
            QTableWidget::item { padding: 4px; }
        """)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)

        for i, (key, label, max_val, _) in enumerate(all_criteria):
            p1_val = scores["p1"].get(key, 0)
            p2_val = scores["p2"].get(key, 0)
            table.setItem(i, 0, QtWidgets.QTableWidgetItem(label))
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(p1_val)))
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(p2_val)))

        # Total row
        row_idx = len(all_criteria)
        total_item = QtWidgets.QTableWidgetItem("ИТОГО")
        total_item.setFont(QtGui.QFont("Segoe UI", 14, QtGui.QFont.Weight.Bold))
        table.setItem(row_idx, 0, total_item)
        table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(str(p1_total)))
        table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(str(p2_total)))

        table.setMinimumHeight(400)
        self.results_layout.addWidget(table)

        # Winner
        if p1_total > p2_total:
            winner = self.p1_name
            verdict = f"🏆 Победитель: {self.p1_name} ({p1_total} vs {p2_total})"
        elif p2_total > p1_total:
            winner = self.p2_name
            verdict = f"🏆 Победитель: {self.p2_name} ({p2_total} vs {p1_total})"
        else:
            winner = "Ничья"
            verdict = f"🤝 Ничья! ({p1_total} vs {p2_total})"

        self._winner = winner

        verdict_lbl = QtWidgets.QLabel(verdict)
        verdict_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        verdict_lbl.setStyleSheet("font-size: 22px; font-weight: bold; color: #2ecc71; margin: 15px 0; border: none;")
        self.results_layout.addWidget(verdict_lbl)

        coins_lbl = QtWidgets.QLabel("💰 +5 аргуконов за работу жюри!")
        coins_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        coins_lbl.setStyleSheet("font-size: 18px; color: #a5d6a7; border: none;")
        self.results_layout.addWidget(coins_lbl)

    def _go_prev(self):
        if self.current_step > 0:
            self._show_step(self.current_step - 1)

    def _go_next(self):
        if self.current_step < 3:
            self._show_step(self.current_step + 1)
        else:
            # Complete
            scores = self._collect_scores()
            winner = getattr(self, '_winner', "Ничья")
            self.evaluation_complete.emit(scores, winner)
