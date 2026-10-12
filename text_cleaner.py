"""
text_cleaner.py — ExamDesk Robust Text Cleaning Pipeline
=========================================================
Removes NTA/Testbook PDF noise from extracted text before it reaches
the question parser or the test simulator.

Confirmed noise categories (empirical analysis of 3 exam PDFs, July 2026):
  1.  NTA/Testbook/National Testing Agency header watermark lines
  2.  Exam metadata lines: Duration, Total Marks, Group Max/Min Duration
  3.  Group Id / Section Id / Sub-Section Id numeric metadata lines
  4.  Lone number lines  (stray page numbers — but NOT valid option values)
  5.  Lone punctuation / bracket-only lines
  6.  Dash / underscore / separator divider lines
  7.  Pipeline internal page-markers:  === Page N (Roman) ===
  8.  OCR letter substitutions: € → C, £ → C, ^ → A  (in non-currency context)
  9.  Unicode ligatures: ﬁ → fi, ﬂ → fl, etc.
 10.  Candidate info fields (roll no, father's name, centre code, etc.)
 11.  Stray empty parentheses lines: ()
 12.  Stray URLs / email addresses
 13.  Non-breaking spaces, zero-width spaces, BOM
 14.  Collapse 3+ blank lines → one blank line
"""

import re

# ─────────────────────────────────────────────────────────────────────────────
# Compiled patterns (module-level for speed)
# ─────────────────────────────────────────────────────────────────────────────

# 1. Pipeline internal page boundary markers
_PAGE_MARKER = re.compile(
    r'===\s*Page\s+\d+\s*(?:\([^)]+\))?\s*===\n?',
    re.IGNORECASE
)

# 2. NTA / Testbook watermark header lines (full-line match)
_HEADER_LINE = re.compile(
    r'(?m)^[^\n]*(?:national\s+testing\s+agency|testbook\.com|testbook|'
    r'test\s+paper|question\s+booklet|booklet\s+code|admit\s+card|'
    r'hall\s+ticket|serial\s+no\.?)[^\n]*$',
    re.IGNORECASE
)

# 3. Exam-level metadata lines
_METADATA_LINE = re.compile(
    r'(?m)^[^\n]*(?:duration\s*:|total\s+marks\s*:|maximum\s+marks\s*:|'
    r'group\s+maximum\s+duration|group\s+minimum\s+duration|'
    r'time\s+allowed\s*:|total\s+time\s*:|total\s+questions\s*:|'
    r'time\s+limit\s*:)[^\n]*$',
    re.IGNORECASE
)

# 4. Group / Section / Sub-Section / Question ID label lines
_ID_LABEL_LINE = re.compile(
    r'(?m)^\s*(?:group\s+id|section\s+id|sub.section\s+id|question\s+id)'
    r'\s*:?\s*\d*\s*$',
    re.IGNORECASE
)
# Standalone 6+-digit numbers (ID values that appear on their own line)
_BARE_ID_NUMBER = re.compile(r'(?m)^\s*\d{6,}\s*$')

# 5. Candidate information fields
_CANDIDATE_INFO = re.compile(
    r'(?m)^[^\n]*(?:candidate.?s?\s+name|roll\s+no\.?|roll\s+number|'
    r'registration\s+number|father.?s?\s+name|mother.?s?\s+name|'
    r'date\s+of\s+birth|centre\s+name|centre\s+code|invigilator|'
    r'space\s+for\s+rough|rough\s+work|do\s+not\s+open|'
    r'read\s+carefully\s+the)[^\n]*$',
    re.IGNORECASE
)

# 6. Separator / divider lines (only dashes, underscores, equals, pipes)
_SEPARATOR_LINE = re.compile(r'(?m)^[\s\-_=|~*#]{4,}$')

# 7. Lone punctuation / bracket-only lines  (≤15 chars, no alphanumeric)
_GARBAGE_LINE = re.compile(r'(?m)^[\s.,:;!?()\[\]{}<>@#&%^*+=~|\\\/\'"]{1,15}$')

# 8. Empty parenthesis lines
_EMPTY_PAREN_LINE = re.compile(r'(?m)^\s*\([\s]*\)\s*$')

# 9. Lone number-only lines  (1–4 digit standalone lines that are NOT inside
#    option text — only applied to raw page text, not parsed question fields)
_LONE_NUMBER_LINE = re.compile(r'(?m)^\s*\d{1,4}\s*$')

# 10. URLs / emails
_URL = re.compile(r'https?://\S+|www\.\S+|\S+@\S+\.\w{2,}')

# 11. OCR symbol → letter substitutions
#     Handles: € → C, £ → C, ^ → A
#     BUT preserves symbols when followed by a digit (currency context: € 756)
_OCR_SYMBOL_MAP = {'€': 'C', '£': 'C', '^': 'A', '¢': 'c', '¥': 'Y'}
_OCR_SYMBOL_PAT = re.compile(
    r'(?<![A-Za-z\d])'           # not preceded by letter/digit
    r'([€£¥¢\^])'               # the substituted symbol
    r'(?!\s*\d)'                  # not followed by whitespace+digit (currency)
)

# Standalone '8' → 'B' ONLY in Devanagari-heavy context or list-item context
# (e.g. "स्टोर 8 और" → "स्टोर B और")  — conservative: only when surrounded by Devanagari
_DEVANAGARI_RANGE = re.compile(r'[\u0900-\u097F]')
_8_AS_B_PAT = re.compile(r'(?<=[\u0900-\u097F\s])8(?=[\u0900-\u097F\s])')

# Garbled OCR tail fragments at end of question — patterns like '(Qj #C,4,C¢, D8'
# These are corrupted option-order strings from NTA PDFs
_GARBLED_TAIL = re.compile(
    r'\n?\s*\(Qj\s*[^\)]{0,40}\)?'       # (Qj ...) fragments
    r'|\n?\s*(?:[A-Z][a-z]?\s*[,#]\s*){2,}[A-Z][a-z]?\s*\d?'  # stray letter sequences
    r'|\n?\s*\([A-Z0-9,\s#¢€£]{3,25}\)\s*$',  # garbage in parens at end of text
    re.MULTILINE
)

# 12. Unicode ligatures and special characters
_LIGATURE_FIX = [
    (re.compile(r'\ufb01'), 'fi'),     # ﬁ
    (re.compile(r'\ufb02'), 'fl'),     # ﬂ
    (re.compile(r'\ufb00'), 'ff'),     # ﬀ
    (re.compile(r'\ufb03'), 'ffi'),    # ﬃ
    (re.compile(r'\ufb04'), 'ffl'),    # ﬄ
    (re.compile(r'[\u2018\u2019]'), "'"),   # curly single quotes → straight
    (re.compile(r'[\u201c\u201d]'), '"'),   # curly double quotes → straight
    (re.compile(r'\u2014'), ' - '),    # em-dash
    (re.compile(r'\u2013'), ' - '),    # en-dash
    (re.compile(r'\xa0'),  ' '),       # non-breaking space
    (re.compile(r'\u200b'), ''),       # zero-width space
    (re.compile(r'\ufeff'), ''),       # BOM
]

# 13. Stray unicode symbols / decorative chars (NOT currency)
_JUNK_SYMBOLS = re.compile(
    r'[®©™°§¶†‡◦·∙◉○●■□▪▫►▶▷→←↑↓↔↕⇒⇐⇑⇓]'
)

# 14. Excess blank lines
_EXCESS_BLANKS = re.compile(r'\n{3,}')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fix_ocr_symbols(text: str) -> str:
    """Replace OCR-substituted symbols with the correct letters."""
    def _repl(m):
        return _OCR_SYMBOL_MAP.get(m.group(1), m.group(1))
    text = _OCR_SYMBOL_PAT.sub(_repl, text)
    # Fix '8' → 'B' when surrounded by Devanagari text
    if _DEVANAGARI_RANGE.search(text):
        text = _8_AS_B_PAT.sub('B', text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def clean_extracted_text(text: str) -> str:
    """
    Full cleaning of raw text extracted from a PDF page (pre-parsing).
    Strips all structural / metadata noise. Safe to call on multi-page text.

    Args:
        text: Raw text from PyMuPDF get_text() or Tesseract OCR output.

    Returns:
        Cleaned text ready for the question parser.
    """
    if not text:
        return text

    # Step 1: Unicode normalisation (ligatures, quotes, spaces)
    for pat, replacement in _LIGATURE_FIX:
        text = pat.sub(replacement, text)

    # Step 2: Remove internal pipeline page markers (=== Page N ===)
    text = _PAGE_MARKER.sub('\n', text)

    # Step 3: Remove NTA / Testbook header lines
    text = _HEADER_LINE.sub('', text)

    # Step 4: Remove exam metadata lines
    text = _METADATA_LINE.sub('', text)

    # Step 5: Remove ID label and bare 6-digit ID value lines
    text = _ID_LABEL_LINE.sub('', text)
    text = _BARE_ID_NUMBER.sub('', text)

    # Step 6: Remove candidate info fields
    text = _CANDIDATE_INFO.sub('', text)

    # Step 7: Remove URLs
    text = _URL.sub('', text)

    # Step 8: Fix OCR symbol substitutions (€ → C, ^ → A, etc.)
    text = _fix_ocr_symbols(text)

    # Step 9: Remove junk decoration symbols
    text = _JUNK_SYMBOLS.sub('', text)

    # Step 10: Remove separator lines
    text = _SEPARATOR_LINE.sub('', text)

    # Step 11: Remove lone punctuation / bracket-only lines
    text = _GARBAGE_LINE.sub('', text)

    # Step 12: Remove empty parenthesis lines
    text = _EMPTY_PAREN_LINE.sub('', text)

    # Step 13: Remove lone number-only lines
    #          (stray page numbers / watermark numbers — NOT option values)
    text = _LONE_NUMBER_LINE.sub('', text)

    # Step 14: Normalise whitespace
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = _EXCESS_BLANKS.sub('\n\n', text)
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    text = text.strip()

    return text


def clean_question_text(text: str) -> str:
    """
    Lighter cleaning for already-parsed question / option text fields.
    Fixes OCR substitutions and unicode issues only.
    Does NOT strip standalone numbers (they are valid option values).

    Args:
        text: Question text or option text from questions.json

    Returns:
        Cleaned text.
    """
    if not text:
        return text

    # Unicode normalisation
    for pat, replacement in _LIGATURE_FIX:
        text = pat.sub(replacement, text)

    # Remove pipeline page markers that leaked into question text
    text = _PAGE_MARKER.sub(' ', text)

    # Fix OCR symbol substitutions
    text = _fix_ocr_symbols(text)

    # Remove junk symbols
    text = _JUNK_SYMBOLS.sub('', text)

    # Remove garbled OCR tail fragments (e.g. (Qj #C,4,C¢, D8))
    text = _GARBLED_TAIL.sub('', text)

    # Normalise whitespace
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def clean_questions_dict(data: dict) -> dict:
    """
    Apply clean_question_text to every text field in a questions.json dict.
    Operates in-place (mutates the dict) and returns it.

    Args:
        data: Parsed questions.json dict with 'sections' → 'questions' structure.

    Returns:
        The same dict with all text fields cleaned.
    """
    for section in data.get('sections', []):
        for q in section.get('questions', []):
            for field in ('text', 'textDevanagari'):
                if q.get(field):
                    q[field] = clean_question_text(q[field])
            for opt in q.get('options', []):
                for field in ('text', 'textDevanagari'):
                    if opt.get(field):
                        opt[field] = clean_question_text(opt[field])
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Standalone CLI demo / test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, json

    if len(sys.argv) > 1 and sys.argv[1].endswith('.json'):
        # Clean a questions.json file
        with open(sys.argv[1], encoding='utf-8') as f:
            data = json.load(f)
        cleaned = clean_questions_dict(data)
        out_path = sys.argv[1].replace('.json', '_cleaned.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        print(f"✓ Cleaned JSON written to: {out_path}")
    else:
        # Demo with synthetic noisy text matching real PDF patterns
        sample = """National Testing Agency
Group Id :
533072130
Section Id :
533072267
Duration :
180
Total Marks :
300
Group Maximum Duration :
180
Group Minimum Duration :
0

=== Page 3 (Roman) ===
Question Number : 1  Question Id : 1234567

What was the total income earned by Store €?
() 5:19
----
(1) Rs. 2050
(2) Rs. 1545
(3) Rs. 1425
(4) Rs. 1650

=== Page 5 (Devanagari) ===
स्टोर € द्वारा अर्जित कुल आय कितनी थी?
केवल ^, 8 और €

A man purchases a shirt for € 756 and a trouser.

Question Number : 2  Question Id : 1234568

Which of the following is correct?
"""
        print("=" * 60)
        print("ORIGINAL TEXT")
        print("=" * 60)
        print(sample)
        print("=" * 60)
        print("CLEANED TEXT")
        print("=" * 60)
        print(clean_extracted_text(sample))
