#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from autoyy.state import file_fingerprint, load_state, recover_running, save_state, set_stage

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat
except ImportError as exc:
    raise SystemExit("Pillow is required: pip install -e .[cover-local]") from exc

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
PROMPT_NAME = "封面提示词-即梦.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build deterministic local topic covers from source video frames")
    parser.add_argument("root", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--ffprobe", type=Path)
    parser.add_argument("--title-font", type=Path, required=True)
    parser.add_argument("--subtitle-font", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def cover_text(folder: Path) -> tuple[str, str]:
    prompt = folder / PROMPT_NAME
    if not prompt.is_file():
        raise ValueError("missing explicit cover prompt")
    text = prompt.read_text(encoding="utf-8")
    main = re.search(r"主标题[「“\"]([^」”\"]+)[」”\"]", text)
    sub = re.search(r"副标题[「“\"]([^」”\"]+)[」”\"]", text)
    if not main or not sub:
        raise ValueError("cover prompt does not contain explicit 主标题/副标题")
    if len(main.group(1)) != 6 or len(sub.group(1)) != 8:
        raise ValueError("cover title lengths must be exactly 6+8")
    return main.group(1), sub.group(1)


def choose_video(folder: Path) -> Path:
    videos = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stat().st_size > 0]
    if not videos:
        raise FileNotFoundError("no non-empty source video")
    return max(videos, key=lambda p: p.stat().st_size)


def duration_seconds(ffprobe: Path, video: Path, timeout: int) -> float:
    proc = subprocess.run([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)], capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:240]}")
    try:
        value = float(json.loads(proc.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid ffprobe duration") from exc
    if value < 2:
        raise RuntimeError("source video is too short for frame selection")
    return value


def frame_score(path: Path) -> float:
    with Image.open(path) as image:
        gray = ImageOps.grayscale(image.convert("RGB").resize((320, 180)))
    stat = ImageStat.Stat(gray)
    mean = stat.mean[0]
    return stat.stddev[0] * 1.7 + gray.entropy() * 7 + ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0] - abs(mean - 112) * 0.2


def extract_best_frame(ffmpeg: Path, ffprobe: Path, video: Path, temp_dir: Path, timeout: int) -> Image.Image:
    duration = duration_seconds(ffprobe, video, timeout)
    candidates: list[tuple[float, Path]] = []
    for index, fraction in enumerate((0.14, 0.26, 0.38, 0.50, 0.62, 0.74, 0.86)):
        output = temp_dir / f"frame-{index}.jpg"
        timestamp = max(1.0, min(duration - 1.0, duration * fraction))
        proc = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=1920:-2", "-q:v", "2", "-y", str(output)], capture_output=True, timeout=timeout, check=False)
        if proc.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            candidates.append((frame_score(output), output))
    if not candidates:
        raise RuntimeError("ffmpeg could not extract any usable frame")
    best = max(candidates, key=lambda item: item[0])[1]
    with Image.open(best) as image:
        return image.convert("RGB").copy()


def fit_background(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
    image = ImageEnhance.Color(image).enhance(0.84)
    image = ImageEnhance.Contrast(image).enhance(1.16)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    alpha = Image.new("L", (1, size[1]), 0)
    pixels = alpha.load()
    for y in range(size[1]):
        t = y / size[1]
        pixels[0, y] = int(125 * max(0.0, 1.0 - t / 0.52) ** 2)
    overlay.putalpha(alpha.resize(size))
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def render_text(image: Image.Image, text: str, font_path: Path, y: int, size: int, max_width: int, fill: tuple[int, int, int, int]) -> int:
    draw = ImageDraw.Draw(image)
    while size >= 36:
        font = ImageFont.truetype(str(font_path), size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=max(2, size // 65))
        if box[2] - box[0] <= max_width:
            break
        size -= 2
    else:
        raise RuntimeError("font cannot fit requested title")
    for char in text:
        if char.strip() and font.getmask(char).getbbox() is None:
            raise RuntimeError(f"font cannot render requested character: {char!r}")
    width, height = box[2] - box[0], box[3] - box[1]
    x = (image.width - width) // 2 - box[0]
    baseline = y - box[1]
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((x + 8, baseline + 8), text, font=font, fill=(0, 0, 0, 235), stroke_width=5, stroke_fill=(0, 0, 0, 245))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)))
    draw.text((x, baseline), text, font=font, fill=fill, stroke_width=4, stroke_fill=(8, 8, 8, 255))
    return y + height


def build_cover(source: Image.Image, size: tuple[int, int], main: str, sub: str, title_font: Path, subtitle_font: Path) -> Image.Image:
    image = fit_background(source, size)
    bottom = render_text(image, main, title_font, int(size[1] * 0.055), int(size[1] * 0.155), int(size[0] * 0.92), (255, 205, 0, 255))
    render_text(image, sub, subtitle_font, bottom + int(size[1] * 0.025), int(size[1] * 0.075), int(size[0] * 0.70), (248, 248, 248, 255))
    return image.convert("RGB")


def main() -> int:
    args = parse_args()
    ffprobe = args.ffprobe or args.ffmpeg.with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    for path in (args.ffmpeg, ffprobe, args.title_font, args.subtitle_font):
        if not path.is_file():
            print(f"required file missing: {path}", file=sys.stderr)
            return 2
    if not args.root.is_dir():
        print(f"root missing: {args.root}", file=sys.stderr)
        return 2
    folders = sorted(p for p in args.root.iterdir() if p.is_dir() and re.match(r"^\d{2}-", p.name))
    if args.limit:
        folders = folders[: args.limit]
    if not folders:
        print("no topic directories", file=sys.stderr)
        return 2
    try:
        state = load_state(args.root.resolve(), create=True)
        recover_running(state)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    failures = 0
    for index, folder in enumerate(folders, 1):
        portrait = folder / "封面-3比4.png"
        landscape = folder / "封面-4比3.png"
        portrait_ok = portrait.is_file() and portrait.stat().st_size > 0
        landscape_ok = landscape.is_file() and landscape.stat().st_size > 0
        if not args.force and portrait_ok and landscape_ok:
            fingerprint = file_fingerprint(portrait) + ":" + file_fingerprint(landscape)
            set_stage(state, folder.name, "cover", "ready", fingerprint=fingerprint)
            print(f"[{index}/{len(folders)}] SKIP {folder.name}")
            continue
        if not args.force and (portrait.exists() or landscape.exists()):
            failures += 1
            reason = "partial/existing cover output present; use --force to replace"
            set_stage(state, folder.name, "cover", "failed", reason=reason)
            print(f"[{index}/{len(folders)}] FAIL {folder.name}: {reason}", file=sys.stderr)
            continue
        try:
            main_text, sub_text = cover_text(folder)
            video = choose_video(folder)
            with tempfile.TemporaryDirectory(prefix="autoyy-cover-") as tmp:
                source = extract_best_frame(args.ffmpeg, ffprobe, video, Path(tmp), args.timeout)
            outputs = [
                (portrait, (1200, 1600), build_cover(source, (1200, 1600), main_text, sub_text, args.title_font, args.subtitle_font)),
                (landscape, (1600, 1200), build_cover(source, (1600, 1200), main_text, sub_text, args.title_font, args.subtitle_font)),
            ]
            staged: list[tuple[Path, Path]] = []
            try:
                for output, expected_size, image in outputs:
                    tmp_output = output.with_name(f"{output.stem}.tmp-{os.getpid()}.png")
                    image.save(tmp_output, format="PNG", optimize=True)
                    with Image.open(tmp_output) as check:
                        if check.size != expected_size:
                            raise RuntimeError(f"unexpected output dimensions: {check.size}")
                    staged.append((tmp_output, output))
                for tmp_output, output in staged:
                    tmp_output.replace(output)
            finally:
                for tmp_output, _ in staged:
                    if tmp_output.exists():
                        tmp_output.unlink()
            fingerprint = file_fingerprint(portrait) + ":" + file_fingerprint(landscape)
            set_stage(state, folder.name, "cover", "ready", fingerprint=fingerprint)
            print(f"[{index}/{len(folders)}] OK {folder.name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            set_stage(state, folder.name, "cover", "failed", reason=str(exc)[:300])
            print(f"[{index}/{len(folders)}] FAIL {folder.name}: {exc}", file=sys.stderr)
    save_state(args.root.resolve(), state)
    print(f"summary total={len(folders)} failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
