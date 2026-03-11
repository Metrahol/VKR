import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ленивая инициализация базы и тяжелых нейронок
_chroma_client = None
_sent_tf_ef_instance = None

def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        print("[RAG] Подключение к локальной БД ChromaDB...")
        _chroma_client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "philosophers_db"))
    return _chroma_client

# Ленивая загрузка модели, чтобы не тормозить запуск приложения
_sent_tf_ef_instance = None

def get_embedding_function():
    global _sent_tf_ef_instance
    if _sent_tf_ef_instance is None:
        from chromadb.utils import embedding_functions
        print("[RAG] Инициализация локальной модели SentenceTransformers (займет пару секунд)...")
        _sent_tf_ef_instance = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _sent_tf_ef_instance


# Философы, для которых мы используем только веб-поиск (нет локальных книг)
WEB_ONLY_PHILOSOPHERS = {
    "Альберт Эйнштейн", 
    "Исаак Ньютон", 
    "Сократ", 
    "Стив Джобс", 
    "Аль-Газали", 
    "Диоген"
}

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

# Маппинг полных имён → имён коллекций в ChromaDB
# (если коллекция была создана под коротким именем)
RAG_NAME_MAP = {
    "Зигмунд Фрейд": "Фрейд",
    "Мюррей Ротбард": "Ротбард",
}


def get_philosopher_context(philosopher_name, user_argument, top_k=3):
    """
    Получает контекст (цитаты/воспоминания) для философа.
    Если философ в списке WEB_ONLY_PHILOSOPHERS, использует онлайн-поиск (DuckDuckGo).
    Иначе использует локальную векторную базу (ChromaDB).
    """
    
    if philosopher_name in WEB_ONLY_PHILOSOPHERS:
        # Онлайн поиск для философов без книг
        query = f"Философия взгляды цитаты {philosopher_name} {user_argument}"
        print(f"[{philosopher_name}] Использую веб-поиск для 'воспоминаний': {query}")
        # Получаем данные из сети
        context_text = get_web_context([query], max_results_per_query=top_k)
        if context_text == "No web context available":
            return "(Онлайн-память недоступна, опирайтесь на свои базовые знания и характер.)"
        return f"(Справка из сети по вашим взглядам на тему '{user_argument}'):\n{context_text}"
    
    # ---------------- Векторный поиск (ChromaDB) ----------------
    # Проверяем маппинг имён (если коллекция создана под другим именем)
    rag_name = RAG_NAME_MAP.get(philosopher_name, philosopher_name)
    collection_name = get_collection_name(rag_name)
    
    try:
        # Проверяем, существует ли коллекция
        client = get_chroma_client()
        collection = client.get_collection(name=collection_name, embedding_function=get_embedding_function())
    except Exception as e:
        print(f"[{philosopher_name}] Векторная база не найдена: {e}")
        return "(Воспоминания из книг недоступны. Опирайтесь на общие знания.)"

    try:
        # Ищем в Chroma, она сама сделает эмбеддинг текста запроса
        db_results = collection.query(
            query_texts=[user_argument],
            n_results=top_k
        )
        
        # Формируем текст
        if not db_results['documents'] or not db_results['documents'][0]:
            return "(Подходящих цитат в ваших трудах не найдено.)"
            
        context_chunks = db_results['documents'][0]
        formatted_context = "\n\n".join([f"Цитата {i+1}:\n{chunk}" for i, chunk in enumerate(context_chunks)])
        
        return formatted_context
        
    except Exception as e:
        print(f"[{philosopher_name}] Ошибка при поиске в векторной базе: {e}")
        return "(Произошла ошибка при поиске воспоминаний.)"

def get_web_context(queries_list, max_results_per_query=2):
    """
    Извлекает информацию из поисковика DuckDuckGo для переданного списка запросов.
    """
    if not queries_list:
        return "No web context available"
        
    from duckduckgo_search import DDGS
    ddgs = DDGS()
    all_results = []
    
    for query in queries_list:
        try:
            print(f"[Web-RAG] Поиск в DuckDuckGo: '{query}'...")
            # Получаем результаты
            results = ddgs.text(query, max_results=max_results_per_query)
            
            for index, r in enumerate(results):
                body = r.get("body", "")
                if body:
                    all_results.append(f"- {body}")
                    
        except Exception as e:
            print(f"[Web-RAG] Ошибка сети при поиске '{query}': {e}")
            continue
            
    if not all_results:
        return "No web context available"
        
    # Склеиваем результаты
    return "\n".join(all_results)

if __name__ == "__main__":
    # Тестирование модуля
    print("Тест WEB-ONLY философа (Сократ):")
    ctx = get_philosopher_context("Сократ", "что такое справедливость?")
    print(ctx)
    print("\n" + "="*50 + "\n")
    
    print("Тест Векторного философа (Карл Маркс - БД должна быть создана предварительно):")
    ctx2 = get_philosopher_context("Карл Маркс", "что такое справедливость?")
    print(ctx2)
    print("\n" + "="*50 + "\n")
    
    print("Тест Жюри (Поиск фактов):")
    jury_facts = get_web_context(["в каком году началась французская революция?"])
    print(jury_facts)
