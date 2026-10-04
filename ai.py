import base64
import json
import logging
import os
import re
import resource
import subprocess
import sys
import threading

import file
import requests
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.identity import ClientSecretCredential
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout
from dotenv import load_dotenv

import ai_rewrite
from reading_level import ReadingLevelError, correct_text, measured_without_repair, score_text
from reading_level import bands as rl_bands
from reading_level import config as rl_config
from reading_level.blocks import BLOCK_TYPES, WorksheetBlock, segment_worksheet_bytes
from reading_level import dok as rl_dok

load_dotenv(override=True)

logger = logging.getLogger(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "").rstrip("/")
LLM_AGENT_NAME = os.getenv("LLM_AGENT_NAME", "")
FLUX_API_KEY = os.getenv("FLUX_API_KEY", "")
FLUX_API_URL = os.getenv("FLUX_API_URL", "")
FLUX_MODEL = os.getenv("FLUX_MODEL", "")

WORKSHEET_SYSTEM_PROMPT = """You are a reading comprehension worksheet generator for special education teachers.

Given information about a student (grade, reading level, and interests), write a short fiction story and five reading comprehension questions.

Rules:
- Write the story at the student's READING LEVEL (not their grade level).
- Incorporate the student's interests naturally into the story.
- Keep the story engaging and age-appropriate for the student's grade.
- Write exactly 5 ordinary comprehension questions about the story. Do not force a Who / What / When / Where / Why template — pick stems that fit the story.
- Each question must end with a question mark and be answerable from the story.
- The story must be one paragraph with no line breaks.
- If a focus vocabulary word is given, use that exact word 3 to 5 times in the story. Weave it in naturally so the reader meets it in context. Do not replace it with a simpler synonym.
- If a phonics pattern is given, include 3 to 5 different words that contain that exact letter pattern (for example, "tion" in station, mention, action). Use those words naturally so the reader practices the pattern.

Respond with ONLY valid JSON (no markdown, no extra text) in this exact shape:
{
  "title": "Story title",
  "story": "The full story text",
  "questions": {
    "q1": "...?",
    "q2": "...?",
    "q3": "...?",
    "q4": "...?",
    "q5": "...?"
  }
}"""


def _llm_endpoint():
    """Project URL from .env. Must end with /api/projects/<project-name>."""
    endpoint = (os.getenv("LLM_ENDPOINT") or LLM_ENDPOINT).rstrip("/")
    last = endpoint.rsplit("/", 1)[-1]
    if not endpoint or last in ("projects", "api"):
        raise RuntimeError(
            "LLM_ENDPOINT must include the Foundry project name, for example "
            "https://<resource>.services.ai.azure.com/api/projects/<project-name>."
        )
    return endpoint


FOUNDRY_AUTH_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")


def require_foundry_config():
    """Fail at startup when the service principal or project endpoint is absent.

    A blank value counts as missing. The client secret is never included in
    the error text.
    """
    missing = [
        name for name in (*FOUNDRY_AUTH_VARS, "LLM_ENDPOINT")
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )
    _llm_endpoint()


def foundry_credential():
    require_foundry_config()
    return ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"].strip(),
        client_id=os.environ["AZURE_CLIENT_ID"].strip(),
        client_secret=os.environ["AZURE_CLIENT_SECRET"].strip(),
    )


def ensure_ai_configured():
    missing = []
    for name in FOUNDRY_AUTH_VARS:
        if not (os.environ.get(name) or "").strip():
            missing.append(name)
    if not FLUX_API_KEY:
        missing.append("FLUX_API_KEY")
    if not (os.environ.get("LLM_ENDPOINT") or LLM_ENDPOINT).strip():
        missing.append("LLM_ENDPOINT")
    if not LLM_AGENT_NAME:
        missing.append("LLM_AGENT_NAME")
    if not FLUX_API_URL:
        missing.append("FLUX_API_URL")
    if not FLUX_MODEL:
        missing.append("FLUX_MODEL")
    # Azure Service Principal authentication
    if not os.getenv("AZURE_CLIENT_ID"):
        missing.append("AZURE_CLIENT_ID")
    if not os.getenv("AZURE_CLIENT_SECRET"):
        missing.append("AZURE_CLIENT_SECRET")
    if not os.getenv("AZURE_TENANT_ID"):
        missing.append("AZURE_TENANT_ID")
    if missing:
        logger.error("AI is not configured; missing %s", ", ".join(missing))
        raise RuntimeError(
            "The writing assistant is not set up yet. Ask an admin to finish Azure setup."
        )
    _llm_endpoint()


FOUNDRY_DOWN = (
    "Azure Foundry (the writing assistant) is not responding. "
    "It may be down. Try again in a few minutes."
)
FLUX_DOWN = (
    "Azure image generation is not responding. "
    "The picture service may be down. Try again in a few minutes."
)
STORAGE_DOWN = (
    "Azure storage is not responding. "
    "Saved worksheets may be unavailable. Try again in a few minutes."
)
GENERIC_RETRY = "Something went wrong. Try again in a few minutes."

_RESOURCE_DOWN = {
    "foundry": FOUNDRY_DOWN,
    "flux": FLUX_DOWN,
    "storage": STORAGE_DOWN,
}
_RESOURCE_AUTH = {
    "foundry": (
        "Could not sign in to Azure Foundry. "
        "Ask an admin to check the writing assistant credentials."
    ),
    "flux": (
        "Could not sign in to Azure image generation. "
        "Ask an admin to check the picture service credentials."
    ),
    "storage": (
        "Could not sign in to Azure storage. "
        "Ask an admin to check the storage connection."
    ),
}
_RESOURCE_TIMEOUT = {
    "foundry": "Azure Foundry took too long to respond. Try again in a few minutes.",
    "flux": "Azure image generation took too long to respond. Try again in a few minutes.",
    "storage": "Azure storage took too long to respond. Try again in a few minutes.",
}
_RESOURCE_BUSY = "Azure is busy right now. Wait a minute and try again."
_RESOURCE_NOT_FOUND = {
    "foundry": (
        "Azure Foundry could not find the writing assistant. "
        "Ask an admin to check the Foundry project."
    ),
    "flux": (
        "Azure could not find the image model. "
        "Ask an admin to check image generation setup."
    ),
    "storage": "That worksheet file was not found in Azure storage.",
}


def _error_text(exc) -> str:
    if exc is None:
        return ""
    if isinstance(exc, dict):
        nested = exc.get("error")
        if isinstance(nested, dict):
            return str(nested.get("message") or nested.get("code") or "")
        if isinstance(nested, str):
            return nested
        return str(exc.get("message") or "")
    if isinstance(exc, str):
        return exc
    return str(exc or "")


def _error_status(exc):
    if isinstance(exc, dict):
        nested = exc.get("error") if isinstance(exc.get("error"), dict) else {}
        for key in ("status", "status_code", "statusCode", "code"):
            value = exc.get(key)
            if value is None:
                value = nested.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _looks_like_dump(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) > 280:
        return True
    lowered = stripped.lower()
    if stripped[:1] in "{[":
        return True
    if "traceback" in lowered or "httpresponseerror" in lowered:
        return True
    if "requestid" in lowered or "request id" in lowered:
        return True
    if "aadsts" in lowered or "error_description" in lowered:
        return True
    if '"error"' in lowered or "'error'" in lowered:
        return True
    if "has no attribute" in lowered or "object is not" in lowered:
        return True
    return False


def _looks_technical(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        needle in lowered
        for needle in (
            "has no attribute",
            "object is not",
            "nonetype",
            "keyerror",
            "typeerror",
            "indexerror",
            "attributeerror",
        )
    )


def user_error_message(exc, resource="foundry") -> str:
    """One short sentence for the teacher. Never includes JSON or Azure dumps."""
    kind = resource if resource in _RESOURCE_DOWN else "foundry"
    text = _error_text(exc)
    status = _error_status(exc)
    if isinstance(exc, dict):
        try:
            lowered = json.dumps(exc).lower()
        except TypeError:
            lowered = text.lower()
    else:
        lowered = text.lower()
        known_azure = (
            ClientAuthenticationError,
            HttpResponseError,
            ServiceRequestError,
            RequestsConnectionError,
            RequestsTimeout,
            TimeoutError,
            ConnectionError,
        )
        if (
            isinstance(exc, Exception)
            and not isinstance(exc, known_azure)
            and text
            and not _looks_like_dump(text)
            and not _looks_technical(text)
        ):
            return text

    if (
        isinstance(exc, ClientAuthenticationError)
        or status in (401, 403)
        or "unauthorized" in lowered
        or "authentication" in lowered
        or "aadsts" in lowered
        or "invalid_client" in lowered
        or "access denied" in lowered
        or "not authorized" in lowered
    ):
        return _RESOURCE_AUTH[kind]

    if (
        isinstance(exc, (TimeoutError, RequestsTimeout))
        or status in (408, 504)
        or "timed out" in lowered
        or "timeout" in lowered
    ):
        return _RESOURCE_TIMEOUT[kind]

    if status == 429 or "too many requests" in lowered or "rate limit" in lowered:
        return _RESOURCE_BUSY

    if status == 404 or "deploymentnotfound" in lowered or "resourcenotfound" in lowered:
        return _RESOURCE_NOT_FOUND[kind]

    if (
        isinstance(exc, (HttpResponseError, ServiceRequestError, RequestsConnectionError))
        or status in (500, 502, 503)
        or "connection" in lowered
        or "temporarily unavailable" in lowered
        or "service unavailable" in lowered
        or "foundry" in lowered
        or "azure" in lowered
        or "flux" in lowered
        or "blob" in lowered
    ):
        return _RESOURCE_DOWN[kind]

    return GENERIC_RETRY


def process_ram_mb() -> int:
    """Resident memory of this process, as a whole number of megabytes."""
    return max(1, round(_rss_bytes() / (1024 * 1024)))


def _rss_bytes() -> int:
    statm = "/proc/self/statm"
    if os.path.exists(statm):
        page = os.sysconf("SC_PAGE_SIZE") or 4096
        with open(statm, encoding="utf-8") as handle:
            parts = handle.read().split()
        return int(parts[1]) * page
    try:
        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            text=True,
        ).strip()
        return int(out.split()[0]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(usage)
        return int(usage) * 1024


_PHONICS_CHUNK = re.compile(r"[A-Za-z]{2,12}")
_STORY_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def parse_focus_vocabulary(raw) -> list[str]:
    """Teacher-chosen words to repeat in the story and leave untouched.

    Commas separate terms. Caps at three so the story does not become a list.
    """
    if not raw:
        return []
    terms = []
    seen = set()
    for part in str(raw).replace(";", ",").split(","):
        term = " ".join(part.split())
        if not term or len(term) > 40:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) == 3:
            break
    return terms


def parse_focus_phonics(raw) -> str:
    """One letter pattern to practice, such as tion or ing.

    Takes the first 2-12 letter chunk so a dropdown label like
    'tion — action, station' still yields tion. Single letters are
    rejected so a stray 'a' does not match every word.
    """
    if not raw:
        return ""
    match = _PHONICS_CHUNK.search(str(raw).strip())
    return match.group(0).lower() if match else ""


def words_matching_phonics(text, pattern) -> list[str]:
    """Unique story words that contain the phonics chunk, first spelling kept."""
    chunk = parse_focus_phonics(pattern)
    if not chunk or not text:
        return []
    seen = set()
    found = []
    for word in _STORY_WORD.findall(text):
        key = word.lower()
        if chunk in key and key not in seen:
            seen.add(key)
            found.append(word)
    return found


def _unique_protected_terms(*groups) -> list[str]:
    seen = set()
    terms = []
    for group in groups:
        for term in group or []:
            key = str(term).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return terms


DOK_LEVELS = (1, 2, 3, 4)

DOK_INSTRUCTIONS = {
    1: (
        "DOK 1 — Recall & Reproduction\n"
        "The answer is explicitly stated in the text, typically in a single "
        "sentence or phrase. The question asks the student to locate or recall "
        "a fact, definition, or detail directly from the passage. Example "
        'shape: "What did Leo ask Maya to do?"'
    ),
    2: (
        "DOK 2 — Skill & Concept (Basic Reasoning)\n"
        "The answer requires connecting two or more pieces of information from "
        "the text, or recognizing a relationship — cause and effect, sequence, "
        "comparison. Not stated in a single sentence, but a short, direct "
        "logical step from what's stated. Example shape: \"Why did Leo take a "
        "deep breath before walking over to Maya?\""
    ),
    3: (
        "DOK 3 — Strategic Thinking (Inference & Analysis)\n"
        "The answer requires synthesizing information from multiple parts of "
        "the passage, drawing a conclusion the text supports but doesn't state "
        "outright, or evaluating motivation or theme with textual "
        "justification. The question should explicitly ask the student to "
        "support their answer with evidence from the text. Example shape: "
        '"What can you infer about how Leo felt about himself by the end of '
        'the story? Support your answer with evidence from the text."'
    ),
    4: (
        "DOK 4 — Extended Thinking\n"
        "True DOK 4 usually means a multi-day project or work across several "
        "texts. This is a single-passage worksheet, so write an "
        "extended-response tier: each question should still require "
        "synthesizing information from the passage, then ask for a longer, "
        "more developed answer that connects the story to the student's own "
        "experience or the real world. The connection must be grounded in "
        "something the passage actually supports. Example shape: \"Have you "
        "ever felt nervous about doing something brave? How does your "
        "experience compare to Leo's?\""
    ),
}


MAX_WORKSHEET_QUESTIONS = 8


def parse_dok_level(raw) -> int:
    """Worksheet-wide question demand. Defaults to recall (DOK 1)."""
    if raw is None or raw == "":
        return 1
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        return raw if raw in DOK_LEVELS else 1
    match = re.search(r"[1-4]", str(raw).strip())
    return int(match.group(0)) if match else 1


def parse_question_count(raw) -> int:
    """How many comprehension questions to write. Defaults to 5."""
    if raw is None or raw == "":
        return 5
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 5
    if count < 1 or count > MAX_WORKSHEET_QUESTIONS:
        return 5
    return count


def parse_dok_levels(raw) -> list[int]:
    """One or more DOK levels. Custom mixes keep order and drop duplicates."""
    if isinstance(raw, (list, tuple, set)):
        levels = []
        for item in raw:
            level = parse_dok_level(item)
            if level not in levels:
                levels.append(level)
        return levels or [1]
    return [parse_dok_level(raw)]


def _question_keys(count: int) -> list[str]:
    return [f"q{index}" for index in range(1, count + 1)]


def ordered_question_items(questions) -> list[tuple[str, str]]:
    """Stable q1, q2, … items with non-empty stems."""
    items = []
    for key, value in (questions or {}).items():
        match = re.fullmatch(r"q(\d+)", str(key), re.I)
        if not match:
            continue
        stem = str(value or "").strip()
        if stem:
            items.append((int(match.group(1)), stem))
    items.sort()
    return [(f"q{number}", stem) for number, stem in items]


def build_questions_prompt(story, title, dok_level, question_count=5, dok_levels=None):
    """Question-only prompt. The story is already written; do not rewrite it."""
    count = parse_question_count(question_count)
    levels = parse_dok_levels(dok_levels if dok_levels is not None else dok_level)
    heading = title.strip() if title else "Untitled"
    keys_json = ",\n".join(f'    "{key}": "...?"' for key in _question_keys(count))
    if len(levels) == 1:
        level = levels[0]
        demand = (
            f"Write exactly {count} questions about the story below at DOK "
            f"level {level}.\n\n"
            f"{DOK_INSTRUCTIONS[level]}\n\n"
        )
        stem_rule = (
            "Choose whatever stems fit this DOK level and this story."
        )
    else:
        labels = ", ".join(f"DOK {level}" for level in levels)
        demand = (
            f"Write exactly {count} questions about the story below at mixed "
            f"DOK levels: {labels}.\n"
            "Mix those levels across the set. Include at least one question "
            "at each listed DOK level when the count allows.\n\n"
            + "\n\n".join(DOK_INSTRUCTIONS[level] for level in levels)
            + "\n\n"
        )
        stem_rule = (
            "Choose whatever stems fit the listed DOK levels and this story."
        )
    return (
        "You are writing reading comprehension questions for a special "
        "education worksheet.\n\n"
        f"{demand}"
        "Rules:\n"
        f"- Write exactly {count} questions.\n"
        "- Write ordinary comprehension questions. Do not force a Who / What / "
        f"When / Where / Why template. {stem_rule}\n"
        "- Each question must end with a question mark.\n"
        "- Do not retell, rewrite, or continue the story.\n"
        "- Do not require facts the story does not support.\n"
        "- The story's reading level is already set. Do not make the questions "
        "easier or harder by changing the story; only the questions change.\n\n"
        f"Story title: {heading}\n\n"
        f"Story:\n{story}\n\n"
        "Respond with ONLY valid JSON (no markdown, no extra text) in this "
        "exact shape:\n"
        "{\n"
        '  "questions": {\n'
        f"{keys_json}\n"
        "  }\n"
        "}"
    )


def parse_questions_output(raw_text, question_count=5) -> dict:
    """Accept a questions object, or a full worksheet JSON that contains one."""
    count = parse_question_count(question_count)
    keys = _question_keys(count)
    if not raw_text or not str(raw_text).strip():
        raise ValueError("The writing assistant returned an empty set of questions. Try generating again.")
    text = str(raw_text).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    questions = None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            questions = data.get("questions")
    except json.JSONDecodeError:
        questions = None
    if not isinstance(questions, dict):
        questions = {}
        for key in keys:
            match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
            if not match:
                raise ValueError("The writing assistant's questions could not be read. Try generating again.")
            questions[key] = json.loads(f'"{match.group(1)}"')
    cleaned = {}
    for key in keys:
        stem = str(questions.get(key) or "").strip()
        if not stem:
            raise ValueError("The writing assistant left out a comprehension question. Try generating again.")
        if not stem.endswith("?"):
            stem = stem.rstrip(".!") + "?"
        cleaned[key] = stem
    return cleaned


def _log_dok_answerability(level: int):
    """There is no span-recovery verifier for worksheet questions.

    The only existing 'answerability' language is a line in the story prompt
    and readability scoring of stems on the leveled-passage API. A verbatim
    span check is not implemented, and would misfire on DOK 2–4. This log
    makes the skip visible so it cannot look like a pass.
    """
    if level == 1:
        _log_stage(
            "dok_answerability",
            dok_level=1,
            check="prompt_only",
            detail=(
                "No span-recovery verifier exists. DOK 1 keeps the prompt "
                "requirement that the answer is a locatable fact in the story."
            ),
        )
        return
    _log_stage(
        "dok_answerability",
        dok_level=level,
        check="skipped",
        detail=(
            "Answerability is skipped for DOK 2+ in this experimental pass. "
            "A span-style check would reject valid synthesis or inference."
        ),
    )


def generate_worksheet_questions(
    story, title, dok_level, question_count=5, dok_levels=None
):
    """Second model call: questions only, at the requested DOK level(s)."""
    count = parse_question_count(question_count)
    levels = parse_dok_levels(dok_levels if dok_levels is not None else dok_level)
    prompt = build_questions_prompt(
        story, title, levels[0], question_count=count, dok_levels=levels
    )
    raw_text = generate_text(prompt)
    questions = parse_questions_output(raw_text, count)
    for level in levels:
        _log_dok_answerability(level)
    return questions


def build_worksheet_prompt(
    grade, reading_level, interests, focus_vocabulary=None, focus_phonics=None
):
    user_prompt = (
        f"I have a student who is in {grade} grade and reads at a "
        f"{reading_level} grade level. {interests}"
    )
    terms = list(focus_vocabulary or [])
    if terms:
        quoted = ", ".join(f'"{term}"' for term in terms)
        user_prompt += (
            f" Focus vocabulary: {quoted}. Use each of these exact words "
            f"or short phrases 3 to 5 times in the story. Keep the spelling "
            f"exactly. Do not swap in an easier synonym."
        )
    phonics = parse_focus_phonics(focus_phonics)
    if phonics:
        user_prompt += (
            f' Focus phonics: "{phonics}". Include 3 to 5 different words '
            f"that contain this exact letter pattern. Use those words "
            f"naturally so the reader practices the pattern. Do not avoid "
            f"a word just because it contains the pattern."
        )
    return f"{WORKSHEET_SYSTEM_PROMPT}\n\nUser request:\n{user_prompt}"


def _classroom_grade_label(grade) -> str | None:
    """Turn a form value like 'K' or '9' into a short classroom label."""
    if grade is None:
        return None
    raw = str(grade).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in ("k", "kg", "kindergarten"):
        return "kindergarten"
    match = re.match(r"^(\d+)", lowered)
    if not match:
        return raw
    number = int(match.group(1))
    if number == 1:
        ordinal = "1st"
    elif number == 2:
        ordinal = "2nd"
    elif number == 3:
        ordinal = "3rd"
    else:
        ordinal = f"{number}th"
    return f"{ordinal} grade"


def _typical_student_age(grade) -> str | None:
    if grade is None:
        return None
    raw = str(grade).strip().lower()
    if raw in ("k", "kg", "kindergarten"):
        return "5-6 year olds"
    match = re.match(r"^(\d+)", raw)
    if not match:
        return None
    number = int(match.group(1))
    younger = number + 5
    return f"{younger}-{younger + 1} year olds"


_NO_TEXT_IN_IMAGE = (
    "The image must be a picture only. Do not paint, overlay, or caption any "
    "typed-out text anywhere in the image: no worksheet title, no story "
    "sentences, no action-beat lines, no captions, no labels, no speech "
    "bubbles with readable words, no watermarks, no signatures, and no "
    "letters or numbers of any kind. Use the story only as a description of "
    "what to draw, never as text to put in the picture."
)


def create_image_prompt(title, story, feedback=None, grade=None):
    """Illustration brief. Age the characters to the student's classroom grade,
    not their reading level — a 9th grader who reads at grade 3 still looks 14.

    Do not put the title in the prompt as a label to draw. Flux treats
    "titled '...'" as text to paint on the image.
    """
    del title  # kept on the signature for callers; never send it to Flux
    scene = (story or "").strip()[:200]
    classroom = _classroom_grade_label(grade)
    ages = _typical_student_age(grade)
    if classroom and ages:
        prompt = (
            f"An educational illustration for a {classroom} classroom. "
            f"Characters should look like typical {classroom} "
            f"students ({ages}): age-appropriate faces, clothing, and setting. "
            f"Do not depict younger children unless the story requires it. "
            f"Scene to draw: {scene} {_NO_TEXT_IN_IMAGE}"
        )
    else:
        prompt = (
            f"An educational illustration. Scene to draw: {scene} "
            f"{_NO_TEXT_IN_IMAGE}"
        )
    if feedback:
        prompt += f" Additional instructions: {feedback.strip()}"
    return prompt


def validate_structure(data):
    required = ["title", "story", "questions"]

    for field in required:
        if field not in data:
            raise ValueError("The writing assistant left out part of the worksheet. Try generating again.")

    if not ordered_question_items(data.get("questions")):
        raise ValueError("The writing assistant left out a comprehension question. Try generating again.")

    return True


def normalize_model_output(data):
    payload = {
        "Title": data["title"],
        "Story": data["story"],
    }
    for index, (_key, stem) in enumerate(ordered_question_items(data.get("questions")), start=1):
        payload[f"Q{index}"] = stem
    return payload


_client_lock = threading.Lock()
_project_client = None
_openai_client = None


def _get_openai_client():
    """Reuse one project client, authenticated as the Foundry service principal."""
    global _project_client, _openai_client
    if _openai_client is not None:
        return _project_client, _openai_client

    with _client_lock:
        if _openai_client is not None:
            return _project_client, _openai_client

        endpoint = _llm_endpoint()
        logger.info("Azure project endpoint: %s", endpoint)
        _project_client = AIProjectClient(
    endpoint=endpoint,
    credential=foundry_credential(),
    allow_preview=True,
)
        _openai_client = _project_client.get_openai_client(
            agent_name=LLM_AGENT_NAME
        )
        return _project_client, _openai_client


def generate_text(prompt):
    ensure_ai_configured()
    try:
        _, openai_client = _get_openai_client()
        response = openai_client.responses.create(input=prompt)
    except Exception as exc:
        raise RuntimeError(user_error_message(exc, "foundry")) from exc
    text = response.output_text
    if text is None or not str(text).strip():
        raise RuntimeError(FOUNDRY_DOWN)
    return text


def generate_image(image_prompt):
    ensure_ai_configured()
    url = f"{FLUX_API_URL.rstrip('/')}?api-version=preview"

    headers = {
        "Authorization": f"Bearer {FLUX_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": FLUX_MODEL,
        "prompt": image_prompt,
        "width": 1024,
        "height": 1024,
        "output_format": "jpeg",
        "num_images": 1,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
    except Exception as exc:
        raise RuntimeError(user_error_message(exc, "flux")) from exc

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        payload = dict(data) if isinstance(data, dict) else {}
        payload.setdefault("status_code", response.status_code)
        raise RuntimeError(user_error_message(payload, "flux"))
    if not isinstance(data, dict) or "data" not in data:
        raise RuntimeError(
            "Azure image generation did not return a picture. Try generating again."
        )

    try:
        img = data["data"][0]
    except (IndexError, KeyError, TypeError):
        raise RuntimeError(
            "Azure image generation did not return a picture. Try generating again."
        )

    if "b64_json" in img:
        return img["b64_json"]

    if "url" in img:
        try:
            img_bytes = requests.get(img["url"], timeout=60).content
        except Exception as exc:
            raise RuntimeError(user_error_message(exc, "flux")) from exc
        return base64.b64encode(img_bytes).decode()

    raise RuntimeError("Azure image generation did not return a picture. Try generating again.")


def parse_worksheet_output(raw_text):
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_fields(text)
        if extracted:
            return extracted
        return _parse_worksheet_text(text)


def _extract_json_fields(text):
    title_match = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    story_match = re.search(r'"story"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    if not title_match or not story_match:
        return None

    questions = {}
    for key in ["q1", "q2", "q3", "q4", "q5"]:
        match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
        if not match:
            return None
        questions[key] = json.loads(f'"{match.group(1)}"')

    return {
        "title": json.loads(f'"{title_match.group(1)}"'),
        "story": json.loads(f'"{story_match.group(1)}"'),
        "questions": questions,
    }


def _parse_worksheet_text(output_text):
    """Fallback parser preserved from the former backend."""
    output_text = output_text.replace("*", "")
    lines = [line.strip() for line in output_text.splitlines() if line.strip()]

    if len(lines) < 7:
        raise ValueError("The writing assistant's worksheet could not be read. Try generating again.")

    title = lines[0]
    question_indices = [i for i, line in enumerate(lines) if line.endswith("?")]
    if len(question_indices) < 5:
        raise ValueError("The writing assistant did not return five questions. Try generating again.")

    last_five_q_indices = question_indices[-5:]
    questions = [lines[i] for i in last_five_q_indices]
    story_lines = lines[1:last_five_q_indices[0]]
    story = "\n\n".join(story_lines)

    return {
        "title": title,
        "story": story,
        "questions": {
            "q1": questions[0],
            "q2": questions[1],
            "q3": questions[2],
            "q4": questions[3],
            "q5": questions[4],
        },
    }


def generate_worksheet_content(
    grade, reading_level, interests, focus_vocabulary=None, focus_phonics=None
):
    prompt = build_worksheet_prompt(
        grade,
        reading_level,
        interests,
        focus_vocabulary=focus_vocabulary,
        focus_phonics=focus_phonics,
    )
    raw_text = generate_text(prompt)
    model_data = parse_worksheet_output(raw_text)
    validate_structure(model_data)
    return model_data, raw_text


def build_pdf_bytes(
    worksheet_content,
    image_feedback=None,
    existing_image_base64=None,
    grade=None,
):
    pdf_json = normalize_model_output(worksheet_content)
    student_grade = grade if grade is not None else worksheet_content.get("grade")

    if image_feedback:
        image_prompt = create_image_prompt(
            pdf_json["Title"],
            pdf_json["Story"],
            feedback=image_feedback,
            grade=student_grade,
        )
        image_base64 = generate_image(image_prompt)
    elif existing_image_base64:
        image_base64 = existing_image_base64
    else:
        image_prompt = create_image_prompt(
            pdf_json["Title"], pdf_json["Story"], grade=student_grade
        )
        image_base64 = generate_image(image_prompt)

    pdf_json["image"] = f"data:image/jpeg;base64,{image_base64}"
    pdf_bytes = file.generate_worksheet_pdf(pdf_json)
    return pdf_bytes, image_base64


def _leveling_report(original_story, first_score, outcome):
    """Before/after numbers for the worksheet page comparison."""
    band = outcome.target_band
    return {
        "target_band": band.display,
        "first_draft": {
            "estimated_grade": round(first_score.estimated_grade, 2),
            "confidence": first_score.confidence,
            "in_band": rl_bands.in_band(first_score, band),
            "below_target": first_score.estimated_grade < band.low,
            "story": original_story,
        },
        "final": {
            "estimated_grade": round(outcome.final_score.estimated_grade, 2),
            "confidence": outcome.final_score.confidence,
            "in_band": outcome.gate_passed,
        },
        "improved": original_story.strip() != outcome.text.strip(),
        "llm_rewrite_applied": outcome.llm_rewrite_applied,
    }


def generate_full_worksheet(
    grade,
    reading_level,
    interests,
    focus_vocabulary=None,
    focus_phonics=None,
    dok_level=None,
    question_count=None,
    dok_levels=None,
):
    """Generate a worksheet, then run the reading-level pipeline on the story.

    The first model draft is kept so the review panel can show what the
    pipeline changed. The PDF uses the leveled story. A leveling miss does
    not abort the worksheet -- the closest result still ships, flagged.
    Focus vocabulary is written into the draft 3-5 times and passed as
    protected terms so the leveler cannot swap it for an easier word.
    A phonics pattern is asked for in 3-5 different words; those matching
    words are also protected so the leveler cannot strip the pattern.
    Questions are a second, independent call at `dok_level` so changing
    question demand cannot change the story or the leveler.
    """
    terms = parse_focus_vocabulary(focus_vocabulary)
    phonics = parse_focus_phonics(focus_phonics)
    model_data, raw_text = generate_worksheet_content(
        grade,
        reading_level,
        interests,
        focus_vocabulary=terms,
        focus_phonics=phonics,
    )
    model_data["grade"] = grade
    original_story = model_data["story"]
    first_score = score_text(original_story)
    protected = _unique_protected_terms(
        terms, words_matching_phonics(original_story, phonics)
    )

    try:
        outcome = correct_with_rewrite(
            original_story,
            reading_level,
            allow_rewrite=True,
            passage_type=rl_config.PASSAGE_TYPE_NARRATIVE,
            protected_terms=protected,
        )
        model_data["story"] = outcome.text
        leveling = _leveling_report(original_story, first_score, outcome)
    except (ReadingLevelError, ValueError):
        logger.exception("Reading-level pipeline failed; shipping the first draft")
        try:
            fallback_band = rl_bands.target_band(reading_level)
            target_display = fallback_band.display
            below_target = first_score.estimated_grade < fallback_band.low
        except ValueError:
            target_display = str(reading_level)
            below_target = False
        leveling = {
            "target_band": target_display,
            "first_draft": {
                "estimated_grade": round(first_score.estimated_grade, 2),
                "confidence": first_score.confidence,
                "in_band": False,
                "below_target": below_target,
                "story": original_story,
            },
            "final": {
                "estimated_grade": round(first_score.estimated_grade, 2),
                "confidence": first_score.confidence,
                "in_band": False,
            },
            "improved": False,
            "llm_rewrite_applied": False,
        }

    levels = parse_dok_levels(dok_levels if dok_levels is not None else dok_level)
    count = parse_question_count(question_count)
    model_data["questions"] = generate_worksheet_questions(
        model_data["story"],
        model_data.get("title", ""),
        levels[0],
        question_count=count,
        dok_levels=levels,
    )
    model_data["dok_level"] = levels[0] if len(levels) == 1 else None
    model_data["dok_levels"] = levels
    model_data["question_count"] = count

    pdf_bytes, image_base64 = build_pdf_bytes(model_data, grade=grade)
    return model_data, pdf_bytes, image_base64, raw_text, leveling


def _log_stage(stage, **fields):
    """Emit a parseable log line.

    These records are the dataset for future calibration and fine-tuning:
    every passage that needed several iterations is a training example, so the
    payload is JSON rather than prose.
    """
    logger.info(json.dumps({"stage": stage, **fields}, default=str))


def _score_summary(score):
    return {
        "estimated_grade": round(score.estimated_grade, 2),
        "confidence": score.confidence,
        "flags": list(score.flags),
    }


def build_leveled_prompt(topic, band, student_interest=None, feedback=None):
    """Prompt for a passage aimed at a band.

    The band is included because it improves first-draft quality, but it is
    only a hint: the scorer, not the model, decides whether the draft counts.
    """
    request = (
        f"Write the story about: {topic}. "
        f"Target reading level: {band.display} "
        f"(roughly grade {band.center:.0f})."
    )
    if student_interest:
        request += f" The student is interested in {student_interest}."
    if feedback:
        request += f" The previous attempt was rejected because {feedback}."

    return f"{WORKSHEET_SYSTEM_PROMPT}\n\nUser request:\n{request}"


def _difficulty_feedback(result):
    """Name the specific failure so the retry prompt can act on it."""
    if result.failure_reason == "below_target_band":
        return (
            "the passage read below the target grade band "
            "(use more specific words and slightly longer sentences)"
        )

    features = result.final_score.features
    limit = rl_bands.max_sentence_length(result.target_band)

    problems = []
    if features.max_sentence_length > limit:
        problems.append(
            f"the sentences were too long (up to {features.max_sentence_length} "
            f"words; keep them under {limit})"
        )
    if features.hard_word_ratio > 0:
        problems.append(
            "the vocabulary was too advanced (use shorter, more common words)"
        )
    if not problems:
        problems.append("the passage read above the target grade band")

    return " and ".join(problems)


def _level_questions(questions, band):
    """Score question stems against a widened band.

    A single stem is far below the length at which readability statistics mean
    anything, so these are measured and logged but never hard-gated.
    """
    tolerant_band = rl_bands.widen(band, rl_config.QUESTION_BAND_TOLERANCE)
    report = {}

    for key, stem in questions.items():
        score = score_text(stem)
        report[key] = {
            **_score_summary(score),
            "in_tolerant_band": rl_bands.in_band(score, tolerant_band),
        }

    _log_stage(
        "questions_scored",
        band=band.label,
        tolerance=rl_config.QUESTION_BAND_TOLERANCE,
        questions=report,
    )
    return report


def _leveled_response(model_data, band, result, protected_terms, rewritten=False):
    """Shape the API payload for an accepted passage.

    Raw feature values and internal coefficients never cross this boundary.
    """
    model_data["story"] = result.text
    questions = _level_questions(model_data["questions"], band)
    lowered = result.text.lower()

    naturalness = ai_rewrite.check_naturalness(
        result.text, band, generate=generate_text
    )

    return {
        "title": model_data["title"],
        "passage": result.text,
        "questions": model_data["questions"],
        "question_levels": questions,
        "reading_level": {
            "estimated_grade": round(result.final_score.estimated_grade, 2),
            "target_band": band.display,
            "in_band": result.gate_passed,
            "confidence": result.final_score.confidence,
        },
        "corrections_applied": len(result.edits),
        "protected_terms_preserved": [
            term for term in protected_terms if term.lower() in lowered
        ],
        "llm_rewrite_applied": rewritten,
        "naturalness": naturalness,
    }


def _log_correction(result, attempt):
    for edit in result.edits:
        _log_stage(
            "edit_applied",
            attempt=attempt,
            operator=edit.operator,
            sentence_index=edit.sentence_index,
            before=edit.before,
            after=edit.after,
            sense_disambiguated=edit.sense_disambiguated,
        )


def correct_with_rewrite(
    text,
    target_grade,
    *,
    protected_terms=None,
    allow_rewrite=False,
    passage_type=None,
):
    """Repair `text`, optionally escalating to an LLM rewrite when repair fails.

    Returns an ai_rewrite.RewriteOutcome and never raises on a content-quality
    failure: this backs the corrector page, where a teacher is reading the
    output and a partial improvement plus an honest "still above band" flag is
    more useful than an error. The generation path takes the opposite line --
    see generate_leveled_passage.

    `allow_rewrite` defaults to False so the deterministic promise of this path
    holds unless a caller explicitly asks to spend a model call.

    When CORRECTION_MODE is single_pass_llm, Operators A–C are skipped and
    Operator D runs from the first diagnostics pass. score_text still reports
    the grade. If the rewrite fails check_semantic_drift, Operator D retries
    once; a second failure returns the original passage with
    rewrite_rejected_semantic_drift. The iterative path is not used as a
    fallback on that failure — that would spend the CPU the flag is meant to
    save, on a request that already drifted.

    `passage_type` has no default here on purpose. This path takes arbitrary
    pasted text, so nothing in the request tells us whether it is a story or a
    science paragraph -- only the teacher knows. Omitting it warns and takes
    the stricter tier rather than handing narrative freedom to a passage whose
    facts a question set may depend on.
    """
    protected_terms = list(protected_terms or [])
    if rl_config.correction_mode() == rl_config.CORRECTION_MODE_SINGLE_PASS_LLM:
        result = measured_without_repair(
            text, target_grade, protected_terms=protected_terms
        )
    else:
        result = correct_text(text, target_grade, protected_terms=protected_terms)
    _log_correction(result, attempt=0)

    outcome = ai_rewrite.RewriteOutcome(
        text=result.text,
        correction=result,
        final_score=result.final_score,
        gate_passed=result.gate_passed,
        failure_reason=result.failure_reason,
    )

    # Deterministic operators only simplify, so a below-band draft is
    # unchanged until this rewrite pass raises it.
    if not allow_rewrite or result.gate_passed:
        return outcome

    rewritten = ai_rewrite.escalate_to_rewrite(
        result,
        target_grade,
        generate=generate_text,
        protected_terms=protected_terms,
        passage_type=passage_type,
        keep_closest=True,
    )
    return _polish_rewrite_with_repair(rewritten, target_grade, protected_terms)


def _polish_rewrite_with_repair(outcome, target_grade, protected_terms):
    """Run Operators A/B on a rewrite that is still above the band.

    A/B only simplify, so a below-band draft is left alone. A near-miss
    like 3.66 on a 3.5 ceiling can still be nicked into band.

    Skipped in single_pass_llm: that mode exists so the iterative loop does
    not run on this request.
    """
    if rl_config.correction_mode() == rl_config.CORRECTION_MODE_SINGLE_PASS_LLM:
        return outcome
    if outcome.gate_passed or not outcome.llm_rewrite_applied:
        return outcome
    score = outcome.final_score
    band = outcome.target_band
    if score is None or band is None or score.estimated_grade <= band.high:
        return outcome
    polished = correct_text(
        outcome.text, target_grade, protected_terms=protected_terms
    )
    if polished.gate_passed or (
        polished.final_score.estimated_grade < score.estimated_grade
    ):
        outcome.text = polished.text
        outcome.final_score = polished.final_score
        outcome.gate_passed = polished.gate_passed
        if polished.gate_passed:
            outcome.failure_reason = None
    return outcome


def generate_leveled_passage(
    topic,
    target_grade,
    *,
    protected_terms=None,
    student_interest=None,
    passage_type=rl_config.PASSAGE_TYPE_NARRATIVE,
):
    """Generate a passage and guarantee it lands in the target reading band.

    Returns a dict with the passage, its measured level, and correction
    metadata.     Raises ReadingLevelError if the passage cannot be brought into
    band -- too hard or too easy. Shipping mis-leveled material to a special
    educator silently is a worse failure than a visible error. That is the
    opposite of what correct_with_rewrite does, deliberately -- this output
    goes to a student, that one goes to a teacher who is reviewing it.

    Deterministic repair runs first. Only when it fails does Operator D spend a
    model call on a diagnostic-driven rewrite.

    `passage_type` defaults to narrative because that is what this path asks
    for: WORKSHEET_SYSTEM_PROMPT requests "a short fiction story", so a rewrite
    here is free to restructure the plot. If this flow ever grows a mode that
    generates factual passages, that mode must pass "informational" instead --
    the default is read off the generation prompt, not assumed.
    """
    band = rl_bands.target_band(target_grade)
    protected_terms = list(protected_terms or [])
    feedback = None
    last_result = None
    last_outcome = None

    # One shared budget across generation AND rewrite calls, so adding Operator
    # D cannot multiply the number of model calls a request makes. With the
    # default of 2 this buys one draft plus one targeted rewrite, in place of
    # two blind drafts -- a rewrite steered by diagnostics is a better use of
    # the second call than regenerating and hoping.
    calls_remaining = rl_config.MAX_GENERATION_ATTEMPTS

    for attempt in range(1, rl_config.MAX_GENERATION_ATTEMPTS + 1):
        if calls_remaining <= 0:
            break

        prompt = build_leveled_prompt(topic, band, student_interest, feedback)
        raw_text = generate_text(prompt)
        calls_remaining -= 1
        model_data = parse_worksheet_output(raw_text)
        validate_structure(model_data)

        passage = model_data["story"]
        initial = score_text(passage)
        _log_stage(
            "generated",
            attempt=attempt,
            band=band.label,
            initial_score=_score_summary(initial),
        )

        if rl_config.correction_mode() == rl_config.CORRECTION_MODE_SINGLE_PASS_LLM:
            result = measured_without_repair(
                passage, target_grade, protected_terms=protected_terms
            )
        else:
            result = correct_text(
                passage, target_grade, protected_terms=protected_terms
            )
        last_result = result
        _log_correction(result, attempt)

        _log_stage(
            "corrected",
            attempt=attempt,
            band=band.label,
            iterations=result.iterations,
            edits=len(result.edits),
            score_trajectory=[round(grade, 2) for grade in result.score_trajectory],
            in_band=result.in_band,
            failure_reason=result.failure_reason,
            final_score=_score_summary(result.final_score),
            protected_terms_preserved=result.protected_terms_preserved,
        )

        if result.in_band:
            return _leveled_response(model_data, band, result, protected_terms)

        # Operator D: deterministic repair could not close the gap, so spend a
        # call on a rewrite steered by what the scorer actually measured.
        if calls_remaining > 0:
            outcome = ai_rewrite.escalate_to_rewrite(
                result,
                target_grade,
                generate=generate_text,
                protected_terms=protected_terms,
                max_attempts=calls_remaining,
                passage_type=passage_type,
            )
            calls_remaining -= outcome.llm_attempts
            last_outcome = outcome

            if outcome.gate_passed:
                model_data["story"] = outcome.text
                return _leveled_response(
                    model_data, band, outcome, protected_terms, rewritten=True
                )

        feedback = _difficulty_feedback(result)

    failure_reason = (
        last_outcome.failure_reason
        if last_outcome is not None
        else (last_result.failure_reason if last_result else None)
    )
    _log_stage(
        "gate_failed",
        band=band.label,
        attempts=rl_config.MAX_GENERATION_ATTEMPTS,
        failure_reason=failure_reason,
        llm_rewrite_attempted=last_outcome is not None,
    )
    raise ReadingLevelError(
        f"Could not bring the passage into {band.display} after "
        f"{rl_config.MAX_GENERATION_ATTEMPTS} attempts.",
        reason=failure_reason,
        score=last_result.final_score if last_result else None,
        band=band,
    )


_BLOCK_CLASSIFY_PROMPT = """You assign a type to one excerpt from a student worksheet.
Do not rewrite, summarize, or add text. Classify only.

Allowed types: passage, instructions, question, answer_choice, ignore

Use ignore for labels and fields the student fills in (Name, Date, Score)
and for anything that should not be rewritten.

Excerpt:
{text}

Respond with ONLY valid JSON: {{"block_type": "passage"}}"""


def classify_unclassified_blocks(blocks, *, generate=None):
    """Kept for callers; ignore/image blocks are never sent to the model."""
    generate = generate or generate_text
    updated = []
    for block in blocks:
        if block.block_type != "ignore" or block.confidence == "high":
            updated.append(block)
            continue
        if block.asset_base64:
            updated.append(block)
            continue
        raw = generate(_BLOCK_CLASSIFY_PROMPT.format(text=block.text))
        assigned = _parse_block_type(raw)
        updated.append(
            WorksheetBlock(
                block_id=block.block_id,
                block_type=assigned,
                text=block.text,
                source_position=block.source_position,
                confidence="low",
                asset_base64=block.asset_base64,
                asset_mime=block.asset_mime,
            )
        )
    return updated


def _parse_block_type(raw_text) -> str:
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        value = str(data.get("block_type") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        value = ""
        match = re.search(
            r"passage|instructions|question|answer_choice|ignore",
            text,
        )
        if match:
            value = match.group(0)
    if value == "unclassified":
        value = "ignore"
    return value if value in BLOCK_TYPES else "ignore"


def segment_worksheet(data: bytes, filename: str, *, generate=None, use_llm=False):
    """Split a worksheet. Leftover labels default to ignore; the teacher confirms.

    `use_llm` is off by default: confirmation is the checkpoint, not a second
    guess from the model. Image blocks always pass through unchanged.
    """
    blocks = segment_worksheet_bytes(data, filename)
    if use_llm and any(
        block.block_type == "ignore" and block.confidence == "low"
        for block in blocks
    ):
        blocks = classify_unclassified_blocks(blocks, generate=generate)
    return blocks


def build_convert_question_prompt(
    stem, target_dok, passage, source_material=None, protected_terms=None
):
    """Rewrite an existing stem to a target DOK. Reuses generation definitions."""
    level = parse_dok_level(target_dok)
    protected = ", ".join(f'"{term}"' for term in (protected_terms or []) if term)
    source = (source_material or "").strip()
    return (
        "You are converting an existing reading-comprehension question to a "
        f"different Depth of Knowledge level. Target: DOK {level}.\n\n"
        f"{DOK_INSTRUCTIONS[level]}\n\n"
        "Keep the same underlying fact or idea as the original question. "
        "Do not invent a new topic. Do not rewrite the passage. "
        "Write ordinary comprehension questions — do not force a "
        "Who / What / When / Where / Why template.\n"
        "Ground the question in the passage"
        + (" and the source material" if source else "")
        + ". Do not require facts that neither one supports.\n"
        + (f"Protected terms, keep verbatim: {protected}\n" if protected else "")
        + f"\nOriginal question:\n{stem}\n\nPassage:\n{passage}\n"
        + (f"\nSource material:\n{source}\n" if source else "")
        + "\nRespond with ONLY valid JSON: {\"question\": \"...?\"}"
    )


def convert_question_to_dok(
    stem,
    target_dok,
    passage,
    source_material=None,
    protected_terms=None,
    *,
    generate=None,
):
    generate = generate or generate_text
    prompt = build_convert_question_prompt(
        stem, target_dok, passage, source_material, protected_terms
    )
    raw = generate(prompt)
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        question = str(data.get("question") or "").strip()
    except json.JSONDecodeError:
        question = ""
        match = re.search(r'"question"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if match:
            question = json.loads(f'"{match.group(1)}"').strip()
    if not question:
        raise ValueError("The writing assistant did not return a rewritten question. Try again.")
    if not question.endswith("?"):
        question = question.rstrip(".!") + "?"
    return question


def modulate_worksheet(
    blocks,
    target_grade,
    *,
    source_material="",
    worksheet_kind="practice",
    dok_mix=None,
    protected_terms=None,
    allow_rewrite=True,
    passage_type=None,
    convert_questions=False,
    generate=None,
    title="Worksheet",
):
    """Level passages and choices. Questions stay put unless asked.

    Convert is off by default: the teacher keeps the original stems.
    When `convert_questions` is on, stems in the wrong DOK bucket are
    rewritten toward the mix (convert, not replace).
    """
    generate = generate or generate_text
    protected = list(protected_terms or [])
    kind = (worksheet_kind or "practice").strip().lower()
    required_source = kind == "test"
    source_tokens = rl_dok.check_source_material(
        source_material, required=required_source
    )

    working = []
    for block in blocks:
        item = block if isinstance(block, WorksheetBlock) else WorksheetBlock.from_dict(block)
        if item.block_type == "image" and item.asset_base64:
            working.append(item)
        elif item.text:
            working.append(item)

    passage_report = []
    leveled = []
    for block in working:
        if block.block_type == "passage":
            outcome = correct_with_rewrite(
                block.text,
                target_grade,
                protected_terms=protected,
                allow_rewrite=allow_rewrite,
                passage_type=passage_type,
            )
            leveled.append(
                WorksheetBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=outcome.text,
                    source_position=block.source_position,
                    confidence=block.confidence,
                )
            )
            passage_report.append({
                "block_id": block.block_id,
                "initial_grade": round(outcome.correction.initial_score.estimated_grade, 2),
                "final_grade": round(outcome.final_score.estimated_grade, 2),
                "in_band": outcome.gate_passed,
                "target_band": outcome.target_band.display,
                "llm_rewrite_applied": outcome.llm_rewrite_applied,
            })
        elif block.block_type == "answer_choice":
            result = correct_text(
                block.text, target_grade, protected_terms=protected
            )
            _, label_text = rl_dok.choice_label_and_text(block.text)
            leveled_body = result.text.strip()
            label, _ = rl_dok.choice_label_and_text(block.text)
            if label and not re.match(r"^[A-D][.)]", leveled_body):
                leveled_body = f"{label}. {leveled_body}"
            elif not label:
                leveled_body = leveled_body or label_text
            leveled.append(
                WorksheetBlock(
                    block_id=block.block_id,
                    block_type=block.block_type,
                    text=leveled_body,
                    source_position=block.source_position,
                    confidence=block.confidence,
                )
            )
        else:
            leveled.append(block)

    passage_text = " ".join(
        block.text for block in leveled if block.block_type == "passage"
    )
    corpora = (passage_text, source_material or "")
    items = rl_dok.group_question_items(leveled)
    before = rl_dok.bucket_questions(items, *corpora)
    percents = dok_mix or rl_dok.default_dok_mix(target_grade)
    target_counts = rl_dok.mix_to_counts(len(items), percents)
    targets = rl_dok.target_dok_for_items(before, target_counts)

    converted_ids = []
    by_id = {block.block_id: block for block in leveled}
    if convert_questions:
        for item, diagnostic, target in zip(items, before, targets):
            if diagnostic.estimated_dok == target:
                continue
            try:
                new_stem = convert_question_to_dok(
                    item.question.text,
                    target,
                    passage_text,
                    source_material=source_material,
                    protected_terms=protected,
                    generate=generate,
                )
            except Exception:
                logger.exception(
                    "question_convert_failed", extra={"block_id": item.question.block_id}
                )
                continue
            old = item.question
            by_id[old.block_id] = WorksheetBlock(
                block_id=old.block_id,
                block_type=old.block_type,
                text=new_stem,
                source_position=old.source_position,
                confidence=old.confidence,
            )
            converted_ids.append(old.block_id)
        leveled = [by_id[block.block_id] for block in leveled]

    items = rl_dok.group_question_items(leveled)
    after = rl_dok.bucket_questions(items, *corpora)
    after_levels = [item.estimated_dok for item in after]
    achieved = rl_dok.mix_from_levels(after_levels)
    _log_stage(
        "dok_mix",
        source_tokens=source_tokens,
        before={row.estimated_dok: None for row in before},
        before_counts=rl_dok.mix_from_levels([row.estimated_dok for row in before]),
        target_counts=target_counts,
        achieved_counts=achieved,
        converted_block_ids=converted_ids,
        convert_questions=convert_questions,
    )

    gate_failures = []
    for row in passage_report:
        if not row["in_band"]:
            gate_failures.append({
                "check": "passage_band",
                "block_id": row["block_id"],
                "detail": (
                    f"Passage measured grade {row['final_grade']}, "
                    f"outside {row['target_band']}."
                ),
            })

    lowered_passages = passage_text.lower()
    for term in protected:
        if term.lower() not in lowered_passages:
            gate_failures.append({
                "check": "protected_terms",
                "block_id": None,
                "detail": f'Protected term "{term}" is missing from the passage.',
            })

    if convert_questions and not rl_dok.mix_within_tolerance(
        achieved, target_counts, len(items)
    ):
        gate_failures.append({
            "check": "dok_mix",
            "block_id": None,
            "detail": f"DOK mix {achieved} is outside tolerance of {target_counts}.",
        })

    if convert_questions:
        for item, diagnostic, target in zip(items, after, targets):
            if diagnostic.unanswerable and target <= 2:
                gate_failures.append({
                    "check": "answerability",
                    "block_id": item.question.block_id,
                    "detail": "No locatable overlap with the passage or source material.",
                })
            if item.choices:
                for problem in rl_dok.check_distractors(item.choices, *corpora):
                    gate_failures.append({
                        "check": "distractors",
                        "block_id": item.question.block_id,
                        "detail": problem,
                    })

    worksheet = file.blocks_to_worksheet_data(title, leveled)
    pdf_bytes = file.generate_blocks_pdf(title, leveled)
    return {
        "title": (worksheet or {}).get("Title") or title,
        "worksheet": worksheet,
        "blocks": [block.to_dict() for block in leveled],
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
        "source_tokens": source_tokens,
        "passages": passage_report,
        "dok": {
            "before": [row.to_dict() for row in before],
            "after": [row.to_dict() for row in after],
            "before_counts": rl_dok.mix_from_levels([row.estimated_dok for row in before]),
            "target_counts": target_counts,
            "achieved_counts": achieved,
            "converted_block_ids": converted_ids,
            "percents": percents,
        },
        "protected_terms_preserved": [
            term for term in protected if term.lower() in lowered_passages
        ],
        "gate_passed": not gate_failures,
        "gate_failures": gate_failures,
    }


if __name__ == "__main__":
    content, _ = generate_worksheet_content(
        grade="6",
        reading_level="1st",
        interests="my student is interested in watercolor paintings",
    )
    print(json.dumps(content, indent=2))
