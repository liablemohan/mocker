#!/usr/bin/env python3
import os
import shutil
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_DIR = os.path.join(BASE_DIR, "extract_pages")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")

def clean_temp_images():
    print("🧹 Cleaning temporary page images...")
    cleaned = 0
    if os.path.exists(IMAGE_DIR):
        for f in os.listdir(IMAGE_DIR):
            if f.endswith((".png", ".jpg", ".jpeg")):
                try:
                    os.remove(os.path.join(IMAGE_DIR, f))
                    cleaned += 1
                except Exception as e:
                    print(f"   ⚠️ Could not delete {f}: {e}")
    print(f"   ✅ Cleaned {cleaned} temporary image files.")

def prune_heavy_pdfs():
    print("🧹 Pruning heavy source PDFs in job folders...")
    cleaned = 0
    if os.path.exists(JOBS_DIR):
        for job_id in os.listdir(JOBS_DIR):
            job_path = os.path.join(JOBS_DIR, job_id)
            if os.path.isdir(job_path):
                pdf_path = os.path.join(job_path, "input.pdf")
                if os.path.exists(pdf_path):
                    try:
                        # Only delete if a result.json already exists (meaning pipeline completed)
                        result_path = os.path.join(job_path, "result.json")
                        if os.path.exists(result_path):
                            os.remove(pdf_path)
                            cleaned += 1
                    except Exception as e:
                        print(f"   ⚠️ Could not delete source PDF in {job_id}: {e}")
    print(f"   ✅ Cleaned {cleaned} source PDF files.")

def prune_old_jobs(keep_count=5):
    print(f"🧹 Pruning older jobs (keeping the latest {keep_count} jobs)...")
    if not os.path.exists(JOBS_DIR):
        return
        
    job_dirs = []
    for entry in os.listdir(JOBS_DIR):
        full_path = os.path.join(JOBS_DIR, entry)
        if os.path.isdir(full_path) and not entry.startswith('.'):
            job_dirs.append((full_path, os.path.getmtime(full_path)))
            
    # Sort by modification time (newest first)
    job_dirs.sort(key=lambda x: x[1], reverse=True)
    
    if len(job_dirs) > keep_count:
        to_delete = job_dirs[keep_count:]
        for path, _ in to_delete:
            try:
                shutil.rmtree(path)
                print(f"   🗑️ Removed old job folder: {os.path.basename(path)}")
            except Exception as e:
                print(f"   ⚠️ Could not delete folder {path}: {e}")
    print("   ✅ Job folders pruning complete.")

def clean_root_test_files():
    print("🧹 Cleaning local test output files in root directory...")
    cleaned = 0
    root_files = [
        "extracted_raw.txt",
        "questions_extracted_1.json",
        "questions_extracted_2.json",
        "questions_extracted_3.json"
    ]
    for filename in root_files:
        path = os.path.join(BASE_DIR, filename)
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"   🗑️ Removed test file: {filename}")
                cleaned += 1
            except Exception as e:
                print(f"   ⚠️ Could not delete {filename}: {e}")
    print(f"   ✅ Cleaned {cleaned} test files.")

if __name__ == "__main__":
    print("🚀 Starting Mocker Workspace Clean-up Utility...")
    print("====================================================")
    clean_temp_images()
    print("----------------------------------------------------")
    prune_heavy_pdfs()
    print("----------------------------------------------------")
    prune_old_jobs()
    print("----------------------------------------------------")
    clean_root_test_files()
    print("====================================================")
    print("🎉 Cleanup completed successfully!")
