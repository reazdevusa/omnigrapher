"""Computer vision & visual intelligence: OpenCV analysis + OCR (RapidOCR,
pytesseract optional) + frame sampling. All heavy imports are lazy so backend
startup stays under the healthcheck budget.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

_ocr_engine = None


@dataclass
class BoundingBox:
    x: int
    y: int
    w: int
    h: int
    label: str
    confidence: float


@dataclass
class VisionResult:
    ocr_text: str
    ocr_blocks: List[Dict[str, Any]] = field(default_factory=list)
    objects: List[BoundingBox] = field(default_factory=list)
    width: int = 0
    height: int = 0
    frames_sampled: int = 0
    chart_rows: List[List[str]] = field(default_factory=list)
    caption: str = ""


def is_image_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def is_video_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS


def get_ocr_engine():
    """Lazy RapidOCR (ONNX Runtime) engine — no external Tesseract binary needed."""
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "rapidocr-onnxruntime is not installed. Add it to requirements and reinstall."
        ) from exc
    _ocr_engine = RapidOCR()
    return _ocr_engine


def reset_ocr_engine() -> None:
    global _ocr_engine
    _ocr_engine = None


def _cv2():
    import cv2
    return cv2


def _np():
    import numpy as np
    return np


VLM_MODEL = os.getenv("VLM_MODEL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def describe_image_with_vlm(path: str | Path) -> str:
    """Optional vision-language-model caption via Ollama (e.g. VLM_MODEL=llava).
    Returns an empty string when no VLM is configured or reachable — callers
    treat it as best-effort enrichment, never a hard dependency."""
    if not VLM_MODEL:
        return ""
    try:
        import base64

        import requests

        image_b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": VLM_MODEL,
                "prompt": "Describe this image in detail, including any text, diagrams, and charts.",
                "images": [image_b64],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()
    except Exception:
        logger.warning("VLM caption failed for %s", path, exc_info=True)
        return ""


def analyze_image(path: str | Path, use_vlm: bool = True) -> VisionResult:
    """Run OCR (bounding boxes) + classical CV region detection on an image."""
    cv2 = _cv2()
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Could not decode image: {path}")
    height, width = img.shape[:2]

    ocr_blocks: List[Dict[str, Any]] = []
    ocr_lines: List[str] = []
    try:
        engine = get_ocr_engine()
        result, _ = engine(str(path))
        if result:
            for box, text, conf in result:
                xs = [int(p[0]) for p in box]
                ys = [int(p[1]) for p in box]
                ocr_blocks.append({
                    "text": text,
                    "confidence": float(conf),
                    "bbox": {
                        "x": min(xs), "y": min(ys),
                        "w": max(xs) - min(xs), "h": max(ys) - min(ys),
                    },
                })
                ocr_lines.append(text)
    except Exception:
        logger.exception("OCR failed for %s", path)

    objects = _detect_regions(img)
    chart_rows = _extract_chart_rows(ocr_lines)
    caption = describe_image_with_vlm(path) if use_vlm else ""

    return VisionResult(
        ocr_text="\n".join(ocr_lines),
        ocr_blocks=ocr_blocks,
        objects=objects,
        width=width,
        height=height,
        chart_rows=chart_rows,
        caption=caption,
    )


def _detect_regions(img) -> List[BoundingBox]:
    """Classical CV salient-region detection (contours after edge extraction)."""
    cv2 = _cv2()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    h, w = img.shape[:2]
    min_area = (w * h) * 0.002  # ignore specks < 0.2% of frame
    boxes: List[BoundingBox] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        boxes.append(BoundingBox(
            x=int(x), y=int(y), w=int(bw), h=int(bh),
            label="region",
            confidence=min(1.0, area / (w * h)),
        ))
    boxes.sort(key=lambda b: b.w * b.h, reverse=True)
    return boxes[:20]


def _extract_chart_rows(ocr_lines: List[str]) -> List[List[str]]:
    """Heuristic chart-data extraction: lines like 'Label 123.4' -> rows."""
    import re
    rows: List[List[str]] = []
    pattern = re.compile(r"^([A-Za-z][A-Za-z _/\-%]{1,40}?)[:\s]+(-?\d[\d,]*(?:\.\d+)?%?)$")
    for line in ocr_lines:
        m = pattern.match(line.strip())
        if m:
            rows.append([m.group(1).strip(), m.group(2)])
    return rows[:50]


def sample_video_frames(path: str | Path, max_frames: int = 8) -> List[Dict[str, Any]]:
    """Sample evenly spaced frames from a video; each frame is OCR'd."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {path}")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if frame_count <= 0:
        cap.release()
        raise ValueError(f"Video has no frames: {path}")

    step = max(1, frame_count // max_frames)
    samples: List[Dict[str, Any]] = []
    engine = None
    try:
        engine = get_ocr_engine()
    except Exception:
        logger.warning("OCR engine unavailable; video frames returned without text")

    try:
        idx = 0
        grabbed = 0
        while grabbed < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            text = ""
            if engine is not None:
                try:
                    result, _ = engine(frame)
                    if result:
                        text = " ".join(r[1] for r in result)
                except Exception:
                    logger.exception("Frame OCR failed at index %d", idx)
            samples.append({
                "frame_index": idx,
                "timestamp_seconds": round(idx / fps, 2),
                "ocr_text": text,
            })
            idx += step
            grabbed += 1
    finally:
        cap.release()
    return samples


def index_visual_features(
    filename: str,
    owner_id: int,
    result: VisionResult,
    source_ref: str,
) -> int:
    """Embed extracted visual features (OCR text, chart rows, detected regions,
    VLM caption) into the shared knowledge-base collection so the image is
    searchable and can ground diagram Q&A."""
    from app.rag_engine import _get_embed_model, _get_knowledge_base_collection

    docs: List[str] = []
    if result.caption:
        docs.append(f"[Image: {filename}] VLM description: {result.caption}")
    if result.ocr_text:
        docs.append(f"[Image: {filename}] OCR text: {result.ocr_text[:4000]}")
    for label, value in result.chart_rows[:50]:
        docs.append(f"[Image: {filename}] Chart data: {label} = {value}")
    if result.objects:
        regions = "; ".join(
            f"{b.label} at ({b.x},{b.y}) size {b.w}x{b.h}" for b in result.objects[:20]
        )
        docs.append(f"[Image: {filename}] Detected regions: {regions}")

    if not docs:
        return 0

    collection = _get_knowledge_base_collection()
    embed = _get_embed_model()
    ids, metas, embeddings = [], [], []
    for i, doc in enumerate(docs):
        ids.append(f"{filename}::visual::{i}")
        metas.append({
            "file_name": filename,
            "owner_id": owner_id,
            "source_ref": source_ref or filename,
            "media_type": "visual",
            "start": 0.0,
            "end": 0.0,
            "start_label": "",
            "page": 0,
        })
        embeddings.append(embed.get_text_embedding(doc))

    collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
    logger.info("Indexed %d visual feature chunks for %s (owner=%s)", len(ids), filename, owner_id)
    return len(ids)


def vision_result_to_json(result: VisionResult) -> dict:
    return {
        "ocr_text": result.ocr_text,
        "ocr_blocks": result.ocr_blocks,
        "objects": [b.__dict__ for b in result.objects],
        "width": result.width,
        "height": result.height,
        "frames_sampled": result.frames_sampled,
        "chart_rows": result.chart_rows,
        "caption": result.caption,
    }
