# Handover: Streamline Video Indexer Pipeline (Option B)

> **Goal**: Eliminate 4 manual steps from the Sandwich Workflow by having Apps Script write a `batch_manifest.csv` directly to Google Drive, and having `Launch_Indexer.command` auto-download it instead of using a file picker.

---

## Current Workflow (6 manual steps)

```
1. Open Sheet → Batch Add Video Links → paste URLs     ← Apps Script
2. File > Download > CSV                                ← MANUAL
3. Navigate to CYAM-YouTube-Knowledgebase folder        ← MANUAL
4. Double-click Launch_Indexer.command                  ← MANUAL
5. Pick CSV via native file picker                      ← MANUAL
6. Copy output CSV → paste back into Sheet              ← MANUAL
```

## Target Workflow (2 manual steps)

```
1. Open Sheet → Batch Add Video Links → paste URLs     ← Apps Script
   (manifest auto-saved to Drive)                       ← AUTOMATIC
2. Double-click Launch_Indexer.command                  ← MANUAL
   (auto-downloads manifest, runs pipeline)             ← AUTOMATIC
3. Copy output CSV → paste back into Sheet              ← MANUAL
```

---

## Architecture

```
┌──────────────────────────────────┐
│ Google Sheet (YT_Video_Index)    │
│                                  │
│  "Batch Add Video Links"         │
│   ├── Appends rows to sheet      │
│   └── Writes batch_manifest.csv  │──── writes to ──┐
│       to Drive folder            │                  │
└──────────────────────────────────┘                  │
                                                      ▼
                                    ┌─────────────────────────────────┐
                                    │ Google Drive                    │
                                    │ "CYAM Public Files"             │
                                    │  Folder ID: 1xVCVbq...sMQB7    │
                                    │                                 │
                                    │  📄 batch_manifest.csv          │
                                    │  📄 KB_YouTube_Index.json       │
                                    └─────────────────────────────────┘
                                                      │
                                         Google Drive for Desktop
                                         auto-syncs to local Mac
                                                      ▼
                                    ┌─────────────────────────────────┐
                                    │ Local Mac                       │
                                    │                                 │
                                    │ ~/Library/CloudStorage/         │
                                    │   GoogleDrive-david.towers@     │
                                    │   employeeimpactai.org/         │
                                    │   My Drive/CYAM Public Files/   │
                                    │     📄 batch_manifest.csv ◄──── synced │
                                    │                                 │
                                    │ Launch_Indexer.command reads it  │
                                    │ and runs index_video.py          │
                                    │                                 │
                                    │ Output: final_video_index.csv   │
                                    └─────────────────────────────────┘
```

---

## Key Facts

| Item | Value |
|------|-------|
| **Drive Folder ID** | `1xVCVbqPBYdx0a0PbadWFmDW_vhKsMQB7` |
| **Drive Folder Name** | `CYAM Public Files` |
| **Drive Account** | `david.towers@employeeimpactai.org` (org) |
| **Local sync path** | `~/Library/CloudStorage/GoogleDrive-david.towers@employeeimpactai.org/My Drive/CYAM Public Files/` ✅ **CONFIRMED** |
| **Manifest filename** | `batch_manifest.csv` |
| **Manifest columns** | `video_url, dkd_doc_ref` |
| **Apps Script project** | `CYAM_Master_Knowledge_Index` (standalone) |
| **Script repo** | `/Users/imac/dev/Test Button/CYAM-YouTube-Knowledgebase/` |

---

## Task 1: Update Apps Script (CYAM_Master_Knowledge_Index)

**Model**: 🟠 Gemini 3.1 Pro or Claude Sonnet 4.6
**Codebase**: `standalone/CYAM-Master-Knowledge-Index/Code.js` (in CYAM-Platform repo) OR direct edit in the Apps Script editor

### 1A. Modify `processBatchVideoLinks` to also write a manifest CSV to Drive

After appending blank rows to the sheet, add this logic:

```javascript
// Build manifest CSV content
var csvRows = ["video_url,dkd_doc_ref"];
validUrls.forEach(function(url) {
  csvRows.push(url + "," + selectedCategory);
});
var csvContent = csvRows.join("\n");

// Write to CYAM Public Files folder
var folderId = '1xVCVbqPBYdx0a0PbadWFmDW_vhKsMQB7';
var folder = DriveApp.getFolderById(folderId);
var fileName = 'batch_manifest.csv';

// Remove old manifest if exists
var existing = folder.getFilesByName(fileName);
while (existing.hasNext()) {
  existing.next().setTrashed(true);
}

// Create new manifest
folder.createFile(fileName, csvContent, 'text/csv');
```

### 1B. Update the success message

```javascript
ui.alert('Success', 
  'Queued ' + validUrls.length + ' videos.\n\n' +
  'batch_manifest.csv has been saved to CYAM Public Files.\n' +
  'Double-click Launch_Indexer.command to process them.',
  ui.ButtonSet.OK);
```

---

## Task 2: Update `Launch_Indexer.command`

**Model**: 🟠 Gemini 3.1 Pro or any
**File**: `/Users/imac/dev/Test Button/CYAM-YouTube-Knowledgebase/Launch_Indexer.command`

Replace the file picker with auto-detection from the Drive sync path:

```bash
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
```

**Key feature**: It tries the Drive path first. If the manifest isn't there (maybe you deleted it, or Drive hasn't synced yet), it falls back to the native file picker. Best of both worlds.

---

## Task 3: Push to GitHub

After both changes are made:

```bash
cd /Users/imac/dev/Test\ Button/CYAM-YouTube-Knowledgebase/
git add -A
git commit -m "Streamline: auto-detect manifest from Google Drive sync"
git push
```

---

## Verification Steps

1. Open `CYAM_Master_Knowledge_Index` spreadsheet
2. Click `CYAM Menu > Batch Add Video Links`
3. Paste 2 test URLs, select category, submit
4. Check `CYAM Public Files` folder in Google Drive — `batch_manifest.csv` should appear
5. Wait ~10 seconds for Drive sync
6. Double-click `Launch_Indexer.command`
7. It should auto-detect the manifest without showing a file picker
8. Pipeline runs and outputs `final_video_index.csv`

---

## Prompt for Next Session

```
Please read the file at:
/Users/imac/dev/Test Button/CYAM-YouTube-Knowledgebase/HANDOVER_STREAMLINE_PIPELINE.md

Execute Tasks 1 and 2. The Apps Script code is in the 
CYAM_Master_Knowledge_Index standalone project.

Before making changes, first confirm the local Google Drive sync path
by checking: ls ~/Library/CloudStorage/

Key files:
- Apps Script (local): /Users/imac/dev/CYAM-Platform/standalone/CYAM-Master-Knowledge-Index/Code.js
- Launch script: /Users/imac/dev/Test Button/CYAM-YouTube-Knowledgebase/Launch_Indexer.command
- Drive folder ID: 1xVCVbqPBYdx0a0PbadWFmDW_vhKsMQB7
```
