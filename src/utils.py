# utils.py

import pyttsx3
from PySide6 import QtCore


class Speaker(QtCore.QObject):
    """
    Класс-рабочий для озвучки текста в отдельном потоке.
    Это гарантирует, что GUI никогда не будет заблокирован.
    """
    # Сигнал, который отправляется в главный поток, когда начинается озвучка предложения.
    speech_started = QtCore.Signal(str)
    # Сигнал, который отправляется, когда вся пачка предложений озвучена.
    sequence_finished = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.tts_engine = None
        self.sentences_queue = []

    @QtCore.Slot()
    def initialize_engine(self):
        """Инициализирует движок TTS внутри рабочего потока."""
        try:
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 150)
            self.tts_engine.setProperty('volume', 0.9)
            print("TTS движок успешно инициализирован в рабочем потоке.")
        except Exception as e:
            self.tts_engine = None
            print(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось инициализировать TTS движок: {e}")

    @QtCore.Slot(list)
    def speak_sequence(self, sentences):
        """
        Получает список предложений и начинает их последовательную озвучку.
        """
        if not self.tts_engine:
            print("Озвучка невозможна, движок не инициализирован.")
            self.sequence_finished.emit()  # Сообщаем, что "закончили" (не начав)
            return

        self.sentences_queue = sentences
        self._speak_next_sentence()

    def _speak_next_sentence(self):
        """Озвучивает следующее предложение из очереди."""
        if self.sentences_queue:
            sentence = self.sentences_queue.pop(0).strip()
            if sentence:
                # Отправляем сигнал НАЧАЛА озвучки в главный поток
                self.speech_started.emit(sentence)
                try:
                    # Эта блокирующая операция теперь безопасна, так как она в другом потоке!
                    self.tts_engine.say(sentence)
                    self.tts_engine.runAndWait()
                    # Рекурсивно вызываем себя для следующего предложения
                    QtCore.QTimer.singleShot(50, self._speak_next_sentence)
                except Exception as e:
                    print(f"Ошибка во время озвучки: {e}")
                    # Все равно пытаемся продолжить или завершить
                    QtCore.QTimer.singleShot(50, self._speak_next_sentence)

            else:  # Если предложение пустое, пропускаем
                self._speak_next_sentence()
        else:
            # Очередь пуста, отправляем сигнал ЗАВЕРШЕНИЯ в главный поток
            self.sequence_finished.emit()

    @QtCore.Slot()
    def shutdown(self):
        """Слот для корректного завершения работы."""
        print("Получена команда на остановку потока озвучки.")
        self.sentences_queue.clear()
        if self.tts_engine:
            self.tts_engine.stop()


# Функция speech_to_text остается без изменений
def speech_to_text():
    # Эта функция остается такой, какой была в твоих рабочих версиях
    pass  # Заглушка, используй свою версию