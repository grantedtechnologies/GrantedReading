"""Split an uploaded worksheet into typed blocks.

Heuristics only. This module never calls a model. Leftover labels such as
Name/Date are `ignore` — the teacher can reassign them, but the leveler
will not rewrite them. Pictures are their own `image` block and pass through.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

logger = logging.getLogger(__name__)

BlockType = Literal[
    "passage", "instructions", "question", "answer_choice", "ignore", "image"
]
Confidence = Literal["high", "low"]

BLOCK_TYPES: tuple[BlockType, ...] = (
    "passage",
    "instructions",
    "question",
    "answer_choice",
    "ignore",
    "image",
)

_QUESTION_NUM = re.compile(
    r"^(?:\d{1,3}[.)]\s+|\(\d{1,3}\)\s+|question\s+\d+\s*[:.)]\s*)",
    re.I,
)
_CHOICE = re.compile(r"^(?:[A-Da-d][.)]\s+|\([A-Da-d]\)\s+)")
_INSTRUCTIONS = re.compile(
    r"^(directions|instructions|read the (passage|story|text)|for each question)\b",
    re.I,
)
_IGNORE_LABEL = re.compile(
    r"^(name|date|score|class|period|teacher|student)\s*:?\s*([_.\-–—\s.]*)$",
    re.I,
)
_IGNORE_PREFIX = re.compile(
    r"^(name|date|score|class|period)\b",
    re.I,
)

_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


@dataclass(frozen=True)
class WorksheetBlock:
    block_id: str
    block_type: BlockType
    text: str
    source_position: int
    confidence: Confidence
    asset_base64: str | None = None
    asset_mime: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "WorksheetBlock":
        block_type = raw.get("block_type") or "ignore"
        if block_type == "unclassified":
            block_type = "ignore"
        if block_type not in BLOCK_TYPES:
            block_type = "ignore"
        confidence = raw.get("confidence") or "low"
        if confidence not in ("high", "low"):
            confidence = "low"
        return cls(
            block_id=str(raw.get("block_id") or ""),
            block_type=block_type,
            text=str(raw.get("text") or "").strip(),
            source_position=int(raw.get("source_position") or 0),
            confidence=confidence,
            asset_base64=raw.get("asset_base64") or None,
            asset_mime=raw.get("asset_mime") or None,
        )


@dataclass(frozen=True)
class _RawLine:
    text: str
    style: str
    is_list: bool
    font_size: float | None
    source: Literal["docx", "pdf"]
    image_bytes: bytes | None = None
    image_mime: str | None = None


def segment_worksheet_bytes(data: bytes, filename: str) -> list[WorksheetBlock]:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        lines = _extract_docx(data)
    elif name.endswith(".pdf"):
        lines = _extract_pdf(data)
    else:
        raise ValueError("Upload a .docx or .pdf worksheet.")
    # PDF (and hard-wrapped DOCX) emit visual lines. Join those into one
    # tag before classifying so a wrap mid-question is not two blocks.
    lines = _coalesce_wrapped_lines(lines)
    classified = [_classify_line(line, index) for index, line in enumerate(lines)]
    return _merge_runs(classified)


def _extract_docx(data: bytes) -> list[_RawLine]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(data))
    lines: list[_RawLine] = []
    for child in document.element.body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            lines.extend(_docx_images(child, document))
            paragraph = Paragraph(child, document)
            text = " ".join(paragraph.text.split())
            if text:
                style = paragraph.style.name if paragraph.style is not None else ""
                lines.append(
                    _RawLine(
                        text=text,
                        style=style,
                        is_list=_docx_is_list(paragraph),
                        font_size=None,
                        source="docx",
                    )
                )
        elif tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    lines.extend(_docx_images(cell._tc, document))
                    text = " ".join(cell.text.split())
                    if text:
                        lines.append(
                            _RawLine(
                                text=text,
                                style="Table",
                                is_list=False,
                                font_size=None,
                                source="docx",
                            )
                        )
    return lines


def _docx_images(element, document) -> list[_RawLine]:
    found = []
    for blip in element.iter(_A_BLIP):
        rid = blip.get(_R_EMBED)
        if not rid:
            continue
        part = document.part.related_parts.get(rid)
        if part is None or not getattr(part, "blob", None):
            continue
        mime = getattr(part, "content_type", None) or "image/png"
        found.append(
            _RawLine(
                text="",
                style="",
                is_list=False,
                font_size=None,
                source="docx",
                image_bytes=part.blob,
                image_mime=mime,
            )
        )
    return found


def _docx_is_list(paragraph) -> bool:
    numbering = paragraph._p.pPr
    if numbering is None:
        return False
    return numbering.numPr is not None


def _extract_pdf(data: bytes) -> list[_RawLine]:
    """Best-effort line reconstruction, including embedded pictures.

    PDF has no native worksheet structure. Most text lines come out
    `confidence: low` unless a numbering pattern is obvious.
    """
    import pymupdf as fitz

    doc = fitz.open(stream=data, filetype="pdf")
    items: list[tuple[float, float, _RawLine]] = []
    try:
        for page in doc:
            payload = page.get_text("dict")
            page_items: list[tuple[float, float, _RawLine]] = []
            sizes = []
            for block in payload.get("blocks", []):
                bbox = block.get("bbox") or (0, 0, 0, 0)
                y0, x0 = float(bbox[1]), float(bbox[0])
                if block.get("type") == 1:
                    raw = _pdf_image_line(doc, block)
                    if raw is not None:
                        page_items.append((y0, x0, raw))
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans") or []
                    text = " ".join(
                        (span.get("text") or "").strip() for span in spans
                    ).strip()
                    text = " ".join(text.split())
                    if not text:
                        continue
                    size = max((float(span.get("size") or 0) for span in spans), default=0)
                    sizes.append(size)
                    lbbox = line.get("bbox") or bbox
                    page_items.append(
                        (
                            float(lbbox[1]),
                            float(lbbox[0]),
                            _RawLine(
                                text=text,
                                style="",
                                is_list=False,
                                font_size=size or None,
                                source="pdf",
                            ),
                        )
                    )
            median = sorted(sizes)[len(sizes) // 2] if sizes else 0
            if median:
                stamped = []
                for y0, x0, raw in page_items:
                    if raw.image_bytes or not raw.font_size:
                        stamped.append((y0, x0, raw))
                        continue
                    heading = raw.font_size >= median * 1.2
                    stamped.append(
                        (
                            y0,
                            x0,
                            _RawLine(
                                text=raw.text,
                                style="Heading" if heading else raw.style,
                                is_list=raw.is_list,
                                font_size=raw.font_size,
                                source=raw.source,
                            ),
                        )
                    )
                page_items = stamped
            items.extend(page_items)
    finally:
        doc.close()
    items.sort(key=lambda row: (row[0], row[1]))
    lines = [row[2] for row in items]
    if not lines:
        logger.warning("pdf_extraction_empty")
    return lines


def _pdf_image_line(doc, block) -> _RawLine | None:
    payload = block.get("image")
    ext = str(block.get("ext") or "png").lower()
    if not payload and block.get("xref"):
        import pymupdf as fitz

        try:
            pix = fitz.Pixmap(doc, int(block["xref"]))
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            payload = pix.tobytes("png")
            ext = "png"
        except Exception:
            logger.exception("pdf_image_extract_failed")
            return None
    if not payload or not isinstance(payload, (bytes, bytearray)):
        return None
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    return _RawLine(
        text="",
        style="",
        is_list=False,
        font_size=None,
        source="pdf",
        image_bytes=bytes(payload),
        image_mime=mime,
    )


def _starts_structural_unit(line: _RawLine) -> bool:
    """True when this line begins a new worksheet tag, not a wrap."""
    if line.image_bytes:
        return True
    text = (line.text or "").strip()
    if not text:
        return True
    if _QUESTION_NUM.match(text) or _CHOICE.match(text):
        return True
    if _INSTRUCTIONS.match(text) or _is_ignore_label(text):
        return True
    return False


def _looks_like_wrap(previous: _RawLine, current: _RawLine) -> bool:
    if previous.image_bytes or current.image_bytes:
        return False
    if _starts_structural_unit(current):
        return False
    prev = (previous.text or "").rstrip()
    curr = (current.text or "").lstrip()
    if not prev or not curr:
        return False
    if _is_ignore_label(prev) or _CHOICE.match(prev) or _INSTRUCTIONS.match(prev):
        return False
    if prev[-1] not in ".!?":
        return True
    return curr[:1].islower()


def _coalesce_wrapped_lines(lines: list[_RawLine]) -> list[_RawLine]:
    """Rebuild tags from visual lines. Do not split on `?` inside a tag."""
    if not lines:
        return []
    out = [lines[0]]
    for line in lines[1:]:
        prev = out[-1]
        if _looks_like_wrap(prev, line):
            out[-1] = _RawLine(
                text=f"{prev.text} {line.text}".strip(),
                style=prev.style,
                is_list=prev.is_list or line.is_list,
                font_size=prev.font_size,
                source=prev.source,
                image_bytes=prev.image_bytes,
                image_mime=prev.image_mime,
            )
        else:
            out.append(line)
    return out


def _is_ignore_label(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.endswith("?"):
        return False
    if _IGNORE_LABEL.match(stripped):
        return True
    if _IGNORE_PREFIX.match(stripped) and (
        "_" in stripped or "___" in stripped or len(stripped.split()) <= 3
    ):
        return True
    return False


def _classify_line(line: _RawLine, index: int) -> WorksheetBlock:
    if line.image_bytes:
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="image",
            text="",
            source_position=index,
            confidence="high",
            asset_base64=base64.b64encode(line.image_bytes).decode(),
            asset_mime=line.image_mime or "image/png",
        )

    text = line.text
    source = line.source
    heading = "heading" in (line.style or "").lower()

    if _is_ignore_label(text):
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="ignore",
            text=text,
            source_position=index,
            confidence="high",
        )

    if _CHOICE.match(text) and "?" not in text:
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="answer_choice",
            text=text,
            source_position=index,
            confidence="high",
        )

    numbered = bool(_QUESTION_NUM.match(text) or line.is_list)
    if numbered or "?" in text:
        # The whole tag is one question if it contains any `?`, including
        # "…goal? Support your answer…?" — do not split on each mark.
        confidence: Confidence
        if "?" in text:
            confidence = "high" if (source == "docx" or numbered) else "low"
        else:
            confidence = "low"
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="question",
            text=text,
            source_position=index,
            confidence=confidence,
        )

    if _INSTRUCTIONS.match(text) or (heading and _INSTRUCTIONS.search(text)):
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="instructions",
            text=text,
            source_position=index,
            confidence="high" if source == "docx" else "low",
        )

    if heading and len(text.split()) <= 12:
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="instructions",
            text=text,
            source_position=index,
            confidence="low",
        )

    words = len(text.split())
    if words >= 20:
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="passage",
            text=text,
            source_position=index,
            confidence="high" if source == "docx" else "low",
        )
    if words >= 8:
        return WorksheetBlock(
            block_id=f"b{index}",
            block_type="passage",
            text=text,
            source_position=index,
            confidence="low",
        )
    return WorksheetBlock(
        block_id=f"b{index}",
        block_type="ignore",
        text=text,
        source_position=index,
        confidence="low",
    )


def _merge_runs(blocks: list[WorksheetBlock]) -> list[WorksheetBlock]:
    """Join wrapped fragments of the same tag. A new question number starts a new tag."""
    if not blocks:
        return []
    merged: list[WorksheetBlock] = []
    current = blocks[0]
    for block in blocks[1:]:
        same = block.block_type == current.block_type
        joinable = current.block_type in ("passage", "instructions")
        question_wrap = (
            current.block_type == "question"
            and block.block_type == "question"
            and not _QUESTION_NUM.match(block.text)
        )
        if (
            ((same and joinable) or question_wrap)
            and not current.asset_base64
            and not block.asset_base64
        ):
            numbered = bool(_QUESTION_NUM.match(current.text))
            joined = f"{current.text} {block.text}".strip()
            if question_wrap and numbered and "?" in joined:
                confidence = "high"
            else:
                confidence = (
                    "low" if "low" in (current.confidence, block.confidence) else "high"
                )
            current = WorksheetBlock(
                block_id=current.block_id,
                block_type=current.block_type,
                text=joined,
                source_position=current.source_position,
                confidence=confidence,
            )
        else:
            merged.append(current)
            current = block
    merged.append(current)
    return [
        WorksheetBlock(
            block_id=f"b{index}",
            block_type=block.block_type,
            text=block.text,
            source_position=index,
            confidence=block.confidence,
            asset_base64=block.asset_base64,
            asset_mime=block.asset_mime,
        )
        for index, block in enumerate(merged)
    ]
