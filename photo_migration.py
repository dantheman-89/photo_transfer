"""
Photo Migration Script with EXIF "Date Taken"
=============================================

Migrates photos/videos from raw folders into a processed archive.

Features:
1) Recursive gathering of raw files, with optional skipping of Live Photo MOV clips.
2) Exclude specific file extensions (e.g., .aae) during evaluation.
3) Use EXIF "Date Taken" (DateTimeOriginal) when available for chronological naming,
   else fall back to the earliest of file modified and created timestamps.
4) Single-pass metadata caching for speed.
5) Counter initialization by scanning existing processed files.
6) Evaluation → evaluation_log.csv (with convert flag).
7) Processing → ffmpeg for conversions, copy others, rename, move into YYYY folders.
"""

import shutil
from pathlib import Path
from datetime import datetime
import csv
import subprocess
from PIL import Image
import piexif
from pillow_heif import register_heif_opener
register_heif_opener()  

# ──────── Configuration ─────────
RAW_DIRS               = ["raw"]       # Input folders
PROCESSED_DIR          = "processed"   # Output base folder
RUN_EVALUATE           = True          # Run evaluation stage
RUN_PROCESS            = True          # Run processing stage
SKIP_LIVE_PHOTO_CLIPS  = True          # Skip .MOV files paired with .HEIC
EXCLUDE_EXTS           = [".aae"]      # File extensions to exclude in evaluation
# ─────────────────────────────────

EVAL_LOG = "evaluation_log.csv"

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
    Evaluation stage:
     - Gather files, compute the best timestamp:
       EXIF Date Taken if available; otherwise earliest of
       file modified and created times.
     - Sort chronologically.
     - Assign zero-padded suffix per date using counters.
     - Tag convert=True for HEIC/MOV, False otherwise.
     - Output evaluation_log.csv.
    """
    files = gather_all_files(RAW_DIRS)
    file_info = []
    for f in files:
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

    # Sort files by chosen timestamp
    file_info.sort(key=lambda x: x[1])

    # Initialize counters for existing processed files
    counters = scan_processed_counters(PROCESSED_DIR)
    evaluated = []

    for f, dt in file_info:
        date_key = dt.strftime("%Y%m%d")
        year     = dt.strftime("%Y")
        # increment the counter for this date
        counters[date_key] = counters.get(date_key, 0) + 1
        suffix = f"{counters[date_key]:03}"

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
            "status":      "pending",
            "convert":     str(convert)
        })

    if evaluated:
        with open(EVAL_LOG, "w", newline="", encoding="utf-8") as csvf:
            writer = csv.DictWriter(csvf, fieldnames=evaluated[0].keys())
            writer.writeheader()
            writer.writerows(evaluated)
        print(f"Evaluation complete: {len(evaluated)} entries -> {EVAL_LOG}")
    else:
        print("No files to evaluate.")

def process():
    """
    Processing stage:
     - Read evaluation_log.csv.
     - For each pending entry, convert or copy and move to processed/YYYY/.
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
        if row["status"] != "pending":
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
        print("No pending items processed.")

if __name__ == "__main__":
    if RUN_EVALUATE:
        evaluate()
    if RUN_PROCESS:
        process()