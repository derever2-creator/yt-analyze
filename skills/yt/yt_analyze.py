"""Fetch a YouTube video's metadata, transcript and key frames for analysis.

Output folder layout:
  meta.json        title, channel, duration, upload date, view count, description
  transcript.txt   "[mm:ss] text" lines (subs > auto subs > whisper)
  frames/NNN_mmss.jpg   key frames, scaled to --width
  grid_N.jpg       optional contact sheets (--grid)
  INDEX.md         what was produced and the estimated image token cost
"""

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yt_dlp

TOKENS_PER_PIXEL = 1 / 750  # Anthropic image token estimate


def log(msg):
    print(f"[yt] {msg}", file=sys.stderr, flush=True)


def download(url, out, height):
    sub_langs = ["ko", "en", "ko-orig", "en-orig"]
    opts = {
        "outtmpl": str(out / "video.%(ext)s"),
        "format": f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}]/b",
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": sub_langs,
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    meta = {k: info.get(k) for k in (
        "id", "title", "channel", "uploader", "duration", "upload_date",
        "view_count", "like_count", "description", "webpage_url", "width", "height",
    )}
    meta["subtitles_manual"] = list((info.get("subtitles") or {}).keys())
    meta["subtitles_auto"] = list((info.get("automatic_captions") or {}).keys())
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    videos = sorted(out.glob("video.*"))
    videos = [v for v in videos if v.suffix not in (".vtt", ".json")]
    if not videos:
        raise SystemExit("download produced no video file")
    return meta, videos[0]


def fmt_ts(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def parse_vtt(path):
    """Collapse VTT cues (auto captions repeat rolling lines) into [mm:ss] text."""
    text = path.read_text("utf-8", errors="replace")
    # YouTube auto captions roll: each cue repeats the previous line and tags only the
    # new words with <00:00:04.799><c>word</c>. Keep tagged lines only in that case.
    tagged = "<c>" in text
    timing = re.compile(r"(\d+):(\d+):(\d+)\.\d+\s+-->")
    cues, cur = [], None
    for line in text.splitlines():
        m = timing.match(line)
        if m:
            cur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)), [])
            cues.append(cur)
        elif cur is not None:
            cur[1].append(line)
    lines = []
    last = ""
    for start, body_lines in cues:
        if tagged:
            body_lines = [l for l in body_lines if "<c>" in l]
        body = " ".join(re.sub(r"<[^>]+>", "", l).strip() for l in body_lines)
        body = " ".join(body.split())
        if not body or body == last or (last and body in last):
            continue
        if last and last in body:
            body_new = body.replace(last, "", 1).strip()
            if not body_new:
                continue
            lines.append(f"[{fmt_ts(start)}] {body_new}")
        else:
            lines.append(f"[{fmt_ts(start)}] {body}")
        last = body
    return lines


def pick_vtt(out):
    vtts = sorted(out.glob("video.*.vtt"))
    if not vtts:
        return None, None
    # prefer Korean, then English, manual before auto (yt-dlp names them identically,
    # so manual-vs-auto is only recorded in meta.json)
    for lang in ("ko", "en"):
        for v in vtts:
            if f".{lang}." in v.name or v.name.endswith(f".{lang}.vtt") or f".{lang}-orig." in v.name:
                return v, lang
    return vtts[0], vtts[0].name.split(".")[-2]


def whisper_transcribe(video, out, model_name):
    from faster_whisper import WhisperModel
    wav = out / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    log(f"whisper model={model_name} (first run downloads the model)")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav), vad_filter=True)
    lines = [f"[{fmt_ts(s.start)}] {s.text.strip()}" for s in segments if s.text.strip()]
    wav.unlink(missing_ok=True)
    return lines, f"whisper:{model_name}:{info.language}"


def scene_times(video, threshold):
    """Timestamps where ffmpeg's scene score exceeds threshold."""
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "info", "-i", str(video),
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace")
    times = [float(t) for t in re.findall(r"pts_time:\s*([\d.]+)", proc.stderr)]
    return times


def choose_times(scenes, duration, n):
    """Evenly thin scene cuts to n; fill from a uniform grid when there are too few."""
    scenes = sorted(set(round(t, 2) for t in scenes if t < duration))
    if len(scenes) >= n:
        idx = [round(i * (len(scenes) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
        return [scenes[i] for i in idx]
    uniform = [duration * (i + 0.5) / n for i in range(n)]
    chosen = list(scenes)
    for t in uniform:
        if len(chosen) >= n:
            break
        if all(abs(t - c) > 1.0 for c in chosen):
            chosen.append(round(t, 2))
    return sorted(chosen)[:n]


def fit_width(meta, n, budget):
    """Largest frame width (multiple of 16, max 640) so n frames stay under budget tokens."""
    if n <= 0:
        return 640
    aspect = (meta.get("width") or 16) / (meta.get("height") or 9)
    area = budget / n / TOKENS_PER_PIXEL
    return max(160, min(640, int(math.sqrt(area * aspect)) // 16 * 16))


def extract_frames(video, times, out, width):
    fdir = out / "frames"
    fdir.mkdir(exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        p = fdir / f"{i:03d}_{fmt_ts(t).replace(':', '')}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(p)], check=True)
        paths.append((t, p))
    return paths


def make_grids(frames, out, cols, rows):
    from PIL import Image, ImageDraw
    per = cols * rows
    grids = []
    for g in range(math.ceil(len(frames) / per)):
        chunk = frames[g * per:(g + 1) * per]
        w, h = Image.open(chunk[0][1]).size
        tw, th = w // 2, h // 2
        sheet = Image.new("RGB", (cols * tw, rows * th), "black")
        draw = ImageDraw.Draw(sheet)
        for k, (t, p) in enumerate(chunk):
            im = Image.open(p).resize((tw, th))
            x, y = (k % cols) * tw, (k // cols) * th
            sheet.paste(im, (x, y))
            draw.rectangle([x, y, x + 46, y + 14], fill="black")
            draw.text((x + 2, y + 1), fmt_ts(t), fill="white")
        gp = out / f"grid_{g + 1}.jpg"
        sheet.save(gp, quality=85)
        grids.append(gp)
    return grids


def img_tokens(path):
    from PIL import Image
    w, h = Image.open(path).size
    return round(w * h * TOKENS_PER_PIXEL)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--frames", type=int, default=60, help="key frames to keep (0 = none)")
    ap.add_argument("--width", type=int, default=None,
                    help="frame width in px (default: largest that fits --budget, max 640)")
    ap.add_argument("--budget", type=int, default=20000,
                    help="token budget for all frames together; sets the default width")
    ap.add_argument("--height", type=int, default=480, help="max download height")
    ap.add_argument("--scene", type=float, default=0.3, help="ffmpeg scene-change threshold")
    ap.add_argument("--grid", action="store_true", help="also build 4x5 contact sheets")
    ap.add_argument("--whisper", default="small", help="faster-whisper model when no captions")
    ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--force-whisper", action="store_true", help="ignore captions, transcribe locally")
    ap.add_argument("--keep-video", action="store_true")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not on PATH")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    log("downloading")
    meta, video = download(a.url, out, a.height)
    duration = float(meta.get("duration") or 0)

    vtt, lang = (None, None) if a.force_whisper else pick_vtt(out)
    if vtt:
        lines = parse_vtt(vtt)
        source = f"captions:{lang}:{'manual' if lang in meta['subtitles_manual'] else 'auto'}"
    elif a.no_whisper:
        lines, source = [], "none"
    else:
        lines, source = whisper_transcribe(video, out, a.whisper)
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", "utf-8")
    log(f"transcript {len(lines)} lines from {source}")

    frames, grids = [], []
    width = a.width or fit_width(meta, a.frames, a.budget)
    if a.frames > 0 and duration > 0:
        log(f"scene detection, width {width}")
        times = choose_times(scene_times(video, a.scene), duration, a.frames)
        frames = extract_frames(video, times, out, width)
        if a.grid:
            grids = make_grids(frames, out, 4, 5)
        log(f"{len(frames)} frames, {len(grids)} grids")

    if not a.keep_video:
        video.unlink(missing_ok=True)
    for v in out.glob("video.*.vtt"):
        v.unlink()

    frame_tok = sum(img_tokens(p) for _, p in frames)
    grid_tok = sum(img_tokens(p) for p in grids)
    idx = [
        f"# {meta['title']}",
        "",
        f"- channel: {meta.get('channel') or meta.get('uploader')}",
        f"- duration: {fmt_ts(duration)}  upload: {meta.get('upload_date')}  views: {meta.get('view_count')}",
        f"- transcript: {len(lines)} lines, source {source}",
        f"- frames: {len(frames)} @ {width}px, est. {frame_tok} tokens if all are read (budget {a.budget})",
        f"- grids: {len(grids)}, est. {grid_tok} tokens",
        "",
        "## frames",
    ] + [f"- {p.name}  t={fmt_ts(t)}" for t, p in frames]
    (out / "INDEX.md").write_text("\n".join(idx) + "\n", "utf-8")
    print(str(out / "INDEX.md"))


if __name__ == "__main__":
    main()
