#!/usr/bin/env python3
"""Blur hardcoded subtitles in a video using the JSON written by `videocr-cli --boxes_output`.

Builds one ffmpeg filtergraph: a black mask video gets a white box drawn for each
subtitle line during its time window, and the blurred copy of the video is merged
into the original through that mask. Audio is copied untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any


def probe(video: str) -> tuple[float, str]:
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate:format=duration',
         '-of', 'json', video],
        check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    return float(data['format']['duration']), data['streams'][0]['r_frame_rate']


def build_filter(data: dict[str, Any], fps: str, duration: float, padding: int, time_padding: float, blur: int, time_end: float | None) -> str:
    width, height = data['video']['width'], data['video']['height']
    # Boxes are in display pixels; with a non-square pixel aspect ratio ffmpeg's filters see coded pixels.
    sar = data['video'].get('sample_aspect_ratio') or 1.0
    if sar != 1:
        print(f"Warning: sample aspect ratio is {sar}, boxes are not rescaled and may be misplaced.", file=sys.stderr)

    boxes: list[tuple[float, float, int, int, int, int]] = []
    for sub in data['subtitles']:
        if time_end is not None and sub['start'] >= time_end:
            continue
        for x1, y1, x2, y2 in sub['boxes']:
            boxes.append((max(0.0, sub['start'] - time_padding), sub['end'] + time_padding, max(0, x1 - padding), max(0, y1 - padding),
                          min(width, x2 + padding), min(height, y2 + padding)))
    if not boxes:
        raise SystemExit("No subtitle boxes to blur.")

    # Only the strip containing every box is processed, aligned to even sizes for 4:2:0 chroma.
    bx1 = min(b[2] for b in boxes) & ~1
    by1 = min(b[3] for b in boxes) & ~1
    bx2 = min(width, (max(b[4] for b in boxes) + 1) & ~1)
    by2 = min(height, (max(b[5] for b in boxes) + 1) & ~1)
    bw, bh = bx2 - bx1, by2 - by1

    mask = [f"color=c=black:s={bw}x{bh}:r={fps}:d={duration:.3f},format=gray"]
    for start, end, x1, y1, x2, y2 in boxes:
        mask.append(
            f"drawbox=x={x1 - bx1}:y={y1 - by1}:w={x2 - x1}:h={y2 - y1}:color=white:t=fill:"
            f"enable='between(t,{start},{end})'")
    # drawbox's white is limited-range (235), so snap the mask to a full 0/255. It is applied as an alpha channel
    # because alpha is never rescaled; as a colour plane, newer ffmpeg converts its range like the video's
    mask.append("lut=y='if(gte(val,128),255,0)'[mask]")

    return ';'.join([
        "[0:v]split=2[full][src]",
        f"[src]crop={bw}:{bh}:{bx1}:{by1},gblur=sigma={blur}:steps=3[blurred]",
        ','.join(mask),
        "[blurred][mask]alphamerge[strip]",
        f"[full][strip]overlay={bx1}:{by1}:eof_action=pass[out]",
    ])


def graph_option(path: str) -> list[str]:
    """Read the filter graph from a file: ffmpeg 7 renamed -filter_complex_script to -/filter_complex, and 8 removed the old name."""
    out = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True).stdout
    match = re.search(r'version\D*(\d+)\.', out)
    if match and int(match.group(1)) < 7:
        return ['-filter_complex_script', path]
    return ['-/filter_complex', path]


def nvenc_works() -> bool:
    """True if ffmpeg can encode with the NVIDIA GPU (the encoder can be listed without a usable GPU, so try it)."""
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.1', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
                            capture_output=True)
    return result.returncode == 0


def blur_video(video: str, boxes_path: str, output: str | None = None, padding: int = 8, time_padding: float = 0.1, blur: int = 40,
               time_end: float | None = None, crf: int = 18, preset: str = 'veryfast', encoder: str = 'auto') -> str:
    with open(boxes_path, encoding='utf-8') as f:
        data = json.load(f)

    duration, fps = probe(video)
    if time_end is not None:
        duration = min(duration, time_end)
    output = output or os.path.splitext(video)[0] + '_blurred.mp4'

    graph = build_filter(data, fps, duration, padding, time_padding, blur, time_end)

    # The graph is long (one drawbox per line), so pass it via a file instead of the command line.
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as gf:
        gf.write(graph)
        graph_path = gf.name

    x264 = ['-c:v', 'libx264', '-crf', str(crf), '-preset', preset]
    nvenc = ['-c:v', 'h264_nvenc', '-rc', 'vbr', '-cq', str(crf + 1), '-b:v', '0', '-preset', 'p5']
    use_nvenc = encoder == 'nvenc' or (encoder == 'auto' and nvenc_works())
    starts = [sub['start'] for sub in data['subtitles'] if time_end is None or sub['start'] < time_end]

    def encode(video_args: list[str]) -> int:
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-filter_complex_threads', str(os.cpu_count() or 1), '-progress', 'pipe:1', '-y', '-i', video, *graph_option(graph_path),
               '-map', '[out]', '-map', '0:a?', *video_args, '-pix_fmt', 'yuv420p', '-c:a', 'copy']
        if time_end is not None:
            cmd += ['-t', str(time_end)]
        cmd.append(output)
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                key, _, value = line.strip().partition('=')
                if key == 'out_time_us' and value.lstrip('-').isdigit():
                    t = int(value) / 1_000_000
                    current = sum(1 for start in starts if start <= t)
                    print(f"\rBlurring subtitle {current}/{len(starts)} (video time {int(t // 60):02d}:{int(t % 60):02d} / {int(duration // 60):02d}:{int(duration % 60):02d})", end='', flush=True)
        print()
        return proc.returncode

    try:
        returncode = encode(nvenc if use_nvenc else x264)
        if returncode and use_nvenc:
            print('GPU encoding failed, retrying on the CPU.')
            returncode = encode(x264)
        if returncode:
            raise subprocess.CalledProcessError(returncode, 'ffmpeg')
    finally:
        os.remove(graph_path)
    print(f"Saved {output}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video')
    parser.add_argument('boxes', help='JSON file from videocr-cli --boxes_output')
    parser.add_argument('-o', '--output', help='Output file (default: <video>_blurred.mp4 next to the input)')
    parser.add_argument('--padding', type=int, default=8, help='Extra pixels around each box (default: 8)')
    parser.add_argument('--time_padding', type=float, default=0.1, help='Seconds to show each box before and after its subtitle (default: 0.1)')
    parser.add_argument('--blur', type=int, default=40, help='Gaussian blur sigma, higher is stronger (default: 40)')
    parser.add_argument('--time_end', type=float, default=None, help='Only process the first N seconds (useful for testing)')
    parser.add_argument('--crf', type=int, default=18, help='x264 quality (default: 18)')
    parser.add_argument('--preset', default='veryfast', help='x264 preset (default: veryfast)')
    parser.add_argument('--encoder', choices=['auto', 'cpu', 'nvenc'], default='auto', help='auto uses the NVIDIA GPU encoder if it works, otherwise x264 on the CPU (default: auto)')
    args = parser.parse_args()
    blur_video(args.video, args.boxes, args.output, args.padding, args.time_padding, args.blur, args.time_end, args.crf, args.preset, args.encoder)


if __name__ == '__main__':
    main()
