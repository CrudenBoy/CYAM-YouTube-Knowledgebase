# CYAM YouTube Knowledgebase - User Journey

The CYAM YouTube Knowledgebase utilizes a two-part **"Sandwich Workflow"** to securely bypass Google Apps Script's strict execution limits while leveraging powerful local AI models for video processing.

## 1. The Queue (Google Sheets)
**Goal:** Gather URLs and assign categories.
1. The User opens the `CYAM_Master_Knowledge_Index` spreadsheet.
2. Under the CYAM Menu, the user selects **Batch Add Video Links**.
3. A modal appears. The user pastes multiple YouTube URLs (one per line) and selects a master category (e.g., *Opal*, *Google Workspace Studio*).
4. The system appends these URLs to the bottom of the `YT_Video_Index` sheet as empty queue rows.
5. The User clicks **File > Download > Comma Separated Values (.csv)** and saves the file locally as `batch_manifest.csv`.

## 2. The Heavy Lifting (Local Mac Desktop)
**Goal:** Transcribe audio, extract frames, and generate hierarchical JSON RAG steps using AI.
1. The User navigates to their local `/Users/imac/dev/CYAM-YouTube-Knowledgebase/` folder.
2. The User double-clicks the **`Launch_Indexer.command`** desktop icon.
3. A native Mac File Picker appears. The user selects the `batch_manifest.csv` they just downloaded.
4. The terminal takes over, executing the `index_video.py` pipeline. It streams progress live.
5. **Fallback Safety:** If a video fails (e.g., YouTube Bot Protection, Private Video), the script skips it and writes the URL to a `failed_batch.csv` file without crashing the entire batch.
6. The script successfully outputs `final_video_index.csv` in the exact same directory as the input file.

## 3. The Integration (Google Sheets)
**Goal:** Publish the structured AI data to the CYAM Platform CDN.
1. The User opens the newly generated `final_video_index.csv` in Excel/Numbers.
2. The User copies all rows (excluding headers) and pastes them directly over the empty queued rows in the `YT_Video_Index` spreadsheet tab.
3. Under the CYAM Menu, the user clicks **Export YouTube Knowledge JSON**.
4. The Apps Script parses the spreadsheet (including the new `url_with_timestamp` and JSON `detailed_steps` columns) and updates the Master JSON file hosted on the Google Drive CDN.
5. The CYAM Playbook AI immediately gains access to the new knowledge index.
