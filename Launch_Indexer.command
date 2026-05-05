#!/bin/bash
cd "$(dirname "$0")"

echo "======================================"
echo "      CYAM Video Indexer Pipeline     "
echo "======================================"
echo ""

# ---- CONFIG ----
# Google Drive for Desktop sync path (org account)
DRIVE_PATH="$HOME/Library/CloudStorage/GoogleDrive-david.towers@employeeimpactai.org/My Drive/CYAM Public Files"
MANIFEST_NAME="batch_manifest.csv"

# ---- AUTO-DETECT OR FILE PICKER ----
if [ -f "$DRIVE_PATH/$MANIFEST_NAME" ]; then
    FILE_PATH="$DRIVE_PATH/$MANIFEST_NAME"
    echo "📂 Auto-detected manifest from Google Drive:"
    echo "   $FILE_PATH"
else
    echo "⚠️  No manifest found in Google Drive. Opening file picker..."
    FILE_PATH=$(osascript -e 'try' -e 'POSIX path of (choose file with prompt "Select your batch_manifest.csv file" of type {"csv"})' -e 'end try')
fi

if [ -z "$FILE_PATH" ]; then
    echo "❌ No file selected. Closing..."
    echo ""
    exit 0
fi

echo "✅ Selected File: $FILE_PATH"
OUT_PATH="$(dirname "$FILE_PATH")/final_video_index.csv"

echo "🚀 Running Video Indexer... Please wait"
echo "----------------------------------------------------------------------"
python3 index_video.py --batch "$FILE_PATH" --out "$OUT_PATH"

echo "----------------------------------------------------------------------"
echo "🎉 Pipeline finished!"
echo "📄 Output: $OUT_PATH"
echo ""
echo "Next step: Open the CSV and paste the rows back into your Google Sheet."
echo ""
