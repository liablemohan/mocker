# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Default port: Railway injects $PORT at runtime; HF Spaces uses 7860
ENV PORT=7860

# Install system dependencies for OCR and PDF processing
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    tesseract-ocr-script-deva \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (required by Hugging Face Spaces)
RUN useradd -m -u 1000 appuser

# Set the working directory
WORKDIR /app

# Copy and install Python dependencies first (layer cache)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Create runtime directories and give ownership to appuser
RUN mkdir -p /app/jobs /app/extract_pages && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port 7860 (required by Hugging Face Spaces)
EXPOSE 7860

# Use gunicorn for production (not Flask dev server)
# $PORT is injected by Railway; falls back to 7860 (HF Spaces default)
CMD gunicorn --bind 0.0.0.0:${PORT:-7860} --workers 2 --timeout 300 --log-level info server:app
