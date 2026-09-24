FROM python:3.12-slim

WORKDIR /app

# Install system dependencies required by Azure CLI
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    python3-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --upgrade pip setuptools wheel

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Azure CLI - with explicit error checking
RUN pip install --no-cache-dir azure-cli 2>&1 | tee /tmp/azure-cli-install.log && \
    az --version || (cat /tmp/azure-cli-install.log && exit 1)

# Download required spacy and nltk models for reading level analysis
RUN python -m spacy download en_core_web_md
RUN python -m nltk.downloader wordnet omw-1.4 names

# Pre-warm the sentence encoder for embedding model
RUN python -c "from reading_level._nlp import get_sentence_encoder; get_sentence_encoder()"

# Copy application code
COPY . .

# Expose Flask port (Railway will assign via PORT env var)
EXPOSE 5000

# Run Flask app
CMD ["python", "app.py"]
