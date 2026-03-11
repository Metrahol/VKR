import os
import time
import gc
import chromadb
import google.generativeai as genai
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настраиваем Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("API ключ Gemini не найден. Убедитесь, что в файле .env указан GEMINI_API_KEY.")

genai.configure(api_key=GEMINI_API_KEY)

# Настройки ChromaDB
DB_PATH = "./philosophers_db"
client = chromadb.PersistentClient(path=DB_PATH)

class NoOpEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:
        # Возвращаем пустой список, так как мы используем Gemini для эмбеддингов
        return []
    
    def name(self) -> str:
        return "noop"

def get_collection_name(name):
    transl = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        ' ': '_', '-': '_'
    }
    name = name.lower()
    res = ''.join(transl.get(c, c) for c in name)
    import re
    res = re.sub(r'[^a-z0-9._-]', '', res)
    return res

def chunk_text(text, chunk_size=600, overlap=100):
    """
    Разбивает текст на чанки указанного размера с перекрытием.
    """
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = min(start + chunk_size, text_length)
        
        # Если мы не в конце текста, пытаемся найти ближайший конец предложения или пробел
        if end < text_length:
            # Ищем ближайшую точку или разрыв строки в последней четверти чанка
            search_window = text[max(start, end - 200):end]
            last_period = search_window.rfind('.')
            last_newline = search_window.rfind('\n')
            
            split_point = max(last_period, last_newline)
            if split_point != -1:
                # Корректируем end относительно начала текста, а не окна
                end = max(start, end - 200) + split_point + 1
            else:
                # Ищем хотя бы пробел
                last_space = search_window.rfind(' ')
                if last_space != -1:
                    end = max(start, end - 200) + last_space + 1
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
            
        start = end - overlap
        
    return chunks

def build_brain_for_philosopher(philosopher_name, file_path):
    """
    Читает книгу философа, разбивает на чанки, получает эмбеддинги и сохраняет в ChromaDB.
    """
    print(f"[{philosopher_name}] Начинаем создание 'мозга'...")
    
    if not os.path.exists(file_path):
        print(f"Ошибка: Файл {file_path} не найден.")
        return

    # Читаем текст
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        print(f"[{philosopher_name}] Кодировка UTF-8 не подошла, пробуем cp1251...")
        with open(file_path, 'r', encoding='cp1251') as f:
            text = f.read()
    print(f"[{philosopher_name}] Текст загружен. Длина: {len(text)} символов.")
    
    # Разбиваем на чанки
    chunks = chunk_text(text)
    print(f"[{philosopher_name}] Текст разбит на {len(chunks)} чанков.")
    
    # Получаем или создаем коллекцию
    # Имена коллекций в ChromaDB не должны содержать пробелов и спецсимволов. 
    # В новых версиях ChromaDB разрешает ТОЛЬКО английские буквы и цифры.
    collection_name = get_collection_name(philosopher_name)
    print(f"[{philosopher_name}] Имя коллекции: {collection_name}")
    
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"description": f"Knowledge base for {collection_name}"},
        embedding_function=NoOpEmbeddingFunction()
    )
    
    # Эмбеддинги и сохранение батчами (ChromaDB и Gemini API лимиты)
    # Снижаем batch_size с 100 до 20, чтобы предотвратить загруженность памяти
    batch_size = 20
    
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i + batch_size]
        batch_ids = [f"{collection_name}_chunk_{j}" for j in range(i, i + len(batch_chunks))]
        
        print(f"[{philosopher_name}] Обработка батча {i//batch_size + 1}/{(len(chunks) + batch_size - 1)//batch_size}...")
        
        try:
            # Получаем эмбеддинги от Gemini
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=batch_chunks,
                task_type="retrieval_document"
            )
            embeddings = result['embedding']
            
            # Сохраняем в ChromaDB
            collection.add(
                embeddings=embeddings,
                documents=batch_chunks,
                ids=batch_ids
            )
            
            # Небольшая пауза для избежания Rate Limits
            time.sleep(1)
            
            # Принудительно чистим мусор, чтобы память не текла
            del embeddings
            del batch_chunks
            del batch_ids
            gc.collect()
            
        except Exception as e:
            print(f"Ошибка при обработке батча: {e}")
            
    print(f"[{philosopher_name}] Завершено! Векторная база сохранена в {DB_PATH}.")

if __name__ == "__main__":
    # Пример использования. Вы можете запустить этот скрипт для каждого файла.
    
    # Список философов и их файлов (можно дополнять)
    philosophers_files = {
        "Фома Аквинский": "books/аквинский.txt",
        "Джордж Вашингтон": "books/вашингтон.txt",
        "Чарльз Дарвин": "books/дарвин.txt",
        "Федор Достоевский": "books/достоевский.txt",
        "Иммануил Кант": "books/кант.txt",
        "Владимир Ленин": "books/ленин.txt",
        "Маймонид": "books/майонид.txt",
        "Никколо Макиавелли": "books/макиавелли.txt",
        "Карл Маркс": "books/маркс.txt",
        "Фридрих Ницше": "books/ницше.txt",
        "Иосиф Сталин": "books/сталин.txt",
        "Никола Тесла": "books/тесла.txt",
        "Лев Толстой": "books/толстой.txt",
    }
    
    # Чтобы запустить инджест для всех, раскомментируйте цикл:
    for name, path in philosophers_files.items():
        if os.path.exists(path):
            build_brain_for_philosopher(name, path)
            time.sleep(2) # Пауза между книгами
        else:
            print(f"Файл {path} не найден. Пропуск.")
