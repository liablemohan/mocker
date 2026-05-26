"""
ExamDesk Pipeline Configuration
================================
Edit the values below to customise your deployment.
"""

import os

# ─── Gemini API ───────────────────────────────────────────────────────────────
# Replace with your actual Gemini API key.
# Get one at: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = "AIzaSyBUSAvljYLAC4v99WgLT47f6X4ku3aRucA"

# Gemini model to use for question extraction
GEMINI_MODEL = "gemini-2.5-flash"

# ─── Sanskrit OCR Site ────────────────────────────────────────────────────────
BASE_OCR_URL = "https://ocr.sanskritdictionary.com/"

# ─── Selenium/Tesseract Timing ────────────────────────────────────────────────
# No delay needed for local Tesseract OCR.
MIN_DELAY = 0
MAX_DELAY = 0


# ─── File / Directory Paths ───────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")

# ─── Flask Server ─────────────────────────────────────────────────────────────
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5050
FLASK_DEBUG = False

# ─── PDF Rendering ───────────────────────────────────────────────────────────
# DPI for PDF → PNG rasterisation (higher = better OCR, larger files)
PDF_DPI = 200
