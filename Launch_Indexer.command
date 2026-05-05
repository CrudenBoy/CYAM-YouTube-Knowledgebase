#!/bin/bash
cd "$(dirname "$0")"

echo "======================================"
echo "      CYAM Video Indexer Pipeline     "
echo "======================================"
echo ""

# Native Mac GUI File Picker
FILE_PATH=$(osascript -e 'try' -e 'POSIX path of (choose file with prompt "Select your batch_manifest.csv file" of type {"csv"})' -e 'end try')

if [ -z "$FILE_PATH" ]; then
    echo "❌ No file selected. Closing..."
    echo ""
    exit 0
fi

echo "✅ Selected File: $FILE_PATH"
# Automatically set output path next to the selected file
OUT_PATH="$(dirname "$FILE_PATH")/final_video_index.csv"

echo "🚀 Running Video Indexer... Please wait (this window will show progress)"
echo "----------------------------------------------------------------------"
python3 index_video.py --batch "$FILE_PATH" --out "$OUT_PATH"

echo "----------------------------------------------------------------------"
echo "🎉 Pipeline finished!"
echo "Check the terminal output above for any errors."
echo ""
