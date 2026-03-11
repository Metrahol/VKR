import random
from moderator_replics import MODERATOR_LINES

class DebateManager:
    def __init__(self, topic, user_name="Артём", opponent_name="Иммануил Кант"):
        self.user_name = user_name
        self.opponent_name = opponent_name

        self.p1_name = self.user_name
        self.p2_name = self.opponent_name

        self.topic = topic
        self.transcript = []

    def _add_to_transcript(self, speaker, text):
        line = f"[{speaker}]: {text}"
        self.transcript.append(line)
        print(f"TRANSCRIPT: {line}") # Добавим логгирование для отладки

    def get_setup_prompt(self):
        self._add_to_transcript("СИСТЕМА", f"Тема дебатов: {self.topic}")
        return MODERATOR_LINES["PROMPT"].format(
            topic=self.topic,
            user_name=self.user_name,
            opponent_name=self.opponent_name
        )

    def get_opponent_opening_prompt(self, user_speech):
        instruction = "───── ФАЗА 1: ВСТУПИТЕЛЬНОЕ СЛОВО ─────\nЭто была вступительная речь твоего оппонента (или ее не было, если ты говоришь первым). Произнеси свою вступительную речь, строго придерживаясь своей роли, личности и темы дебатов."
        return f"[ИНСТРУКЦИЯ]: {instruction}\n[РЕЧЬ ОППОНЕНТА]: {user_speech}"

    def get_critique_prompt(self):
        transcript_str = "\n".join(self.transcript[-6:]) # Последние 6 реплик для контекста
        instruction = "Ты Академический Рецензент. Опираясь на СВОИ ПРАВИЛА, изучи историю общения и задай свой СЛЕДУЮЩИЙ каверзный вопрос."
        return f"[ИНСТРУКЦИЯ]: {instruction}\n\n[ИСТОРИЯ ОБСУЖДЕНИЯ]:\n{transcript_str}"

    def get_clash_leader_prompt(self, last_responder_speech):
        instruction = f"───── ФАЗА 2: РАУНД ПОЛЕМИКИ ─────\n>> РЕЖИМ АТАКИ (ты задаёшь вопросы):\nТЫ ВЕДУЩИЙ этого раунда полемики. ЗАДАВАЙ ОЧЕНЬ КОРОТКИЙ (20-25 слов максимум) И ОДИН УТОЧНЯЮЩИЙ ВОПРОС, ВЫВОДЯЩИЙ ПРОТИВОРЕЧИЕ В ПОЗИЦИИ ПРОТИВНИКА. Твоя задача - атаковать, задавать острые вопросы, основываясь на последней реплике оппонента. ТОЛЬКО ОДИН ВОПРОС ЗАДАЙ."
        return f"[ИНСТРУКЦИЯ]: {instruction}\n[ПОСЛЕДНЯЯ РЕПЛИКА ОППОНЕНТА]: {last_responder_speech}"

    def get_clash_responder_prompt(self, last_leader_speech):
        instruction = f"───── ФАЗА 2: РАУНД ПОЛЕМИКИ ─────\n>> РЕЖИМ ЗАЩИТЫ (тебе задают вопросы):\nТЫ ОТВЕЧАЮЩИЙ в этом раунде полемики. Твой оппонент сейчас ведущий. Твоя задача - КРАТКО (Максимум 50 слов) и по существу ОТВЕТИТЬ на его реплику/вопрос. ЗАПРЕЩЕНО задавать встречные вопросы."
        return f"[ИНСТРУКЦИЯ]: {instruction}\n[РЕПЛИКА ВЕДУЩЕГО]: {last_leader_speech}"

    def get_jury_questions_prompt(self):
        transcript_str = "\n".join(self.transcript)
        return f"""
        Проанализируй стенограмму дебатов и сгенерируй по одному вопросу каждому участнику.
        Твой ответ ДОЛЖЕН БЫТЬ в строгом формате JSON с ключами "question_for_user" и "question_for_opponent".

        --- СТЕНОГРАММА ---
        {transcript_str}
        """

    def get_jury_answer_prompt(self, jury_question, for_opponent=True):
        if for_opponent:
            instruction = f"───── ФАЗА 3: ВОПРОС ОТ ЖЮРИ ─────\nКоллегия жюри задала тебе вопрос. Ответь на него четко и по существу, оставаясь в своей роли. Начни ответ, обращаясь к жюри."
            return f"[ИНСТРУКЦИЯ]: {instruction}\n[ВОПРОС ОТ ЖЮРИ]: {jury_question}"
        else:
            return ""

    def get_summary_prompt(self, for_opponent=True):
        instruction = "───── ФАЗА 4: ЗАКЛЮЧИТЕЛЬНОЕ СЛОВО ─────\nПроизнеси свою заключительную речь. Тебе нужно кратко подвести итоги и финальный раз убедить жюри в своей правоте. Не вводи новых аргументов."
        if for_opponent:
            return f"[ИНСТРУКЦИЯ]: {instruction}"
        else:
            return ""

    def get_final_verdict_prompt(self):
        # --- ИСПРАВЛЕНИЕ 4: Усиленный промпт для вердикта ---
        transcript_str = "\n".join(self.transcript)
        return f"""
        ЗАБУДЬ ВСЕ ЧТО БЫЛО ДО. Проанализируй следующую стенограмму дебатов и вынеси свой вердикт.
        Определи победителя, дай четкое обоснование и краткий фидбэк для пользователя.
        Твой ответ ДОЛЖЕН БЫТЬ в строгом формате JSON с ключами "winner", "reasoning" и "feedback_for_user".

        --- СТЕНОГРАММА ---
        {transcript_str}
        """

    def get_3m_verdict_prompt(self):
        transcript_str = "\n".join(self.transcript)
        return f"""
 Ты — беспристрастное независимое ИИ-жюри. Тебе предоставлена стенограмма дебатов между Пользователем и Оппонентом. Твоя задача — определить победителя, строго опираясь на международный стандарт оценки дебатов "Правило 3M" (Matter, Manner, Method).

КРИТЕРИИ ОЦЕНКИ (Максимум 100 баллов):
1. Matter / Содержание (макс. 40 баллов): Логичность аргументов, отсутствие противоречий, качество контраргументации (Clash) и способность отбивать атаки противника.
2. Manner / Подача (макс. 40 баллов): Риторика, богатство словарного запаса, убедительность, соответствие стилю (для философов — использование характерных метафор и концепций).
3. Method / Стратегия (макс. 20 баллов): Четкость структуры ответов, последовательность мысли, умение бить в суть проблемы.

ВЫХОДНОЙ ФОРМАТ:
Ты обязан вернуть ответ ИСКЛЮЧИТЕЛЬНО в формате валидного JSON без markdown-разметки и лишнего текста. Структура JSON:
{{
"user_scores": {{
"matter": 0,
"manner": 0,
"method": 0,
"total": 0
}},
"opponent_scores": {{
"matter": 0,
"manner": 0,
"method": 0,
"total": 0
}},
"winner": "Пользователь / Оппонент / Ничья",
"verdict_explanation": "Краткое обоснование на 3-4 предложения: почему победила именно эта сторона и в чем была главная ошибка проигравшего."
}}

--- СТЕНОГРАММА ---
{transcript_str}
"""