"""FlexSearch Backend - Ingestion strategies."""

from app.rag.ingestion.base import BaseExtractionStrategy, ExtractedContent
from app.rag.ingestion.docling_extract import DoclingExtractionStrategy
from app.rag.ingestion.hybrid_pdf import HybridPdfExtractionStrategy
from app.rag.ingestion.ocr import OCRExtractionStrategy
from app.rag.ingestion.preprocess import preprocess_extracted_text
from app.rag.ingestion.vlm import VLMExtractionStrategy

__all__ = [
    "BaseExtractionStrategy",
    "ExtractedContent",
    "DoclingExtractionStrategy",
    "HybridPdfExtractionStrategy",
    "OCRExtractionStrategy",
    "VLMExtractionStrategy",
    "preprocess_extracted_text",
]
