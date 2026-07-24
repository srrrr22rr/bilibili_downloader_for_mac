#!/usr/bin/env python3

"""Regression tests for packaged FFmpeg resolution."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import runtime_compat


class FrozenFfmpegResolutionTests(unittest.TestCase):
    @staticmethod
    def _make_executable(path):
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    def test_frozen_app_uses_only_bundled_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            bundled_ffmpeg = temporary_root / "bundle" / "bin" / "ffmpeg"
            external_ffmpeg = temporary_root / "external" / "ffmpeg"
            self._make_executable(bundled_ffmpeg)
            self._make_executable(external_ffmpeg)

            with mock.patch.dict(
                os.environ,
                {"BILIBILI_DOWNLOADER_FFMPEG": str(external_ffmpeg)},
                clear=True,
            ), mock.patch.object(
                sys,
                "frozen",
                True,
                create=True,
            ), mock.patch.object(
                sys,
                "_MEIPASS",
                str(temporary_root / "bundle"),
                create=True,
            ):
                resolved = runtime_compat.configure_ffmpeg()

            self.assertEqual(resolved, str(bundled_ffmpeg))

    def test_frozen_app_rejects_external_ffmpeg_when_bundle_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            external_ffmpeg = temporary_root / "external" / "ffmpeg"
            self._make_executable(external_ffmpeg)

            with mock.patch.dict(
                os.environ,
                {"BILIBILI_DOWNLOADER_FFMPEG": str(external_ffmpeg)},
                clear=True,
            ), mock.patch.object(
                sys,
                "frozen",
                True,
                create=True,
            ), mock.patch.object(
                sys,
                "_MEIPASS",
                str(temporary_root / "missing-bundle"),
                create=True,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "应用包内缺少可执行的 FFmpeg",
                ):
                    runtime_compat.configure_ffmpeg()


if __name__ == "__main__":
    unittest.main()
