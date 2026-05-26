# ExamDesk — Premium Bilingual CBT Simulator & Extraction Pipeline

ExamDesk is a high-fidelity Computer Based Test (CBT) practice platform designed to match the user interface and functionality of **Testbook.com**. It is powered by a high-accuracy, hybrid question extraction pipeline that performs **dual-pass OCR (Roman + Devanagari Tesseract)** and utilizes the **Gemini API** to align and format bilingual exam questions.

---

## ✨ Features

- **Testbook-Style Live Simulator**: Dual-pane layout, 2x2 exam stats, interactive Question palette with legend, responsive option selectors, dynamic timer, and animated submit modal.
- **Dual-Pass OCR Pipeline**:
  - Automatically copy-pastes clean embedded PDF text.
  - Falls back to local Tesseract OCR page-by-page when copy-paste yields only metadata.
  - Runs Tesseract **twice** (Pass 1 with `-l eng` for English, Pass 2 with `-l script/Devanagari` for Sanskrit/Hindi) to guarantee pristine text rendering.
- **Real-Time Language Switcher**: Pill toggle `[ English | हिंदी ]` to instantly switch the active question and options from English to Hindi in real-time.
- **Deep Analytics Dashboard**: Dynamic score progress ring, section-wise progress tracking, and stacked bilingual answer review list.

---

## 🛠️ Local Installation & Setup

### 1. Prerequisites (macOS)
ExamDesk requires **Tesseract** and **Poppler** system libraries:

```bash
# Install Tesseract OCR and the Devanagari script pack
brew install tesseract
brew install tesseract-lang

# Install Poppler (for PDF-to-image conversion)
brew install poppler
```

### 2. Python Dependencies
Install the required packages listed in `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 3. Set up Gemini API Key
Configure your Gemini API key in [config.py](file:///Users/mohankumar/Desktop/Mocker/config.py):
```python
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
```

---

## 🚀 Running Locally

### Start the Flask Server
Run the backend web app:
```bash
python server.py
```

Open the following URLs in your browser:
- **Test Simulator Interface**: [http://127.0.0.1:5050/test](http://127.0.0.1:5050/test)
- **PDF Upload Dashboard**: [http://127.0.0.1:5050/](http://127.0.0.1:5050/)

---

## 📂 Project Structure

- `server.py` — Flask API orchestrating background extraction jobs and static asset hosting.
- `test.html` — Live Testbook-style CBT simulation interface.
- `index.html` — Upload and pipeline tracking portal.
- `extract_questions.py` — Standalone copy-paste and local dual-pass Tesseract OCR pipeline.
- `config.py` — Pipeline configuration constants (DPI, model, directories).
