#!/usr/bin/env python3

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import bilibili_ytdlp_macos as app
from download_controls import ProcessResult
from download_options import Part, PartCatalog, PartSelection


class NormalizeUrlTests(unittest.TestCase):
    def test_normalizes_numeric_aid(self):
        self.assertEqual(
            app.normalize_bilibili_url("49842011"),
            "https://www.bilibili.com/video/av49842011",
        )

    def test_normalizes_av_id(self):
        self.assertEqual(
            app.normalize_bilibili_url("av49842011"),
            "https://www.bilibili.com/video/av49842011",
        )

    def test_normalizes_bv_id(self):
        self.assertEqual(
            app.normalize_bilibili_url("BV1cb411V7Lm"),
            "https://www.bilibili.com/video/BV1cb411V7Lm",
        )

    def test_allows_official_video_and_short_links(self):
        urls = (
            "https://www.bilibili.com/video/BV1cb411V7Lm?p=2",
            "https://www.bilibili.com/bangumi/play/ep123",
            "https://b23.tv/abcdef",
        )
        for url in urls:
            self.assertEqual(app.normalize_bilibili_url(url), url)

    def test_extracts_link_from_bilibili_copied_title_text(self):
        copied_text = (
            "【在百万豪装录音棚大声听 蔡琴《渡口》【Hi-res】】 "
            "https://www.bilibili.com/video/BV1Sc411U7m6/"
            "?share_source=copy_web&vd_source=1a6ff0940c53b98710070c34bbcb8e72"
        )
        self.assertEqual(
            app.normalize_bilibili_url(copied_text),
            "https://www.bilibili.com/video/BV1Sc411U7m6/"
            "?share_source=copy_web&vd_source=1a6ff0940c53b98710070c34bbcb8e72",
        )
        self.assertEqual(
            app.normalize_bilibili_url(
                "视频标题\nhttps://b23.tv/abcdef。"
            ),
            "https://b23.tv/abcdef",
        )

    def test_rejects_non_bilibili_and_credentials(self):
        invalid = (
            "https://example.com/video/BV1cb411V7Lm",
            "https://user:password@www.bilibili.com/video/BV1cb411V7Lm",
            "--help",
        )
        for value in invalid:
            with self.assertRaises(app.UserInputError):
                app.normalize_bilibili_url(value)


class CommandTests(unittest.TestCase):
    def test_chrome_command_reads_browser_without_cookie_file(self):
        command = app.build_download_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            "chrome",
            "3",
            ["--no-playlist"],
            "/usr/local/bin/ffmpeg",
        )
        self.assertIn("--cookies-from-browser", command)
        self.assertNotIn("--cookies", command)
        self.assertNotIn("SESSDATA", " ".join(command))
        self.assertEqual(command[-2], "--")
        self.assertIn("height<=1080", " ".join(command))
        self.assertIn("--continue", command)
        self.assertIn("--part", command)

    def test_anonymous_command_does_not_read_browser(self):
        command = app.build_download_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            None,
            "1",
            ["--yes-playlist", "--playlist-items", "1,3-4"],
            "/usr/local/bin/ffmpeg",
        )
        self.assertIn("--no-cookies-from-browser", command)
        self.assertNotIn("--cookies-from-browser", command)
        self.assertIn("--yes-playlist", command)
        self.assertEqual(
            command[command.index("--playlist-items") + 1],
            "1,3-4",
        )

    def test_video_only_sidecar_matches_selected_quality(self):
        command = app.build_video_only_track_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            None,
            "4",
            ["--yes-playlist", "--playlist-items", "2-3"],
            "/usr/local/bin/ffmpeg",
        )
        self.assertEqual(
            command[command.index("--format") + 1],
            "bv[height<=720]",
        )
        self.assertIn(".video-only-", command[command.index("--output") + 1])
        self.assertIn("--yes-playlist", command)
        self.assertEqual(
            command[command.index("--playlist-items") + 1],
            "2-3",
        )

    def test_part_probe_can_flatten_a_playlist_or_keep_current_part(self):
        flattened = app.build_part_probe_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm?p=2",
            "chrome",
            "/usr/local/bin/ffmpeg",
            as_playlist=True,
        )
        current = app.build_part_probe_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm?p=2",
            None,
            "/usr/local/bin/ffmpeg",
            as_playlist=False,
        )
        self.assertIn("--flat-playlist", flattened)
        self.assertIn("--yes-playlist", flattened)
        self.assertNotIn("--flat-playlist", current)
        self.assertIn("--no-playlist", current)


class PlanConfirmationTests(unittest.TestCase):
    def setUp(self):
        catalog = PartCatalog(
            "多P测试视频",
            tuple(Part(index, "第{}段".format(index)) for index in range(1, 6)),
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            "https://www.bilibili.com/video/BV1cb411V7Lm?p=2",
            2,
        )
        self.selection = PartSelection(catalog, (1, 3, 4), "custom")

    def test_audio_mode_supports_none_mp3_and_strict_flac_choices(self):
        for answer, expected in (("", "none"), ("1", "mp3"), ("2", "flac")):
            with self.subTest(answer=answer):
                self.assertEqual(
                    app.choose_audio_mode(
                        prompt=lambda _: answer,
                        write=lambda _: None,
                    ),
                    expected,
                )

    def test_selection_summary_compresses_custom_part_ranges(self):
        self.assertEqual(
            app.selection_summary(self.selection),
            "P1,3-4（共 3 个）",
        )

    def test_confirmation_returns_start_redo_or_quit_and_renders_plan(self):
        for answer, expected in (("", "start"), ("R", "redo"), ("Q", "quit")):
            messages = []
            with self.subTest(answer=answer):
                action = app.confirm_download_plan(
                    self.selection,
                    "Google Chrome",
                    "3",
                    "flac",
                    True,
                    prompt=lambda _: answer,
                    write=messages.append,
                )
                self.assertEqual(action, expected)
                rendered = "\n".join(messages)
                self.assertIn("多P测试视频", rendered)
                self.assertIn("P1,3-4", rendered)
                self.assertIn("1080P", rendered)
                self.assertIn("FLAC", rendered)
                self.assertIn("无音频视频：额外保留", rendered)


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.catalog = PartCatalog(
            "多P测试视频",
            tuple(Part(index, "P{}".format(index)) for index in range(1, 6)),
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            "https://www.bilibili.com/video/BV1cb411V7Lm?p=2",
            2,
        )
        self.selection = PartSelection(
            self.catalog,
            (1, 3, 4),
            "custom",
        )

    def _patch_plan_inputs(
        self,
        stack,
        *,
        audio_mode,
        keep_video_only,
        decision,
    ):
        stack.enter_context(
            mock.patch.object(
                app,
                "ensure_runtime",
                return_value="/usr/local/bin/ffmpeg",
            )
        )
        stack.enter_context(
            mock.patch.object(app, "choose_login_source", return_value=None)
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "prompt_video_url",
                return_value=self.catalog.current_url,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "fetch_part_catalog",
                return_value=self.catalog,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "choose_part_selection",
                return_value=self.selection,
            )
        )
        stack.enter_context(
            mock.patch.object(app, "choose_quality", return_value="3")
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "choose_audio_mode",
                return_value=audio_mode,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "prompt_yes_no",
                return_value=keep_video_only,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "confirm_download_plan",
                return_value=decision,
            )
        )

    def test_quit_at_confirmation_starts_no_download_or_open(self):
        with ExitStack() as stack:
            self._patch_plan_inputs(
                stack,
                audio_mode="mp3",
                keep_video_only=True,
                decision="quit",
            )
            run_process = stack.enter_context(
                mock.patch.object(app, "run_process_with_pause")
            )
            subprocess_run = stack.enter_context(
                mock.patch.object(app.subprocess, "run")
            )

            self.assertEqual(app.main(), 0)

        run_process.assert_not_called()
        subprocess_run.assert_not_called()

    def test_custom_parts_are_shared_by_video_audio_and_video_only(self):
        commands = []

        def fake_run(command):
            commands.append(command)
            return ProcessResult(0, False)

        with tempfile.TemporaryDirectory() as output_dir, ExitStack() as stack:
            output_path = Path(output_dir)
            cache_dir = output_path / "cache"
            manifest_path = cache_dir / "audio-sources.jsonl"
            stack.enter_context(
                mock.patch.object(app, "OUTPUT_DIR", output_path)
            )
            self._patch_plan_inputs(
                stack,
                audio_mode="mp3",
                keep_video_only=True,
                decision="start",
            )
            stack.enter_context(
                mock.patch.object(
                    app,
                    "run_process_with_pause",
                    side_effect=fake_run,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    app,
                    "audio_cache_paths",
                    return_value=(cache_dir, manifest_path),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    app,
                    "read_audio_manifest",
                    return_value=[object(), object(), object()],
                )
            )
            convert_audio = stack.enter_context(
                mock.patch.object(
                    app,
                    "convert_audio_sources",
                    return_value=(False, []),
                )
            )
            subprocess_run = stack.enter_context(
                mock.patch.object(app.subprocess, "run")
            )

            self.assertEqual(app.main(), 0)

        self.assertEqual(len(commands), 3)
        playlist_values = [
            command[command.index("--playlist-items") + 1]
            for command in commands
        ]
        self.assertEqual(playlist_values, ["1,3,4", "1,3,4", "1,3,4"])
        convert_audio.assert_called_once()
        subprocess_run.assert_called_once_with(
            ["/usr/bin/open", str(output_path)],
            check=False,
        )


class AudioSafetyTests(unittest.TestCase):
    def test_manifest_path_outside_cache_never_runs_ffmpeg_or_deletes_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            outside_source = root / "outside-source.flac"
            outside_source.write_bytes(b"must remain untouched")
            manifest_path = cache_dir / "audio-sources.jsonl"
            manifest_path.write_text(
                json.dumps(
                    {
                        "filepath": str(outside_source),
                        "acodec": "flac",
                        "id": "BV1_OUTSIDE",
                        "title": "外部源",
                        "playlist_index": 1,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(app, "OUTPUT_DIR", root / "output"), \
                    mock.patch.object(
                        app,
                        "build_audio_conversion",
                    ) as build_conversion, \
                    mock.patch.object(
                        app,
                        "run_process_with_pause",
                    ) as run_process:
                cancelled, failures = app.convert_audio_sources(
                    "flac",
                    "/usr/local/bin/ffmpeg",
                    cache_dir,
                    manifest_path,
                )

            self.assertFalse(cancelled)
            self.assertEqual(failures, ["外部源（缓存路径异常）"])
            self.assertTrue(outside_source.is_file())
            self.assertEqual(
                outside_source.read_bytes(),
                b"must remain untouched",
            )
            build_conversion.assert_not_called()
            run_process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
