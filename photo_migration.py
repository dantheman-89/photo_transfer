"""
Photo Migration Script with EXIF "Date Taken" and Duplicate Detection
====================================================================

Migrates photos/videos from raw folders into a processed archive.

Features:
1) Recursive gathering of raw files, with optional skipping of Live Photo MOV clips.
2) Exclude specific file extensions (e.g., .aae) during evaluation.
3) Use EXIF "Date Taken" (DateTimeOriginal) when available for chronological naming,
   else fall back to the earliest of file modified and created timestamps.
4) Single-pass metadata caching for speed.
5) Counter initialization by scanning existing processed files.
6) Duplicate detection using file size filtering + full file hashing.
7) Evaluation → evaluation_log.csv (with convert flag and import control).
8) Processing → ffmpeg for conversions, copy others, rename, move into YYYY folders.
"""

import shutil
import hashlib
from pathlib import Path
from datetime import datetime
import csv
import subprocess
from PIL import Image
import piexif
from pillow_heif import register_heif_opener
from collections import defaultdict
register_heif_opener()  

# ──────── Configuration ─────────
RAW_DIRS               = [r"E:\Photos\Unsorted"]       # Input folders
PROCESSED_DIR          = r"E:\Photos\processed"   # Output base folder
RUN_EVALUATE           = True          # Run evaluation stage
RUN_PROCESS            = True          # Run processing stage
SKIP_LIVE_PHOTO_CLIPS  = True          # Skip .MOV files paired with .HEIC
EXCLUDE_EXTS           = [".aae"]      # File extensions to exclude in evaluation
# ─────────────────────────────────

EVAL_LOG = "evaluation_log.csv"

def calculate_file_hash(file_path):
    """Calculate MD5 hash of entire file for duplicate detection."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        print(f"Error calculating hash for {file_path}: {e}")
        return None

def build_duplicate_index(raw_files, processed_files):
    """
    Build an efficient index for duplicate detection using file size filtering.
    Returns sets of files to exclude from import.
    """
    all_files = raw_files + processed_files
    print(f"Building duplicate index for {len(all_files)} total files ({len(raw_files)} raw + {len(processed_files)} processed)...")
    
    # Step 1: Group by file size only
    size_groups = defaultdict(list)
    
    for i, f in enumerate(all_files, 1):
        if i % 1000 == 0:
            print(f"Grouping progress: {i} / {len(all_files)} files processed")
        
        try:
            size = f.stat().st_size
            size_groups[size].append(f)
        except Exception as e:
            print(f"Error getting file size for {f}: {e}")
    
    # Step 2: Calculate hashes only for groups with multiple files (same size)
    potential_dupes = []
    for group in size_groups.values():
        if len(group) > 1:
            potential_dupes.extend(group)
    
    print(f"Found {len(potential_dupes)} files that need hash comparison (same file size)")
    print(f"Skipping hash calculation for {len(all_files) - len(potential_dupes)} files with unique sizes")
    
    raw_duplicates_to_skip = set()
    
    if potential_dupes:
        print("Calculating hashes for files with matching sizes...")
        hash_groups = defaultdict(list)
        
        # Calculate hashes for potential duplicates
        for i, f in enumerate(potential_dupes, 1):
            if i % 100 == 0:
                print(f"Hash progress: {i} / {len(potential_dupes)} files processed")
            
            file_hash = calculate_file_hash(f)
            if file_hash:
                hash_groups[file_hash].append(f)
        
        # Process each duplicate group
        for file_list in hash_groups.values():
            if len(file_list) > 1:
                # Separate raw and processed files in this group
                raw_in_group = [f for f in file_list if f in raw_files]
                processed_in_group = [f for f in file_list if f in processed_files]
                
                if processed_in_group:
                    # If there are processed files, mark ALL raw files in this group as duplicates
                    raw_duplicates_to_skip.update(raw_in_group)
                else:
                    # Only raw files in group - keep first, mark rest as duplicates
                    raw_duplicates_to_skip.update(raw_in_group[1:])
    
    print(f"Total raw files to skip due to duplicates: {len(raw_duplicates_to_skip)}")
    return raw_duplicates_to_skip

def gather_processed_files(processed_dir):
    """Gather all files from processed directory for duplicate comparison."""
    processed_files = []
    base = Path(processed_dir)
    
    if base.exists():
        for f in base.rglob("*"):
            if f.is_file():
                processed_files.append(f)
    
    print(f"Found {len(processed_files)} files in processed directory")
    return processed_files

def get_exif_date_taken(file_path):
    """
    Attempt to read EXIF DateTimeOriginal (Tag 36867) from image.
    Returns a datetime if successful, else None.
    """
    try:
        img = Image.open(file_path)
        exif_bytes = img.info.get("exif", b"")
        exif_dict = piexif.load(img.info.get("exif", b""))
        date_str = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
        if date_str:
            # decode bytes to string if necessary
            if isinstance(date_str, bytes):
                date_str = date_str.decode("utf-8", errors="ignore")
            # Format: "YYYY:MM:DD HH:MM:SS"
            return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None

def gather_all_files(raw_dirs):
    """
    Recursively collect all files under each raw_dir.
    Applies filters:
      - Skips extensions in EXCLUDE_EXTS.
      - If SKIP_LIVE_PHOTO_CLIPS is True, skips .MOV paired with .HEIC.
    """
    all_files = []
    for d in raw_dirs:
        root = Path(d)
        if not root.exists():
            print(f"[WARN] raw folder not found: {root}")
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            # Exclude unwanted extensions
            if ext in EXCLUDE_EXTS:
                continue
            all_files.append(f)

    # Skip MOV clips of Live Photos if desired
    if SKIP_LIVE_PHOTO_CLIPS:
        heic_stems = {f.stem for f in all_files if f.suffix.lower() == ".heic"}
        all_files = [f for f in all_files 
                     if not (f.suffix.lower() == ".mov" and f.stem in heic_stems)]
    
    # Log total files found
    print(f"Total files found: {len(all_files)}")
    return all_files

def scan_processed_counters(processed_dir):
    """
    Build a mapping {date_string: max_counter} from existing processed files named YYYYMMDD_NNN.ext.
    """
    counters = {}
    base = Path(processed_dir)
    if base.exists():
        for year_folder in base.iterdir():
            if not year_folder.is_dir():
                continue
            for f in year_folder.iterdir():
                stem = f.stem  # e.g. "20230615_042"
                if "_" in stem:
                    date_part, num = stem.split("_", 1)
                    if date_part.isdigit() and num.isdigit():
                        counters[date_part] = max(counters.get(date_part, 0), int(num))
    return counters

def evaluate():
    """
    Evaluation stage with efficient duplicate detection:
     - Gather files from raw and processed directories
     - Single-pass duplicate detection with smart file prioritization
     - Use EXIF Date Taken when available for chronological naming
     - Sort chronologically and assign sequential names
     - Mark duplicates with status="duplicate" and import="False"
     - Output enhanced evaluation_log.csv
    """
    # Gather files
    raw_files = gather_all_files(RAW_DIRS)
    processed_files = gather_processed_files(PROCESSED_DIR)
    
    print(f"\nStarting duplicate detection...")
    print("=" * 50)
    
    # Single efficient duplicate detection pass
    raw_duplicates_to_skip = build_duplicate_index(raw_files, processed_files)
    
    print(f"\nStarting evaluation of {len(raw_files)} raw files...")
    print("Extracting timestamps and metadata...")
    
    file_info = []
    total_files = len(raw_files)
    
    for i, f in enumerate(raw_files, 1):
        # Progress logging every 100 files
        if i % 100 == 0 or i == total_files:
            print(f"Progress: {i} / {total_files} files scanned ({i/total_files*100:.1f}%)")
            
        # First try EXIF Date Taken for images
        dt = None
        if f.suffix.lower() in [".jpg", ".jpeg", ".tiff", ".png", ".heic"]:
            dt = get_exif_date_taken(f)
        # Fallback to earliest of file times
        if not dt:
            timestamps = []
            try:
                timestamps.append(f.stat().st_mtime)
            except:
                pass
            try:
                timestamps.append(f.stat().st_ctime)
            except:
                pass
            if timestamps:
                dt = datetime.fromtimestamp(min(timestamps))
        if not dt:
            continue
        file_info.append((f, dt))

    print(f"Successfully processed {len(file_info)} files with valid timestamps")
    print("Sorting files chronologically...")

    # Sort files by chosen timestamp
    file_info.sort(key=lambda x: x[1])

    print("Assigning target names...")
    
    # Initialize counters for existing processed files
    counters = scan_processed_counters(PROCESSED_DIR)
    evaluated = []

    for f, dt in file_info:
        date_key = dt.strftime("%Y%m%d")
        year     = dt.strftime("%Y")
        
        # Check if this is a duplicate
        is_duplicate = f in raw_duplicates_to_skip
        
        if not is_duplicate:
            # Only increment counter for non-duplicates
            counters[date_key] = counters.get(date_key, 0) + 1
            suffix = f"{counters[date_key]:03}"
            status = "pending"
            import_file = True
        else:
            suffix = "000"  # Placeholder for duplicates
            status = "duplicate"
            import_file = False

        orig_ext = f.suffix.lower()
        if orig_ext == ".heic":
            ext, convert = ".jpg", True
        elif orig_ext == ".mov":
            ext, convert = ".mp4", True
        else:
            ext, convert = orig_ext, False

        target_name = f"{date_key}_{suffix}{ext}"
        evaluated.append({
            "source":      str(f),
            "timestamp":   dt.isoformat(),
            "target_year": year,
            "target_name": target_name,
            "status":      status,
            "convert":     str(convert),
            "import":      str(import_file)
        })

    if evaluated:
        with open(EVAL_LOG, "w", newline="", encoding="utf-8") as csvf:
            writer = csv.DictWriter(csvf, fieldnames=evaluated[0].keys())
            writer.writeheader()
            writer.writerows(evaluated)
        
        import_count = sum(1 for e in evaluated if e["import"] == "True")
        duplicate_count = len(evaluated) - import_count
        
        print(f"\nEvaluation complete:")
        print(f"Total files evaluated: {len(evaluated)}")
        print(f"Files to import: {import_count}")
        print(f"Duplicates skipped: {duplicate_count}")
        print(f"Results saved to: {EVAL_LOG}")
    else:
        print("No files to evaluate.")

def process():
    """
    Processing stage:
     - Read evaluation_log.csv.
     - For each pending entry with import=True, convert or copy and move to processed/YYYY/.
     - Update status in CSV.
    """
    if not Path(EVAL_LOG).exists():
        print("ERROR: Run evaluation first.")
        return

    rows = []
    with open(EVAL_LOG, newline="", encoding="utf-8") as csvf:
        for row in csv.DictReader(csvf):
            rows.append(row)

    base = Path(PROCESSED_DIR)
    base.mkdir(exist_ok=True)
    changed = False

    for row in rows:
        # Only process files marked for import
        if row["status"] != "pending" or row.get("import", "True") != "True":
            continue
            
        src = Path(row["source"])
        year_folder = base / row["target_year"]
        year_folder.mkdir(exist_ok=True)
        target = year_folder / row["target_name"]

        try:
            if row["convert"] == "True":
                subprocess.run(
                    ["ffmpeg", "-i", str(src), str(target)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                shutil.copy2(src, target)
            row["status"] = "done"
            print(f"Processed: {src.name} -> {target.name}")
        except Exception as e:
            row["status"] = f"error: {e}"
            print(f"Error: {src.name} -> {e}")
        changed = True

    if changed:
        with open(EVAL_LOG, "w", newline="", encoding="utf-8") as csvf:
            writer = csv.DictWriter(csvf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print("Processing complete. Log updated.")
    else:
        print("No pending items to process.")

if __name__ == "__main__":
    if RUN_EVALUATE:
        evaluate()
    if RUN_PROCESS:
        process()