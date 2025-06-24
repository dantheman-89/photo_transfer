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
import ffmpeg
import numpy as np
from pathlib import Path
from datetime import datetime
import csv
from PIL import Image, ImageOps
import piexif
from pillow_heif import register_heif_opener
from collections import defaultdict
import imagehash
import multiprocessing
from tqdm import tqdm
register_heif_opener()  

#==================================================================================
# CONFIGURATION
#==================================================================================

RAW_DIRS               = [r"raw"]        # Input folders
PROCESSED_DIR          = r"processed"   # Output base folder
DUPLICATES_DIR         = r"duplicate"       # Folder to move duplicates to
RUN_EVALUATE           = True          # Run evaluation stage
RUN_MOVE_DUPLICATES    = True        # Run duplicate moving stage
RUN_PROCESS            = False          # Run processing stage
SKIP_LIVE_PHOTO_CLIPS  = True          # Skip .MOV files paired with .HEIC
EXCLUDE_EXTS           = [".aae", ".nomedia"]      # File extensions to exclude in evaluation

EVAL_LOG = "evaluation_log.csv"

#==================================================================================
# DUPLICATE DETECTION
#==================================================================================

def find_duplicates(raw_files, processed_files, metadata_cache):
    """
    Finds all duplicates using a cached metadata strategy for efficiency.
    """
    all_files = raw_files + processed_files
    print(f"-> Analyzing {len(all_files)} total files for duplicates...")
    
    # Step 1: Find exact duplicates using hash comparison.
    exact_duplicates = find_exact_duplicates(all_files, raw_files, metadata_cache)
    
    # Step 2: Find WhatsApp compressed versions, excluding files already marked as exact duplicates.
    compression_duplicates = find_whatsapp_compressed_versions(raw_files, metadata_cache, exact_duplicates)
    
    # Combine all duplicates
    all_duplicates = exact_duplicates | compression_duplicates
    
    print("\n- Duplicate Detection Complete:")
    print(f"  - Found {len(exact_duplicates)} exact duplicates.")
    print(f"  - Found {len(compression_duplicates)} WhatsApp compressed duplicates.")
    print(f"  - Total unique duplicates to skip: {len(all_duplicates)}")
    
    return all_duplicates


#========== Find Redundant WhatsApp photos ============

def _get_image_features(file_path):
    """Extracts visual features from an image for fast comparison."""
    try:
        img = Image.open(file_path)
        
        # --- FIX FOR ROTATION ---
        # Auto-rotate the image based on its EXIF orientation tag before hashing.
        # This corrects for images that are stored sideways but meant to be viewed upright.
        # It solves cases where one image has the orientation tag and the other has it "baked in".
        img = ImageOps.exif_transpose(img)
        
        # For mean and histogram, use a standardized thumbnail for speed.
        thumb = img.convert('RGB').resize((64, 64), Image.Resampling.BILINEAR)
        arr = np.array(thumb)

        # Calculate both pHash and dHash for a more robust comparison.
        phash = imagehash.phash(thumb)
        
        mean = np.mean(arr, axis=(0, 1))
        hist = np.histogram(arr, bins=64, range=(0, 256))[0]
        
        # Normalize histogram
        if np.sum(hist) > 0:
            hist = hist / np.sum(hist)
        
        # Return features, adding dhash
        return {'mean': mean, 'hist': hist, 'phash': phash, 'path': file_path}
    except Exception:
        # Printing from worker processes can be messy, so we just return None on error.
        return None

def _compare_image_features(feat1, feat2):
    """Compares two sets of pre-calculated image features using a hybrid hash check."""
    # Calculate distances for both perceptual and difference hashes.
    phash_distance = feat1['phash'] - feat2['phash']
    
    hist_correlation = np.corrcoef(feat1['hist'], feat2['hist'])[0, 1]
    
    # Condition 1: pHash is very close. This is the primary check.
    is_similar = phash_distance <= 3

    # mean_diff = np.mean(np.abs(feat1['mean'] - feat2['mean']))
    # print(f"  Similarity match: {feat1['path'].name} vs {feat2['path'].name}")
    # print(f"    Mean diff: {mean_diff:.2f}, pHash dist: {phash_distance},  Hist corr: {hist_correlation:.3f}")
    
    return is_similar


def find_whatsapp_compressed_versions(raw_files, metadata_cache, existing_duplicates):
    """
    Find WhatsApp compressed versions using pre-fetched metadata and cached visual features.
    1. WhatsApp photos: < 500KB, Normal photos: > 1MB.
    2. Normal photos must be taken within 10 days BEFORE WhatsApp photo.
    3. Visual similarity check.
    """
    print("\n  > Phase 2: Finding WhatsApp compressed versions...")
    compression_duplicates = set()
    
    # Step 1: Get metadata from cache and separate image files by size category.
    whatsapp_candidates = []
    normal_photos = []
    all_image_meta = {p: m for p, m in metadata_cache.items() if p.suffix.lower() in ['.jpg', '.jpeg', '.png', '.heic']}

    for f, meta in all_image_meta.items():
        size_kb = meta['size'] / 1024
        if size_kb < 500:
            whatsapp_candidates.append(meta)
        elif size_kb > 1024:
            normal_photos.append(meta)
            
    print(f"    - Found {len(whatsapp_candidates)} potential WhatsApp candidates (<500KB) and {len(normal_photos)} potential originals (>1MB).")
    
    if not whatsapp_candidates or not normal_photos:
        print("    - No candidates for compressed duplicate check.")
        return compression_duplicates

    # Step 2: Pre-filter normal_photos to only include those with a potential match.
    # This avoids feature extraction for normal photos that can't possibly have a duplicate.
    whatsapp_dates = {meta['dt'] for meta in whatsapp_candidates if meta['dt']}
    filtered_normal_photos = []
    for normal_meta in normal_photos:
        normal_date = normal_meta.get('dt')
        if not normal_date:
            continue
        # Check if any WhatsApp photo exists within 10 days *after* this normal photo
        for whatsapp_date in whatsapp_dates:
            if 0 <= (whatsapp_date - normal_date).days <= 10:
                filtered_normal_photos.append(normal_meta)
                break
    
    print(f"    - Pruned originals list to {len(filtered_normal_photos)} relevant candidates based on date.")
    normal_photos = filtered_normal_photos

    if not normal_photos:
        print("    - No relevant normal photos found after date filtering.")
        return compression_duplicates

    # Step 3: Pre-calculate visual features in parallel using all CPU cores.
    # The tqdm bar will show progress for this step.
    image_features = {}
    all_image_files = [meta['path'] for meta in whatsapp_candidates + normal_photos]
    
    # Use a multiprocessing Pool to parallelize the feature extraction.
    with multiprocessing.Pool() as pool:
        # Use tqdm for a clean progress bar. imap_unordered is efficient.
        results = list(tqdm(pool.imap_unordered(_get_image_features, all_image_files), total=len(all_image_files), desc="Calculating features"))

    # Convert the list of feature dicts back into a path-keyed dictionary
    for features in results:
        if features:
            image_features[features['path']] = features
            
    raw_files_set = set(raw_files)

    # Step 4: Compare each WhatsApp candidate with the filtered normal photos.
    # Using tqdm for a progress bar in the comparison loop
    for whatsapp_meta in tqdm(whatsapp_candidates, desc="Comparing candidates"):
        whatsapp_file = whatsapp_meta['path']
        
        if whatsapp_file not in raw_files_set or whatsapp_file in existing_duplicates:
            continue
            
        whatsapp_date = whatsapp_meta['dt']
        whatsapp_features = image_features.get(whatsapp_file)
        if not whatsapp_date or not whatsapp_features:
            continue
            
        for normal_meta in normal_photos:
            normal_date = normal_meta['dt']
            normal_file = normal_meta['path']
            normal_features = image_features.get(normal_file)

            if not normal_date or not normal_features:
                continue
                
            days_diff = (whatsapp_date - normal_date).days
            if not (0 <= days_diff <= 10):
                continue
                
            if _compare_image_features(whatsapp_features, normal_features):
                compression_duplicates.add(whatsapp_file)
                # print(f"Found WhatsApp compressed version: {whatsapp_file.name} "
                #       f"({whatsapp_meta['size']/1024:.0f}KB) -> original: {normal_file.name} ({normal_meta['size']/1024:.0f}KB), "
                #       f"{days_diff} days later")
                break
    
    return compression_duplicates


#========== Find duplicate photos ============

def find_exact_duplicates(all_files, raw_files, metadata_cache):
    """
    Find exact duplicates using file size from cache and then full hash comparison.
    Prioritizes keeping processed files over raw files.
    """
    print("\n  > Phase 1: Finding exact duplicates...")
    exact_duplicates = set()
    raw_files_set = set(raw_files)

    # Group by file size from the metadata cache
    size_groups = defaultdict(list)
    for f in all_files:
        meta = metadata_cache.get(f)
        if meta and meta['size'] > 0:
            size_groups[meta['size']].append(f)
    
    # Identify groups with potential duplicates (more than one file of the same size)
    potential_dupe_groups = [group for group in size_groups.values() if len(group) > 1]
    
    total_groups = len(potential_dupe_groups)
    print(f"    - Found {total_groups} groups of same-sized files to check.")

    # Hash comparison only for potential duplicates
    for group in tqdm(potential_dupe_groups, desc="      - Hashing groups"):
        hash_groups = defaultdict(list)
        for f in group:
            file_hash = calculate_file_hash(f)
            if file_hash:
                hash_groups[file_hash].append(f)
        
        # Mark duplicates based on hash
        for file_list in hash_groups.values():
            if len(file_list) < 2:
                continue

            # Separate raw and processed files in the group
            processed_in_group = [f for f in file_list if f not in raw_files_set]
            raw_in_group = [f for f in file_list if f in raw_files_set]

            if processed_in_group:
                # If a processed file exists, all raw files with the same hash are duplicates.
                exact_duplicates.update(raw_in_group)
            elif len(raw_in_group) > 1:
                # If only raw files, keep one and mark the rest as duplicates.
                # Sort to make it deterministic.
                raw_in_group.sort() 
                exact_duplicates.update(raw_in_group[1:])
    
    return exact_duplicates

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

#==================================================================================
# FILE GATHERING FUNCTIONS
#==================================================================================
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

#==================================================================================
# METADATA EXTRACTION FUNCTIONS
#==================================================================================

def get_file_metadata(file_path):
    """
    Extracts timestamp and size from a file, caching results.
    This is the single source for file metadata extraction.
    """
    dt = None
    ext = file_path.suffix.lower()
    
    # Get timestamp from media metadata if available
    if ext in [".jpg", ".jpeg", ".tiff", ".png", ".heic"]:
        dt = get_exif_date_taken(file_path)
    elif ext in [".mp4", ".mov"]:
        dt = get_mp4_creation_time(file_path)
    
    # Fallback to file system timestamps if no media metadata found
    if not dt:
        try:
            # Use the earliest of modify or create time for robustness
            mtime = file_path.stat().st_mtime
            ctime = file_path.stat().st_ctime
            dt = datetime.fromtimestamp(min(mtime, ctime))
        except OSError:
            pass  # File might not exist or be accessible
    
    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0

    return {"dt": dt, "size": size, "path": file_path}

def get_exif_date_taken(file_path):
    """
    Attempt to read EXIF DateTimeOriginal (Tag 36867) from image.
    Returns a datetime if successful, else None.
    """
    try:
        img = Image.open(file_path)
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

def get_mp4_creation_time(file_path):
    """
    Extract creation_time from MP4 metadata using ffmpeg-python package.
    Faster than subprocess calls to ffprobe.
    """
    try:
        # Use ffmpeg.probe instead of subprocess
        probe = ffmpeg.probe(str(file_path))
        creation_time_str = probe.get('format', {}).get('tags', {}).get('creation_time')
        
        if creation_time_str:
            # Parse ISO format: "2023-10-27T18:23:32.000000Z"
            creation_time_str = creation_time_str.split('.')[0].split('T')[0]
            return datetime.fromisoformat(creation_time_str)
    except Exception as e:
        print(f"Error extracting MP4 creation time from {file_path}: {e}")
    return None

def gather_raw_files(raw_dirs):
    """
    Recursively collect all files from raw directories.
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

#==================================================================================
# MAIN PROCESSING FUNCTIONS
#==================================================================================

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
     - Gather files from raw and processed directories.
     - Perform a single pass to cache all file metadata.
     - Run duplicate detection using the cache.
     - Sort chronologically and assign sequential names.
     - Output enhanced evaluation_log.csv.
    """
    # Gather files
    raw_files = gather_raw_files(RAW_DIRS)
    processed_files = gather_processed_files(PROCESSED_DIR)
    all_files = raw_files + processed_files
    
    # --- Parallel Metadata Caching Pass ---
    print(f"\nCaching metadata for {len(all_files)} files... (this may take a moment)")
    metadata_cache = {}
    
    # Use a multiprocessing Pool to parallelize metadata extraction.
    with multiprocessing.Pool() as pool:
        results = list(tqdm(pool.imap_unordered(get_file_metadata, all_files), total=len(all_files), desc="Caching metadata"))

    # Convert the list of results back into a path-keyed dictionary
    for meta in results:
        if meta:
            metadata_cache[meta['path']] = meta
            
    print("\n" + "=" * 50)
    print("Starting Duplicate Detection")
    print("=" * 50)
    
    # Single efficient duplicate detection pass using the cache
    raw_duplicates_to_skip = find_duplicates(raw_files, processed_files, metadata_cache)
    
    print(f"\nStarting evaluation of {len(raw_files)} raw files...")
    
    file_info = []
    for f in raw_files:
        meta = metadata_cache.get(f)
        if meta and meta.get('dt'):
            file_info.append((f, meta['dt']))
        else:
            print(f"Warning: Could not get a valid timestamp for {f}, skipping.")

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
    Processing stage with ffmpeg-python for conversions:
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

    # Filter for rows to process
    rows_to_process = [row for row in rows if row["status"] == "pending" and row.get("import", "True") == "True"]
    
    if not rows_to_process:
        print("No pending items to process.")
        return

    for row in tqdm(rows_to_process, desc="Processing files"):
        src = Path(row["source"])
        year_folder = base / row["target_year"]
        year_folder.mkdir(exist_ok=True)
        target = year_folder / row["target_name"]

        try:
            if row["convert"] == "True":
                # Use ffmpeg-python instead of subprocess
                input_ext = src.suffix.lower()
                
                if input_ext == ".heic":
                    # HEIC to JPG conversion
                    (
                        ffmpeg
                        .input(str(src))
                        .output(str(target), vcodec='mjpeg', q=2)
                        .overwrite_output()
                        .run(quiet=True)
                    )
                elif input_ext == ".mov":
                    # MOV to MP4 conversion
                    (
                        ffmpeg
                        .input(str(src))
                        .output(str(target), vcodec='libx264', acodec='aac', preset='fast')
                        .overwrite_output()
                        .run(quiet=True)
                    )
                else:
                    # Default conversion
                    (
                        ffmpeg
                        .input(str(src))
                        .output(str(target))
                        .overwrite_output()
                        .run(quiet=True)
                    )
            else:
                shutil.copy2(src, target)
                
            row["status"] = "done"
        except Exception as e:
            row["status"] = f"error: {e}"
            print(f"Error processing {src.name}: {e}")
        changed = True

    if changed:
        with open(EVAL_LOG, "w", newline="", encoding="utf-8") as csvf:
            writer = csv.DictWriter(csvf, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print("Processing complete. Log updated.")


#==================================================================================
# DUPLICATE MANAGEMENT FUNCTIONS
#==================================================================================

def move_duplicates():
    """
    Move duplicate files to a separate duplicates folder.
    Reads evaluation_log.csv and moves files marked as duplicates.
    Uses cut (move) operation instead of copy to remove duplicates from raw folders.
    All duplicates are dumped into a single folder without year organization.
    """
    if not Path(EVAL_LOG).exists():
        print("ERROR: Run evaluation first to generate evaluation log.")
        return

    rows = []
    with open(EVAL_LOG, newline="", encoding="utf-8") as csvf:
        for row in csv.DictReader(csvf):
            rows.append(row)

    # Filter for duplicate files only
    duplicate_rows = [row for row in rows if row["status"] == "duplicate"]
    
    if not duplicate_rows:
        print("No duplicate files found to move.")
        return

    print(f"Found {len(duplicate_rows)} duplicate files to move...")
    
    # Create duplicates directory
    base_duplicates = Path(DUPLICATES_DIR)
    base_duplicates.mkdir(exist_ok=True)
    
    moved_count = 0
    error_count = 0
    
    for row in tqdm(duplicate_rows, desc="Moving duplicates"):
        src = Path(row["source"])
        
        if not src.exists():
            print(f"Warning: Source file not found: {src}")
            continue
            
        try:
            # Use original filename - dump all into single duplicates folder
            target = base_duplicates / src.name
            
            # Handle filename conflicts by adding counter
            counter = 1
            original_target = target
            while target.exists():
                stem = original_target.stem
                suffix = original_target.suffix
                target = base_duplicates / f"{stem}_dup{counter:03d}{suffix}"
                counter += 1
            
            # Move (cut) the file
            shutil.move(str(src), str(target))
            moved_count += 1
            
        except Exception as e:
            print(f"Error moving {src}: {e}")
            error_count += 1
    
    print(f"\nDuplicate moving complete:")
    print(f"Files moved: {moved_count}")
    print(f"Errors: {error_count}")
    print(f"Duplicates saved to: {DUPLICATES_DIR}")

#==================================================================================
# MAIN SCRIPT EXECUTION
#==================================================================================

if __name__ == "__main__":
    if RUN_EVALUATE:
        evaluate()
    if RUN_MOVE_DUPLICATES:
        move_duplicates()
    if RUN_PROCESS:
        process()