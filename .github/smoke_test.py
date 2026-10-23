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
OTHER_AREA = 'crop=1280:400:0:0'  # a part of the frame that is never blurred
SHOWN = (2, 6)  # seconds during which the subtitle is visible
BEFORE, DURING, AFTER = 0.8, 4.0, 7.2  # sample times: no subtitle, subtitle, no subtitle


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
                f"fontsize=56:fontcolor=white:borderw=3:x=(w-text_w)/2:y=h-150:enable='between(t,{SHOWN[0]},{SHOWN[1]})'")
    run([ffmpeg, '-y', '-f', 'lavfi', '-i', 'color=c=0x303030:s=1280x720:r=25:d=8', '-vf', drawtext,
         '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv', str(path)])  # tagged like real videos


def psnr(ffmpeg: str, a: Path, b: Path, t: float, area: str) -> float:
    """PSNR between the same part of the same frame in two videos; infinity if identical."""
    out = run([ffmpeg, '-ss', str(t), '-i', str(a), '-ss', str(t), '-i', str(b), '-lavfi',
               f'[0:v]{area}[a];[1:v]{area}[b];[a][b]psnr', '-frames:v', '1', '-f', 'null', '-'])
    value = re.search(r'average:(\S+)', out)
    if not value:
        sys.exit('Could not measure the difference between the videos.')
    return float('inf') if value.group(1) == 'inf' else float(value.group(1))


def detail(ffmpeg: str, video: Path, t: float, area: str) -> float:
    """Amount of edges (text strokes) in part of a frame; 0 for a flat area."""
    out = run([ffmpeg, '-ss', str(t), '-i', str(video), '-lavfi',
               f'{area},format=gray,sobel=planes=1,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-',
               '-frames:v', '1', '-f', 'null', '-'])
    value = re.search(r'YAVG=(\S+)', out)
    if not value:
        sys.exit('Could not measure the amount of detail.')
    return float(value.group(1))


def check_blur(ffmpeg: str, video: Path, blurred: Path) -> None:
    """The subtitle must be unreadable while it is shown, and nothing may change before, after or elsewhere."""
    before, after = detail(ffmpeg, video, DURING, TEXT_AREA), detail(ffmpeg, blurred, DURING, TEXT_AREA)
    print(f'Detail in the subtitle area while the subtitle is shown: {before:.2f} before, {after:.2f} after blurring')
    if before < 1 or after > before * 0.2:
        sys.exit('The subtitle is not blurred enough: it should keep less than a fifth of its edges.')

    for t, area, what in ((BEFORE, TEXT_AREA, 'the subtitle area before the subtitle'), (AFTER, TEXT_AREA, 'the subtitle area after the subtitle'),
                          (DURING, OTHER_AREA, 'the rest of the frame')):
        value = psnr(ffmpeg, video, blurred, t, area)
        print(f'Difference in {what}: PSNR {value} dB')
        if value < 45:
            sys.exit(f'{what.capitalize()} was changed by the blurring (PSNR {value} dB, unchanged is above 45).')
    print('Blur check passed.')


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

        check_blur(ffmpeg, video, blurred)
    print('Full test passed.')


if __name__ == '__main__':
    main()
