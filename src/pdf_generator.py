import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

def generate_debate_report(transcript_lines, topic, winner, verdict_data=None, filepath="debate_report.pdf"):
    """
    Генерирует PDF-отчет со стенограммой дебатов с помощью ReportLab.
    Использует шрифты DejaVu для поддержки кириллицы.
    """
    font_path = "C:\\Windows\\Fonts\\calibri.ttf"
    font_bold_path = "C:\\Windows\\Fonts\\calibrib.ttf"
    
    has_fonts = False
    if os.path.exists(font_path) and os.path.exists(font_bold_path):
        pdfmetrics.registerFont(TTFont('Calibri', font_path))
        pdfmetrics.registerFont(TTFont('Calibri-Bold', font_bold_path))
        has_fonts = True
    
    font_regular = 'Calibri' if has_fonts else 'Helvetica'
    font_bold = 'Calibri-Bold' if has_fonts else 'Helvetica-Bold'
    
    doc = SimpleDocTemplate(filepath, pagesize=letter)
    styles = getSampleStyleSheet()
    
    style_title = ParagraphStyle(
        name='TitleStyle',
        parent=styles['Heading1'],
        fontName=font_bold,
        fontSize=18,
        spaceAfter=15,
        alignment=TA_CENTER
    )
    
    style_header = ParagraphStyle(
        name='HeaderStyle',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=14,
        spaceAfter=10
    )
    
    style_body = ParagraphStyle(
        name='BodyStyle',
        parent=styles['Normal'],
        fontName=font_regular,
        fontSize=12,
        spaceAfter=6,
        leading=16
    )
    
    style_separator = ParagraphStyle(
        name='SeparatorStyle',
        parent=styles['Normal'],
        fontName=font_regular,
        fontSize=14,
        spaceAfter=15,
        spaceBefore=15,
        alignment=TA_CENTER
    )
    
    story = []
    
    # Заголовок
    story.append(Paragraph("Отчет о дебатах", style_title))
    
    # Тема и Победитель
    story.append(Paragraph(f"<b>Тема:</b> {topic}", style_header))
    story.append(Paragraph(f"<b>Вердикт Жюри (Победитель):</b> {winner}", style_header))

    # Детальные оценки жюри
    if verdict_data and isinstance(verdict_data, dict):
        story.append(Paragraph("<b>Детальные оценки жюри:</b>", style_header))
        
        # Функция для безопасного получения значений
        def get_score(scores_dict, key):
            return scores_dict.get(key, 0)
            
        u_scores = verdict_data.get('user_scores', {})
        o_scores = verdict_data.get('opponent_scores', {})
        
        roles = [
            ("Пользователь", u_scores),
            ("Оппонент", o_scores)
        ]
        
        for role_name, scores in roles:
            story.append(Spacer(1, 10))
            story.append(Paragraph(f"<b>Оценки ({role_name}):</b>", style_header))
            
            # Matter
            matter = get_score(scores, 'matter')
            matter_str = (f"• <b>Matter (Содержание) [{matter}]:</b> "
                         f"Аргументация: {get_score(scores, 'matter_argumentation')} | "
                         f"Полемика: {get_score(scores, 'matter_clash')} | "
                         f"Ответы: {get_score(scores, 'matter_answers')} | "
                         f"Последовательность: {get_score(scores, 'matter_consistency')}")
            story.append(Paragraph(matter_str, style_body))
            
            # Manner
            manner = get_score(scores, 'manner')
            manner_str = (f"• <b>Manner (Подача) [{manner}]:</b> "
                         f"Риторические приемы: {get_score(scores, 'manner_rhetoric')} | "
                         f"Язык: {get_score(scores, 'manner_language')}")
            story.append(Paragraph(manner_str, style_body))
            
            # Method
            method = get_score(scores, 'method')
            method_str = (f"• <b>Method (Стратегия) [{method}]:</b> "
                         f"Связность: {get_score(scores, 'method_coherence')} | "
                         f"Фокус: {get_score(scores, 'method_targeting')} | "
                         f"Вопросы: {get_score(scores, 'method_questions')}")
            story.append(Paragraph(method_str, style_body))
            
            # Total
            total = get_score(scores, 'total')
            story.append(Paragraph(f"<b>• ИТОГОВЫЙ БАЛЛ: {total}</b>", style_body))
        
        story.append(Spacer(1, 15))

    
    # Разделитель
    story.append(Paragraph("--- Полная стенограмма ---", style_separator))
    
    # Текст стенограммы
    for line in transcript_lines:
        clean_line = line.replace('\r\n', '\n').strip()
        if not clean_line:
            continue
        
        clean_line = clean_line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        if clean_line.startswith('['):
            parts = clean_line.split(']', 1)
            if len(parts) == 2:
                speaker = parts[0][1:]
                text = parts[1]
                clean_line = f"<b>[{speaker}]</b>{text}"
                
        story.append(Paragraph(clean_line, style_body))
        
    doc.build(story)
    return filepath
