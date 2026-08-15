"""Layout-aware, multimodal document parser.

Uses the ``unstructured`` library to extract text, tables, and embedded images
from PDFs while preserving reading order and filtering repeating headers/footers.
Extracted images are sent to a local vision model (Ollama ``llava`` by default)
for textual descriptions; if vision fails, RapidOCR / Tesseract are used as
fallbacks.  If ``unstructured`` is not installed or fails, the caller should
fall back to the existing PyMuPDF pipeline.
"""

import base64
import logging
import os
import tempfile
import traceback
from pathlib import Path
from typing import Optional

import requests

try:
    from unstructured.partition.pdf import partition_pdf
    from unstructured.documents.elements import Header, Footer, Table, Image

    _unstructured_available = True
except Exception:  # pragma: no cover
    _unstructured_available = False
    partition_pdf = None  # type: ignore[misc,assignment]
    Header = None  # type: ignore[misc,assignment]
    Footer = None  # type: ignore[misc,assignment]
    Table = None  # type: ignore[misc,assignment]
    Image = None  # type: ignore[misc,assignment]

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover
    RapidOCR = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
VISION_MODEL = os.getenv(
    "MULTIMODAL_VISION_MODEL",
    os.getenv("VISION_MODEL", "llava:latest"),
)


def is_multimodal_available() -> bool:
    """Return True when the unstructured parser can be imported."""
    return _unstructured_available


def _describe_image_with_ollama(image_path: Path) -> str:
    """Ask a local vision model to describe an extracted image/chart/diagram."""
    prompt = (
        "Describe the following image, chart, or diagram in one concise paragraph. "
        "Include visible text, labels, axes, legends, and any important data relationships."
    )
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        payload = {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 512},
        }
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        desc = (data.get("message") or {}).get("content", "").strip()
        if desc:
            logger.info("Vision model described image %s", image_path.name)
            return desc
    except Exception:
        logger.warning(
            "Vision model description failed for %s; falling back to OCR",
            image_path.name,
            exc_info=True,
        )
    return ""


def _ocr_image(image_path: Path) -> str:
    """Extract text from an image using RapidOCR if available, then Tesseract."""
    text = ""
    if RapidOCR is not None:
        try:
            engine = RapidOCR()
            result, _ = engine(str(image_path))
            if result:
                text = "\n".join(line[1] for line in result if line[1].strip())
        except Exception:
            logger.warning("RapidOCR failed for %s", image_path.name, exc_info=True)

    if not text:
        try:
            import pytesseract
            from PIL import Image as PILImage

            text = pytesseract.image_to_string(PILImage.open(image_path))
        except Exception:
            logger.warning("Tesseract OCR failed for %s", image_path.name, exc_info=True)

    return text.strip()


def _describe_image(image_path: Path) -> str:
    """Return a textual description of an image, preferring the vision model."""
    desc = _describe_image_with_ollama(image_path)
    if desc:
        return desc
    ocr = _ocr_image(image_path)
    return f"Embedded visual (OCR): {ocr}" if ocr else ""


def parse_pdf(file_path: Path) -> Optional[list]:
    """Parse a PDF with layout-aware extraction and image description.

    Returns a list of LlamaIndex ``Document`` objects (one per page), or ``None``
    when unstructured is unavailable or parsing fails.
    """
    if not _unstructured_available:
        return None

    from llama_index.core import Document

    image_dir = Path(tempfile.gettempdir()) / f"kb_parsed_images_{file_path.stem}"
    image_dir.mkdir(parents=True, exist_ok=True)

    elements = None
    try:
        elements = partition_pdf(
            filename=str(file_path),
            strategy="hi_res",
            extract_images_in_pdf=True,
            infer_table_structure=True,
            include_page_breaks=False,
            languages=["eng"],
            image_output_dir_path=str(image_dir),
        )
    except Exception:
        logger.warning(
            "Unstructured hi_res parsing failed for %s; trying fast strategy",
            file_path.name,
            exc_info=True,
        )
        try:
            elements = partition_pdf(
                filename=str(file_path),
                strategy="fast",
                include_page_breaks=False,
            )
        except Exception:
            logger.warning(
                "Unstructured fast parsing failed for %s",
                file_path.name,
                exc_info=True,
            )
            return None

    if not elements:
        return None

    pages: dict[int, dict] = {}
    total_tables = 0
    total_images = 0

    for element in elements:
        page_num = getattr(element.metadata, "page_number", None) or 1
        if isinstance(element, (Header, Footer)):
            continue

        page = pages.setdefault(page_num, {"texts": [], "tables": [], "images": []})

        if isinstance(element, Table):
            html = getattr(element.metadata, "text_as_html", None)
            text = html if html else str(element)
            page["tables"].append(text)
            total_tables += 1
        elif isinstance(element, Image):
            img_path = getattr(element.metadata, "image_path", None)
            if img_path:
                description = _describe_image(Path(img_path))
                if description:
                    page["images"].append(description)
                    total_images += 1
        else:
            txt = str(element).strip()
            if txt:
                page["texts"].append(txt)

    documents = []
    for page_num, parts in sorted(pages.items()):
        sections = []
        if parts["texts"]:
            sections.append("\n\n".join(parts["texts"]))
        for idx, table in enumerate(parts["tables"], start=1):
            sections.append(f"\n\n[Table {idx}]\n{table}\n")
        for idx, img_desc in enumerate(parts["images"], start=1):
            sections.append(f"\n\n[Image {idx} description]\n{img_desc}\n")

        full_text = "\n\n".join(sections).strip()
        if not full_text:
            continue

        metadata = {
            "file_name": file_path.name,
            "file_path": str(file_path),
            "page": page_num,
            "parser": "unstructured-multimodal",
            "table_count": total_tables,
            "image_count": total_images,
        }
        documents.append(Document(text=full_text, metadata=metadata))

    logger.info(
        "Multimodal parser produced %d page-documents for %s (tables=%d, images=%d)",
        len(documents),
        file_path.name,
        total_tables,
        total_images,
    )
    return documents if documents else None
