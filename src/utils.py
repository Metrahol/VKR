import pyttsx3
from PySide6 import QtCore


class Speaker(QtCore.QObject):
    """
    Класс-рабочий для озвучки текста в отдельном потоке.
    Это гарантирует, что GUI никогда не будет заблокирован.
    """
    speech_started = QtCore.Signal(str)
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
            self.sequence_finished.emit()  
            return

        self.sentences_queue = sentences
        self._speak_next_sentence()

    def _speak_next_sentence(self):
        """Озвучивает следующее предложение из очереди."""
        if self.sentences_queue:
            sentence = self.sentences_queue.pop(0).strip()
            if sentence:
                self.speech_started.emit(sentence)
                try:
                    self.tts_engine.say(sentence)
                    self.tts_engine.runAndWait()
                    QtCore.QTimer.singleShot(50, self._speak_next_sentence)
                except Exception as e:
                    print(f"Ошибка во время озвучки: {e}")
                    QtCore.QTimer.singleShot(50, self._speak_next_sentence)

            else:  
                self._speak_next_sentence()
        else:
            self.sequence_finished.emit()

    @QtCore.Slot()
    def shutdown(self):
        """Слот для корректного завершения работы."""
        print("Получена команда на остановку потока озвучки.")
        self.sentences_queue.clear()
        if self.tts_engine:
            self.tts_engine.stop()



def speech_to_text():
    pass  