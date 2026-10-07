# yt-analyze

Let Claude Code (or any agent that can read files and images) analyze a YouTube video.

`/yt <url>` fetches three things into a folder and the agent reads them:

| Layer | File | How |
|---|---|---|
| metadata | `meta.json` | yt-dlp |
| transcript | `transcript.txt` as `[mm:ss] text` | manual captions, else YouTube auto captions, else local [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| key frames | `frames/NNN_mmss.jpg`, optional `grid_N.jpg` | ffmpeg scene-change detection, filled with uniform samples, scaled to 640 px |

The frame count is the token knob. 60 frames at 640 px is about 18k tokens if the agent reads them all; `--grid` packs 20 frames into one contact sheet.

## Install

Requirements: Python 3.10+, ffmpeg on PATH.

```
git clone https://github.com/derever2/yt-analyze
cd yt-analyze/skills/yt
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # macOS / Linux
```

Then make the skill visible to Claude Code, either way:

- **Plugin**: `claude plugin install <path or git URL of this repo>` (see `.claude-plugin/plugin.json`)
- **Copy**: copy `skills/yt` into `~/.claude/skills/yt` (user-wide) or `<project>/.claude/skills/yt`

## Use

```
/yt https://www.youtube.com/watch?v=VIDEO_ID
/yt https://youtu.be/VIDEO_ID --frames 20
/yt https://www.youtube.com/shorts/VIDEO_ID --grid
```

Then ask: "요약해줘", "대본 구조 뜯어줘", "편집 리듬 봐줘", "썸네일이랑 자막 디자인 분석해줘".

The script also works without an agent:

```
python skills/yt/yt_analyze.py <url> --out out/VIDEO_ID --frames 60 --width 640 --grid
```

Options: `--frames N` (default 60, 0 = none), `--budget TOKENS` (20000, picks the frame width from the aspect ratio: 640 px for 16:9, 576 for 4:3, 368 for Shorts), `--width PX` (overrides the budget), `--height PX` max download height (480), `--scene 0.3` cut threshold, `--grid` 4x5 sheets, `--whisper small|base|medium`, `--no-whisper`, `--force-whisper` (ignore captions, useful when auto captions are poor), `--keep-video`.

## Notes

- Nothing leaves your machine except the YouTube download. Whisper runs on CPU.
- Auto captions misspell names and numbers; the transcript header in `INDEX.md` says which source was used.
- Age-restricted and members-only videos fail at download.

MIT
