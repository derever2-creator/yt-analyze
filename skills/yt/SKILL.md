---
name: yt
description: Analyze a YouTube video - fetch its metadata, transcript (captions, or local whisper when there are none) and up to 60 scene-change key frames, then read them. Use when the user gives a YouTube URL and asks to summarize, analyze, critique, compare, or copy the structure / editing / visual style of a video ("이 영상 분석해줘", "요약해줘", "편집 스타일 봐줘", "대본 구조 뜯어줘"). NOT for downloading videos for the user to keep.
---

# /yt <url> [options]

Pull a YouTube video apart into text and pictures, then analyze what the user asked for.

## 1. Run the fetcher

```
python "<this skill dir>/yt_analyze.py" <url> --out "<scratchpad>/yt/<video id>" [--frames 60] [--width 640] [--grid]
```

- Use the venv beside the script if it exists: `<this skill dir>/.venv/Scripts/python.exe` (Windows) or `.venv/bin/python`.
- First run with no venv: `python -m venv .venv` inside the skill dir, then `pip install -r requirements.txt`. ffmpeg must be on PATH (`winget install Gyan.FFmpeg` / `brew install ffmpeg` / `apt install ffmpeg`).
- Output folder: `meta.json`, `transcript.txt`, `frames/*.jpg`, `INDEX.md` (and `grid_N.jpg` with `--grid`). The script prints the INDEX.md path on success.
- A video with no captions at all falls back to faster-whisper on CPU; the first run downloads the model (about 500 MB for `small`). Pass `--no-whisper` to skip, `--force-whisper` when the auto captions are too poor to use.
- ffmpeg installed by winget lands in `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\...\bin`; a shell opened before the install does not see it. Prepend that folder to PATH for the call instead of reinstalling.

## 2. Choose what to read by what was asked

| Ask | Read |
|---|---|
| summary, argument, script structure, hooks, claims | `INDEX.md` + `transcript.txt` only. Zero frames. |
| editing rhythm, pacing, scene count | `--grid`: read the contact sheets, not the frames. 60 frames cost 3 images. |
| visual style, thumbnails, captions design, composition | the frames, up to the budget below. |

## 3. Token budget

Image cost is about width x height / 750 tokens per image. INDEX.md prints the estimate.

- Default: 60 frames at 640 px, about 18k tokens when every frame is read. Do not exceed this without asking.
- Long video (over 20 min) or a quick look: `--frames 20`, or `--grid` and read only the grids.
- Never read frames the question does not need. Transcript first, frames second.

## 4. Report

Lead with the answer to what was asked. Cite timestamps from the transcript ([mm:ss]) and name the frame file when a picture is the evidence. Say which transcript source was used (manual captions, auto captions, whisper) because auto captions and whisper misspell names and numbers.

## Traps

- Auto captions repeat rolling lines; the parser drops any line already emitted within the last four cues. A chorus or a chant repeated back to back is therefore collapsed to one line, and counts of "how many times X was said" are unreliable.
- Scene detection returns few cuts on static talking-head videos; the script fills with uniform samples, so frames are not all "cuts".
- Age-restricted or members-only videos fail at download. Report it, do not retry with cookies unless the user asks.
- Shorts URLs (`youtube.com/shorts/ID`) work as-is.
