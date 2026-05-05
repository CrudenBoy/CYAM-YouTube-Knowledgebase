---
name: cyam-video-indexer
description: AI-powered multimodal video indexing pipeline for creating hierarchical, RAG-ready documentation from YouTube videos.
---

# CYAM Video Indexer Skill

## Overview
This skill documents the local Multimodal Video Indexing Pipeline. Because Google Apps Script cannot natively download videos or execute Python, this local pipeline serves as the secure backend processor for the CYAM Knowledgebase.

## Architecture & Toolchain
- **Environment**: macOS Native, executing via `Launch_Indexer.command` (AppleScript file picker -> bash).
- **Core Downloader**: `yt-dlp` (Extracts MP4 and metadata).
- **Audio Extraction**: `ffmpeg-python`
- **Transcription**: `whisper.py` (OpenAI Whisper locally or API based).
- **Vision Processing**: `frames.py` (Extracts keyframes at timestamps).
- **Intelligence**: `index_video.py` uses Gemini 1.5 Flash-Lite (Vision Pass) and DeepSeek V3 (Text/Structure Pass) to generate output.
- **API Management**: Managed via `.env` file in the user's root configuration path.

## The Data Contract
The script strictly ingests a CSV (`batch_manifest.csv`) requiring at least these two headers:
`video_url, dkd_doc_ref`

The script strictly outputs `final_video_index.csv` containing 16 exact headers:
`video_url, video_id, video_title, platform, publish_date, chapter_title, timestamp_seconds, url_with_timestamp, transcript_chunk, dkd_doc_ref, key_concepts, faq_question, faq_answer, difficulty_level, prerequisites, detailed_steps`

## Error Handling & Robustness
The system implements a "graceful failure" design. If `yt-dlp` encounters an Age-Restricted, Deleted, or Bot-Protected video, it will:
1. Log an explicitly formatted terminal error `❌ Download failed...`
2. Skip the video entirely, preventing pipeline crashes.
3. Automatically append the failed URL to a `failed_batch.csv` file for manual retry or logging.

## AI Developer Guidelines
If asked to debug or modify this pipeline:
1. **Never attempt to migrate this to Google Apps Script**: It structurally relies on binaries (`ffmpeg`, `yt-dlp`) that are impossible to run in GAS.
2. **Never modify the 16-column Data Contract**: The CYAM Dashboard UI fundamentally relies on exact matches for `detailed_steps` (JSON format) and `url_with_timestamp`.
3. **If `yt-dlp` fails**: Do not rewrite the Python code. Ensure the user has run `brew upgrade yt-dlp` to combat YouTube's API changes.
