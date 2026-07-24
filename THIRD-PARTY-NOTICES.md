# Third-party notices

`b站downloader.app` 是一个聚合发布包。媒体处理程序以独立子进程方式调用
yt-dlp 和 FFmpeg；各组件继续遵循自己的许可证。

## yt-dlp

- Version: 2026.07.04
- Binary: official `yt-dlp_macos` universal2 release, thinned to arm64 by the
  Apple Silicon build process when applicable
- Source: <https://github.com/yt-dlp/yt-dlp/tree/2026.07.04>
- Release: <https://github.com/yt-dlp/yt-dlp/releases/tag/2026.07.04>
- License: The Unlicense
- License file: `licenses/yt-dlp-Unlicense.txt`

The checked-in binary must match:

```text
498bd0dae17855c599d371d68ec5bafc439a9d8640e838be25c765a9792f261b
```

## FFmpeg

- Version: 7.1 arm64
- Binary provider: `imageio-ffmpeg 0.6.0` macOS arm64 wheel
- FFmpeg source tag: <https://github.com/FFmpeg/FFmpeg/tree/n7.1>
- Provider source: <https://github.com/imageio/imageio-ffmpeg/tree/v0.6.0>
- Wheel: <https://pypi.org/project/imageio-ffmpeg/0.6.0/#files>
- License reported by `ffmpeg -L`: GNU GPL version 2 or later
- License file: `licenses/FFmpeg-GPL-2.0.txt`

Build configuration reported by the bundled binary includes `--enable-gpl`,
`--enable-libmp3lame`, `--enable-libx264`, and `--enable-libx265`. Anyone
redistributing the binary must preserve the GPL notice and provide the
corresponding source/build information. FFmpeg's own legal guidance:
<https://ffmpeg.org/legal.html>.

## Python and Python packages

- Python 3.9.6: Python Software Foundation License
- Requests 2.31.0: Apache-2.0
- urllib3 1.26.20: MIT
- certifi 2026.7.22: MPL-2.0
- charset-normalizer 3.4.9: MIT
- idna 3.18: BSD-3-Clause
- PyInstaller 6.21.0 bootloader: GPL-2.0-or-later with the PyInstaller
  Bootloader Exception
- imageio-ffmpeg 0.6.0 Python package: BSD-2-Clause

The corresponding license texts are included under `licenses/`. Pillow is
used only while creating the icon and is not imported into the application
runtime.
