import os

def extract_text(filepath):
    """
    Извлекает текст из различных форматов файлов.
    Поддерживаемые форматы: .txt, .pdf, .docx
    """
    ext = os.path.splitext(filepath)[1].lower()
    
    try:
        if ext == '.txt':
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
                
        elif ext == '.pdf':
            import fitz # PyMuPDF
            doc = fitz.open(filepath)
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            return text
            
        elif ext == '.docx':
            import docx
            doc = docx.Document(filepath)
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            return text
            
        else:
            raise ValueError(f"Неподдерживаемый формат файла: {ext}")
            
    except Exception as e:
        return f"Ошибка чтения файла: {str(e)}"
