"""
settings_manager.py
-------------------
Менеджер настроек приложения «Интеллектуальный Гладиатор».

Использует QSettings (формат INI) для постоянного хранения пользовательских
настроек между запусками. Файл сохраняется рядом с исполняемым скриптом.

Доступные настройки:
    microphone_name   (str)  — имя выбранного устройства ввода
    tts_volume        (int)  — громкость голоса оппонента, 0–100 (%)
    music_volume      (int)  — громкость фоновой музыки, 0–100 (%)
    speech_rate       (str)  — скорость речи: "Медленная" | "Обычная" | "Быстрая"
    subtitle_font_size(int)  — размер шрифта субтитров, 14–24 (px)
"""

import os
from PySide6.QtCore import QSettings

# Путь к INI-файлу рядом с корнем проекта
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETTINGS_FILE = os.path.join(_BASE_DIR, "app_settings.ini")

# Скорость речи → параметр rate для edge-tts
SPEECH_RATE_MAP = {
    "Медленная": "-30%",
    "Обычная":   "-15%",
    "Быстрая":   "+5%",
}

# Допустимые значения по умолчанию
_DEFAULTS = {
    "microphone_name":    "",
    "tts_volume":         80,
    "music_volume":       15,
    "speech_rate":        "Обычная",
    "subtitle_font_size": 18,
}


class SettingsManager:
    """
    Тонкая обёртка над QSettings.

    Пример использования:
        sm = SettingsManager()
        sm.tts_volume         # → int
        sm.tts_volume = 60    # сохраняется немедленно
    """

    def __init__(self):
        self._qs = QSettings(_SETTINGS_FILE, QSettings.Format.IniFormat)

    # ------------------------------------------------------------------
    # Универсальные get / set / reset
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        """Читает значение из QSettings, приводя к типу значения по умолчанию."""
        fallback = _DEFAULTS.get(key, default)
        raw = self._qs.value(key, fallback)
        # QSettings возвращает строки; приводим к нужному типу
        if isinstance(fallback, int):
            try:
                return int(raw)
            except (ValueError, TypeError):
                return fallback
        return raw

    def set(self, key: str, value) -> None:
        """Записывает значение в QSettings и немедленно сбрасывает на диск."""
        self._qs.setValue(key, value)
        self._qs.sync()

    def reset_to_defaults(self) -> None:
        """Сбрасывает все настройки к значениям по умолчанию."""
        for key, value in _DEFAULTS.items():
            self._qs.setValue(key, value)
        self._qs.sync()

    # ------------------------------------------------------------------
    # Свойства-ярлыки для удобства
    # ------------------------------------------------------------------

    @property
    def microphone_name(self) -> str:
        return self.get("microphone_name")

    @microphone_name.setter
    def microphone_name(self, value: str) -> None:
        self.set("microphone_name", value)

    @property
    def tts_volume(self) -> int:
        return self.get("tts_volume")

    @tts_volume.setter
    def tts_volume(self, value: int) -> None:
        self.set("tts_volume", max(0, min(100, int(value))))

    @property
    def music_volume(self) -> int:
        return self.get("music_volume")

    @music_volume.setter
    def music_volume(self, value: int) -> None:
        self.set("music_volume", max(0, min(100, int(value))))

    @property
    def speech_rate(self) -> str:
        return self.get("speech_rate")

    @speech_rate.setter
    def speech_rate(self, value: str) -> None:
        if value in SPEECH_RATE_MAP:
            self.set("speech_rate", value)

    @property
    def subtitle_font_size(self) -> int:
        """Размер шрифта субтитров; всегда в диапазоне 14–24."""
        return max(14, min(24, self.get("subtitle_font_size")))

    @subtitle_font_size.setter
    def subtitle_font_size(self, value: int) -> None:
        self.set("subtitle_font_size", max(14, min(24, int(value))))

    def get_edge_tts_rate(self) -> str:
        """Возвращает параметр rate для edge-tts на основе текущей скорости речи."""
        return SPEECH_RATE_MAP.get(self.speech_rate, "-15%")
