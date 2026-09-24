FROM python:3.12-slim

WORKDIR /app

# Install system dependencies including Azure CLI
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    lsb-release \
    ca-certificates \
    apt-transport-https \
    && echo "deb [arch=amd64] https://packages.microsoft.com/repos/azure-cli/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/azure-cli.list \
    && curl -sL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor | tee /etc/apt/trusted.gpg.d/microsoft.gpg > /dev/null \
    && apt-get update \
    && apt-get install -y azure-cli \
    && which az \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
