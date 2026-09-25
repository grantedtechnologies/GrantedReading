FROM python:3.12

WORKDIR /app

# Install only essential system dependencies (no Azure CLI yet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --upgrade pip setuptools wheel

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Download required spacy and nltk models for reading level analysis
RUN python -m spacy download en_core_web_md
RUN python -m nltk.downloader wordnet omw-1.4 names

# Pre-warm the sentence encoder for embedding model
RUN python -c "from reading_level._nlp import get_sentence_encoder; get_sentence_encoder()"

# Expose Flask port (Railway will assign via PORT env var)
EXPOSE 5000

# Run Flask app
CMD ["python", "app.py"]
