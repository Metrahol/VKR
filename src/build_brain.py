import os
import chromadb
from chromadb.utils import embedding_functions
import re

DB_PATH = "./philosophers_db"
client = chromadb.PersistentClient(path=DB_PATH)

embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

def get_collection_name(name):
    transl = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',' ':'_','-':'_'}
    res = ''.join(transl.get(c, c) for c in name.lower())
    return re.sub(r'[^a-z0-9._-]', '', res)

def chunk_text(text, chunk_size=600, overlap=100):
    chunks = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            search_window = text[max(start, end - 200):end]
            split_point = max(search_window.rfind('.'), search_window.rfind('\n'))
            if split_point != -1:
                end = max(start, end - 200) + split_point + 1
            else:
                last_space = search_window.rfind(' ')
                if last_space != -1:
                    end = max(start, end - 200) + last_space + 1
        if end <= start:
            end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = max(start + 1, end - overlap)
    return chunks

def build_brain_for_philosopher(philosopher_name, file_path):
    print(f"\n[{philosopher_name}] Читаем файл {file_path}...")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='cp1251') as f:
            text = f.read()

    chunks = chunk_text(text)
    collection_name = get_collection_name(philosopher_name)

    print(f"[{philosopher_name}] Создаем коллекцию '{collection_name}' и векторизуем {len(chunks)} чанков. Это займет пару минут...")

    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_function
    )

    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i + batch_size]
        batch_ids = [f"{collection_name}_chunk_{j}" for j in range(i, i + len(batch_chunks))]
        collection.add(documents=batch_chunks, ids=batch_ids)

    print(f"[{philosopher_name}] Успешно добавлено в базу!")

if __name__ == "__main__":
    philosophers_files = {
        "Фрейд": "books/фрейд.txt",
        "Ротбард": "books/ротбард.txt",
        "Гай Юлий Цезарь": "books/цезарь.txt",
        "Уильям Оккам": "books/оккам.txt",
    }

    for name, path in philosophers_files.items():
        if os.path.exists(path):
            build_brain_for_philosopher(name, path)
        else:
            print(f"ОШИБКА: Файл {path} не найден! Проверь, лежит ли он в папке books.")