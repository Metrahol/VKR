class MainMenuWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        title_label = QLabel("Интеллектуальный Поединок")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 20px;")

        self.start_debate_button = QPushButton("Начать Новые Дебаты")
        self.start_debate_button.setMinimumSize(200, 50)  # Зададим минимальный размер кнопки

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        layout.addWidget(self.start_debate_button)

        self.setLayout(layout)




class DebateWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setAutoFillBackground(True)

        self.background_pixmap = QPixmap("assets/stage_background.png")
        self.user_pixmap = QPixmap("assets/user_avatar_no_bg.png")
        self.kant_pixmap = QPixmap("assets/kant_avatar_no_bg.png")

        self.init_ui_elements()

    def init_ui_elements(self):
        """Создаем все "плавающие" элементы."""

        self.user_avatar_label = QLabel(self)
        self.user_avatar_label.setPixmap(self.user_pixmap)

        self.kant_avatar_label = QLabel(self)
        self.kant_avatar_label.setPixmap(self.kant_pixmap)

    def resizeEvent(self, event):
        """Пересчитываем позиции и размеры при изменении окна."""
        super().resizeEvent(event)

        avatar_height = int(self.height() * 0.6)  
        scaled_user_pixmap = self.user_pixmap.scaledToHeight(avatar_height, Qt.TransformationMode.SmoothTransformation)
        scaled_kant_pixmap = self.kant_pixmap.scaledToHeight(avatar_height, Qt.TransformationMode.SmoothTransformation)

        self.user_avatar_label.setPixmap(scaled_user_pixmap)
        self.kant_avatar_label.setPixmap(scaled_kant_pixmap)

        self.user_avatar_label.adjustSize()
        self.kant_avatar_label.adjustSize()

        user_x = int(self.width() * 0.1)
        kant_x = self.width() - self.kant_avatar_label.width() - int(self.width() * 0.1)

        avatar_y = self.height() - self.user_avatar_label.height() - int(self.height() * 0.1)

        self.user_avatar_label.move(user_x, avatar_y)
        self.kant_avatar_label.move(kant_x, avatar_y)

    def paintEvent(self, event):
        """Рисуем фон."""
        painter = QPainter(self)
        scaled_pixmap = self.background_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                      Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(self.rect(), scaled_pixmap)