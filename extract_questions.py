"""
extract_questions.py
====================
Extracts questions and relevant information from a question paper PDF using a robust,
multi-stage heuristic pipeline with a targeted Gemini LLM fallback.

Strategy:
  1. Text Extraction: PyMuPDF.
  2. Fallback Tesseract OCR: Runs page-by-page when copy-paste yields only metadata.
  3. Text Normalization: ftfy (for mojibake) + Indic NLP (Devanagari normalizer).
  4. Metadata & Section Detection: Heuristic layout and regex scanning.
  5. Segment and Align: Process Roman and Devanagari streams sequentially to handle page splits,
     and group blocks by Question ID.
  6. Deduplication: RapidFuzz to merge or discard repeated questions.
  7. Validation: Pydantic schemas validate structure.
  8. Targeted Fallback: Call Gemini API only on specific Question IDs that fail validation.
"""

import os
import re
import sys
import json
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import fitz  # PyMuPDF
import ftfy
from pdf2image import convert_from_path
from rapidfuzz import fuzz
from pydantic import BaseModel, Field, ValidationError

# ─── Configuration ────────────────────────────────────────────────────────────
PDF_FILE    = "Paper_20260410143543.pdf"   # Input PDF
RAW_OUTPUT  = "extracted_raw.txt"          # All raw OCR/extracted text
JSON_OUTPUT = "questions.json"             # Structured question JSON
IMAGE_DIR   = "extract_pages"             # Temp directory for rendered PNGs
PDF_DPI     = 200                          # DPI for rendering

# Import config constants
try:
    from config import GEMINI_API_KEY, GEMINI_MODEL
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = "gemini-2.5-flash"

# ─── Normalizer Setup ─────────────────────────────────────────────────────────
try:
    from indicnlp.normalize.indic_normalize import DevanagariNormalizer
    INDIC_NORMALIZER = DevanagariNormalizer()
except ImportError:
    INDIC_NORMALIZER = None

def normalize_indic_text(text: str) -> str:
    if not text:
        return ""
    text = ftfy.fix_text(text)
    if INDIC_NORMALIZER and any('\u0900' <= char <= '\u097F' for char in text):
        text = INDIC_NORMALIZER.normalize(text)
    return text.strip()

# ─── Heuristic Regex Patterns ─────────────────────────────────────────────────
STRUCTURAL_PATTERNS = re.compile(
    r"(Question Id\s*:|Option Shuffling|Question Type\s*:|"
    r"Display Question Number|Is\s*Question Mandatory|Option Orientation|"
    r"Correct Marks\s*:|Wrong Marks\s*:|Options\s*:|Sub questions|"
    r"Sub-Section|Question Shuffling Allowed|Is Section Default|"
    r"^\s*\d+\.\s*\d+\s*$|^\s*\d+\s*$|"
    r"\d{10,}\.?\s*\d{1,2}$)",
    re.IGNORECASE | re.MULTILINE,
)

OPTION_PREFIX_PAT = re.compile(
    r"^\s*[\(\[]?\s*([1-4]|A-D|a-d|I-IV|i-iv|®|6|MD|१|२|३|४)\s*[\)\]\.]?\s+(.*)",
    re.IGNORECASE
)

ENGLISH_STOPWORDS = {
    "the", "of", "and", "to", "a", "in", "is", "that", "for", "it", "on", "was",
    "as", "with", "by", "an", "which", "are", "from", "be", "at"
}

# ─── Pydantic Validation Models ───────────────────────────────────────────────
class OptionSchema(BaseModel):
    key: str
    text: str
    textDevanagari: str

class QuestionSchema(BaseModel):
    number: int
    text: str
    textDevanagari: str
    options: List[OptionSchema]
    correctAnswer: Optional[str] = None

class SectionSchema(BaseModel):
    name: str
    questions: List[QuestionSchema]

class ExamSchema(BaseModel):
    testTitle: Optional[str] = None
    language: str = "English"
    duration: Optional[int] = None
    totalMarks: Optional[int] = None
    marksPerQuestion: Optional[float] = None
    negativeMarking: Optional[float] = None
    instructions: Optional[str] = None
    sections: List[SectionSchema]

# ─── Helper: find Tesseract ───────────────────────────────────────────────────
def find_tesseract():
    path = shutil.which("tesseract")
    if path:
        return path
    for p in ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"]:
        if os.path.exists(p):
            return p
    return "tesseract"

TESSERACT = find_tesseract()

# ─── Text Classification Helper ───────────────────────────────────────────────
def is_structural_only(text: str) -> bool:
    if not text or len(text.strip()) < 30:
        return True
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    structural = sum(1 for l in lines if STRUCTURAL_PATTERNS.search(l))
    if len(lines) > 0 and (structural / len(lines)) > 0.6:
        return True
    return False

def contains_devanagari(text: str) -> bool:
    return any('\u0900' <= char <= '\u097F' for char in text)

def count_devanagari(text: str) -> int:
    return sum(1 for char in text if '\u0900' <= char <= '\u097F')

def count_english_stopwords(text: str) -> int:
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return sum(1 for w in words if w in ENGLISH_STOPWORDS)

# ─── OCR Helper ───────────────────────────────────────────────────────────────
def ocr_page_tesseract(image_path: str, lang: str = "eng") -> str:
    abs_path = str(Path(image_path).absolute())
    cwd = str(Path(image_path).parent.absolute())
    result = subprocess.run(
        [TESSERACT, abs_path, "stdout", "--oem", "3", "--psm", "6", "-l", lang],
        capture_output=True,
        cwd=cwd,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()

# ─── Extract Page Text (Heuristic Copy-Paste + OCR Fallback) ──────────────────
def extract_pdf_pages(pdf_path: str, progress_callback=None) -> Dict[int, str]:
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    all_pages = {}
    
    os.makedirs(IMAGE_DIR, exist_ok=True)
    # Clear any old orphaned images to keep folders light
    for f in os.listdir(IMAGE_DIR):
        if f.endswith(".png"):
            try:
                os.remove(os.path.join(IMAGE_DIR, f))
            except:
                pass
    
    for i in range(total_pages):
        pg = i + 1
        page = doc[i]
        text = page.get_text("text") or ""
        
        if progress_callback:
            progress_callback(pg, total_pages, "extracting")
            
        if not is_structural_only(text):
            all_pages[pg] = text
        else:
            images = convert_from_path(pdf_path, dpi=PDF_DPI, first_page=pg, last_page=pg)
            if images:
                img_path = os.path.join(IMAGE_DIR, f"page_{pg:03d}.png")
                images[0].save(img_path, "PNG")
                
                text_deva = ocr_page_tesseract(img_path, lang="script/Devanagari")
                text_eng = ocr_page_tesseract(img_path, lang="eng")
                
                combined_text = (
                    f"=== Page {pg} (Roman Script) ===\n{text_eng}\n\n"
                    f"=== Page {pg} (Devanagari Script) ===\n{text_deva}\n"
                )
                all_pages[pg] = combined_text
                
                if os.path.exists(img_path):
                    os.remove(img_path)
            else:
                all_pages[pg] = text
                
    return all_pages

# ─── Parse Options & Question Bodies from Raw Block ──────────────────────────
def parse_option_prefixes(body_lines: List[str]) -> Tuple[List[str], Dict[str, str]]:
    prefix_map = {
        '1': '1', 'MD': '1', '१': '1', 'A': '1', 'a': '1', 'I': '1', 'i': '1',
        '2': '2', '®': '2', '२': '2', 'B': '2', 'b': '2', 'II': '2', 'ii': '2',
        '3': '3', '6': '3', '३': '3', 'C': '3', 'c': '3', 'III': '3', 'iii': '3',
        '4': '4', '४': '4', 'D': '4', 'd': '4', 'IV': '4', 'iv': '4'
    }
    
    options = {}
    q_lines = []
    
    for line in body_lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        m = OPTION_PREFIX_PAT.match(line_clean)
        if m:
            raw_prefix = m.group(1)
            opt_text = m.group(2).strip()
            std_key = prefix_map.get(raw_prefix)
            if std_key:
                options[std_key] = opt_text
            else:
                q_lines.append(line)
        else:
            q_lines.append(line)
            
    return q_lines, options

def parse_question_content(header: str, content: str, qid: str, pos: int) -> Dict[str, Any]:
    qnum_match = re.search(r"Question Number\s*:\s*(\d+)", header, re.IGNORECASE)
    qnum = int(qnum_match.group(1)) if qnum_match else None
    
    lines = [normalize_indic_text(l) for l in content.split("\n")]
    meta_keywords = ["mandatory", "orientation", "shuffling", "display", "single line", "type", "options:"]
    
    body_lines = []
    in_metadata = True
    for line in lines:
        if not line:
            continue
        if in_metadata:
            if any(k in line.lower() for k in meta_keywords if "options:" not in k):
                continue
            else:
                in_metadata = False
        body_lines.append(line)
        
    body_text = "\n".join(body_lines)
    
    options_match = re.search(r"Options\s*:\s*(.*)", body_text, re.IGNORECASE | re.DOTALL)
    options_map = {}
    if options_match:
        options_part = options_match.group(1).strip()
        for line in options_part.split("\n"):
            line_clean = line.strip()
            opt_id_match = re.match(r"^(\d+)\.?\s+(\d+|\w+)", line_clean)
            if opt_id_match:
                opt_id = opt_id_match.group(1)
                opt_key = opt_id_match.group(2)
                options_map[opt_key] = opt_id
        body_text = body_text[:options_match.start()].strip()
        
    q_lines, parsed_options = parse_option_prefixes(body_text.split("\n"))
    q_text = "\n".join(q_lines).strip()
    
    return {
        "qid": qid,
        "number": qnum,
        "text": q_text,
        "options": parsed_options,
        "options_map": options_map,
        "pos": pos,
        "is_hindi": contains_devanagari(q_text) or any(contains_devanagari(v) for v in parsed_options.values())
    }

def get_blocks_from_stream(stream_text: str) -> List[Dict[str, Any]]:
    header_pattern = re.compile(
        r"(Question Number\s*:\s*\d+\s+Question\s+(?:Id|1d)\s*:\s*(\d+))",
        re.IGNORECASE
    )
    
    page_pattern = re.compile(r"=== Page (\d+)", re.IGNORECASE)
    pages_pos = []
    for m in page_pattern.finditer(stream_text):
        pages_pos.append((m.start(), int(m.group(1))))
        
    def get_page_for_pos(pos):
        current_page = 1
        for start_pos, pg in pages_pos:
            if pos >= start_pos:
                current_page = pg
            else:
                break
        return current_page

    matches = list(header_pattern.finditer(stream_text))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        header = m.group(0)
        qid = m.group(2)
        end = matches[i+1].start() if i + 1 < len(matches) else len(stream_text)
        content = stream_text[m.end():end].strip()
        
        parsed = parse_question_content(header, content, qid, start)
        parsed["page"] = get_page_for_pos(start)
        blocks.append(parsed)
    return blocks

# ─── Exam Metadata Local Parser ────────────────────────────────────────────────
def parse_exam_metadata(raw_text: str) -> Dict[str, Any]:
    metadata = {
        "testTitle": None,
        "language": "English",
        "duration": None,
        "totalMarks": None,
        "marksPerQuestion": None,
        "negativeMarking": 0.0,
        "instructions": None
    }
    
    header_area = raw_text[:8000]
    
    # Heuristics for Title, Duration, Marks
    m_dur = re.search(r"Duration\s*(?:\n|:|\s)+\s*(\d+)", header_area, re.IGNORECASE)
    if m_dur:
        metadata["duration"] = int(m_dur.group(1))
        
    m_marks = re.search(r"Total Marks\s*(?:\n|:|\s)+\s*(\d+)", header_area, re.IGNORECASE)
    if m_marks:
        metadata["totalMarks"] = int(m_marks.group(1))
        
    m_title = re.search(r"Subject Name\s*:\s*([^\n]+)", header_area, re.IGNORECASE)
    if m_title and m_title.group(1).strip():
        metadata["testTitle"] = m_title.group(1).strip()
    else:
        m_qname = re.search(r"Question Paper Name\s*:\s*([^\n]+)", header_area, re.IGNORECASE)
        if m_qname and m_qname.group(1).strip():
            metadata["testTitle"] = m_qname.group(1).strip()
            
    # Heuristics for Marks per Question & Negative Marking
    m_mpq = re.search(r"marks?\s*per\s*question\s*(?:\n|:|\s)+\s*(\d+(?:\.\d+)?)", header_area, re.IGNORECASE)
    if m_mpq:
        metadata["marksPerQuestion"] = float(m_mpq.group(1))
        
    m_neg = re.search(r"negative\s*mark(?:ing)?\s*(?:\n|:|\s)+\s*(\d+(?:\.\d+)?)", header_area, re.IGNORECASE)
    if m_neg:
        metadata["negativeMarking"] = float(m_neg.group(1))
    else:
        if re.search(r"(?:0\.25|1/4|one-quarter)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 0.25
        elif re.search(r"(?:0\.33|1/3|one-third)\s*(?:negative|minus|deduction)", header_area, re.IGNORECASE):
            metadata["negativeMarking"] = 0.33
            
    return metadata

# ─── Gemini LLM Fallback Engine ───────────────────────────────────────────────
GEMINI_FALLBACK_PROMPT = """You are an expert bilingual exam parser.
Your task is to extract the question and options for the given Question ID from the raw text segments provided below.

Rules:
1. Extract both the English (Roman script) and Hindi/Sanskrit (Devanagari script) version of the question.
2. The output MUST be a valid JSON matching this structure:
{{
  "number": {qnum},
  "text": "English question text",
  "textDevanagari": "Hindi question text",
  "options": [
    {{ "key": "1", "text": "English option 1 text", "textDevanagari": "Hindi option 1 text" }},
    {{ "key": "2", "text": "English option 2 text", "textDevanagari": "Hindi option 2 text" }},
    {{ "key": "3", "text": "English option 3 text", "textDevanagari": "Hindi option 3 text" }},
    {{ "key": "4", "text": "English option 4 text", "textDevanagari": "Hindi option 4 text" }}
  ],
  "correctAnswer": "1" | "2" | "3" | "4" | null
}}

Note:
- Completely clean up any OCR errors, garbled text, or noise.
- If there are missing parentheses or characters (e.g., '(1)' read as '(', '(2)' read as '®'), correct them.
- If the question contains match lists or tables, reconstruct them clearly in markdown or text formatting.

Question ID to extract: {qid}
Question Number: {qnum}

Raw Text Segments:
---
{raw_segments}
---
Return ONLY the JSON — no markdown code blocks, no other text."""

def call_gemini_fallback(qid: str, qnum: int, raw_blocks: List[Dict[str, Any]], api_key: str = None) -> Optional[Dict[str, Any]]:
    raw_segments = ""
    for b in raw_blocks:
        raw_segments += f"--- BLOCK ---\n{b['header']}\n{b['content']}\n\n"
        
    prompt = GEMINI_FALLBACK_PROMPT.format(qid=qid, qnum=qnum, raw_segments=raw_segments)
    
    key = api_key or GEMINI_API_KEY
    if not key:
        print(f"   ⚠️ No Gemini API key for fallback, skipping QID {qid}")
        return None
        
    try:
        from google import genai as google_genai
        client = google_genai.Client(api_key=key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw_response = response.text.strip()
        
        raw_response = re.sub(r"^```[a-z]*\n?", "", raw_response)
        raw_response = re.sub(r"\n?```$", "", raw_response).strip()
        
        parsed = json.loads(raw_response)
        QuestionSchema(**parsed)
        return parsed
    except Exception as e:
        print(f"   ❌ Gemini fallback failed for QID {qid}: {e}")
        return None

def run_extraction_pipeline(pdf_path: str, api_key: str = None, progress_callback=None, raw_txt_path: str = RAW_OUTPUT, output_json_path: str = JSON_OUTPUT) -> Dict[str, Any]:
    if progress_callback:
        progress_callback(0, 100, "extracting")
        
    pages = extract_pdf_pages(pdf_path, progress_callback)
    
    combined_raw_list = []
    for pg in sorted(pages):
        combined_raw_list.append(f"{'='*60}\nPage {pg}\n{'='*60}\n" + pages[pg])
    full_raw_text = "\n\n".join(combined_raw_list)
    
    with open(raw_txt_path, "w", encoding="utf-8") as f:
        f.write(full_raw_text)
        
    roman_stream_list = []
    devanagari_stream_list = []
    
    for pg in sorted(pages):
        text = pages[pg]
        r_match = re.search(r"=== Page \d+ \(Roman Script\) ===", text)
        d_match = re.search(r"=== Page \d+ \(Devanagari Script\) ===", text)
        
        if r_match and d_match:
            roman_text = text[r_match.end():d_match.start()].strip()
            deva_text = text[d_match.end():].strip()
            roman_stream_list.append(f"\n=== Page {pg} (Roman) ===\n" + roman_text)
            devanagari_stream_list.append(f"\n=== Page {pg} (Devanagari) ===\n" + deva_text)
        else:
            if contains_devanagari(text):
                devanagari_stream_list.append(f"\n=== Page {pg} ===\n" + text.strip())
            else:
                roman_stream_list.append(f"\n=== Page {pg} ===\n" + text.strip())
                
    roman_stream = "\n".join(roman_stream_list)
    deva_stream = "\n".join(devanagari_stream_list)
    
    exam_meta = parse_exam_metadata(full_raw_text)
    
    section_pattern = re.compile(
        r"([^\n]+)\n(?<!Sub-)Section\s+Id\s*:\s*\n*(\d+)",
        re.IGNORECASE
    )
    
    # Build list of pages and their positions in full_raw_text
    page_pattern = re.compile(r"={60}\nPage (\d+)\n={60}\n|=== Page (\d+)", re.IGNORECASE)
    pages_pos = []
    for m in page_pattern.finditer(full_raw_text):
        pg = int(m.group(1) or m.group(2))
        pages_pos.append((m.start(), pg))
        
    def get_page_for_pos_raw(pos):
        current_page = 1
        for start_pos, pg in pages_pos:
            if pos >= start_pos:
                current_page = pg
            else:
                break
        return current_page

    sections = []
    for m in section_pattern.finditer(full_raw_text):
        name = m.group(1).strip()
        sec_id = m.group(2).strip()
        if re.match(r"^[\d\.\s\u00a0]+$", name) or any(k in name.lower() for k in ["mandatory", "marks", "time", "group", "section"]):
            lines = full_raw_text[:m.start()].split("\n")
            for l in reversed(lines[-5:]):
                l_clean = l.strip()
                if l_clean and not any(k in l_clean.lower() for k in ["mandatory", "marks", "time", "group", "section"]):
                    name = l_clean
                    break
        sections.append({
            "name": name,
            "id": sec_id,
            "page": get_page_for_pos_raw(m.start())
        })
        
    # Deduplicate sections by name
    seen = set()
    unique_sections = []
    for s in sections:
        if s["name"] not in seen:
            seen.add(s["name"])
            unique_sections.append(s)
    sections = unique_sections
        
    if not sections:
        sections = [
            {"name": "General Paper", "id": "1", "page": 1},
            {"name": "Subject Specific", "id": "2", "page": 44}
        ]
        
    eng_blocks = get_blocks_from_stream(roman_stream)
    hin_blocks = get_blocks_from_stream(deva_stream)
    
    header_pattern = re.compile(
        r"(Question Number\s*:\s*\d+\s+Question\s+(?:Id|1d)\s*:\s*(\d+))",
        re.IGNORECASE
    )
    matches = list(header_pattern.finditer(full_raw_text))
    raw_blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        header = m.group(0)
        qid = m.group(2)
        end = matches[i+1].start() if i + 1 < len(matches) else len(full_raw_text)
        content = full_raw_text[m.end():end].strip()
        raw_blocks.append({
            "qid": qid,
            "header": header,
            "content": content,
            "pos": start
        })
        
    # For English stream: keep the block with the most English stopwords
    eng_by_qid = {}
    for b in eng_blocks:
        qid = b["qid"]
        if qid not in eng_by_qid:
            eng_by_qid[qid] = b
        else:
            current_score = count_english_stopwords(eng_by_qid[qid]["text"])
            new_score = count_english_stopwords(b["text"])
            if new_score > current_score:
                eng_by_qid[qid] = b
                
    # For Devanagari stream: keep the block with the most Devanagari characters
    hin_by_qid = {}
    for b in hin_blocks:
        qid = b["qid"]
        if qid not in hin_by_qid:
            hin_by_qid[qid] = b
        else:
            current_score = count_devanagari(hin_by_qid[qid]["text"])
            new_score = count_devanagari(b["text"])
            if new_score > current_score:
                hin_by_qid[qid] = b
    
    all_qids = sorted(list(set(eng_by_qid.keys()) | set(hin_by_qid.keys())), key=lambda q: (eng_by_qid.get(q) or hin_by_qid.get(q))["number"] or 0)
    
    parsed_questions = []
    heuristics_count = 0
    fallback_count = 0
    total_qids = len(all_qids)
    
    for idx, qid in enumerate(all_qids):
        if progress_callback:
            pct = 80 + int((idx / total_qids) * 19)
            progress_callback(pct, 100, f"parsing question {idx+1}/{total_qids}")
            
        eng_b = eng_by_qid.get(qid)
        hin_b = hin_by_qid.get(qid)
        
        is_comp = False
        matching_raw = [b for b in raw_blocks if b["qid"] == qid]
        if matching_raw and any("COMPREHENSION" in b["header"].upper() for b in matching_raw):
            is_comp = True
            
        if is_comp:
            continue
            
        valid = True
        if not eng_b or not hin_b:
            valid = False
        else:
            if len(eng_b["options"]) != 4 or len(hin_b["options"]) != 4:
                valid = False
                
        question_data = None
        if valid:
            try:
                options = []
                for k in ['1', '2', '3', '4']:
                    options.append(OptionSchema(
                        key=k,
                        text=eng_b["options"][k],
                        textDevanagari=hin_b["options"][k]
                    ))
                
                correct_ans = None
                for t in [eng_b["text"], hin_b["text"]]:
                    ans_m = re.search(r"\b(?:Ans|Answer)\s*:\s*([1-4])", t, re.IGNORECASE)
                    if ans_m:
                        correct_ans = ans_m.group(1)
                        break
                
                q_model = QuestionSchema(
                    number=eng_b["number"] or hin_b["number"] or 0,
                    text=eng_b["text"],
                    textDevanagari=hin_b["text"],
                    options=options,
                    correctAnswer=correct_ans
                )
                question_data = q_model.model_dump()
                heuristics_count += 1
            except Exception as e:
                valid = False
                
        if not valid:
            qnum = (eng_b.get("number") if eng_b else None) or (hin_b.get("number") if hin_b else None) or 0
            print(f"   🤖 Falling back to Gemini for QID {qid} (Question Number: {qnum})")
            llm_res = call_gemini_fallback(qid, qnum, matching_raw, api_key)
            if llm_res:
                question_data = llm_res
                fallback_count += 1
            else:
                options = []
                for k in ['1', '2', '3', '4']:
                    opt_eng = eng_b["options"].get(k, "Option text missing") if eng_b else "Option text missing"
                    opt_hin = hin_b["options"].get(k, "Option text missing") if hin_b else "Option text missing"
                    options.append({"key": k, "text": opt_eng, "textDevanagari": opt_hin})
                    
                question_data = {
                    "number": qnum,
                    "text": eng_b["text"] if eng_b else "Question text missing",
                    "textDevanagari": hin_b["text"] if hin_b else "Question text missing",
                    "options": options,
                    "correctAnswer": None
                }
                heuristics_count += 1
                
        if question_data:
            q_page = (eng_b.get("page") if eng_b else None) or (hin_b.get("page") if hin_b else None) or 1
            
            active_section = sections[0]["name"]
            sorted_sections = sorted(sections, key=lambda s: s["page"])
            for sec in sorted_sections:
                if q_page >= sec["page"]:
                    active_section = sec["name"]
                    
            question_data["_section_name"] = active_section
            parsed_questions.append(question_data)
            
    sections_map = {sec["name"]: [] for sec in sections}
    for q in parsed_questions:
        sec_name = q.pop("_section_name")
        sections_map.setdefault(sec_name, []).append(q)
        
    final_sections = []
    for sec_name, q_list in sections_map.items():
        if q_list:
            final_sections.append({
                "name": sec_name,
                "questions": q_list
            })
            
    result = {
        "testTitle": exam_meta["testTitle"],
        "language": exam_meta["language"],
        "duration": exam_meta["duration"],
        "totalMarks": exam_meta["totalMarks"],
        "marksPerQuestion": exam_meta["marksPerQuestion"],
        "negativeMarking": exam_meta["negativeMarking"],
        "instructions": exam_meta["instructions"],
        "sections": final_sections
    }
    
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        
    print(f"\n✅ Pipeline Complete! Extracted {len(parsed_questions)} questions.")
    print(f"   Heuristics parsed : {heuristics_count}")
    print(f"   Gemini Fallback   : {fallback_count}")
    print(f"   Output saved to   : {output_json_path}")
    
    return result

def main():
    if not os.path.exists(PDF_FILE):
        print(f"❌ PDF not found: {PDF_FILE}")
        sys.exit(1)
    run_extraction_pipeline(PDF_FILE)

if __name__ == "__main__":
    main()
