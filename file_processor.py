import os
import pytesseract
import fitz  # PyMuPDF
import docx
from pdf2image import convert_from_path
from PIL import Image
from google.cloud import vision
import io
import logging

# Set up Google Cloud credentials
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "skoolme-ocr-b933da63cd81.json"

logger = logging.getLogger(__name__)

class FileProcessor:
    def __init__(self):
        pass
    
    def process_file(self, file_path):
        """Process a file and extract text content"""
        file_extension = os.path.splitext(file_path)[1].lower()
        
        if file_extension == '.pdf':
            return self._extract_from_pdf(file_path)
        elif file_extension == '.docx':
            return self._extract_from_docx(file_path)
        elif file_extension == '.txt':
            return self._extract_from_txt(file_path)
        elif file_extension in ['.png', '.jpg', '.jpeg', '.bmp']:
            return self._extract_from_image(file_path)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}")
    
    def _extract_from_pdf(self, file_path):
        """Extract text from PDF file - matches original OCR.py logic"""
        text = ""
        
        # First try to extract text directly (same as original)
        doc = fitz.open(file_path)
        for page in doc:
            page_text = page.get_text()
            if page_text.strip():
                text += page_text
        doc.close()
        
        # If no text found, use OCR (same fallback as original)
        if not text.strip():
            images = convert_from_path(file_path)
            for img in images:
                text += self._ocr_with_google(img)
        
        return text
    
    def _extract_from_docx(self, file_path):
        """Extract text from DOCX file - matches original OCR.py logic"""
        doc = docx.Document(file_path)
        return '\n'.join([para.text for para in doc.paragraphs])
    
    def _extract_from_txt(self, file_path):
        """Extract text from TXT file - matches original OCR.py logic"""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    def _extract_from_image(self, file_path):
        """Extract text from image file - matches original OCR.py logic"""
        image = Image.open(file_path)
        return self._ocr_with_google(image)
    
    def _ocr_with_google(self, image):
        """Use Google Vision API for OCR - matches original OCR.py logic"""
        client = vision.ImageAnnotatorClient()
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        image_content = buffered.getvalue()

        image = vision.Image(content=image_content)
        response = client.document_text_detection(image=image)
        return response.full_text_annotation.text if response.full_text_annotation else ""
