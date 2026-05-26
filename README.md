---
title: ExamDesk
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
app_port: 7860
---

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
- **BYOK (Bring Your Own Key)**: Submit your own Gemini API Key dynamically from the frontend.

---

## 🛠️ Local Setup Guidelines

Follow these steps to run ExamDesk on your own machine. 

### 1. System Dependencies (OCR & PDF Processing)
ExamDesk relies on **Tesseract** (for OCR) and **Poppler** (for PDF rendering). You must install these at the system level.

**For macOS (using Homebrew):**
```bash
brew install tesseract
brew install tesseract-lang
brew install poppler
```

**For Ubuntu/Debian Linux:**
```bash
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin poppler-utils
```

**For Windows:**
- Download and install Tesseract from [UB-Mannheim Tesseract installers](https://github.com/UB-Mannheim/tesseract/wiki).
- Download and extract Poppler for Windows from [oschwartz10612/poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases). Add the `bin/` folder to your system PATH.

### 2. Python Environment Setup
We recommend using a virtual environment.

```bash
# Clone the repository
git clone https://github.com/yourusername/ExamDesk.git
cd ExamDesk

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. API Configuration
While the web app allows you to paste a Gemini API Key on the upload screen, you can also set a default server key. 
In `config.py`, add your API key:
```python
GEMINI_API_KEY = "YOUR_DEFAULT_API_KEY"
```

### 4. Running the App
Start the Flask backend:
```bash
python server.py
```
Open your browser and navigate to:
- **Main Portal**: [http://127.0.0.1:5050/](http://127.0.0.1:5050/)

---

## 🤖 AI Setup Prompt

If you are using an AI Agent (like GitHub Copilot, Gemini IDE, or Cursor) and want it to set up this repository for you, just copy and paste the prompt below into the chat:

> **"I have just cloned the ExamDesk repository. Please help me set up the local environment. I am on [Insert OS: macOS / Ubuntu / Windows]. First, check if Tesseract and Poppler are installed, and install them if they aren't. Then, create a Python virtual environment, install the dependencies from requirements.txt, and finally, start the Flask server by running `python server.py` in the background."**

---

## 📂 Project Structure

- `server.py` — Flask API orchestrating background extraction jobs and static asset hosting.
- `test.html` — Live Testbook-style CBT simulation interface.
- `index.html` — Upload and pipeline tracking portal.
- `extract_questions.py` — Standalone copy-paste and local dual-pass Tesseract OCR pipeline.
- `config.py` — Pipeline configuration constants (DPI, model, directories).
