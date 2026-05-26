"""
extract_questions.py
====================
Extracts questions and relevant information from a question paper PDF.

Strategy:
  1. Try copy-paste (PyPDF2 text extraction) — fast, no dependencies on Tesseract.
     If a page yields meaningful text (>50 chars of actual content), use it.
  2. Fallback to local Tesseract OCR (pdf2image + tesseract subprocess) for
     pages where copy-paste fails or returns only structural metadata.

Output:
  - extracted_raw.txt  : raw text from every page (for inspection)
  - questions.json     : structured JSON with questions parsed by Gemini
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path
from PyPDF2 import PdfReader
from pdf2image import convert_from_path

# ─── Configuration ────────────────────────────────────────────────────────────
PDF_FILE    = "Paper_20260410143543.pdf"   # Input PDF
RAW_OUTPUT  = "extracted_raw.txt"          # All raw OCR/extracted text
JSON_OUTPUT = "questions.json"             # Structured question JSON
IMAGE_DIR   = "extract_pages"             # Temp directory for rendered PNGs
PDF_DPI     = 300                          # DPI for rendering (higher = better)
LANG        = "eng"                        # Tesseract language

# ─── Helper: find Tesseract ───────────────────────────────────────────────────
def find_tesseract():
    import shutil
    path = shutil.which("tesseract")
    if path:
        return path
    for p in ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"]:
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Tesseract not found. Install with: brew install tesseract")

TESSERACT = find_tesseract()
print(f"✅ Tesseract found: {TESSERACT}")

# ─── Helper: detect structural-only text (metadata, no real content) ──────────
STRUCTURAL_PATTERNS = re.compile(
    r"(Question Id\s*:|Option Shuffling|Question Type\s*:|"
    r"Display Question Number|Is\nQuestion Mandatory|Option Orientation|"
    r"\d{10,}\.?\s*\d{1,2}$)",  # long IDs like 43244947421. 1
    re.IGNORECASE | re.MULTILINE,
)

def is_structural_only(text: str) -> bool:
    """Return True if the extracted text is only PDF metadata, not actual content."""
    if not text or len(text.strip()) < 30:
        return True
    # Count structural lines vs content lines
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    structural = sum(1 for l in lines if STRUCTURAL_PATTERNS.search(l))
    content = len(lines) - structural
    # If 70%+ of non-empty lines are structural metadata, it's structural-only
    if len(lines) > 0 and structural / len(lines) > 0.7:
        return True
    return False

# ─── Step 1: Try copy-paste extraction ────────────────────────────────────────
def extract_via_copy_paste(pdf_path: str) -> dict[int, str]:
    """Try PyPDF2 text extraction. Returns {page_num: text} for pages with real content."""
    print("\n📋 Step 1: Trying copy-paste (PyPDF2) extraction…")
    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    print(f"   Total pages: {total}")

    good_pages = {}
    structural_pages = []

    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not is_structural_only(text):
            good_pages[i] = text
        else:
            structural_pages.append(i)

    print(f"   ✅ Pages with real content via copy-paste: {len(good_pages)}")
    print(f"   ⚠️  Pages needing OCR: {len(structural_pages)}")
    return good_pages, structural_pages, total

# ─── Step 2: Tesseract OCR for remaining pages ────────────────────────────────
def ocr_page_tesseract(image_path: str, lang: str = "eng") -> str:
    """Run Tesseract on a single PNG image. Returns extracted text."""
    abs_path = str(Path(image_path).absolute())
    cwd = str(Path(image_path).parent.absolute())

    result = subprocess.run(
        [TESSERACT, abs_path, "stdout", "--oem", "3", "--psm", "6", "-l", lang],
        capture_output=True,
        cwd=cwd,
    )
    text = result.stdout.decode("utf-8", errors="replace").strip()
    return text

def extract_via_tesseract(pdf_path: str, page_numbers: list[int]) -> dict[int, str]:
    """Render and OCR specific pages using dual-pass script extraction. Returns {page_num: text}."""
    if not page_numbers:
        return {}

    print(f"\n🔍 Step 2: Tesseract Dual-Pass OCR on {len(page_numbers)} pages…")
    os.makedirs(IMAGE_DIR, exist_ok=True)

    ocr_results = {}
    for idx, pg in enumerate(page_numbers, 1):
        print(f"   [{idx}/{len(page_numbers)}] OCR page {pg} (dual-pass)…", end="", flush=True)
        images = convert_from_path(pdf_path, dpi=PDF_DPI, first_page=pg, last_page=pg)
        if not images:
            print(" ❌ render failed")
            continue

        img_path = os.path.join(IMAGE_DIR, f"page_{pg:03d}.png")
        images[0].save(img_path, "PNG")

        # Pass 1: Roman Script (English)
        text_roman = ocr_page_tesseract(img_path, lang="eng")
        # Pass 2: Devanagari Script (Hindi/Sanskrit)
        text_devanagari = ocr_page_tesseract(img_path, lang="script/Devanagari")

        combined_text = (
            f"=== Page {pg} (Roman Script) ===\n{text_roman}\n\n"
            f"=== Page {pg} (Devanagari Script) ===\n{text_devanagari}\n"
        )
        ocr_results[pg] = combined_text
        
        status = "✅" if len(text_roman) > 50 or len(text_devanagari) > 50 else "⚠️ (short)"
        print(f" {status} [Roman: {len(text_roman)} chars, Devanagari: {len(text_devanagari)} chars]")

    return ocr_results

# ─── Step 3: Merge and save raw text ──────────────────────────────────────────
def save_raw_text(all_pages: dict[int, str], output_path: str):
    print(f"\n💾 Saving raw text to {output_path}…")
    with open(output_path, "w", encoding="utf-8") as f:
        for pg in sorted(all_pages):
            f.write(f"{'='*60}\nPage {pg}\n{'='*60}\n")
            f.write(all_pages[pg])
            f.write("\n\n")
    print(f"   ✅ Written {len(all_pages)} pages")

# ─── Step 4 (Optional): Parse with Gemini ─────────────────────────────────────
GEMINI_PROMPT = """Return this exact JSON structure with NO extra text:
{{
  "sections": [
    {{
      "name": "string (e.g. 'General', 'Reasoning', 'Section A')",
      "questions": [
        {{
          "number": <integer>,
          "text": "full question text in English (Roman script)",
          "textDevanagari": "full question text in Hindi/Sanskrit (Devanagari script)",
          "options": [
            {{ "key": "1", "text": "English option text", "textDevanagari": "Devanagari option text" }},
            {{ "key": "2", "text": "English option text", "textDevanagari": "Devanagari option text" }},
            {{ "key": "3", "text": "English option text", "textDevanagari": "Devanagari option text" }},
            {{ "key": "4", "text": "English option text", "textDevanagari": "Devanagari option text" }}
          ],
          "correctAnswer": "1" | "2" | "3" | "4" | null
        }}
      ]
    }}
  ]
}}

Rules:
- For bilingual questions (presented in both English/Roman and Hindi/Devanagari scripts), you MUST extract both languages and match them by their Question ID, Question Number, and Option Codes.
- Merge the English and Devanagari translations of the same question into a single question object with "text" (English) and "textDevanagari" (Hindi/Sanskrit) fields.
- Similarly, match option codes to populate "text" and "textDevanagari" for each option.
- Completely clean up any OCR errors, garbled text, or noise.
- MCQ only. correctAnswer should be filled only if an explicit answer key is in the text (e.g., "Ans: 1" or "Answer: 2"). Otherwise null.
- Return ONLY the JSON — no markdown code blocks, no other text.

Raw text (containing separate Roman and Devanagari OCR blocks):
---
{raw_text}
---"""

def parse_with_gemini(raw_text: str) -> dict:
    """Call Gemini API to extract structured questions from raw text."""
    try:
        from config import GEMINI_API_KEY, GEMINI_MODEL
        from google import genai as google_genai

        print(f"\n🤖 Calling Gemini ({GEMINI_MODEL}) to extract questions…")
        client = google_genai.Client(api_key=GEMINI_API_KEY)
        prompt = GEMINI_PROMPT.format(raw_text=raw_text[:150000])  # token guard

        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw_response = response.text.strip()

        # Strip markdown fences if Gemini wraps the response
        raw_response = re.sub(r"^```[a-z]*\n?", "", raw_response)
        raw_response = re.sub(r"\n?```$", "", raw_response).strip()

        return json.loads(raw_response)

    except ImportError as e:
        print(f"   ⚠️ Skipping Gemini step (missing module: {e})")
        return {}
    except Exception as e:
        print(f"   ❌ Gemini call failed: {e}")
        return {}

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    pdf_path = PDF_FILE
    if not os.path.exists(pdf_path):
        print(f"❌ PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"📄 Processing: {pdf_path}")

    # Step 1: Copy-paste
    good_pages, structural_pages, total = extract_via_copy_paste(pdf_path)

    # Step 2: Tesseract for structural/empty pages
    ocr_pages = extract_via_tesseract(pdf_path, structural_pages)

    # Merge: OCR result takes priority for pages that needed it
    all_pages = {**good_pages}
    for pg, text in ocr_pages.items():
        # Use OCR if it produced more content than copy-paste, or if copy-paste was structural
        cp_text = good_pages.get(pg, "")
        all_pages[pg] = text if len(text) > len(cp_text) else cp_text

    # Step 3: Save raw text
    save_raw_text(all_pages, RAW_OUTPUT)

    # Step 4: Parse with Gemini (optional)
    combined_raw = "\n\n".join(all_pages[pg] for pg in sorted(all_pages))
    parsed = parse_with_gemini(combined_raw)

    if parsed:
        with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        total_q = sum(len(s.get("questions", [])) for s in parsed.get("sections", []))
        print(f"\n✅ Extracted {total_q} questions across {len(parsed.get('sections', []))} sections")
        print(f"   Saved to: {JSON_OUTPUT}")
    else:
        print(f"\n⚠️  Gemini step skipped — raw text only in {RAW_OUTPUT}")

    print(f"\n📊 Summary:")
    print(f"   Total PDF pages : {total}")
    print(f"   Copy-paste OK   : {len(good_pages)}")
    print(f"   Tesseract OCR   : {len(ocr_pages)}")
    print(f"   Raw text saved  : {RAW_OUTPUT}")

if __name__ == "__main__":
    main()
