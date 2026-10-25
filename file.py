import base64
import html
import os
import re
import tempfile

import pymupdf as fitz

PAGE_MARGIN = 36
STORY_COL_WIDTH = 280
IMAGE_WIDTH = 180
IMAGE_HEIGHT = 180
IMAGE_TOP = 118
IMAGE_RIGHT_MARGIN = 36
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")
LOGO_DISPLAY_WIDTH = 84
LOGO_DISPLAY_HEIGHT = LOGO_DISPLAY_WIDTH * (343 / 872)
FOOTER_BOTTOM = 14
FOOTER_RESERVE = FOOTER_BOTTOM + LOGO_DISPLAY_HEIGHT + 8

WORKSHEET_CSS = """
body {
  font-family: Helvetica, Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.4;
  color: #1a1a1a;
}

.meta-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 8px;
}

.meta-table td {
  vertical-align: top;
}

.meta-label {
  margin-bottom: 3px;
}

.meta-line {
  border-bottom: 1px solid #1a1a1a;
  height: 12px;
  width: 95%;
}

.title {
  text-align: center;
  font-size: 16pt;
  font-weight: 700;
  margin: 4px 0 10px 0;
}

.story-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 12px;
}

.story-table td {
  vertical-align: top;
}

.story-col {
  width: 300px;
  padding-right: 14px;
  font-size: 11pt;
  line-height: 1.4;
}

.image-col {
  width: 180px;
}

.image-slot {
  width: 180px;
  height: 180px;
}

.question {
  margin-bottom: 10px;
}

.question-number {
  font-weight: 700;
}

.question-text {
  font-weight: 400;
}

.answer-line {
  border-bottom: 1px solid #9ca3af;
  height: 18px;
  margin-top: 4px;
}

.instructions {
  font-style: italic;
  margin-bottom: 10px;
}

.ignore-line {
  color: #6b7280;
  margin-bottom: 8px;
}

.asset {
  margin: 10px 0;
}

.asset img {
  max-width: 180px;
  max-height: 180px;
}
"""

# Tighter stylesheet used when the default layout spills onto a second page.
WORKSHEET_CSS_COMPACT = """
body {
  font-family: Helvetica, Arial, sans-serif;
  font-size: 10pt;
  line-height: 1.3;
  color: #1a1a1a;
}

.meta-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 6px;
}

.meta-table td {
  vertical-align: top;
}

.meta-label {
  margin-bottom: 2px;
  font-size: 9pt;
}

.meta-line {
  border-bottom: 1px solid #1a1a1a;
  height: 10px;
  width: 95%;
}

.title {
  text-align: center;
  font-size: 14pt;
  font-weight: 700;
  margin: 2px 0 8px 0;
}

.story-table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 8px;
}

.story-table td {
  vertical-align: top;
}

.story-col {
  width: 320px;
  padding-right: 12px;
  font-size: 10pt;
  line-height: 1.3;
}

.image-col {
  width: 150px;
}

.image-slot {
  width: 150px;
  height: 150px;
}

.question {
  margin-bottom: 7px;
}

.question-number {
  font-weight: 700;
}

.question-text {
  font-weight: 400;
}

.answer-line {
  border-bottom: 1px solid #9ca3af;
  height: 15px;
  margin-top: 3px;
}

.instructions {
  font-style: italic;
  margin-bottom: 8px;
}

.ignore-line {
  color: #6b7280;
  margin-bottom: 6px;
}

.asset {
  margin: 8px 0;
}

.asset img {
  max-width: 150px;
  max-height: 150px;
}
"""

QUESTION_FIELDS = [
    ("Q1", "Question 1"),
    ("Q2", "Question 2"),
    ("Q3", "Question 3"),
    ("Q4", "Question 4"),
    ("Q5", "Question 5"),
    ("Q6", "Question 6"),
    ("Q7", "Question 7"),
    ("Q8", "Question 8"),
]

_GENERIC_TITLES = {"leveled worksheet", "worksheet"}
_META_LABEL = re.compile(
    r"^(name|date|score|class|period|teacher|student)\b",
    re.I,
)
_STEM_NUMBER = re.compile(
    r"^(?:\d{1,3}[.)]\s+|\(\d{1,3}\)\s+|question\s+\d+\s*[:.)]\s*)",
    re.I,
)
_DIRECTION_START = re.compile(
    r"^(directions|instructions|read the (passage|story|text)|for each question)\b",
    re.I,
)


def decode_image_bytes(data):
    image = str(data.get("image", "")).strip()
    if not image:
        return None

    if image.startswith("data:"):
        match = re.match(r"data:image/[\w+.-]+;base64,(.+)", image, re.DOTALL)
        if not match:
            return None
        return base64.b64decode(match.group(1))

    try:
        return base64.b64decode(image)
    except (ValueError, TypeError):
        return None


def build_worksheet_html(data, include_image_slot=False):
    title = html.escape(str(data.get("Title", "")))
    story = html.escape(str(data.get("Story", ""))).replace("\n", "<br/>")

    image_cell = ""
    if include_image_slot:
        image_cell = """
          <td class="image-col">
            <div class="image-slot"></div>
          </td>
        """

    questions_html = []
    number = 1
    for key, _label in QUESTION_FIELDS:
        question = str(data.get(key, "")).strip()
        if not question:
            continue
        questions_html.append(
            f"""
            <div class="question">
              <div>
                <span class="question-number">{number}.</span>
                <span class="question-text">{html.escape(question)}</span>
              </div>
              <div class="answer-line"></div>
            </div>
            """
        )
        number += 1

    return f"""
    <div class="worksheet">
      <table class="meta-table">
        <tr>
          <td width="50%">
            <div class="meta-label">Name</div>
            <div class="meta-line"></div>
          </td>
          <td width="50%">
            <div class="meta-label">Date</div>
            <div class="meta-line"></div>
          </td>
        </tr>
      </table>
      <h1 class="title">{title}</h1>
      <table class="story-table">
        <tr>
          <td class="story-col">{story}</td>
          {image_cell}
        </tr>
      </table>
      {''.join(questions_html)}
    </div>
    """


def _pdf_page_count(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc.page_count
    finally:
        doc.close()


def _render_html_to_pdf(worksheet_html, archive=None, user_css=None):
    css = user_css or WORKSHEET_CSS
    story = fitz.Story(html=worksheet_html, user_css=css, archive=archive)

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        writer = fitz.DocumentWriter(path)
        mediabox = fitz.paper_rect("a4")
        margin = PAGE_MARGIN
        content_rect = fitz.Rect(
            mediabox.x0 + margin,
            mediabox.y0 + margin,
            mediabox.x1 - margin,
            mediabox.y1 - FOOTER_RESERVE,
        )

        more = 1
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(content_rect)
            story.draw(device)
            writer.end_page()

        writer.close()

        with open(path, "rb") as pdf_file:
            return _stamp_logo(pdf_file.read())
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _stamp_logo(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        x0 = page.rect.width - PAGE_MARGIN - LOGO_DISPLAY_WIDTH
        y1 = page.rect.height - FOOTER_BOTTOM
        y0 = y1 - LOGO_DISPLAY_HEIGHT
        page.insert_image(
            fitz.Rect(x0, y0, x0 + LOGO_DISPLAY_WIDTH, y1),
            filename=LOGO_PATH,
            keep_proportion=True,
        )
    return doc.tobytes()


def _image_rect(page, size=None):
    side = size or IMAGE_WIDTH
    x1 = page.rect.width - IMAGE_RIGHT_MARGIN
    x0 = x1 - side
    y0 = IMAGE_TOP
    return fitz.Rect(x0, y0, x1, y0 + side)


def _insert_image_on_first_page(pdf_bytes, image_bytes, size=None):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    page.insert_image(
        _image_rect(page, size=size),
        stream=image_bytes,
        keep_proportion=True,
    )
    return doc.tobytes()


def _as_block_dict(block):
    if isinstance(block, dict):
        return block
    if hasattr(block, "to_dict"):
        return block.to_dict()
    return {
        "block_type": getattr(block, "block_type", ""),
        "text": getattr(block, "text", ""),
        "asset_base64": getattr(block, "asset_base64", None),
        "asset_mime": getattr(block, "asset_mime", None),
    }


def _strip_stem_number(text):
    return _STEM_NUMBER.sub("", str(text or "").strip()).strip()


def _is_meta_label(text):
    return bool(_META_LABEL.match((text or "").strip()))


def blocks_to_worksheet_data(title, blocks):
    """Map typed blocks onto the generated-worksheet fields.

    Generated PDFs use one HTML skeleton (Name/Date lines, title, story +
    200px image slot, questions with answer lines). Rebuilds should
    reuse that skeleton and only swap Title / Story / Q1–Q8 / image.
    Returns None when the sheet is not that shape (e.g. multiple-choice).
    """
    items = [_as_block_dict(block) for block in (blocks or [])]
    if any(item.get("block_type") == "answer_choice" for item in items):
        return None
    passages = [
        (item.get("text") or "").strip()
        for item in items
        if item.get("block_type") == "passage" and (item.get("text") or "").strip()
    ]
    questions = [
        (item.get("text") or "").strip()
        for item in items
        if item.get("block_type") == "question" and (item.get("text") or "").strip()
    ]
    if not passages or not questions:
        return None

    title_text = ""
    for item in items:
        text = (item.get("text") or "").strip()
        if item.get("block_type") not in ("ignore", "instructions"):
            continue
        if not text or _is_meta_label(text) or _DIRECTION_START.match(text):
            continue
        if "?" in text or len(text.split()) > 12:
            continue
        title_text = text
        break
    if not title_text:
        candidate = str(title or "").strip()
        if candidate.lower() not in _GENERIC_TITLES:
            title_text = candidate

    image = ""
    for item in items:
        if item.get("block_type") == "image" and item.get("asset_base64"):
            mime = item.get("asset_mime") or "image/png"
            image = f"data:{mime};base64,{item['asset_base64']}"
            break

    data = {
        "Title": title_text,
        "Story": " ".join(passages),
        "image": image,
    }
    for index, question in enumerate(questions[: len(QUESTION_FIELDS)], start=1):
        data[f"Q{index}"] = _strip_stem_number(question)
    return data


def _block_field(block, name, default=None):
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _image_asset(block):
    payload = _block_field(block, "asset_base64")
    if not payload:
        return None
    try:
        raw = base64.b64decode(payload)
    except (ValueError, TypeError):
        return None
    if not raw:
        return None
    mime = (_block_field(block, "asset_mime") or "image/png").lower()
    ext = "jpg" if "jpeg" in mime or mime.endswith("/jpg") else "png"
    return raw, ext


def build_blocks_html(title, blocks):
    """Reflow a typed block list into the existing worksheet HTML/CSS.

    Pictures are referenced by archive name so the PDF writer can embed them.
    """
    heading = html.escape(str(title or "Worksheet"))
    parts = []
    assets = []
    question_index = 0
    for block in blocks:
        text = html.escape(_block_field(block, "text") or "")
        text = text.replace("\n", "<br/>")
        block_type = _block_field(block, "block_type")
        if block_type == "instructions":
            parts.append(f'<p class="instructions">{text}</p>')
        elif block_type == "passage":
            parts.append(
                f'<table class="story-table"><tr><td class="story-col">{text}</td></tr></table>'
            )
        elif block_type == "question":
            question_index += 1
            parts.append(
                f"""
            <div class="question">
              <div>
                <span class="question-number">{question_index}.</span>
                <span class="question-text">{text}</span>
              </div>
              <div class="answer-line"></div>
            </div>
            """
            )
        elif block_type == "answer_choice":
            parts.append(f'<div class="choice">{text}</div>')
        elif block_type == "image":
            asset = _image_asset(block)
            if asset:
                raw, ext = asset
                name = f"asset-{len(assets)}.{ext}"
                assets.append((name, raw))
                parts.append(f'<p class="asset"><img src="{html.escape(name)}" /></p>')
        elif block_type == "ignore":
            parts.append(f'<p class="ignore-line">{text}</p>')
        else:
            parts.append(f"<p>{text}</p>")
    html_out = f"""
    <div class="worksheet">
      <h1 class="title">{heading}</h1>
      {''.join(parts)}
    </div>
    """
    return html_out, assets


def generate_blocks_pdf(title, blocks):
    payload = blocks_to_worksheet_data(title, blocks)
    if payload is not None:
        return generate_worksheet_pdf(payload)
    worksheet_html, assets = build_blocks_html(title, blocks)
    archive = None
    if assets:
        archive = fitz.Archive()
        for name, raw in assets:
            archive.add((raw, name))
    return _render_html_to_pdf(worksheet_html, archive=archive)


def generate_worksheet_pdf(data):
    image_bytes = decode_image_bytes(data)
    worksheet_html = build_worksheet_html(data, include_image_slot=bool(image_bytes))
    pdf_bytes = _render_html_to_pdf(worksheet_html)
    image_size = IMAGE_WIDTH

    # Prefer a single page: retry with compact spacing if content overflowed.
    if _pdf_page_count(pdf_bytes) > 1:
        compact_pdf = _render_html_to_pdf(worksheet_html, user_css=WORKSHEET_CSS_COMPACT)
        if _pdf_page_count(compact_pdf) <= _pdf_page_count(pdf_bytes):
            pdf_bytes = compact_pdf
            image_size = 150

    if image_bytes:
        pdf_bytes = _insert_image_on_first_page(pdf_bytes, image_bytes, size=image_size)

    return pdf_bytes
