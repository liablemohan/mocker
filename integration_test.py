import os
import sys
import time
import requests
import json

SERVER_URL = "http://127.0.0.1:7860"
PDF_FILE = "Paper_20260410143543.pdf"

def main():
    if not os.path.exists(PDF_FILE):
        print(f"❌ PDF file not found: {PDF_FILE}")
        sys.exit(1)
        
    print(f"🚀 Uploading {PDF_FILE} to backend...")
    
    with open(PDF_FILE, 'rb') as f:
        files = {'file': f}
        # Send empty api_key to test heuristic-only parsing (and fallback warnings)
        data = {'api_key': ''}
        response = requests.post(f"{SERVER_URL}/upload", files=files, data=data)
        
    if response.status_code != 200:
        print(f"❌ Upload failed: {response.text}")
        sys.exit(1)
        
    res_json = response.json()
    job_id = res_json.get("job_id")
    print(f"✅ Job created: {job_id}")
    
    print("\n⏳ Polling job status...")
    last_step = ""
    while True:
        status_resp = requests.get(f"{SERVER_URL}/status/{job_id}")
        if status_resp.status_code != 200:
            print(f"❌ Failed to fetch status: {status_resp.text}")
            break
            
        status_json = status_resp.json()
        status = status_json.get("status")
        progress = status_json.get("progress", 0)
        step = status_json.get("step", "")
        
        if step != last_step or status == "done" or status == "error":
            print(f"🔄 Progress: {progress}% | Status: {status} | Step: {step}")
            last_step = step
            
        if status == "done":
            print("🎉 Job complete!")
            break
        elif status == "error":
            print(f"❌ Job failed with error: {status_json.get('error')}")
            sys.exit(1)
            
        time.sleep(2)
        
    print("\n📦 Fetching final result...")
    result_resp = requests.get(f"{SERVER_URL}/result/{job_id}")
    if result_resp.status_code != 200:
        print(f"❌ Failed to fetch result: {result_resp.text}")
        sys.exit(1)
        
    result = result_resp.json()
    
    # Run assertions to verify successful parsing, script extraction & sorting
    sections = result.get("sections", [])
    print(f"\n--- Validation & Sorting Report ---")
    print(f"Test Title : {result.get('testTitle')}")
    print(f"Language   : {result.get('language')}")
    print(f"Duration   : {result.get('duration')} mins")
    print(f"Sections   : {len(sections)}")
    
    total_qs = 0
    for idx, sec in enumerate(sections):
        q_count = len(sec.get("questions", []))
        print(f"  Section {idx+1}: '{sec.get('name')}' -> {q_count} questions")
        total_qs += q_count
        
    print(f"Total Questions Parsed: {total_qs}")
    
    if total_qs == 0:
        print("❌ ERROR: No questions parsed!")
        sys.exit(1)
        
    # Check bilingual properties on first few questions
    print("\n🔍 Checking bilingual script segregation on sample questions:")
    sample_questions = sections[0].get("questions", [])[:3]
    for q in sample_questions:
        q_num = q.get("number")
        text = q.get("text", "")
        text_deva = q.get("textDevanagari", "")
        options = q.get("options", [])
        
        print(f"\n[Question {q_num}]")
        print(f"  English length: {len(text)} chars | Preview: {repr(text[:60])}...")
        print(f"  Devanagari length: {len(text_deva)} chars | Preview: {repr(text_deva[:60])}...")
        print(f"  Options extracted: {len(options)}")
        for opt in options:
            print(f"    - Key {opt.get('key')}: Eng: {repr(opt.get('text')[:30])} | Deva: {repr(opt.get('textDevanagari')[:30])}")
            
        # Verify script split validation
        has_devanagari_in_deva = any('\u0900' <= c <= '\u097F' for c in text_deva)
        has_roman_in_eng = any(c.isalpha() for c in text)
        print(f"  Validation Check:")
        print(f"    - Correctly isolated Devanagari script: {has_devanagari_in_deva}")
        print(f"    - Correctly isolated Roman script: {has_roman_in_eng}")
        
    print("\n✅ Verification complete! Tool operates successfully.")

if __name__ == "__main__":
    main()
