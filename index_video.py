"""Multimodal Video Indexer — generates hierarchical Task > Subtask > Step
indexes from tutorial videos using a two-pass approach:

Pass 1 (Vision): Sends extracted frames + transcript to a multimodal model
to produce a structured JSON hierarchy with timestamps.

Pass 2 (Text): Enriches each task/subtask with key_concepts, FAQ, difficulty,
and prerequisites via a text model.

Output: sheet-ready CSV rows matching the YT_Video_Index schema.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Import watch.py internals — they live in the same directory
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import download, is_url          # noqa: E402
from frames import auto_fps, extract, get_metadata  # noqa: E402
from transcribe import filter_range, format_transcript, parse_vtt  # noqa: E402
from whisper import load_api_key, transcribe_video  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_VISION_MODEL = "google/gemini-3.1-flash-lite-preview"
DEFAULT_TEXT_MODEL = "deepseek/deepseek-v3.2"
OPENROUTER_CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def _load_openrouter_key() -> str:
    """Load OPENROUTER_API_KEY from env or ~/.config/watch/.env."""
    # Try environment first
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key

    # Try dotenv files (script dir as fallback for sandboxed environments)
    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        SCRIPT_DIR / ".env",
        Path.cwd() / ".env",
    ]
    for dotenv_path in dotenv_paths:
        if not dotenv_path.exists():
            continue
        try:
            for line in dotenv_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "OPENROUTER_API_KEY":
                    v = v.strip()
                    if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
                        v = v[1:-1]
                    if v:
                        return v
        except OSError:
            continue

    raise SystemExit(
        "OPENROUTER_API_KEY not found. Set it in the environment or in "
        "~/.config/watch/.env"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="index_video",
        description=(
            "Generate a hierarchical Task > Subtask > Step index from a "
            "tutorial video using multimodal AI (vision + text models)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Index a single video:\n"
            "  python3 index_video.py 'https://youtu.be/CW4i6dRO_b8' \\\n"
            "      --dkd-ref 'PLAYBOOK:C:STEP1' --out step1c.csv\n"
            "\n"
            "  # Batch-process a manifest:\n"
            "  python3 index_video.py --batch manifest.csv --out master.csv\n"
            "\n"
            "  # Generate a manifest from existing index:\n"
            "  python3 index_video.py --generate-manifest \\\n"
            "      --from KB_YouTube_Index.json --out manifest.csv\n"
        ),
    )

    # --- Positional (optional when --batch or --generate-manifest is used) ---
    ap.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Video URL or local file path (required for single-video mode)",
    )

    # --- Optional arguments ---
    ap.add_argument(
        "--dkd-ref",
        default="",
        help="dkd_doc_ref value applied to all output rows (blank if omitted)",
    )
    ap.add_argument(
        "--out",
        default="video_index.csv",
        help="Output CSV file path (default: video_index.csv)",
    )
    ap.add_argument(
        "--model",
        default=DEFAULT_VISION_MODEL,
        help=f"OpenRouter vision model for Pass 1 (default: {DEFAULT_VISION_MODEL})",
    )
    ap.add_argument(
        "--text-model",
        default=DEFAULT_TEXT_MODEL,
        help=f"OpenRouter text model for Pass 2 (default: {DEFAULT_TEXT_MODEL})",
    )
    ap.add_argument(
        "--max-frames",
        type=int,
        default=80,
        help="Maximum frames to extract (default: 80)",
    )

    # --- Batch mode ---
    ap.add_argument(
        "--batch",
        default=None,
        metavar="MANIFEST_CSV",
        help="Process multiple videos from a manifest CSV (columns: video_url, dkd_doc_ref)",
    )

    # --- Manifest generation ---
    ap.add_argument(
        "--generate-manifest",
        action="store_true",
        help="Generate a batch manifest CSV from an existing JSON index",
    )
    ap.add_argument(
        "--from",
        dest="from_json",
        default=None,
        metavar="JSON_PATH",
        help="Source JSON file for --generate-manifest",
    )

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    out_path = Path(args.out)

    # --- Manifest generation ---
    if args.generate_manifest:
        if not args.from_json:
            ap.error("--generate-manifest requires --from <JSON_PATH>")
        
        json_path = Path(args.from_json)
        if not json_path.exists():
            print(f"[index_video] ❌ Source JSON not found: {args.from_json}", file=sys.stderr)
            return 1
            
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[index_video] ❌ Invalid JSON in {args.from_json}: {exc}", file=sys.stderr)
            return 1
            
        unique_videos = {}
        for item in data:
            url = item.get("video_url", "").strip()
            if url:
                if url not in unique_videos:
                    unique_videos[url] = item.get("dkd_doc_ref", "").strip()
                elif not unique_videos[url]:
                    # Update with non-blank ref if we find one later
                    unique_videos[url] = item.get("dkd_doc_ref", "").strip()
                    
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["video_url", "dkd_doc_ref"])
            for url, ref in unique_videos.items():
                writer.writerow([url, ref])
                
        print(f"[index_video] ✅ Generated manifest with {len(unique_videos)} unique videos -> {out_path}", file=sys.stderr)
        return 0

    # Load API key for vision/text passes
    api_key = _load_openrouter_key()
    print(f"[index_video] API key loaded (starts with {api_key[:8]}...)", file=sys.stderr)
    print(f"[index_video] Vision model : {args.model}", file=sys.stderr)
    print(f"[index_video] Text model   : {args.text_model}", file=sys.stderr)
    print(f"[index_video] Max frames   : {args.max_frames}", file=sys.stderr)
    print(f"[index_video] Output       : {args.out}", file=sys.stderr)

    # --- Batch mode ---
    if args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"[index_video] ❌ Batch manifest not found: {args.batch}", file=sys.stderr)
            return 1
            
        videos = []
        with open(batch_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "video_url" in row and row["video_url"].strip():
                    videos.append({
                        "video_url": row["video_url"].strip(),
                        "dkd_doc_ref": row.get("dkd_doc_ref", "").strip()
                    })
                    
        print(f"[index_video] Loaded {len(videos)} videos from manifest", file=sys.stderr)
        success_count = 0
        failed_videos = []
        for i, vid in enumerate(videos, 1):
            print(f"\n[index_video] Processing video {i}/{len(videos)}...", file=sys.stderr)
            if _process_one(vid["video_url"], vid["dkd_doc_ref"], args, api_key, out_path):
                success_count += 1
            else:
                failed_videos.append(vid)
                
        print(f"\n[index_video] Batch complete. Successfully processed {success_count}/{len(videos)} videos.", file=sys.stderr)
        if failed_videos:
            failed_csv = Path("failed_batch.csv")
            with open(failed_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["video_url", "dkd_doc_ref"])
                for fv in failed_videos:
                    writer.writerow([fv["video_url"], fv["dkd_doc_ref"]])
            
            print(f"[index_video] ❌ {len(failed_videos)} videos failed to process.", file=sys.stderr)
            print(f"[index_video] ℹ️  A retry manifest has been created at: {failed_csv}", file=sys.stderr)
            print(f"[index_video]    Run: python3 index_video.py --batch {failed_csv} --out {out_path.name}", file=sys.stderr)
        return 0 if success_count == len(videos) else 1

    # --- Single-video mode ---
    if not args.source:
        ap.error("source is required for single-video mode (or use --batch / --generate-manifest)")
        
    if args.dkd_ref:
        print(f"[index_video] dkd_doc_ref  : {args.dkd_ref}", file=sys.stderr)
        
    ok = _process_one(args.source, args.dkd_ref or "", args, api_key, out_path)
    return 0 if ok else 1


def _process_one(source_url: str, dkd_ref: str, args: argparse.Namespace, api_key: str, out_path: Path) -> bool:
    """Run the pipeline for a single video. Returns True on success."""
    result = process_single_video(source_url, args, api_key)
    if result is None:
        return False

    frames, transcript_segments, transcript_text, metadata = result
    print(f"[index_video] ✅ Got {len(frames)} frames and {len(transcript_segments)} transcript segments", file=sys.stderr)

    # Pass 1: Vision model — extract structured hierarchy
    debug_path = out_path.with_suffix("._debug.json")

    structured_json = pass1_vision(
        frames=frames,
        transcript_text=transcript_text,
        model=args.model,
        api_key=api_key,
        debug_path=debug_path,
    )

    if structured_json is None:
        print("[index_video] ❌ Pass 1 failed — check the debug file", file=sys.stderr)
        return False

    # Print summary of what the vision model found
    tasks = structured_json.get("tasks", [])
    total_subtasks = sum(len(t.get("subtasks", [])) for t in tasks)
    total_steps = sum(
        len(st.get("steps", []))
        for t in tasks
        for st in t.get("subtasks", [])
    )
    print(f"[index_video] ✅ Pass 1 complete: {len(tasks)} tasks, {total_subtasks} subtasks, {total_steps} steps", file=sys.stderr)

    # Print the full JSON for user review
    print("\n" + "=" * 60, file=sys.stderr)
    print("PASS 1 OUTPUT — Review this JSON:", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(json.dumps(structured_json, indent=2))
    print("=" * 60, file=sys.stderr)
    print(f"Debug JSON saved to: {debug_path}", file=sys.stderr)

    # Validate the structured JSON response
    errors = validate_pass1_json(structured_json)
    if errors:
        print("[index_video] ❌ Validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return False
        
    print(f"[index_video] ✅ JSON validation passed: Found {len(tasks)} tasks, {total_subtasks} subtasks, {total_steps} total steps", file=sys.stderr)

    # Task 2.1: Pass 2 Text Enrichment
    enrichments = pass2_enrichment(
        structured_json=structured_json,
        transcript_text=transcript_text,
        model=args.text_model,
        api_key=api_key,
        debug_path=debug_path,
    )

    if enrichments is None:
        print("[index_video] ❌ Pass 2 failed — check the debug file", file=sys.stderr)
        return False

    print(f"[index_video] ✅ Pass 2 complete: enriched {len(enrichments)} tasks", file=sys.stderr)

    # Task 2.2: Flatten to CSV
    flatten_to_csv(
        video_url=source_url,
        metadata=metadata,
        dkd_ref=dkd_ref,
        structured_json=structured_json,
        enrichments=enrichments,
        transcript_segments=transcript_segments,
        out_path=out_path
    )
    print(f"[index_video] ✅ Saved {len(tasks)} rows to {out_path}", file=sys.stderr)

    return True


# ---------------------------------------------------------------------------
# Pass 1: Vision Model — Structured Hierarchy Extraction
# ---------------------------------------------------------------------------

PASS1_SYSTEM_PROMPT = """\
You are a video tutorial indexer. You receive frames (screenshots) from a \
tutorial video along with its transcript. Your job is to analyze the visual \
content and transcript to produce a structured hierarchical index.

Identify the logical TASKS shown in the video. Each task is a major goal or \
activity (e.g., "Create a new document", "Configure sharing settings"). \
Within each task, identify SUBTASKS (smaller logical steps within the task). \
Within each subtask, identify individual STEPS (atomic UI actions like \
"Click the Share button", "Enter email address").

CRITICAL RULES:
1. Timestamps must be INTEGER seconds (not strings, not ranges).
2. start_seconds and end_seconds must reflect WHEN that action appears in the video.
3. Timestamps must be monotonically increasing — each task starts after or at the previous one ends.
4. Use the frame timestamps provided to anchor your time estimates.
5. Every task, subtask, and step MUST have both start_seconds and end_seconds.
6. Return ONLY valid JSON — no markdown fences, no commentary.

Output this exact JSON structure:
{
  "video_summary": "One-sentence summary of what the entire video teaches",
  "tasks": [
    {
      "title": "Task title (action-oriented, e.g. 'Create a new Opal app')",
      "description": "What this task accomplishes",
      "start_seconds": 0,
      "end_seconds": 30,
      "subtasks": [
        {
          "title": "Subtask title",
          "start_seconds": 0,
          "end_seconds": 15,
          "steps": [
            {
              "title": "Step title (atomic action)",
              "description": "What the user does and sees",
              "start_seconds": 0,
              "end_seconds": 5
            }
          ]
        }
      ]
    }
  ]
}
"""


def _encode_frames_for_api(frames: list[dict], max_send: int = 60) -> list[dict]:
    """Base64-encode frame images for the OpenRouter multimodal API.

    Subsamples evenly if there are more frames than max_send.
    Returns a list of content parts (image_url objects with timestamps).
    """
    # Subsample if needed
    if len(frames) > max_send:
        step = len(frames) / max_send
        indices = [int(i * step) for i in range(max_send)]
        selected = [frames[i] for i in indices]
    else:
        selected = frames

    parts: list[dict] = []
    for frame in selected:
        frame_path = Path(frame["path"])
        if not frame_path.exists():
            continue
        b64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
        ts = frame["timestamp_seconds"]

        # Add a text label for the timestamp
        parts.append({
            "type": "text",
            "text": f"[Frame at t={int(ts)}s]",
        })
        # Add the image
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    return parts


def _extract_json_from_text(text: str) -> dict | None:
    """Try to extract valid JSON from model output that may contain markdown fences."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    if "```" in text:
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

    # Try to find the outermost { ... } block
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[brace_start : i + 1])
                    except json.JSONDecodeError:
                        break

    return None


def pass1_vision(
    frames: list[dict],
    transcript_text: str | None,
    model: str,
    api_key: str,
    debug_path: Path,
    max_attempts: int = 2,
) -> dict | None:
    """Send frames + transcript to a vision model and extract structured JSON.

    Returns the parsed JSON dict or None on failure.
    """
    print(f"[index_video] Pass 1: encoding {len(frames)} frames for vision model…", file=sys.stderr)

    # Build the user message content parts
    user_parts = _encode_frames_for_api(frames, max_send=60)
    print(f"[index_video] Encoded {len(user_parts) // 2} frames as base64", file=sys.stderr)

    # Add transcript if available
    if transcript_text:
        user_parts.append({
            "type": "text",
            "text": f"\n\n--- TRANSCRIPT ---\n{transcript_text}\n--- END TRANSCRIPT ---",
        })
        print(f"[index_video] Included transcript ({len(transcript_text)} chars)", file=sys.stderr)
    else:
        user_parts.append({
            "type": "text",
            "text": "\n\n(No transcript available — analyze frames only.)",
        })

    # Final instruction
    user_parts.append({
        "type": "text",
        "text": (
            "\n\nAnalyze all the frames and transcript above. "
            "Produce the hierarchical Task > Subtask > Step JSON as specified. "
            "Return ONLY the JSON object — no markdown fences, no extra text."
        ),
    })

    # Build the API request
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PASS1_SYSTEM_PROMPT},
            {"role": "user", "content": user_parts},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "index-video/1.0 (+claude-code; python-urllib)",
        "HTTP-Referer": "https://github.com/index-video",
    }

    context = ssl.create_default_context()
    body = json.dumps(payload).encode("utf-8")

    print(f"[index_video] Sending to {model} ({len(body) / 1024:.0f} KB payload)…", file=sys.stderr)

    for attempt in range(max_attempts):
        if attempt > 0:
            wait = 5 * attempt
            print(f"[index_video] Retry {attempt + 1}/{max_attempts} in {wait}s…", file=sys.stderr)
            time.sleep(wait)

        try:
            req = urllib.request.Request(
                OPENROUTER_CHAT_ENDPOINT,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120, context=context) as resp:
                raw_response = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            print(f"[index_video] ❌ HTTP {exc.code}: {error_body}", file=sys.stderr)
            if 400 <= exc.code < 500 and exc.code != 429:
                # Client error (not rate limit) — save debug and give up
                debug_path.write_text(json.dumps({
                    "error": f"HTTP {exc.code}",
                    "body": error_body,
                    "attempt": attempt + 1,
                }, indent=2))
                return None
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[index_video] ❌ Network error: {exc}", file=sys.stderr)
            continue

        # Parse the OpenRouter response envelope
        try:
            envelope = json.loads(raw_response)
        except json.JSONDecodeError:
            print(f"[index_video] ❌ Non-JSON response: {raw_response[:200]}", file=sys.stderr)
            debug_path.write_text(raw_response)
            continue

        # Save raw response for debugging
        debug_path.write_text(json.dumps(envelope, indent=2))

        # Extract the model's text output
        choices = envelope.get("choices", [])
        if not choices:
            print(f"[index_video] ❌ No choices in response", file=sys.stderr)
            continue

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            print(f"[index_video] ❌ Empty content in response", file=sys.stderr)
            continue

        print(f"[index_video] Got response ({len(content)} chars), parsing JSON…", file=sys.stderr)

        # Parse the structured JSON from the model's output
        result = _extract_json_from_text(content)
        if result is not None and "tasks" in result:
            # Save the clean parsed result alongside the raw debug
            debug_path.write_text(json.dumps({
                "raw_envelope": envelope,
                "parsed_structure": result,
            }, indent=2))
            return result

        print(f"[index_video] ⚠️ Could not parse valid JSON from response (attempt {attempt + 1})", file=sys.stderr)
        print(f"[index_video] Raw content preview: {content[:300]}", file=sys.stderr)

    print(f"[index_video] ❌ Pass 1 failed after {max_attempts} attempts", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Pipeline Step: Download + Extract Frames + Transcript
# ---------------------------------------------------------------------------

def validate_pass1_json(data: dict) -> list[str]:
    """Validate the Pass 1 JSON structure.
    
    Returns a list of error strings. Empty list means valid.
    """
    errors = []
    tasks = data.get("tasks", [])
    if not tasks:
        return ["No tasks found in JSON or tasks array is empty."]
        
    last_end = -1
    for t_idx, task in enumerate(tasks):
        title = task.get("title", f"Task {t_idx}")
        start = task.get("start_seconds")
        end = task.get("end_seconds")
        
        if start is None or end is None:
            errors.append(f"Task '{title}' missing start_seconds or end_seconds.")
            continue
            
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"Task '{title}' has non-integer timestamps.")
            
        if start < last_end:
            errors.append(f"Task '{title}' start ({start}s) is before previous end ({last_end}s).")
        last_end = end
        
        subtasks = task.get("subtasks", [])
        if not subtasks:
            errors.append(f"Task '{title}' has no subtasks.")
            continue
            
        last_sub_end = -1
        for st_idx, subtask in enumerate(subtasks):
            st_title = subtask.get("title", f"Subtask {st_idx}")
            st_start = subtask.get("start_seconds")
            st_end = subtask.get("end_seconds")
            
            if st_start is None or st_end is None:
                errors.append(f"Subtask '{st_title}' missing timestamps.")
                continue
                
            if st_start < last_sub_end:
                errors.append(f"Subtask '{st_title}' start ({st_start}s) overlaps previous subtask ({last_sub_end}s).")
            last_sub_end = st_end
            
            steps = subtask.get("steps", [])
            if not steps:
                errors.append(f"Subtask '{st_title}' has no steps.")
                
            last_step_end = -1
            for s_idx, step in enumerate(steps):
                s_title = step.get("title", f"Step {s_idx}")
                s_start = step.get("start_seconds")
                s_end = step.get("end_seconds")
                
                if s_start is None or s_end is None:
                    errors.append(f"Step '{s_title}' missing timestamps.")
                    continue
                
                if s_start < last_step_end:
                    errors.append(f"Step '{s_title}' start overlaps previous step.")
                last_step_end = s_end

    return errors


# ---------------------------------------------------------------------------
# Pass 2: Text Model — Metadata Enrichment
# ---------------------------------------------------------------------------

PASS2_SYSTEM_PROMPT = """\
You are an expert technical writer and video tutorial analyzer.
You will be provided with a structured hierarchical index of a video (Tasks, Subtasks, Steps) \
and its transcript.

For EACH task in the provided JSON, you must generate enrichment metadata.

Return ONLY a valid JSON array of enrichment objects, one for each task, in the exact same order.
Do not wrap in markdown or add commentary.

Each object in the array must have the following exact keys:
{
  "platform": "The software/tool being used (e.g., 'Opal', 'Google Docs', 'AWS')",
  "key_concepts": "A short, comma-separated list of 'How to...' concepts covered in this task",
  "faq_question": "A natural question a user might search for that this task answers",
  "faq_answer": "A concise 1-2 sentence answer to the faq_question based on the video",
  "difficulty_level": "beginner",  // Must be one of: beginner, intermediate, advanced
  "prerequisites": "What the user needs before starting this task (e.g., 'An active Opal account', 'None')"
}
"""

def pass2_enrichment(
    structured_json: dict,
    transcript_text: str | None,
    model: str,
    api_key: str,
    debug_path: Path,
    max_attempts: int = 2,
) -> list[dict] | None:
    """Send structured JSON + transcript to a text model for metadata enrichment."""
    print(f"[index_video] Pass 2: encoding enrichment request for text model…", file=sys.stderr)

    user_text = f"--- STRUCTURED JSON (PASS 1) ---\n{json.dumps(structured_json, indent=2)}\n"
    if transcript_text:
        user_text += f"\n--- TRANSCRIPT ---\n{transcript_text}\n"

    user_text += "\nGenerate the enrichment JSON array for these tasks."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PASS2_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "index-video/1.0",
        "HTTP-Referer": "https://github.com/index-video",
    }

    context = ssl.create_default_context()
    body = json.dumps(payload).encode("utf-8")

    print(f"[index_video] Sending to {model} ({len(body) / 1024:.0f} KB payload)…", file=sys.stderr)

    for attempt in range(max_attempts):
        if attempt > 0:
            wait = 5 * attempt
            print(f"[index_video] Retry {attempt + 1}/{max_attempts} in {wait}s…", file=sys.stderr)
            time.sleep(wait)

        try:
            req = urllib.request.Request(
                OPENROUTER_CHAT_ENDPOINT,
                data=body,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120, context=context) as resp:
                raw_response = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            print(f"[index_video] ❌ HTTP {exc.code}: {error_body}", file=sys.stderr)
            if 400 <= exc.code < 500 and exc.code != 429:
                debug_path.with_suffix("._debug_pass2.json").write_text(json.dumps({
                    "error": f"HTTP {exc.code}",
                    "body": error_body,
                    "attempt": attempt + 1,
                }, indent=2))
                return None
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[index_video] ❌ Network error: {exc}", file=sys.stderr)
            continue

        try:
            envelope = json.loads(raw_response)
        except json.JSONDecodeError:
            print(f"[index_video] ❌ Non-JSON response: {raw_response[:200]}", file=sys.stderr)
            debug_path.with_suffix("._debug_pass2.json").write_text(raw_response)
            continue

        debug_path.with_suffix("._debug_pass2.json").write_text(json.dumps(envelope, indent=2))

        choices = envelope.get("choices", [])
        if not choices:
            print(f"[index_video] ❌ No choices in response", file=sys.stderr)
            continue

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            print(f"[index_video] ❌ Empty content in response", file=sys.stderr)
            continue

        result = _extract_json_from_text(content)
        if isinstance(result, list):
            num_tasks = len(structured_json.get("tasks", []))
            if len(result) == num_tasks:
                return result
            else:
                print(f"[index_video] ⚠️ Got {len(result)} enrichment items, but expected {num_tasks} (attempt {attempt + 1})", file=sys.stderr)
        else:
            print(f"[index_video] ⚠️ Could not parse valid JSON array from response (attempt {attempt + 1})", file=sys.stderr)

    print(f"[index_video] ❌ Pass 2 failed after {max_attempts} attempts", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Pipeline Step: Flatten to CSV
# ---------------------------------------------------------------------------

def _get_transcript_chunk(segments: list[dict], start_s: int, end_s: int) -> str:
    """Get all transcript text that overlaps with [start_s, end_s]."""
    text = []
    for seg in segments:
        seg_s = seg["start"]
        seg_e = seg["end"]
        # Overlap condition: start <= seg_e and end >= seg_s
        if start_s <= seg_e and end_s >= seg_s:
            text.append(seg["text"])
    return " ".join(text)


def flatten_to_csv(
    video_url: str,
    metadata: dict,
    dkd_ref: str,
    structured_json: dict,
    enrichments: list[dict],
    transcript_segments: list[dict],
    out_path: Path
):
    """Convert the enriched hierarchical JSON into flat CSV rows matching YT_Video_Index."""
    info = metadata.get("info", {})
    video_id = info.get("id", "")
    video_title = info.get("title", "")
    # YT-dlp upload_date is often YYYYMMDD
    upload_date = info.get("upload_date", "")
    if len(upload_date) == 8:
        publish_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    else:
        publish_date = upload_date

    file_exists = out_path.exists()

    headers = [
        "video_url", "video_id", "video_title", "platform", "publish_date",
        "chapter_title", "timestamp_seconds", "url_with_timestamp", "transcript_chunk",
        "dkd_doc_ref", "key_concepts", "faq_question", "faq_answer",
        "difficulty_level", "prerequisites", "detailed_steps"
    ]

    tasks = structured_json.get("tasks", [])

    with open(out_path, "a" if file_exists else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()

        for idx, task in enumerate(tasks):
            enrichment = enrichments[idx] if idx < len(enrichments) else {}

            start_s = task.get("start_seconds", 0)
            end_s = task.get("end_seconds", 0)

            # Generate YouTube timestamp URL
            # e.g., https://youtu.be/CW4i6dRO_b8?t=45
            if "?" in video_url:
                url_with_ts = f"{video_url}&t={start_s}"
            else:
                url_with_ts = f"{video_url}?t={start_s}"

            row = {
                "video_url": video_url,
                "video_id": video_id,
                "video_title": video_title,
                "platform": enrichment.get("platform", ""),
                "publish_date": publish_date,
                "chapter_title": task.get("title", ""),
                "timestamp_seconds": start_s,
                "url_with_timestamp": url_with_ts,
                "transcript_chunk": _get_transcript_chunk(transcript_segments, start_s, end_s),
                "dkd_doc_ref": dkd_ref,
                "key_concepts": enrichment.get("key_concepts", ""),
                "faq_question": enrichment.get("faq_question", ""),
                "faq_answer": enrichment.get("faq_answer", ""),
                "difficulty_level": enrichment.get("difficulty_level", ""),
                "prerequisites": enrichment.get("prerequisites", ""),
                "detailed_steps": json.dumps(task.get("subtasks", []))
            }
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Pipeline Step: Download + Extract Frames + Transcript
# ---------------------------------------------------------------------------

def process_single_video(
    source: str,
    args: argparse.Namespace,
    api_key: str,
) -> tuple[list[dict], list[dict], str | None, dict] | None:
    """Download video, extract frames, and get transcript.

    Returns (frames, transcript_segments, transcript_text, metadata) or None on failure.
    """
    work = Path(tempfile.mkdtemp(prefix="index-video-"))
    print(f"[index_video] Working dir: {work}", file=sys.stderr)

    # Step 1: Download
    print(
        "[index_video] Downloading via yt-dlp…" if is_url(source) else "[index_video] Using local file…",
        file=sys.stderr,
    )
    try:
        dl = download(source, work / "download")
    except SystemExit as exc:
        print(f"[index_video] ❌ Download failed: {exc}", file=sys.stderr)
        return None
    video_path = dl["video_path"]
    info = dl.get("info") or {}
    print(f"[index_video] Downloaded: {info.get('title', 'unknown')} (id={info.get('id', '?')})", file=sys.stderr)

    # Step 2: Get metadata (duration)
    meta = get_metadata(video_path)
    duration = meta["duration_seconds"]
    print(f"[index_video] Duration: {duration:.1f}s", file=sys.stderr)

    # Step 3: Extract frames
    max_frames = min(args.max_frames, 100)
    fps, target = auto_fps(duration, max_frames=max_frames)
    print(f"[index_video] Extracting ~{target} frames at {fps:.3f} fps…", file=sys.stderr)

    frames = extract(
        video_path,
        work / "frames",
        fps=fps,
        resolution=512,
        max_frames=max_frames,
    )
    print(f"[index_video] Extracted {len(frames)} frames", file=sys.stderr)

    # Step 4: Get transcript (subtitles → Whisper fallback)
    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None

    # Try subtitle from download.py, then scan for auto-sub .vtt files
    # (yt-dlp names auto-subs like 'ID.en.vtt' which download.py's exact-stem
    # match misses)
    subtitle_path = dl.get("subtitle_path")
    if not subtitle_path:
        download_dir = work / "download"
        if download_dir.exists():
            vtt_files = sorted(download_dir.glob("*.vtt"))
            if vtt_files:
                subtitle_path = str(vtt_files[0])
                print(f"[index_video] Found auto-sub: {vtt_files[0].name}", file=sys.stderr)

    if subtitle_path:
        try:
            transcript_segments = parse_vtt(subtitle_path)
            transcript_text = format_transcript(transcript_segments)
            transcript_source = "captions"
        except Exception as exc:
            print(f"[index_video] Subtitle parse failed: {exc}", file=sys.stderr)

    if not transcript_segments:
        # Try Whisper fallback
        backend, whisper_key = load_api_key()
        if backend and whisper_key:
            try:
                transcript_segments, used_backend = transcribe_video(
                    video_path,
                    work / "audio.mp3",
                    backend=backend,
                    api_key=whisper_key,
                )
                transcript_text = format_transcript(transcript_segments)
                transcript_source = f"whisper ({used_backend})"
            except SystemExit as exc:
                print(f"[index_video] Whisper fallback failed: {exc}", file=sys.stderr)
        else:
            print("[index_video] No subtitles and no Whisper key — proceeding without transcript", file=sys.stderr)

    if transcript_source:
        print(f"[index_video] Transcript: {len(transcript_segments)} segments via {transcript_source}", file=sys.stderr)
    else:
        print("[index_video] Transcript: none available", file=sys.stderr)

    # Bundle metadata for downstream use
    metadata = {
        "video_path": video_path,
        "work_dir": str(work),
        "info": info,
        "duration_seconds": duration,
        "transcript_source": transcript_source,
    }

    return frames, transcript_segments, transcript_text, metadata


if __name__ == "__main__":
    raise SystemExit(main())
