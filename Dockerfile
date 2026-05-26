# Use an official lightweight Python image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Hugging Face Spaces requires port 7860. For local dev, override with:
#   FLASK_RUN_PORT=5050 python server.py
ENV FLASK_RUN_PORT=7860
# GEMINI_API_KEY must be provided at runtime, e.g.:
#   docker run -e GEMINI_API_KEY=your_key examdesk
# It is intentionally NOT set here to avoid baking secrets into the image.

# Install system dependencies required for OCR and PDF processing
# We install tesseract-ocr, poppler-utils, and language packs (English, Hindi, and Devanagari script)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    tesseract-ocr-script-deva \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the requirements file and install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Expose port 7860 (required by Hugging Face Spaces)
EXPOSE 7860

# Run the Flask server
CMD ["python", "server.py"]
