# Photo Migration Tool

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)

A comprehensive photo and video migration tool that organizes media files chronologically while intelligently detecting and handling both exact and visually similar duplicates.

## 🎯 Overview

This tool helps you migrate and organize large photo/video collections from multiple raw folders into a structured, chronologically-organized archive. It uses EXIF metadata for accurate dating and provides a powerful, multi-stage duplicate detection system to prevent data redundancy and clean up compressed copies (e.g., from WhatsApp).

## ✨ Features

- **📅 Smart Date Detection**: Uses EXIF "Date Taken" metadata when available, falls back to file timestamps.
- **🔍 Advanced Duplicate Detection**:
    - **Exact Duplicates**: Finds bit-for-bit identical files using MD5 hashing.
    - **Visually Similar Duplicates**: Uses perceptual hashing (`pHash`) to find compressed versions of photos (e.g., WhatsApp images).
- **⚡ High-Performance Processing**: Leverages `multiprocessing` to parallelize metadata caching and image feature calculation across all CPU cores.
- **📊 Real-Time Progress**: Uses `tqdm` to provide clean, detailed progress bars for long-running operations.
- **🔄 Format Conversion**: Automatically converts HEIC to JPG and MOV to MP4 using FFmpeg.
- **📋 CSV Logging**: Generates a comprehensive `evaluation_log.csv` with a detailed plan for review and control.
- **📁 Duplicate Archiving**: Optionally moves all detected duplicates to a separate folder for review or deletion.

## 🏗️ Architecture

The tool operates in up to three distinct phases, controlled by flags in the configuration:

1.  **Evaluation Phase** (`evaluate()`):
    - Gathers all files from source and destination directories.
    - Caches file metadata (size, date) in parallel.
    - Runs a two-stage duplicate detection process.
    - Generates `evaluation_log.csv` with a detailed plan (`pending`, `duplicate`).
2.  **Duplicate Moving Phase** (`move_duplicates()`):
    - Reads `evaluation_log.csv`.
    - Moves all files marked as `duplicate` to the specified `DUPLICATES_DIR`.
3.  **Processing Phase** (`process()`):
    - Reads `evaluation_log.csv`.
    - Copies or converts all files marked as `pending` into the chronologically structured `PROCESSED_DIR`.

## 📋 Prerequisites

- Python 3.10 or higher
- FFmpeg (must be in the system's PATH)

## 🚀 Installation

1.  **Clone the repository**
    ```bash
    git clone https://github.com/yourusername/photo-migration-tool.git
    cd photo-migration-tool
    ```

2.  **Install Python dependencies**
    ```bash
    pip install pillow piexif pillow-heif ffmpeg-python numpy imagehash tqdm
    ```

3.  **Install FFmpeg**
    - **Windows (via Chocolatey):** `choco install ffmpeg`
    - **macOS (via Homebrew):** `brew install ffmpeg`
    - **Linux (Debian/Ubuntu):** `sudo apt update && sudo apt install ffmpeg`
    - Or download from the [official FFmpeg site](https://ffmpeg.org/download.html).

## ⚙️ Configuration

Edit the configuration section at the top of `photo_migration.py`:

```python
#==================================================================================
# CONFIGURATION
#==================================================================================

RAW_DIRS               = [r"E:\Photos\2024"]        # Input folders
PROCESSED_DIR          = "processed"                 # Output base folder
DUPLICATES_DIR         = r"E:\Photos\duplicate"       # Folder to move duplicates to
RUN_EVALUATE           = True                        # Run evaluation stage
RUN_MOVE_DUPLICATES    = True                        # Run duplicate moving stage
RUN_PROCESS            = False                       # Run processing stage
SKIP_LIVE_PHOTO_CLIPS  = True                        # Skip .MOV files paired with .HEIC
EXCLUDE_EXTS           = [".aae", ".nomedia"]        # File extensions to exclude in evaluation
```

### Configuration Options

| Option | Description |
| :--- | :--- |
| `RAW_DIRS` | List of source directories to scan for media. |
| `PROCESSED_DIR` | The main destination directory for organized files. |
| `DUPLICATES_DIR` | Directory where duplicate files will be moved. |
| `RUN_EVALUATE` | Enables the evaluation phase. |
| `RUN_MOVE_DUPLICATES` | Enables the duplicate moving phase. |
| `RUN_PROCESS` | Enables the final processing/migration phase. |
| `SKIP_LIVE_PHOTO_CLIPS` | Skips `.MOV` files that are paired with `.HEIC` Live Photos. |
| `EXCLUDE_EXTS` | File extensions to completely ignore during scanning. |

## 🎮 Usage

### Recommended Workflow

1.  **Configure** the script, setting your `RAW_DIRS`, `PROCESSED_DIR`, and `DUPLICATES_DIR`.
2.  **Run Evaluation**: Set `RUN_EVALUATE = True` and the other two flags to `False`. Run the script.
    ```bash
    python photo_migration.py
    ```
3.  **Review Plan**: Open the generated `evaluation_log.csv`. Carefully check which files are marked as `pending` and which are `duplicate`.
4.  **Move Duplicates (Optional)**: If you are satisfied with the duplicate list, set `RUN_MOVE_DUPLICATES = True` and `RUN_EVALUATE = False`. Run the script again to archive the duplicates.
5.  **Process Files**: Finally, set `RUN_PROCESS = True` and the other flags to `False`. Run the script to perform the final migration of `pending` files.

**⚠️ Important Safety Note:** Always back up your original files before running the `RUN_MOVE_DUPLICATES` or `RUN_PROCESS` stages. The `RUN_EVALUATE` stage is completely safe and only reads files.

## 🔍 Duplicate Detection Logic

The tool uses a powerful two-phase process to find duplicates:

1.  **Phase 1: Exact Duplicates**
    - Groups all files by exact file size.
    - For files with matching sizes, it calculates a full MD5 hash.
    - Files with the same hash are marked as exact duplicates.

2.  **Phase 2: Visually Similar Duplicates**
    - This phase specifically targets compressed versions of photos (e.g., originals vs. WhatsApp copies).
    - It filters for potential candidates based on file size (e.g., WhatsApp < 500KB, Originals > 1MB).
    - It further filters by date, only comparing photos taken within a 10-day window of each other.
    - Finally, it calculates a perceptual hash (`pHash`) for the remaining candidates and marks them as duplicates if they are visually similar.

## 🚀 Performance

- **Parallel Processing**: Uses `multiprocessing` to significantly speed up metadata and image feature calculation on multi-core CPUs.
- **Efficient Hashing**: Full-file MD5 hashing is only performed on a small subset of files that have identical sizes.
- **Targeted Comparisons**: Visual similarity checks are heavily filtered by size and date to reduce the number of comparisons.
- **Real-time Feedback**: `tqdm` progress bars provide clear insight into the progress of time-consuming steps.

## 🤝 Contributing

Contributions are welcome! Please feel free to fork the repository, make changes, and open a pull request.

## 📄 License

This project is licensed under the Apache 2.0 License - see the LICENSE file for details.

## 🙏 Acknowledgments

- [Pillow](https://python-pillow.org/)
- [piexif](https://github.com/hMatoba/Piexif)
- [pillow-heif](https://github.com/bigcat88/pillow_heif)
- [ffmpeg-python](https://github.com/kkroening/ffmpeg-python)
- [NumPy](https://numpy.org/)
- [ImageHash](https://github.com/JohannesBuchner/imagehash)
- [tqdm](https://github.com/tqdm/tqdm)