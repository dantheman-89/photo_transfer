# Photo Migration Tool

[![Python 3.10.13](https://img.shields.io/badge/python-3.10.13-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](https://opensource.org/licenses/Apache-2.0)

A comprehensive photo and video migration tool that organizes media files chronologically while detecting and handling duplicates intelligently.

## 🎯 Overview

This tool helps you migrate and organize large photo/video collections from multiple raw folders into a structured, chronologically-organized archive. It uses EXIF metadata when available and provides intelligent duplicate detection to prevent data redundancy.

## ✨ Features

- **📅 Smart Date Detection**: Uses EXIF "Date Taken" metadata when available, falls back to file timestamps
- **🔍 Intelligent Duplicate Detection**: Efficient file size + hash-based duplicate identification
- **🔄 Format Conversion**: Automatically converts HEIC to JPG and MOV to MP4 using FFmpeg
- **📊 Progress Tracking**: Detailed logging and progress reporting throughout the process
- **⚡ Performance Optimized**: Only calculates hashes for potential duplicates, significantly reducing processing time
- **📋 CSV Logging**: Comprehensive evaluation logs with import control flags
- **🎯 Cross-Directory Comparison**: Compares raw files against existing processed files

## 🏗️ Architecture

The tool operates in two distinct phases:

1. **Evaluation Phase** (`evaluate()`): Analyzes files, detects duplicates, assigns chronological names
2. **Processing Phase** (`process()`): Performs actual file operations (copy/convert/organize)

## 📋 Prerequisites

- Python 3.10.13 or higher
- FFmpeg (for video conversion)
- Required Python packages (see [Installation](#installation))

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/photo-migration-tool.git
   cd photo-migration-tool
   ```

2. **Install Python dependencies**
   ```bash
   pip install pillow piexif pillow-heif
   ```

3. **Install FFmpeg**
   
   **Windows:**
   ```bash
   # Using chocolatey
   choco install ffmpeg
   
   # Or download from https://ffmpeg.org/download.html
   ```
   
   **macOS:**
   ```bash
   brew install ffmpeg
   ```
   
   **Linux:**
   ```bash
   sudo apt update
   sudo apt install ffmpeg
   ```

## ⚙️ Configuration

Edit the configuration section in `photo_migration.py`:

```python
# ──────── Configuration ─────────
RAW_DIRS               = [r"E:\Photos\Unsorted"]       # Input folders
PROCESSED_DIR          = r"E:\Photos\processed"        # Output base folder
RUN_EVALUATE           = True                          # Run evaluation stage
RUN_PROCESS            = True                         # Run processing stage
SKIP_LIVE_PHOTO_CLIPS  = True                         # Skip .MOV files paired with .HEIC
EXCLUDE_EXTS           = [".aae"]                      # File extensions to exclude
# ─────────────────────────────────
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `RAW_DIRS` | List of source directories to scan | `[r"E:\Photos\Unsorted"]` |
| `PROCESSED_DIR` | Destination directory for organized files | `r"E:\Photos\processed"` |
| `RUN_EVALUATE` | Enable evaluation phase | `True` |
| `RUN_PROCESS` | Enable processing phase | `True` |
| `SKIP_LIVE_PHOTO_CLIPS` | Skip MOV files paired with HEIC Live Photos | `True` |
| `EXCLUDE_EXTS` | File extensions to ignore during evaluation | `[".aae"]` |

## 🎮 Usage

### Basic Workflow

1. **Configure** the tool by editing the configuration section
2. **Run evaluation only** (`RUN_PROCESS = False`) to analyze files and generate processing plan
3. **Review** the `evaluation_log.csv` file to verify the plan
4. **Run processing** (`RUN_PROCESS = True`) to execute the migration

### Step-by-Step Usage

**Step 1: Initial Setup and Evaluation**

Ensure `RUN_PROCESS = False` in your configuration to run evaluation only:

```python
# In photo_migration.py configuration section
RUN_EVALUATE = True   # Generate evaluation plan
RUN_PROCESS  = False  # Don't execute file operations yet
```

```bash
# Run evaluation only (safe, no file operations)
python photo_migration.py
```

This creates `evaluation_log.csv` with the migration plan. **Review this file carefully** to:
- Check duplicate detection results
- Verify target naming scheme (YYYYMMDD_NNN format)
- Confirm which files will be imported vs. skipped
- Ensure the chronological ordering looks correct

**Step 2: Execute Processing**

After reviewing and confirming the evaluation plan is correct:

```python
# In photo_migration.py configuration section
RUN_EVALUATE = False  # Skip re-evaluation (optional, saves time)
RUN_PROCESS  = True   # Execute the planned operations
```

```bash
# Execute the migration based on evaluation_log.csv
python photo_migration.py
```

**⚠️ Important Safety Notes:**
- Always run evaluation first with `RUN_PROCESS = False`
- Backup your original files before enabling processing
- The evaluation phase is completely safe and only reads files
- Review the evaluation log thoroughly before proceeding

### Output Structure

```
processed/
├── 2019/
│   ├── 20190604_001.jpg
│   ├── 20190622_001.jpg
│   └── ...
├── 2020/
│   ├── 20200129_001.jpg
│   └── ...
└── 2021/
    ├── 20210202_001.jpg
    ├── 20210202_002.jpg
    └── ...
```

## 📊 Evaluation Log

The `evaluation_log.csv` contains:

| Column | Description |
|--------|-------------|
| `source` | Original file path |
| `timestamp` | Detected date/time (EXIF or file system) |
| `target_year` | Target year folder |
| `target_name` | Assigned filename in format `YYYYMMDD_NNN.ext` |
| `status` | Processing status (`pending`, `duplicate`, `done`, `error`) |
| `convert` | Whether format conversion is needed |
| `import` | Whether file should be imported (`True`/`False`) |

## 🔍 Duplicate Detection

The tool uses a two-stage approach for efficient duplicate detection:

1. **Size Grouping**: Groups files by identical file size
2. **Hash Verification**: Calculates MD5 hashes only for files with matching sizes

### Duplicate Handling Rules

- **Raw vs. Processed**: If a raw file duplicates an existing processed file, the raw file is skipped
- **Raw vs. Raw**: If multiple raw files are identical, the first (chronologically) is kept, others are marked as duplicates
- **Priority**: Processed files always take precedence over raw files

## 🚀 Performance

- **Efficient**: Only hashes files with matching sizes (typically <5% of total files)
- **Progress Tracking**: Real-time progress updates every 100 files processed
- **Memory Optimized**: Processes files in chunks to handle large collections
- **Fast I/O**: Uses optimized file operations and chunked reading

## 🐛 Troubleshooting

### Common Issues

**FFmpeg not found**
```bash
# Ensure FFmpeg is in your PATH
ffmpeg -version
```

**Permission errors**
- Ensure write permissions to the destination directory
- Run as administrator if needed (Windows)

**Memory issues with large collections**
- The tool is optimized for large collections but ensure adequate RAM
- Process in smaller batches if needed

**EXIF reading errors**
- Some corrupted files may cause EXIF reading to fail
- The tool gracefully falls back to file timestamps

### Debug Mode

Enable verbose logging by adding debug prints in the code or check the console output for detailed progress information.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Pillow](https://python-pillow.org/) for image processing
- [piexif](https://github.com/hMatoba/Piexif) for EXIF metadata handling
- [pillow-heif](https://github.com/bigcat88/pillow_heif) for HEIF/HEIC support
- [FFmpeg](https://ffmpeg.org/) for video conversion

## 📞 Support

If you encounter any issues or have questions:

1. Check the [Troubleshooting](#troubleshooting) section
2. Search existing [Issues](https://github.com/yourusername/photo-migration-tool/issues)
3. Create a new issue with detailed information about your problem

---

**⚠️ Important**: Always backup your original files before running the processing phase. The evaluation phase is safe and only reads files.
