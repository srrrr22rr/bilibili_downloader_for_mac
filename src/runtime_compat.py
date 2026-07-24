#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resolve external media tools in source and frozen macOS builds."""

import os
import shutil
import sys
from pathlib import Path


def configure_ffmpeg():
    """Return a trusted FFmpeg path and configure legacy callers.

    A packaged application must use the executable sealed inside its bundle;
    looking in Homebrew first would make the build non-reproducible and would
    fail on a clean Mac. Source checkouts retain the PATH/Homebrew fallbacks
    for developer convenience.
    """
    frozen = bool(getattr(sys, "frozen", False))
    explicit_path = os.environ.get("BILIBILI_DOWNLOADER_FFMPEG")
    resource_root = Path(
        getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
    )
    bundled_path = resource_root / "bin" / "ffmpeg"

    ffmpeg_path = None
    preferred_candidates = (
        (str(bundled_path),)
        if frozen
        else (explicit_path, str(bundled_path))
    )
    for candidate in preferred_candidates:
        if (
            candidate
            and os.path.isfile(candidate)
            and os.access(candidate, os.X_OK)
        ):
            ffmpeg_path = candidate
            break

    if not ffmpeg_path and not frozen:
        ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path and not frozen:
        candidates = (
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            os.path.expanduser("~/.local/bin/ffmpeg"),
        )
        ffmpeg_path = next(
            (
                path
                for path in candidates
                if os.path.isfile(path) and os.access(path, os.X_OK)
            ),
            None,
        )
    if not ffmpeg_path:
        raise RuntimeError(
            "应用包内缺少可执行的 FFmpeg。请重新下载完整发布包；"
            "源码模式可通过 Homebrew 安装 FFmpeg。"
        )

    os.environ["FFMPEG_BINARY"] = ffmpeg_path
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_path
    return ffmpeg_path
