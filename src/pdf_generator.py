import os
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

def generate_debate_report(transcript_lines, topic, winner, filepath="debate_report.pdf"):
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
