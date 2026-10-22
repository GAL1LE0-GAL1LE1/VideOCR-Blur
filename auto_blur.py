#!/usr/bin/env python3
# nuitka-project: --standalone
# nuitka-project: --no-prefer-source-code
# nuitka-project: --assume-yes-for-downloads
# nuitka-project: --enable-plugin=tk-inter
# nuitka-project: --output-filename=auto-blur
# nuitka-project: --windows-console-mode=attach
"""Generate subtitles for a video with VideOCR and a copy of it with the subtitles blurred.

    auto_blur.py                                   open the window
    auto_blur.py VIDEO --lang LANG [--output_dir DIR] [--time_end SECONDS] [--cli_dir DIR]

Writes to the output folder (default: next to the video): <name>.srt and <name>_blurred.mp4.
Needs a videocr-cli build that supports --boxes_output (in the same folder as this program, in a
videocr-cli-* folder next to it, or pass --cli_dir), and ffmpeg/ffprobe on the PATH.
"""
from __future__ import annotations

import argparse
import codecs
import glob
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading

from blur_subtitles import blur_video

HERE = os.path.dirname(os.path.abspath(sys.argv[0]))
CLI_NAME = 'videocr-cli.exe' if sys.platform == 'win32' else 'videocr-cli.bin'

# PaddleOCR languages as in the VideOCR GUI: display name -> code
LANGUAGES = {
    'Abaza': 'abq', 'Adyghe': 'ady', 'Afrikaans': 'af', 'Albanian': 'sq', 'Angika': 'ang', 'Arabic': 'ar',
    'Avar': 'ava', 'Azerbaijani': 'az', 'Baluchi': 'bal', 'Bashkir': 'ba', 'Basque': 'eu', 'Belarusian': 'be',
    'Bhojpuri': 'bho', 'Bihari': 'bh', 'Bosnian': 'bs', 'Bulgarian': 'bg', 'Buryat': 'bua', 'Catalan': 'ca',
    'Chechen': 'che', 'Chinese': 'ch', 'Chinese Traditional': 'chinese_cht', 'Chuvash': 'cv', 'Croatian': 'hr',
    'Czech': 'cs', 'Danish': 'da', 'Dargwa': 'dar', 'Dutch': 'nl', 'English': 'en', 'Estonian': 'et',
    'Finnish': 'fi', 'French': 'fr', 'Galician': 'gl', 'Kannada': 'ka', 'German': 'german', 'Goan Konkani': 'gom',
    'Greek': 'el', 'Haryanvi': 'bgc', 'Hindi': 'hi', 'Hungarian': 'hu', 'Icelandic': 'is', 'Indonesian': 'id',
    'Ingush': 'inh', 'Irish': 'ga', 'Italian': 'it', 'Japanese': 'japan', 'Kabardian': 'kbd', 'Kalmyk': 'xal',
    'Karakalpak': 'kaa', 'Kazakh': 'kk', 'Komi': 'kv', 'Korean': 'korean', 'Kurdish': 'ku', 'Kyrgyz': 'ky',
    'Lak': 'lbe', 'Latin': 'la', 'Latvian': 'lv', 'Lezghian': 'lez', 'Lithuanian': 'lt', 'Luxembourgish': 'lb',
    'Macedonian': 'mk', 'Magahi': 'mah', 'Maithili': 'mai', 'Malay': 'ms', 'Maltese': 'mt', 'Maori': 'mi',
    'Marathi': 'mr', 'Meadow Mari': 'mhr', 'Moldovan': 'mo', 'Mongolian': 'mn', 'Nagpuri': 'sck', 'Nepali': 'ne',
    'Newari': 'new', 'Norwegian': 'no', 'Occitan': 'oc', 'Ossetian': 'os', 'Pali': 'pi', 'Pashto': 'ps',
    'Persian': 'fa', 'Polish': 'pl', 'Portuguese': 'pt', 'Quechua': 'qu', 'Romansh': 'rm', 'Romanian': 'ro',
    'Russian': 'ru', 'Sakha': 'sah', 'Sanskrit': 'sa', 'Serbian(cyrillic)': 'rs_cyrillic',
    'Serbian(latin)': 'rs_latin', 'Sindhi': 'sd', 'Slovak': 'sk', 'Slovenian': 'sl', 'Spanish': 'es',
    'Swahili': 'sw', 'Swedish': 'sv', 'Tabassaran': 'tab', 'Tagalog': 'tl', 'Tajik': 'tg', 'Tamil': 'ta',
    'Tatar': 'tt', 'Telugu': 'te', 'Thai': 'th', 'Turkish': 'tr', 'Tuvan': 'tyv', 'Udmurt': 'udm',
    'Ukrainian': 'uk', 'Urdu': 'ur', 'Uyghur': 'ug', 'Uzbek': 'uz', 'Vietnamese': 'vi', 'Welsh': 'cy',
}


def check_ffmpeg() -> None:
    missing = [tool for tool in ('ffmpeg', 'ffprobe') if not shutil.which(tool)]
    if missing:
        hint = 'Install it from https://ffmpeg.org/download.html and add it to your PATH.' if sys.platform == 'win32' \
            else 'Install it with your package manager, e.g. sudo apt install ffmpeg.'
        sys.exit(f"{' and '.join(missing)} not found on the PATH. {hint}")


def find_cli(cli_dir: str | None) -> tuple[str, bool]:
    """Return the videocr-cli executable and whether it comes with the GPU build of PaddleOCR."""
    if cli_dir:
        candidates = [cli_dir]
    else:
        candidates = [HERE]
        for base in (HERE, os.path.dirname(HERE)):
            candidates += sorted(glob.glob(os.path.join(base, 'videocr-cli-*')), reverse=True)
            candidates += sorted(glob.glob(os.path.join(base, 'Releases', 'videocr-cli-*')), reverse=True)
    for directory in candidates:
        exe = os.path.join(directory, CLI_NAME)
        if os.path.isfile(exe):
            break
    else:
        sys.exit(f'Could not find {CLI_NAME}. Put this program in the videocr-cli folder or pass that folder with --cli_dir.')

    help_text = subprocess.run([exe, '--help'], capture_output=True, text=True).stdout
    if '--boxes_output' not in help_text:
        sys.exit(f'{exe} is too old: it does not support --boxes_output. Use a build that includes it.')
    return exe, bool(glob.glob(os.path.join(os.path.dirname(exe), 'PaddleOCR-GPU*')))


def hms(seconds: float) -> str:
    s = int(seconds)
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


def process(video: str, lang: str, output_dir: str, time_end: float | None, cli_dir: str | None) -> None:
    if not os.path.isfile(video):
        sys.exit(f'Video not found: {video}')
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.join(output_dir, os.path.splitext(os.path.basename(video))[0])
    srt, blurred = f'{base}.srt', f'{base}_blurred.mp4'

    check_ffmpeg()
    cli, gpu = find_cli(cli_dir)

    # The boxes file is only the hand-over between the two parts, so it lives in a temp dir that is removed afterwards
    with tempfile.TemporaryDirectory() as tmp:
        boxes = os.path.join(tmp, 'boxes.json')
        ocr = [cli, '--video_path', video, '--lang', lang, '--output', srt, '--boxes_output', boxes,
               '--allow_system_sleep', 'true', '--use_gpu', 'true' if gpu else 'false']
        if time_end is not None:
            ocr += ['--time_end', hms(time_end)]

        print('\n=== Part 1/2: extracting subtitles ===', flush=True)
        subprocess.run(ocr, check=True)
        print('\n=== Part 2/2: blurring subtitles ===', flush=True)
        blur_video(video, boxes, blurred, time_end=time_end)
    print(f'\nDone:\n  {srt}\n  {blurred}')


def self_command() -> list[str]:
    """Command that starts this program again (as the compiled executable or as a script)."""
    if '__compiled__' in globals():
        return [os.path.abspath(sys.argv[0])]
    return [sys.executable, '-u', os.path.abspath(__file__)]


def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title('Subtitle blur')
            self.geometry('760x520')
            self.minsize(560, 380)

            self.video = tk.StringVar()
            self.output_dir = tk.StringVar()
            self.language = tk.StringVar(value='Chinese')
            self.proc: subprocess.Popen[bytes] | None = None
            self.lines: queue.Queue[str | None] = queue.Queue()
            self.overwrite_line = False

            form = ttk.Frame(self, padding=10)
            form.pack(fill='x')
            form.columnconfigure(1, weight=1)

            ttk.Label(form, text='Video').grid(row=0, column=0, sticky='w', pady=3)
            ttk.Entry(form, textvariable=self.video).grid(row=0, column=1, sticky='ew', padx=6)
            ttk.Button(form, text='Browse...', command=self.pick_video).grid(row=0, column=2)

            ttk.Label(form, text='Output folder').grid(row=1, column=0, sticky='w', pady=3)
            ttk.Entry(form, textvariable=self.output_dir).grid(row=1, column=1, sticky='ew', padx=6)
            ttk.Button(form, text='Browse...', command=self.pick_output_dir).grid(row=1, column=2)

            ttk.Label(form, text='Subtitle language').grid(row=2, column=0, sticky='w', pady=3)
            ttk.Combobox(form, textvariable=self.language, values=sorted(LANGUAGES), state='readonly', width=24).grid(
                row=2, column=1, sticky='w', padx=6)

            self.run_button = ttk.Button(form, text='Run', command=self.start)
            self.run_button.grid(row=3, column=0, columnspan=3, pady=(8, 0))

            log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
            log_frame.pack(fill='both', expand=True)
            self.log = tk.Text(log_frame, wrap='word', state='disabled', font='TkFixedFont')
            scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
            self.log.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            self.log.pack(side='left', fill='both', expand=True)

            self.protocol('WM_DELETE_WINDOW', self.on_close)
            self.after(100, self.poll)

        def pick_video(self) -> None:
            path = filedialog.askopenfilename(title='Select video', filetypes=[
                ('Video files', '*.mp4 *.mkv *.avi *.mov *.webm *.flv *.m4v *.ts'), ('All files', '*.*')])
            if path:
                self.video.set(path)
                if not self.output_dir.get():
                    self.output_dir.set(os.path.dirname(path))

        def pick_output_dir(self) -> None:
            path = filedialog.askdirectory(title='Select output folder', initialdir=self.output_dir.get() or None)
            if path:
                self.output_dir.set(path)

        def start(self) -> None:
            video = self.video.get().strip()
            if not os.path.isfile(video):
                messagebox.showerror('Subtitle blur', 'Please select a video file.')
                return
            output_dir = self.output_dir.get().strip() or os.path.dirname(video)

            cmd = [*self_command(), video, '--lang', LANGUAGES[self.language.get()], '--output_dir', output_dir]
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            self.clear_log()
            self.run_button.configure(state='disabled')
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                             env={**os.environ, 'PYTHONUNBUFFERED': '1'}, creationflags=flags)
            except OSError as e:
                self.append(f'Could not start: {e}\n')
                self.run_button.configure(state='normal')
                return
            threading.Thread(target=self.read_output, args=(self.proc,), daemon=True).start()

        def read_output(self, proc: subprocess.Popen[bytes]) -> None:
            assert proc.stdout is not None
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            while chunk := proc.stdout.read1(4096):  # type: ignore[attr-defined]
                self.lines.put(decoder.decode(chunk))
            code = proc.wait()
            self.lines.put(f'\n{"Finished." if code == 0 else f"Failed (exit code {code})."}\n')
            self.lines.put(None)

        def poll(self) -> None:
            try:
                while True:
                    text = self.lines.get_nowait()
                    if text is None:
                        self.proc = None
                        self.run_button.configure(state='normal')
                    else:
                        self.append(text)
            except queue.Empty:
                pass
            self.after(100, self.poll)

        def clear_log(self) -> None:
            self.log.configure(state='normal')
            self.log.delete('1.0', 'end')
            self.log.configure(state='disabled')
            self.overwrite_line = False

        def append(self, text: str) -> None:
            """Append process output; a carriage return makes the next text replace the current line (progress output)."""
            self.log.configure(state='normal')
            parts = text.replace('\r\n', '\n').split('\r')
            for i, part in enumerate(parts):
                if part:
                    if self.overwrite_line and not part.startswith('\n'):
                        self.log.delete('end-1c linestart', 'end-1c')
                    self.overwrite_line = False
                    self.log.insert('end', part)
                if i < len(parts) - 1:
                    self.overwrite_line = True
            self.log.see('end')
            self.log.configure(state='disabled')

        def on_close(self) -> None:
            if self.proc and self.proc.poll() is None:
                if not messagebox.askyesno('Subtitle blur', 'A job is still running. Stop it and close?'):
                    return
                self.proc.terminate()
            self.destroy()

    App().mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('video', nargs='?', help='Path to the video (omit to open the window)')
    parser.add_argument('--lang', help='Subtitle language code for the OCR, e.g. ch, en, japan, korean')
    parser.add_argument('--output_dir', help='Folder for the results (default: next to the video)')
    parser.add_argument('--time_end', type=float, default=None, help='Only process the first N seconds (default: whole video)')
    parser.add_argument('--cli_dir', help='Folder containing videocr-cli (default: auto-detected)')
    args = parser.parse_args()

    if args.video is None:
        gui()
        return
    if not args.lang:
        parser.error('--lang is required')
    video = os.path.abspath(args.video)
    process(video, args.lang, os.path.abspath(args.output_dir) if args.output_dir else os.path.dirname(video),
            args.time_end, args.cli_dir)


if __name__ == '__main__':
    main()
