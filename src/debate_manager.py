import random
from moderator_replics import MODERATOR_LINES

class DebateManager:
    def __init__(self, topic, user_name="Артём", opponent_name="Иммануил Кант", time_limit_sec=60, rounds_count=3):
        self.user_name = user_name
        self.opponent_name = opponent_name

        self.p1_name = self.user_name
        self.p2_name = self.opponent_name

        self.topic = topic
        self.time_limit_sec = time_limit_sec
        self.rounds_count = rounds_count
        self.transcript = []

        self.opponent_declared_position = None
        self.user_declared_position = None

    def set_positions_after_opening(self, user_opening: str, opponent_opening: str):
        self.user_declared_position = user_opening[:300] if user_opening else "не озвучена"
        self.opponent_declared_position = opponent_opening[:300] if opponent_opening else "не озвучена"

    def _position_anchor_block(self) -> str:
        if self.opponent_declared_position and self.opponent_declared_position != "не озвучена":
            return (
                "⚠️ НАПОМИНАНИЕ ПОЗИЦИЙ:\n"
                f"ТЫ защищаешь: «{self.opponent_declared_position[:200]}»\n"
                f"Твой ВРАГ защищает: «{self.user_declared_position[:200]}»\n"
                "Твой вопрос ОБЯЗАН атаковать позицию ВРАГА.\n\n"
            )
        return ""

    def _add_to_transcript(self, speaker, text):
        line = f"[{speaker}]: {text}"
        self.transcript.append(line)
        print(f"TRANSCRIPT: {line}") 

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
        transcript_str = "\n".join(self.transcript[-6:]) 
        instruction = "Ты Академический Рецензент. Опираясь на СВОИ ПРАВИЛА, изучи историю общения и задай свой СЛЕДУЮЩИЙ каверзный вопрос."
        return f"[ИНСТРУКЦИЯ]: {instruction}\n\n[ИСТОРИЯ ОБСУЖДЕНИЯ]:\n{transcript_str}"

    def get_clash_leader_prompt(self, last_responder_speech):
        anchor = self._position_anchor_block()
        instruction = (
            f"{anchor}\n"
            "───── ФАЗА 2: РАУНД ПОЛЕМИКИ ─────\n"
            ">> РЕЖИМ АТАКИ (ты задаёшь вопросы):\n"
            "ТЫ ВЕДУЩИЙ этого раунда. Твоя задача — нанести точечный логический удар по позиции противника.\n"
            "ПРАВИЛО АНТИ-ХАМЕЛЕОН: Твой вопрос должен исходить СТРОГО из ТВОЕЙ позиции. "
            "ЗАПРЕЩЕНО задавать вопросы, которые звучат так, будто ты соглашаешься с оппонентом или сомневаешься в своей правоте. "
            "Не поддакивай его логике!\n\n"
            "🚫 ОБЯЗАТЕЛЬНАЯ САМОПРОВЕРКА (3 шага)\n"
            "Шаг 1: Сформулируй вопрос\n"
            "Шаг 2: Представь что оппонент блестяще ответил — это усиливает ЕГО позицию или ТВОЮ?\n"
            "Шаг 3: Если усиливает ЕГО → САМОСТРЕЛ → удали и придумай другой\n\n"
            "❌ Пример САМОСТРЕЛА (если ты защищаешь науку): 'Разве наука не убивает чувства?'\n"
            "✅ Пример ВЕРНО (если ты защищаешь науку): 'Разве ваши хваленые чувства способны вылечить болезнь?'\n\n"
            "ЗАДАЙ РОВНО ОДИН, ОЧЕНЬ КОРОТКИЙ (20-25 слов) И КАВЕРЗНЫЙ ВОПРОС, который вскрывает слабость его последней реплики.\n"
            "ВЫВЕДИ ТОЛЬКО ВОПРОС. Без преамбулы, без рассуждений."
        )
        return f"[ИНСТРУКЦИЯ]: {instruction}\n[РЕПЛИКА ОППОНЕНТА ДЛЯ АТАКИ]: {last_responder_speech}"

    def get_clash_responder_prompt(self, last_leader_speech):
        instruction = (
            "───── ФАЗА 2: РАУНД ПОЛЕМИКИ ─────\n"
            ">> РЕЖИМ ЗАЩИТЫ (тебе задают вопросы):\n"
            "ТЫ ОТВЕЧАЮЩИЙ. Оппонент пытается загнать тебя в ловушку.\n"
            "ЖЕСТКИЙ ЗАПРЕТ: НИКОГДА не соглашайся с его доводами, не иди на компромисс и не признавай его правоту. "
            "Твоя задача — КРАТКО (до 50 слов) отбить атаку, переосмыслить его аргумент в свою пользу и защитить свою позицию.\n"
            "ЗАПРЕЩЕНО задавать встречные вопросы — только уверенный, железобетонный ответ."
        )
        return f"[ИНСТРУКЦИЯ]: {instruction}\n[АТАКА ОППОНЕНТА (ОТБЕЙ ЕЁ)]: {last_leader_speech}"

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

КРИТЕРИИ ОЦЕНКИ (Макс 100 баллов на участника):

1. MATTER / Содержание (Макс 40 баллов):
  - 1a. Аргументация (0-10): Сила, логичность и доказанность тезисов.
  - 1b. Полемика (Clash) (0-15): Качество контраргументации и способность отбивать атаки.
  - 1c. Качество ответов (0-10): Глубина и точность ответов на прямые вопросы.
  - 1d. Последовательность (Consistency) (0-5): Отсутствие логических дыр и противоречий самому себе.

2. MANNER / Подача (Макс 20 баллов):
  - 2a. Риторические приёмы (0-10):
    * 0-3: 0 приёмов. Говорит как инструкция: "я думаю X, потому что Y".
    * 4-7: 1 приём, использованный хотя бы раз.
    * 8-11: 2 разных приёма, использованных уместно (усиливают аргумент, а не просто украшают).
    * 12-15: 3+ разных приёма, ИЛИ 2, но хотя бы одна формулировка запоминается как цитата вне контекста дебатов.
    (Адаптируй шкалу до 0-10: 0-2 баллов за 0 приемов, 3-5 за 1 прием, 6-8 за 2 приема, 9-10 за 3+ приема)
  - 2b. Лексическое разнообразие (0-10): Богатство языка, словарный запас, соответствие стилю роли.

3. METHOD / Стратегия (Макс 40 баллов):
  - 3a. Coherence / Связность (0-10): Четкость структуры ответов.
  - 3b. Targeting / Фокус (0-10): Умение бить в суть проблемы оппонента, не отвлекаясь на мелочи.
  - 3c. Качество вопросов в полемике (0-20): Проверяй ТОЛЬКО Фазу 2, ТОЛЬКО реплики где участник ЗАДАЁТ вопрос оппоненту.
    * 0-5: Вопросы общие и легко парируемые ("а вы не думали что...?"). Оппонент ответил без затруднений.
    * 6-10: Вопросы конкретные, но бьют по второстепенным деталям, а не по фундаменту позиции оппонента.
    * 11-15: Хотя бы 1 вопрос поставил оппонента в затруднительное положение (оппонент в ответе уклонился, ответил расплывчато или сменил тему).
    * 16-20: Хотя бы 1 вопрос вскрыл ФУНДАМЕНТАЛЬНОЕ противоречие или необоснованное допущение в позиции оппонента, И оппонент НЕ СМОГ его полноценно закрыть.

ВЫХОДНОЙ ФОРМАТ:
Ты ОБЯЗАН вернуть ответ ИСКЛЮЧИТЕЛЬНО в формате валидного JSON без markdown-разметки и лишнего текста. 
НЕ считай суммы — выставляй ТОЛЬКО подкритерии. Суммы будут посчитаны программно.

Структура JSON:
{{
  "user_scores": {{
    "matter_argumentation": 0,
    "matter_clash": 0,
    "matter_answers": 0,
    "matter_consistency": 0,
    "manner_rhetoric": 0,
    "manner_language": 0,
    "method_coherence": 0,
    "method_targeting": 0,
    "method_questions": 0
  }},
  "opponent_scores": {{
    "matter_argumentation": 0,
    "matter_clash": 0,
    "matter_answers": 0,
    "matter_consistency": 0,
    "manner_rhetoric": 0,
    "manner_language": 0,
    "method_coherence": 0,
    "method_targeting": 0,
    "method_questions": 0
  }},
  "winner": "Пользователь / Оппонент / Ничья",
  "verdict_explanation": "Краткое обоснование на 3-4 предложения: почему победила именно эта сторона и в чем была главная ошибка проигравшего."
}}

--- СТЕНОГРАММА ---
{transcript_str}
"""