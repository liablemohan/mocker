"""
ExamDesk Backend Server
=======================
Provides a REST API that orchestrates:
  1. PDF → PNG conversion  (pdf2image / Poppler)
  2. Page-by-page OCR      (Tesseract — dual-pass: English + Devanagari)
  3. Gemini API call        (google-genai SDK)
  4. Structured JSON output saved to disk and returned to the browser

Run:
    python server.py

Endpoints:
    POST /upload                    — Upload a PDF; returns { job_id }
    GET  /status/<job_id>           — Returns current pipeline status JSON
    GET  /result/<job_id>           — Returns the final parsed JSON
    GET  /health                    — Returns { ok: true }
    POST /reset                     — Clears all jobs and cached data
"""

import os
import re
import sys
import json
import uuid
import shutil
import threading
import traceback
import subprocess
from pathlib import Path
from datetime import datetime

# ─── Flask ────────────────────────────────────────────────────────────────────
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ─── Config ───────────────────────────────────────────────────────────────────
from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    BASE_DIR, JOBS_DIR,
    FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    PDF_DPI,
)

# ─── Gemini SDK (google-genai) ────────────────────────────────────────────────
from google import genai as google_genai

gemini_client = google_genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ─── PDF tools ────────────────────────────────────────────────────────────────
from pdf2image import convert_from_path
from PyPDF2 import PdfReader

# ─── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit

os.makedirs(JOBS_DIR, exist_ok=True)

# In-memory job state dictionary  {job_id: {...}}
jobs = {}
jobs_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# JOB STATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def new_job(job_id):
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",          # queued | converting | ocr | gemini | done | error
            "step": "Waiting to start",
            "progress": 0,               # 0-100
            "total_pages": 0,
            "ocr_done": 0,
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
        }


def update_job(job_id, **kwargs):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)


def get_job(job_id):
    with jobs_lock:
        return dict(jobs.get(job_id, {}))


def job_dir(job_id):
    return os.path.join(JOBS_DIR, job_id)


def image_dir(job_id):
    return os.path.join(job_dir(job_id), "images")


def results_dir(job_id):
    return os.path.join(job_dir(job_id), "results")


def failed_dir(job_id):
    return os.path.join(job_dir(job_id), "failed")


# ══════════════════════════════════════════════════════════════════════════════
# HYBRID EXTRACTION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

STRUCTURAL_PATTERNS = re.compile(
    r"(Question Id\s*:|Option Shuffling|Question Type\s*:|"
    r"Display Question Number|Is\nQuestion Mandatory|Option Orientation|"
    r"Correct Marks\s*:|Wrong Marks\s*:|Options\s*:|Sub questions|"
    r"^\s*\d+\.\s*\d+\s*$|^\s*\d+\s*$|"
    r"\d{10,}\.?\s*\d{1,2}$)",
    re.IGNORECASE | re.MULTILINE,
)

def is_structural_only(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    
    # Strip out all known structural patterns and see how much real text is left
    cleaned_text = STRUCTURAL_PATTERNS.sub("", text)
    # Also strip out basic numbers, punctuation, and whitespace to see if real words exist
    real_words = re.sub(r"[\d\W_]+", "", cleaned_text)
    
    # If the remaining actual letters (English/Hindi) are very few, it's a structural page
    if len(real_words) < 50:
        return True
        
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    structural = sum(1 for l in lines if STRUCTURAL_PATTERNS.search(l))
    if len(lines) > 0 and structural / len(lines) > 0.6:
        return True
    return False

def get_tesseract_path():
    path = shutil.which("tesseract")
    if path: return path
    for p in ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"]:
        if os.path.exists(p): return p
    return "tesseract"

def ocr_image_tesseract(image_path: str, lang: str = "eng") -> tuple[bool, str]:
    tess_path = get_tesseract_path()
    try:
        abs_path = str(Path(image_path).absolute())
        cwd = str(Path(image_path).parent.absolute())
        result = subprocess.run(
            [tess_path, abs_path, "stdout", "--oem", "3", "--psm", "6", "-l", lang],
            capture_output=True, cwd=cwd,
        )
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return (True, text) if text else (False, "")
    except Exception as e:
        print(f"Tesseract error: {e}")
        return False, ""

def step_hybrid_extract(job_id, pdf_path):
    update_job(job_id, status="converting", step="Reading PDF…", progress=5)
    
    img_dir = image_dir(job_id)
    res_dir = results_dir(job_id)
    fail_dir = failed_dir(job_id)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(res_dir, exist_ok=True)
    os.makedirs(fail_dir, exist_ok=True)

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    update_job(job_id, total_pages=total_pages)

    good_pages = {}
    structural_pages = []

    # Copy-paste pass
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not is_structural_only(text):
            good_pages[i] = text
        else:
            structural_pages.append(i)
        
        pct = 5 + int(i / total_pages * 10) # 5 -> 15%
        update_job(job_id, step=f"Scanning page {i}/{total_pages}…", progress=pct)

    print(f"[{job_id}] PyPDF2 found {len(good_pages)} good pages, {len(structural_pages)} need OCR.")

    # Selective OCR pass
    ocr_pages = {}
    if structural_pages:
        update_job(job_id, status="ocr", step=f"Starting OCR on {len(structural_pages)} pages…", progress=20)
        
        for idx, pg in enumerate(structural_pages, 1):
            update_job(job_id, step=f"Rendering page {pg} for OCR…", progress=20 + int(idx/len(structural_pages)*5))
            images = convert_from_path(pdf_path, dpi=PDF_DPI, first_page=pg, last_page=pg)
            if not images:
                continue
            
            img_path = os.path.join(img_dir, f"page_{pg:03d}.png")
            images[0].save(img_path, "PNG")

            update_job(job_id, step=f"OCR: page {pg} ({idx}/{len(structural_pages)})", progress=25 + int(idx/len(structural_pages)*45), ocr_done=idx)
            
            success_roman, text_roman = ocr_image_tesseract(img_path, lang="eng")
            success_deva, text_deva = ocr_image_tesseract(img_path, lang="script/Devanagari")
            
            if success_roman or success_deva:
                combined_text = (
                    f"=== Page {pg} (Roman Script) ===\n{text_roman}\n\n"
                    f"=== Page {pg} (Devanagari Script) ===\n{text_deva}\n"
                )
                ocr_pages[pg] = combined_text
            else:
                shutil.copy(img_path, os.path.join(fail_dir, f"page_{pg:03d}.png"))
                ocr_pages[pg] = ""

    # Merge
    update_job(job_id, status="ocr", step="Merging extracted text…", progress=75)
    all_pages = {**good_pages}
    for pg, text in ocr_pages.items():
        cp_text = good_pages.get(pg, "")
        all_pages[pg] = text if len(text) > len(cp_text) else cp_text

    combined_path = os.path.join(res_dir, "all_pages.txt")
    with open(combined_path, "w", encoding="utf-8") as out:
        for pg in sorted(all_pages):
            out.write(f"{'=' * 60}\nPage {pg}\n{'=' * 60}\n{all_pages[pg]}\n\n")

    full_text = "\n\n".join(all_pages[pg] for pg in sorted(all_pages))
    print(f"[{job_id}] ✅ Hybrid extraction complete — {len(all_pages)} pages extracted")
    return full_text


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — GEMINI API
# ══════════════════════════════════════════════════════════════════════════════

GEMINI_PROMPT_TEMPLATE = """Return this exact structure:
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

Raw text to parse:
---
{rawText}
---"""


def extract_metadata_locally(raw_text):
    """Parse common exam header patterns locally from the raw OCR text using regex and NTA heuristics."""
    metadata = {
        "testTitle": None,
        "language": "English",
        "duration": None,
        "totalMarks": None,
        "marksPerQuestion": None,
        "negativeMarking": 0.0,
        "instructions": None
    }
    
    # 1. First try the NTA column-linear-unwrapped layout matching heuristic
    # This matches tabular blocks that unwrap as all label lines followed by all value lines
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    has_labels = False
    for line in lines[:30]:
        if "Question Paper Name" in line or "Subject Name" in line or "Total Marks" in line:
            has_labels = True
            break
            
    if has_labels:
        # Find where values begin (usually after National Testing Agency)
        nta_idx = -1
        for idx, line in enumerate(lines[:40]):
            if "National Testing Agency" in line or "National Testing" in line:
                nta_idx = idx
                break
                
        if nta_idx != -1:
            candidates = []
            for line in lines[nta_idx+1 : nta_idx+15]:
                # Stop if we hit duplicate group or section parameters
                if any(x in line for x in ["Group Number", "Group Id", "Group Maximum", "Section Id", "Section Number"]):
                    break
                candidates.append(line)
            
            # Map candidates linearly to labels
            if len(candidates) >= 5:
                metadata["testTitle"] = candidates[1] if len(candidates) > 1 else candidates[0]
                try:
                    metadata["duration"] = int(candidates[3])
                except:
                    pass
                try:
                    metadata["totalMarks"] = int(candidates[4])
                except:
                    pass

    # 2. If the NTA heuristic did not resolve the fields, use robust regex patterns
    header_area = raw_text[:5000]
    
    if not metadata["duration"]:
        m_dur = re.search(r"Duration\s*(?:\n|:|\s)+\s*(\d+)", header_area, re.IGNORECASE)
        if m_dur:
            metadata["duration"] = int(m_dur.group(1))
        else:
            m_dur2 = re.search(r"Time\s*(?:\n|:|\s)+\s*(\d+)\s*(?:mins|minutes|hours|hrs)", header_area, re.IGNORECASE)
            if m_dur2:
                metadata["duration"] = int(m_dur2.group(1))

    if not metadata["totalMarks"]:
        m_marks = re.search(r"Total Marks\s*(?:\n|:|\s)+\s*(\d+)", header_area, re.IGNORECASE)
        if m_marks:
            metadata["totalMarks"] = int(m_marks.group(1))
        else:
            m_marks2 = re.search(r"Max(?:imum)?\s*Marks\s*(?:\n|:|\s)+\s*(\d+)", header_area, re.IGNORECASE)
            if m_marks2:
                metadata["totalMarks"] = int(m_marks2.group(1))

    if not metadata["testTitle"]:
        m_title = re.search(r"Subject Name\s*:\s*([^\n]+)", header_area, re.IGNORECASE)
        if m_title and m_title.group(1).strip():
            metadata["testTitle"] = m_title.group(1).strip()
        else:
            m_title2 = re.search(r"Question Paper Name\s*:\s*([^\n]+)", header_area, re.IGNORECASE)
            if m_title2 and m_title2.group(1).strip():
                metadata["testTitle"] = m_title2.group(1).strip()
            else:
                m_shift = re.search(r"([^\n]*Shift\s*\d+[^\n]*)", header_area, re.IGNORECASE)
                if m_shift and m_shift.group(1).strip():
                    metadata["testTitle"] = m_shift.group(1).strip()
                else:
                    # Select the first line that is long enough and not a metadata key
                    for line in lines[:15]:
                        if any(x in line.lower() for x in ["question paper", "subject name", "duration", "total marks", "display marks", "creation date", "group id", "group number"]):
                            continue
                        if len(line) > 10 and len(line) < 80:
                            metadata["testTitle"] = line
                            break

    # 3. Parse Marks per Question and Negative Marking
    m_mpq = re.search(r"marks?\s*per\s*question\s*(?:\n|:|\s)+\s*(\d+(?:\.\d+)?)", header_area, re.IGNORECASE)
    if m_mpq:
        metadata["marksPerQuestion"] = float(m_mpq.group(1))
    else:
        m_mpq2 = re.search(r"(\d+(?:\.\d+)?)\s*marks?\s*(?:for each|per question)", header_area, re.IGNORECASE)
        if m_mpq2:
            metadata["marksPerQuestion"] = float(m_mpq2.group(1))

    m_neg = re.search(r"negative\s*mark(?:ing)?\s*(?:\n|:|\s)+\s*(\d+(?:\.\d+)?)", header_area, re.IGNORECASE)
    if m_neg:
        metadata["negativeMarking"] = float(m_neg.group(1))
    else:
        if re.search(r"(?:0\.25|1/4|one-quarter)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 0.25
        elif re.search(r"(?:0\.33|1/3|one-third)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 0.33
        elif re.search(r"(?:0\.5|1/2|half)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 0.5
        elif re.search(r"(?:1\.0|1)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 1.0

    # 4. Parse Instructions
    m_inst = re.search(r"(?:instructions|guidelines)(?:\n|:|\s)+(.*)", header_area, re.IGNORECASE | re.DOTALL)
    if m_inst:
        metadata["instructions"] = m_inst.group(1)[:400].strip()
        
    return metadata


def step_gemini(job_id, raw_text, api_key=None):
    """Send OCR text to Gemini to parse questions, merging with locally extracted metadata."""
    update_job(job_id, status="gemini", step="Calling Gemini AI…", progress=80)

    # 1. Extract metadata locally
    local_meta = extract_metadata_locally(raw_text)
    print(f"[{job_id}] 📝 Locally extracted metadata: {local_meta}")

    # 2. Call Gemini for question structure only
    prompt = GEMINI_PROMPT_TEMPLATE.format(rawText=raw_text)

    if api_key:
        client = google_genai.Client(api_key=api_key)
    else:
        client = gemini_client
        
    if not client:
        raise ValueError("No Gemini API key provided. Set the GEMINI_API_KEY environment variable or provide one in the UI.")

    response_stream = client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    
    raw_response = ""
    current_progress = 80
    chunk_count = 0
    
    for chunk in response_stream:
        if chunk.text:
            raw_response += chunk.text
            chunk_count += 1
            
            # Increment progress by 1 for every 5 chunks, capped at 98%
            if chunk_count % 5 == 0 and current_progress < 98:
                current_progress += 1
                
            update_job(job_id, progress=current_progress, step=f"Receiving Gemini AI response... ({len(raw_response)} bytes)")

    raw_response = raw_response.strip()

    # Strip markdown code fences if Gemini wraps the JSON
    if raw_response.startswith("```"):
        raw_response = re.sub(r"^```[a-z]*\n?", "", raw_response)
        raw_response = re.sub(r"\n?```$", "", raw_response).strip()

    # Save raw response for debugging (especially useful if truncated)
    raw_path = os.path.join(job_dir(job_id), "raw_gemini_response.txt")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_response)

    # Validate JSON
    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as e:
        print(f"[{job_id}] ⚠️ JSON truncated or invalid: {e}. Attempting basic fix...")
        # If truncated, we try a hacky fix by closing arrays/objects
        try:
            fixed_response = raw_response + "\n}\n]\n}\n]\n}"
            parsed = json.loads(fixed_response)
        except:
            # Fallback
            parsed = {"sections": []}

    # 3. Merge locally parsed metadata with Gemini sections/questions
    result_json = {
        "testTitle": local_meta["testTitle"],
        "language": local_meta["language"],
        "duration": local_meta["duration"],
        "totalMarks": local_meta["totalMarks"],
        "marksPerQuestion": local_meta["marksPerQuestion"],
        "negativeMarking": local_meta["negativeMarking"],
        "instructions": local_meta["instructions"],
        "sections": parsed.get("sections", [])
    }

    # Save to disk
    result_path = os.path.join(job_dir(job_id), "result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)

    print(f"[{job_id}] ✅ Gemini & Local Parsing done — result saved to {result_path}")
    return result_json


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(job_id, pdf_path, api_key=None):
    """Full pipeline: Hybrid Extraction → Gemini. Runs in a background thread."""
    try:
        # Step 1 & 2: Hybrid Extract (PyPDF2 + Tesseract)
        raw_text = step_hybrid_extract(job_id, pdf_path)

        # Step 3: Gemini
        result = step_gemini(job_id, raw_text, api_key)

        update_job(
            job_id,
            status="done",
            step="Complete ✓",
            progress=100,
        )

    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[{job_id}] ❌ Pipeline error:\n{tb}")
        update_job(
            job_id,
            status="error",
            step="Pipeline failed",
            error=str(exc),
        )


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


@app.route("/upload", methods=["POST"])
def upload():
    """Accept a PDF upload and start the pipeline."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    job_id = str(uuid.uuid4())
    jdir = job_dir(job_id)
    os.makedirs(jdir, exist_ok=True)

    pdf_path = os.path.join(jdir, "input.pdf")
    f.save(pdf_path)

    new_job(job_id)

    api_key = request.form.get("api_key")

    # Start pipeline in background thread
    t = threading.Thread(target=run_pipeline, args=(job_id, pdf_path, api_key), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    """Return the current state of a job."""
    j = get_job(job_id)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(j)


@app.route("/result/<job_id>")
def result(job_id):
    """Return the final parsed JSON for a completed job."""
    j = get_job(job_id)
    if not j:
        return jsonify({"error": "Job not found"}), 404
    if j["status"] != "done":
        return jsonify({"error": f"Job not complete yet (status: {j['status']})"}), 409

    result_path = os.path.join(job_dir(job_id), "result.json")
    if not os.path.exists(result_path):
        return jsonify({"error": "Result file missing"}), 500

    with open(result_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    return jsonify(data)


# Serve index.html at root
@app.route("/")
def root():
    return send_from_directory(".", "index.html")


@app.route("/test")
@app.route("/test.html")
def test_page():
    """Serve the test simulation interface."""
    return send_from_directory(".", "test.html")


@app.route("/questions.json")
def questions_json():
    """
    Serve questions.json — first tries the root-level file created by
    extract_questions.py, then falls back to the most recently completed job.
    """
    # 1. Root-level questions.json (from extract_questions.py)
    root_json = os.path.join(BASE_DIR, "questions.json")
    if os.path.exists(root_json):
        with open(root_json, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))

    # 2. Most recent completed job result
    with jobs_lock:
        done_jobs = [
            (jid, jdata) for jid, jdata in jobs.items()
            if jdata.get("status") == "done"
        ]

    if not done_jobs:
        return jsonify({"error": "No questions available yet. Upload a PDF first."}), 404

    # Pick the most recently created job
    done_jobs.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    latest_job_id = done_jobs[0][0]
    result_path = os.path.join(job_dir(latest_job_id), "result.json")

    if not os.path.exists(result_path):
        return jsonify({"error": "Result file missing for latest job."}), 500

    with open(result_path, "r", encoding="utf-8") as fp:
        return jsonify(json.load(fp))


@app.route("/reset", methods=["POST"])
def reset():
    """Resets the platform data (clears jobs and deletes cached questions)."""
    # 1. Clear in-memory jobs list
    with jobs_lock:
        jobs.clear()

    # 2. Clear out jobs folder
    if os.path.exists(JOBS_DIR):
        try:
            shutil.rmtree(JOBS_DIR)
            os.makedirs(JOBS_DIR, exist_ok=True)
        except Exception as e:
            print(f"Error clearing JOBS_DIR: {e}")

    # 3. Delete root level questions.json and extracted_raw.txt
    root_json = os.path.join(BASE_DIR, "questions.json")
    raw_txt = os.path.join(BASE_DIR, "extracted_raw.txt")

    for path in [root_json, raw_txt]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                print(f"Error deleting file {path}: {e}")

    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"🚀 ExamDesk Backend starting on http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"   Upload interface : http://{FLASK_HOST}:{FLASK_PORT}/")
    print(f"   Test interface   : http://{FLASK_HOST}:{FLASK_PORT}/test")
    print(f"   Questions JSON   : http://{FLASK_HOST}:{FLASK_PORT}/questions.json")
    print(f"   Jobs directory   : {JOBS_DIR}")
    print(f"   Gemini model     : {GEMINI_MODEL}")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
