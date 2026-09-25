FROM python:3.12

WORKDIR /app

# Install system dependencies including everything Azure CLI needs
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --upgrade pip setuptools wheel

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Azure CLI
RUN pip install --no-cache-dir azure-cli

# Verify Azure CLI is installed and in PATH
RUN az --version || (which az && echo "az found at: $(which az)") || echo "WARNING: az not found"

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
