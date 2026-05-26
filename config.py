"""
ExamDesk Pipeline Configuration
================================
Edit the values below to customise your deployment.
"""

import os

# ─── Gemini API ────────────────────────────────────────────────────────────────────
# API key is read from the GEMINI_API_KEY environment variable.
# For local development, you can set it by running:
#   export GEMINI_API_KEY="your_key_here"
# Or create a .env file (see README for instructions).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Gemini model to use for question extraction
GEMINI_MODEL = "gemini-2.5-flash"


# ─── File / Directory Paths ───────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")

# ─── Flask Server ─────────────────────────────────────────────────────────────
FLASK_HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.environ.get("FLASK_RUN_PORT", 7860))  # 7860 = HF Spaces default; use 5050 for local dev
FLASK_DEBUG = False

# ─── PDF Rendering ───────────────────────────────────────────────────────────
# DPI for PDF → PNG rasterisation (higher = better OCR, larger files)
PDF_DPI = 200
