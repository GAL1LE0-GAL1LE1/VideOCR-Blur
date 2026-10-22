# VideOCR-Blur

Blurs hardcoded (burned-in) subtitles in a video. It finds the subtitles with OCR, writes them to an `.srt` file, and produces a copy of the video with every subtitle line blurred.

You pick a video, an output folder and the subtitle language, and press Run. The results are:

- `<video name>.srt`: the extracted subtitles
- `<video name>_blurred.mp4`: the video with the subtitles blurred (audio is copied unchanged)

Based on [VideOCR](https://github.com/timminator/VideOCR) by timminator (MIT licence), which does the OCR.

## Installation

1. Download a build from the **Releases** page of this repository and extract it. The files are `.7z` archives, so Windows needs [7-Zip](https://www.7-zip.org/).

   | File name contains | Meaning |
   |---|---|
   | `-Windows` or `-Linux` | The operating system. |
   | `CPU` | Works everywhere, slower. |
   | `GPU` + `CUDA-11.8` | NVIDIA GPU, 10 series cards. |
   | `GPU` + `CUDA-12.9` | NVIDIA GPU, 16 to 50 series cards. Needs a current NVIDIA driver. |

   The GPU builds speed up the OCR step. If the archive was split (`.7z.001`, `.7z.002`), download all parts into one folder and extract the `.001` file.

## Usage

Start `auto-blur` (`auto-blur.exe` on Windows) in the extracted folder. On Windows, SmartScreen may warn about an unknown publisher: click "More info", then "Run anyway".

In the window:

1. Choose the video.
2. Choose the output folder (it defaults to the video's folder).
3. Choose the language of the subtitles.
4. Press Run. The progress is shown in the text area. A long video takes a while.

From a terminal:

```
auto-blur VIDEO --lang LANG [--output_dir DIR] [--time_end SECONDS]
```

`LANG` is the OCR language code, e.g. `ch` (Chinese), `en`, `japan`, `korean`, `german`, `fr`. `--time_end` only processes the first N seconds, which is useful for a quick test. Run `auto-blur --help` for all options.

Notes:

- Only the **bottom third** of the frame is searched for subtitles. Subtitles elsewhere in the picture are not found.
- The GPU build is used automatically. The final video is encoded on the NVIDIA GPU if ffmpeg supports it, otherwise on the CPU.
- `blur_subtitles.py` is the blur step on its own: `python3 blur_subtitles.py VIDEO BOXES.json [-o OUT]`, where `BOXES.json` comes from `videocr-cli --boxes_output`.

## Running from source

The release builds include ffmpeg. If you run `auto_blur.py` or `blur_subtitles.py` with Python instead, `ffmpeg` and `ffprobe` must be installed and on the PATH (Windows: `winget install Gyan.FFmpeg`, Linux: `sudo apt install ffmpeg`).

## OCR options

`auto-blur` runs `videocr-cli`, which is in the same folder. It saves the subtitle positions with the `--boxes_output FILE` option, a JSON file with the bounding box of every subtitle line and its start and end time in seconds. All other options of `videocr-cli` (crop area, confidence threshold, ...) are documented in the [VideOCR README](https://github.com/timminator/VideOCR#command-line-parameters-cli-version).

## Building

Releases are built by the **Build releases** workflow (Actions tab, "Run workflow"). The exact steps for building yourself are in `.github/workflows/build.yml`; the core is `python build.py --cli-only true --target cpu` (or `gpu-cuda11.8`, `gpu-cuda12.9`) with the packages from `pyproject.toml` plus `nuitka`, and `av` below version 19. Windows builds have to be made on Windows, Linux builds on Linux.
