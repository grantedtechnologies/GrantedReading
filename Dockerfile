FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NLTK_DATA=/usr/local/share/nltk_data \
    PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN grep -v '^sentence-transformers' requirements.txt > /tmp/requirements-docker.txt \
    && pip install -r /tmp/requirements-docker.txt \
    && python -m spacy download en_core_web_md \
    && python -m nltk.downloader -d /usr/local/share/nltk_data wordnet omw-1.4 names

COPY . .

EXPOSE 5000

# One worker: the Foundry client and spaCy pipeline are process-local.
# Generation can take longer than gunicorn's default 30s.
# -u flag unbuffers Python output so logs appear immediately
CMD ["sh", "-c", "python -u scripts/load_granted_sql.py && gunicorn --bind 0.0.0.0:${PORT} --workers 1 --timeout 180 app:app"]
