FROM python:3.12

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Azure CLI from official Microsoft repository using the install script
RUN curl -sL https://aka.ms/InstallAzureCliDeb | bash

# Verify Azure CLI installation
RUN az --version && which az

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code FIRST (before trying to import it)
COPY . .

# Download required spacy and nltk models for reading level analysis
RUN python -m spacy download en_core_web_md
RUN python -m nltk.downloader wordnet omw-1.4 names

# Pre-warm the sentence encoder for embedding model (now that reading_level is available)
RUN python -c "from reading_level._nlp import get_sentence_encoder; get_sentence_encoder()"

# Expose Flask port (Railway will assign via PORT env var)
EXPOSE 5000

# Create a startup script to verify Azure CLI is available at runtime
RUN cat > /app/start.sh << 'EOF'
#!/bin/bash
echo "=== Runtime diagnostics ==="
echo "PATH: $PATH"
echo "Checking for az command..."
which az && az --version || echo "WARNING: az command not found"
echo "Starting Flask app..."
exec python app.py
EOF
RUN chmod +x /app/start.sh

# Run Flask app via startup script for diagnostics
CMD ["/app/start.sh"]
