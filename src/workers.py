import sys
import re
import os
import uuid
import threading
import time
import hashlib
from gtts import gTTS
import pygame
import numpy as np
from PySide6 import QtCore
import asyncio


class AgentWorker(QtCore.QObject):
    generation_complete = QtCore.Signal(str, dict)
    generation_chunk = QtCore.Signal(str, dict)

    @QtCore.Slot(str, object, dict)
    def generate(self, prompt, model_or_manager, metadata):
        """
        Генерация текста через DeepSeek API.
        metadata должен содержать:
          - 'deepseek_manager': экземпляр DeepSeekManager
          - 'system_prompt': системный промпт для роли
          - 'role': 'moderator' | 'opponent' | 'jury'
          - 'is_stream': True/False (для потоковой генерации)
        Для обратной совместимости prompt используется как user_prompt.
        """
        print(f"WORKER_AGENT: Получил задачу. Начинаю генерацию...")
        start_time = time.time()
        try:
            ds = metadata.get('deepseek_manager')
            system_prompt = metadata.get('system_prompt', '')
            role = metadata.get('role', 'opponent')
            is_stream = metadata.get('is_stream', False)

            if not ds:
                self.generation_complete.emit("Ошибка: DeepSeekManager не передан", metadata)
                return

            if not is_stream:
                if role == 'moderator':
                    generated_text = ds.generate_moderator(system_prompt, prompt)
                elif role == 'jury':
                    generated_text = ds.generate_jury(system_prompt, prompt)
                else:
                    generated_text = ds.generate_opponent(system_prompt, prompt, stream=False)

                print(f"WORKER_AGENT: Генерация завершена за {time.time() - start_time:.2f} сек.")
                self.generation_complete.emit(generated_text, metadata)
                return

            stream = ds.generate_opponent(system_prompt, prompt, stream=True)
            full_text = ""
            current_buffer = ""

            for text_chunk in stream:
                full_text += text_chunk
                current_buffer += text_chunk

                if any(p in current_buffer for p in ['. ', '! ', '? ', '\n']):
                    parts = re.split(r'(?<=[.!?\n])\s+', current_buffer)
                    for i in range(len(parts) - 1):
                        sentence = parts[i].strip()
                        if sentence:
                            self.generation_chunk.emit(sentence, metadata)
                    current_buffer = parts[-1]

            if current_buffer.strip():
                self.generation_chunk.emit(current_buffer.strip(), metadata)

            clean_text = re.sub(r'<think>.*?</think>', '', full_text, flags=re.DOTALL).strip()

            print(f"WORKER_AGENT: Потоковая генерация завершена за {time.time() - start_time:.2f} сек.")
            self.generation_complete.emit(clean_text, metadata)
        except Exception as e:
            print(f"WORKER_AGENT: Ошибка при генерации текста: {e}")
            error_message = f"Произошла ошибка при обращении к модели: {e}"
            self.generation_complete.emit(error_message, metadata)



class SpeakerWorker(QtCore.QObject):
    speech_started = QtCore.Signal(str)
    sequence_finished = QtCore.Signal()

    def __init__(self):
        super().__init__()
        import queue
        self._is_speaking = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._text_queue = queue.Queue()
        self._playback_queue = queue.Queue()
        self._tts_thread = None
        self._playback_thread = None
        self._current_voice = "ru-RU-DmitryNeural"
        self._current_rate = "-15%"
        self._current_pitch = "+0Hz"

        self.cache_dir = "audio_cache"
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

        try:
            pygame.mixer.init(frequency=24000, size=-16, channels=1)
            print("WORKER_SPEAKER: Pygame mixer успешно инициализирован.")
        except Exception as e:
            pygame.mixer = None

    @QtCore.Slot()
    def initialize_engine(self):
        print("WORKER_SPEAKER (Async+Cache): Готов к работе.")
        pass

    @QtCore.Slot()
    def stop_current_speech(self):
        """
        Принудительно останавливает текущую озвучку и очищает очередь.
        """
        print("WORKER_SPEAKER: Получен сигнал принудительной остановки.")
        self._stop_event.set()
        while not self._text_queue.empty(): self._text_queue.get()
        while not self._playback_queue.empty(): self._playback_queue.get()
        if pygame.mixer:
            pygame.mixer.stop()

    @QtCore.Slot(str, str, str)
    def set_voice(self, voice_id, rate, pitch):
        """Устанавливает текущий голос для озвучки."""
        with self._lock:
            self._current_voice = voice_id
            self._current_rate = rate
            self._current_pitch = pitch

    def clean_and_split(self, text_list):
        full_text = ' '.join(text_list)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', full_text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = text.replace('…', '...').strip()
        text = text.replace('*', '')

        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []
        for sentence in sentences:
            if len(sentence) > 400:
                parts = re.split(r'(?<=[,:;])\s+', sentence)
                result.extend(p for p in parts if p.strip())
            elif sentence.strip():
                result.append(sentence)
        return [s.strip() for s in result if s.strip()]

    def _get_sound_from_file(self, file_path, speed=1.15):
        try:
            sound = pygame.mixer.Sound(file_path)
            audio_data = pygame.sndarray.samples(sound)
            step = speed if speed != 0 else 1.0
            new_indices = np.arange(0, len(audio_data), step).astype(int)
            new_indices = new_indices[new_indices < len(audio_data)]
            fast_data = audio_data[new_indices]
            return pygame.sndarray.make_sound(fast_data)
        except Exception as e:
            print(f"WORKER_SPEAKER: Не удалось ускорить аудио: {e}. Воспроизвожу с обычной скоростью.")
            return pygame.mixer.Sound(file_path)

    def _get_or_create_audio_file(self, text, voice_id, rate, pitch):
        text_hash = hashlib.md5((text + voice_id + rate + pitch).encode('utf-8')).hexdigest()
        cache_file_path = os.path.join(self.cache_dir, f"{text_hash}.mp3")

        if os.path.exists(cache_file_path):
            print(f"WORKER_SPEAKER: [Cache HIT] Загрузка из кэша для: '{text[:30]}...'")
            return cache_file_path
        else:
            print(f"WORKER_SPEAKER: [Cache MISS] Генерация edge-tts для: '{text[:30]}...'")
            start_time = time.time()
            try:
                import edge_tts
                communicate = edge_tts.Communicate(text, voice_id, rate=rate, pitch=pitch)
                asyncio.run(communicate.save(cache_file_path))
                print(f"WORKER_SPEAKER: edge-tts сгенерирован за {time.time() - start_time:.2f} сек.")
                return cache_file_path
            except Exception as e:
                print(f"WORKER_SPEAKER: Ошибка генерации edge-tts: {e}")
                return None

    @QtCore.Slot()
    def start_stream(self):
        with self._lock:
            if self._is_speaking:
                self.stop_current_speech()
                if self._tts_thread: self._tts_thread.join(timeout=1.0)
                if self._playback_thread: self._playback_thread.join(timeout=1.0)
            self._is_speaking = True

        self._stop_event.clear()
        import queue
        while not self._text_queue.empty(): self._text_queue.get()
        while not self._playback_queue.empty(): self._playback_queue.get()

        self._tts_thread = threading.Thread(target=self._tts_worker)
        self._tts_thread.daemon = True
        self._tts_thread.start()

        self._playback_thread = threading.Thread(target=self._playback_worker)
        self._playback_thread.daemon = True
        self._playback_thread.start()

    @QtCore.Slot(str)
    def append_stream_text(self, text):
        chunks = self.clean_and_split([text])
        for c in chunks:
            self._text_queue.put(c)

    @QtCore.Slot()
    def finish_stream(self):
        self._text_queue.put(None)

    @QtCore.Slot(list)
    def speak_sequence(self, sentences):
        if not pygame.mixer:
            self.sequence_finished.emit()
            return

        self.start_stream()
        for s in sentences:
            self.append_stream_text(s)
        self.finish_stream()

    def _tts_worker(self):
        import queue
        voice_id = self._current_voice
        rate = self._current_rate
        pitch = self._current_pitch
        while not self._stop_event.is_set():
            try:
                text = self._text_queue.get(timeout=0.1)
                if text is None:
                    self._playback_queue.put(None)
                    break

                file_path = self._get_or_create_audio_file(text, voice_id, rate, pitch)
                if file_path:
                    sound_object = self._get_sound_from_file(file_path, speed=1.15)
                    self._playback_queue.put({'sound': sound_object, 'text': text})
            except queue.Empty:
                continue

    def _playback_worker(self):
        import queue
        try:
            while not self._stop_event.is_set():
                try:
                    item = self._playback_queue.get(timeout=0.1)
                    if item is None:
                        break

                    self.speech_started.emit(item['text'])
                    item['sound'].play()

                    playback_start_time = time.time()
                    while time.time() - playback_start_time < item['sound'].get_length():
                        if self._stop_event.is_set():
                            pygame.mixer.stop()
                            break
                        time.sleep(0.01)
                except queue.Empty:
                    continue
        finally:
            with self._lock:
                self._is_speaking = False

            if not self._stop_event.is_set():
                print("WORKER_SPEAKER: Последовательность завершена естественным образом.")
                self.sequence_finished.emit()
            else:
                print("WORKER_SPEAKER: Последовательность была прервана.")

    @QtCore.Slot()
    def shutdown(self):
        print("WORKER_SPEAKER: Завершение работы...")
        self.stop_current_speech()
        if self._tts_thread and self._tts_thread.is_alive():
            self._tts_thread.join(timeout=1.0)
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)
        if pygame.mixer:
            pygame.mixer.quit()

    def is_busy(self):
        """Проверяет, занят ли воркер озвучкой в данный момент."""
        with self._lock:
            return self._is_speaking