import os
import sys
import json
import traceback

sys.path.append("/Users/mohankumar/Desktop/Mocker")
import extract_questions

PDF_FILES = [
    "Paper_20260410143543.pdf",
    "Paper_20251223094413_49729ef2.pdf",
    "Paper_20251223094413_d7711199.pdf"
]

def main():
    print("🧪 Starting test on all three PDFs in the directory...")
    
    for idx, pdf in enumerate(PDF_FILES):
        if not os.path.exists(pdf):
            print(f"⚠️ PDF file not found: {pdf}, skipping...")
            continue
            
        print(f"\n============================================================\n")
        print(f"📄 Processing PDF {idx+1}/{len(PDF_FILES)}: {pdf}")
        print(f"============================================================\n")
        
        raw_txt_path = f"raw_extracted_{idx+1}.txt"
        output_json_path = f"questions_extracted_{idx+1}.json"
        
        try:
            # Run the extraction pipeline
            result = extract_questions.run_extraction_pipeline(
                pdf_path=pdf,
                api_key=None,  # Heuristic run with default fallback warnings
                raw_txt_path=raw_txt_path,
                output_json_path=output_json_path
            )
            
            # Print stats
            sections = result.get("sections", [])
            print(f"✅ Success processing {pdf}!")
            print(f"   Test Title : {result.get('testTitle')}")
            print(f"   Language   : {result.get('language')}")
            print(f"   Duration   : {result.get('duration')} mins")
            print(f"   Sections   : {len(sections)}")
            
            total_qs = 0
            for s_idx, sec in enumerate(sections):
                q_count = len(sec.get("questions", []))
                print(f"     - Section {s_idx+1}: '{sec.get('name')}' -> {q_count} questions")
                total_qs += q_count
            print(f"   Total Questions Parsed: {total_qs}")
            
            # Verify bilingual checks on first question of this PDF
            if sections and sections[0].get("questions"):
                q0 = sections[0].get("questions")[0]
                text = q0.get("text", "")
                text_deva = q0.get("textDevanagari", "")
                print(f"   Bilingual Isolation Check (Q{q0.get('number')}):")
                has_deva = any(0x0900 <= ord(c) <= 0x097F for c in text_deva)
                print(f"     - Has English (Roman): {any(c.isalpha() for c in text)}")
                print(f"     - Has Hindi (Devanagari): {has_deva}")
                
        except Exception as e:
            print(f"❌ Failed processing {pdf}!")
            traceback.print_exc()
            
        # Cleanup intermediate txt file to save disk space
        if os.path.exists(raw_txt_path):
            os.remove(raw_txt_path)
        # Keep questions_extracted_X.json for user review if they want

if __name__ == "__main__":
    main()
