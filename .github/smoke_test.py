"""Checks a finished build: the programs start, and with --full the whole pipeline runs on a generated test video.

    python smoke_test.py BUILD_FOLDER [--full]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS = sys.platform == 'win32'
EXE = '.exe' if WINDOWS else ''
CLI = 'videocr-cli.exe' if WINDOWS else 'videocr-cli.bin'
FONTS = ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/dejavu/DejaVuSans.ttf', '/usr/share/fonts/TTF/DejaVuSans.ttf']
TEXT_AREA = 'crop=880:140:200:540'  # where the generated subtitle sits in the 1280x720 test video


def run(cmd: list[str], timeout: int = 1800) -> str:
    print('$', ' '.join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + result.stderr
    if result.returncode:
        print(output)
        sys.exit(f'FAILED (exit code {result.returncode}): {cmd[0]}')
    return output


def make_test_video(ffmpeg: str, path: Path) -> None:
    font = next((f for f in FONTS if os.path.isfile(f)), None)
    if font is None:
        sys.exit('No font found for generating the test video.')
    drawtext = (f"drawtext=fontfile='{font.replace(':', chr(92) + ':')}':text='Hello world this is a test':"
                'fontsize=56:fontcolor=white:borderw=3:x=(w-text_w)/2:y=h-150')
    run([ffmpeg, '-y', '-f', 'lavfi', '-i', 'color=c=0x303030:s=1280x720:r=25:d=8', '-vf', drawtext,
         '-pix_fmt', 'yuv420p', str(path)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--full', action='store_true', help='Also run the whole pipeline on a generated video')
    args = parser.parse_args()

    folder = Path(args.folder).resolve()
    auto_blur, cli = str(folder / f'auto-blur{EXE}'), str(folder / CLI)
    ffmpeg, ffprobe = str(folder / f'ffmpeg{EXE}'), str(folder / f'ffprobe{EXE}')
    for program in (auto_blur, cli, ffmpeg, ffprobe):
        if not os.path.isfile(program):
            sys.exit(f'Missing in the build: {os.path.basename(program)}')

    run([auto_blur, '--help'])
    if '--boxes_output' not in run([cli, '--help']):
        sys.exit('videocr-cli has no --boxes_output option')
    run([ffmpeg, '-version'])
    run([ffprobe, '-version'])
    if not args.full:
        print('Programs start correctly.')
        return

    with tempfile.TemporaryDirectory() as tmp:
        video, out = Path(tmp) / 'test.mp4', Path(tmp) / 'out'
        make_test_video(ffmpeg, video)
        print(run([auto_blur, str(video), '--lang', 'en', '--output_dir', str(out)]).replace('\r', '\n')[-1500:])

        srt = (out / 'test.srt').read_text(encoding='utf-8')
        print('Subtitles found:', srt.strip(), sep='\n')
        if not re.search('hello|world', srt, re.IGNORECASE):
            sys.exit('The expected text was not found in the subtitles.')

        blurred = out / 'test_blurred.mp4'
        if not blurred.is_file() or blurred.stat().st_size == 0:
            sys.exit('The blurred video was not created.')

        # The text area must differ strongly from the original; encoding noise alone would give a PSNR above ~40 dB
        psnr = run([ffmpeg, '-i', str(video), '-i', str(blurred), '-lavfi',
                    f'[0:v]{TEXT_AREA}[a];[1:v]{TEXT_AREA}[b];[a][b]psnr', '-f', 'null', '-'])
        match = re.search(r'average:(\S+)', psnr)
        if not match or match.group(1) == 'inf' or float(match.group(1)) > 35:
            sys.exit(f'The subtitle area does not look blurred (PSNR {match.group(1) if match else "unknown"}).')
        print(f'Blur check passed (PSNR {match.group(1)} dB in the subtitle area).')
    print('Full test passed.')


if __name__ == '__main__':
    main()
