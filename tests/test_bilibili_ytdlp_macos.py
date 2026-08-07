#!/usr/bin/env python3

import json
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
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
        bangumi_text = (
            "【青春猪头少年不会梦到兔女郎学姐：第1话 学姐是兔女郎】 "
            "https://www.bilibili.com/bangumi/play/ep251076/"
            "?share_source=copy_web"
        )
        self.assertEqual(
            app.normalize_bilibili_url(bangumi_text),
            "https://www.bilibili.com/bangumi/play/ep251076/"
            "?share_source=copy_web",
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
            "1",
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
        self.assertIn(
            ".video-f%(format_id)s.",
            command[command.index("--output") + 1],
        )

    def test_anonymous_command_does_not_read_browser(self):
        command = app.build_download_command(
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            None,
            "1",
            "2",
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

    def test_auto_audio_uses_best_track_and_legacy_single_file_fallback(self):
        self.assertEqual(
            app.build_combined_format_selector("3", "1"),
            "bv[height<=1080]+ba/b[height<=1080]",
        )

    def test_aac_audio_tiers_fall_back_only_to_documented_lower_tiers(self):
        expected = {
            "2": (
                "bv[height<=1080]+30280/"
                "bv[height<=1080]+30232/"
                "bv[height<=1080]+30216"
            ),
            "3": "bv[height<=1080]+30232/bv[height<=1080]+30216",
            "4": "bv[height<=1080]+30216",
        }
        for audio_quality_key, selector in expected.items():
            with self.subTest(audio_quality_key=audio_quality_key):
                self.assertEqual(
                    app.build_combined_format_selector(
                        "3",
                        audio_quality_key,
                    ),
                    selector,
                )

    def test_special_audio_tiers_are_strict_without_silent_aac_fallback(self):
        self.assertEqual(
            app.build_combined_format_selector("1", "5"),
            "bv+30250",
        )
        self.assertEqual(
            app.build_combined_format_selector("1", "6"),
            "bv+30251",
        )

    def test_combined_selector_rejects_unknown_video_or_audio_choice(self):
        for quality_key, audio_quality_key in (("9", "1"), ("1", "9")):
            with self.subTest(
                quality_key=quality_key,
                audio_quality_key=audio_quality_key,
            ):
                with self.assertRaises(ValueError):
                    app.build_combined_format_selector(
                        quality_key,
                        audio_quality_key,
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


class BangumiCatalogFetchTests(unittest.TestCase):
    def setUp(self):
        self.season_info = {
            "season_id": 25733,
            "title": "青春猪头少年不会梦到兔女郎学姐",
            "episodes": [
                {
                    "id": 251076,
                    "title": "1",
                    "long_title": "学姐是兔女郎",
                },
                {
                    "id": 251077,
                    "title": "2",
                    "long_title": "初次约会难免风波",
                },
            ],
        }

    def test_episode_url_uses_season_catalog_without_flat_probe(self):
        url = (
            "https://www.bilibili.com/bangumi/play/ep251076/"
            "?share_source=copy_web"
        )
        with mock.patch.object(
            app,
            "get_bangumi_info",
            return_value=(self.season_info, 251076),
        ) as get_bangumi, mock.patch.object(
            app,
            "get_video_info",
        ) as get_video, mock.patch.object(
            app,
            "_run_json_probe",
        ) as run_probe:
            catalog = app.fetch_part_catalog(url, None, "/usr/bin/ffmpeg")

        self.assertEqual(len(catalog.parts), 2)
        self.assertEqual(catalog.kind_label, "分集")
        self.assertEqual(catalog.current_index, 1)
        self.assertTrue(catalog.base_url.endswith("/ss25733"))
        get_bangumi.assert_called_once_with(url)
        get_video.assert_not_called()
        run_probe.assert_not_called()

    def test_bangumi_selection_summary_uses_episode_wording(self):
        catalog = app.catalog_from_bangumi_info(
            self.season_info,
            251077,
        )
        current = PartSelection(catalog, (2,), "current")
        custom = PartSelection(catalog, (1, 2), "custom")

        self.assertEqual(
            app.selection_summary(current),
            "当前第2集：初次约会难免风波",
        )
        self.assertEqual(
            app.selection_summary(custom),
            "第1-2集（共 2 集）",
        )

    def test_short_link_can_use_canonical_episode_url(self):
        short_url = "https://b23.tv/example"
        canonical_url = (
            "https://www.bilibili.com/bangumi/play/ep251077"
        )
        with mock.patch.object(
            app,
            "get_video_info",
            side_effect=app.BilibiliAPIError("不是普通 BV/AV 链接"),
        ), mock.patch.object(
            app,
            "_run_json_probe",
            return_value=({"webpage_url": canonical_url}, ""),
        ) as run_probe, mock.patch.object(
            app,
            "get_bangumi_info",
            return_value=(self.season_info, 251077),
        ) as get_bangumi:
            catalog = app.fetch_part_catalog(
                short_url,
                None,
                "/usr/bin/ffmpeg",
            )

        self.assertEqual(catalog.current_index, 2)
        get_bangumi.assert_called_once_with(canonical_url)
        run_probe.assert_called_once()


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

    def test_combined_audio_quality_defaults_and_accepts_every_tier(self):
        for answer, expected in (
            ("", "1"),
            ("1", "1"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
            ("6", "6"),
            ("7", "7"),
        ):
            with self.subTest(answer=answer):
                self.assertEqual(
                    app.choose_combined_audio_quality(
                        prompt=lambda _: answer,
                        write=lambda _: None,
                    ),
                    expected,
                )

    def test_combined_audio_quality_reprompts_after_invalid_input(self):
        answers = iter(("wrong", "3"))
        messages = []
        self.assertEqual(
            app.choose_combined_audio_quality(
                prompt=lambda _: next(answers),
                write=messages.append,
            ),
            "3",
        )
        self.assertTrue(any("1 至 7" in message for message in messages))

    def test_output_plan_defaults_to_combined_video(self):
        answers = iter(("", "", ""))
        outputs = app.choose_output_plan(
            prompt=lambda _: next(answers),
            write=lambda _: None,
        )
        self.assertEqual(
            outputs,
            app.DownloadOutputs(True, "none", False),
        )

    def test_output_plan_rejects_all_off_then_accepts_audio_only(self):
        answers = iter(("n", "0", "n", "n", "1", "n"))
        messages = []
        outputs = app.choose_output_plan(
            prompt=lambda _: next(answers),
            write=messages.append,
        )
        self.assertEqual(
            outputs,
            app.DownloadOutputs(False, "mp3", False),
        )
        self.assertEqual(
            sum("[选择无效]" in message for message in messages),
            1,
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
                    "5",
                    app.DownloadOutputs(True, "flac", True),
                    prompt=lambda _: answer,
                    write=messages.append,
                )
                self.assertEqual(action, expected)
                rendered = "\n".join(messages)
                self.assertIn("多P测试视频", rendered)
                self.assertIn("P1,3-4", rendered)
                self.assertIn("1080P", rendered)
                self.assertIn("完整视频音质：杜比音频", rendered)
                self.assertIn("FLAC", rendered)
                self.assertIn("完整视频：下载", rendered)
                self.assertIn("无声音视频：下载", rendered)

    def test_audio_only_confirmation_skips_video_quality(self):
        messages = []
        action = app.confirm_download_plan(
            self.selection,
            "匿名",
            None,
            None,
            app.DownloadOutputs(False, "mp3", False),
            prompt=lambda _: "Q",
            write=messages.append,
        )
        rendered = "\n".join(messages)
        self.assertEqual(action, "quit")
        self.assertIn("画质：不适用（仅下载音频）", rendered)
        self.assertIn("完整视频音质：不适用（未选择完整视频）", rendered)
        self.assertIn("完整视频：不下载", rendered)
        self.assertIn("无画面音频：MP3", rendered)
        self.assertIn("无声音视频：不下载", rendered)

    def test_confirmation_rejects_empty_or_unqualified_video_plan(self):
        invalid = (
            (None, None, app.DownloadOutputs(False, "none", False)),
            (None, None, app.DownloadOutputs(False, "none", True)),
            (None, None, app.DownloadOutputs(False, "wav", False)),
            ("3", None, app.DownloadOutputs(True, "none", False)),
            ("3", "9", app.DownloadOutputs(True, "none", False)),
            ("3", "1", app.DownloadOutputs(False, "none", True)),
        )
        for quality_key, combined_audio_quality_key, outputs in invalid:
            with self.subTest(outputs=outputs):
                with self.assertRaises(ValueError):
                    app.confirm_download_plan(
                        self.selection,
                        "匿名",
                        quality_key,
                        combined_audio_quality_key,
                        outputs,
                        prompt=lambda _: "Q",
                        write=lambda _: None,
                    )


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
        outputs,
        decision,
        quality_key="3",
        combined_audio_quality_key="2",
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
        choose_quality = stack.enter_context(
            mock.patch.object(
                app,
                "choose_quality",
                return_value=quality_key,
            )
        )
        choose_combined_audio_quality = stack.enter_context(
            mock.patch.object(
                app,
                "choose_combined_audio_quality",
                return_value=combined_audio_quality_key,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "choose_output_plan",
                return_value=outputs,
            )
        )
        stack.enter_context(
            mock.patch.object(
                app,
                "confirm_download_plan",
                return_value=decision,
            )
        )
        return choose_quality, choose_combined_audio_quality

    def test_quit_at_confirmation_starts_no_download_or_open(self):
        with ExitStack() as stack:
            (
                choose_quality,
                choose_combined_audio_quality,
            ) = self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(True, "mp3", True),
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
        choose_quality.assert_called_once_with()
        choose_combined_audio_quality.assert_called_once_with()

    def test_audio_format_listing_returns_to_combined_audio_menu(self):
        with ExitStack() as stack:
            _, choose_combined_audio_quality = self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(True, "none", False),
                decision="quit",
            )
            choose_combined_audio_quality.side_effect = ("7", "3")
            show_available_formats = stack.enter_context(
                mock.patch.object(app, "show_available_formats")
            )

            self.assertEqual(app.main(), 0)

        self.assertEqual(choose_combined_audio_quality.call_count, 2)
        show_available_formats.assert_called_once_with(
            self.selection,
            None,
            "/usr/local/bin/ffmpeg",
            "完整视频音质",
        )

    def test_format_listing_targets_first_selected_part_not_current_part(self):
        with mock.patch.object(
            app.subprocess,
            "run",
            return_value=mock.Mock(returncode=0),
        ) as subprocess_run, mock.patch("builtins.input", return_value=""):
            app.show_available_formats(
                self.selection,
                None,
                "/usr/local/bin/ffmpeg",
                "完整视频音质",
            )

        command = subprocess_run.call_args.args[0]
        self.assertEqual(command[-2], "--")
        self.assertEqual(command[-1], self.selection.url)
        self.assertIn("--yes-playlist", command)
        self.assertEqual(
            command[command.index("--playlist-items") + 1],
            "1",
        )

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
                outputs=app.DownloadOutputs(True, "mp3", True),
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
        self.assertEqual(
            commands[0][commands[0].index("--format") + 1],
            "bv[height<=1080]+30280/"
            "bv[height<=1080]+30232/"
            "bv[height<=1080]+30216",
        )
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

    def test_combined_failure_still_runs_selected_video_only_output(self):
        commands = []
        results = iter((ProcessResult(4, False), ProcessResult(0, False)))

        def fake_run(command):
            commands.append(command)
            return next(results)

        with tempfile.TemporaryDirectory() as output_dir, ExitStack() as stack:
            output_path = Path(output_dir)
            stack.enter_context(
                mock.patch.object(app, "OUTPUT_DIR", output_path)
            )
            self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(True, "none", True),
                decision="start",
            )
            stack.enter_context(
                mock.patch.object(
                    app,
                    "run_process_with_pause",
                    side_effect=fake_run,
                )
            )
            stack.enter_context(mock.patch.object(app.subprocess, "run"))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(app.main(), 1)

        self.assertEqual(len(commands), 2)
        self.assertIn("--merge-output-format", commands[0])
        self.assertIn(
            ".video-only-",
            commands[1][commands[1].index("--output") + 1],
        )
        rendered = stdout.getvalue()
        self.assertIn("[1/2] 正在处理：完整视频", rendered)
        self.assertIn("[2/2] 正在处理：无声音视频", rendered)
        self.assertIn("完整视频下载失败", rendered)
        self.assertIn("以下所选输出未能全部完成：完整视频", rendered)

    def test_audio_only_skips_quality_and_runs_one_numbered_task(self):
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
            (
                choose_quality,
                choose_combined_audio_quality,
            ) = self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(False, "mp3", False),
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
            stack.enter_context(
                mock.patch.object(
                    app,
                    "convert_audio_sources",
                    return_value=(False, []),
                )
            )
            subprocess_run = stack.enter_context(
                mock.patch.object(app.subprocess, "run")
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(app.main(), 0)

        choose_quality.assert_not_called()
        choose_combined_audio_quality.assert_not_called()
        self.assertEqual(len(commands), 1)
        self.assertIn("--print-to-file", commands[0])
        self.assertNotIn("--merge-output-format", commands[0])
        self.assertIn("[1/1] 正在取得独立 MP3", stdout.getvalue())
        self.assertNotIn("正在处理：完整视频", stdout.getvalue())
        subprocess_run.assert_called_once_with(
            ["/usr/bin/open", str(output_path)],
            check=False,
        )

    def test_video_only_runs_without_combined_or_audio_task(self):
        commands = []

        def fake_run(command):
            commands.append(command)
            return ProcessResult(0, False)

        with tempfile.TemporaryDirectory() as output_dir, ExitStack() as stack:
            output_path = Path(output_dir)
            stack.enter_context(
                mock.patch.object(app, "OUTPUT_DIR", output_path)
            )
            (
                choose_quality,
                choose_combined_audio_quality,
            ) = self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(False, "none", True),
                decision="start",
            )
            stack.enter_context(
                mock.patch.object(
                    app,
                    "run_process_with_pause",
                    side_effect=fake_run,
                )
            )
            subprocess_run = stack.enter_context(
                mock.patch.object(app.subprocess, "run")
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(app.main(), 0)

        choose_quality.assert_called_once_with()
        choose_combined_audio_quality.assert_not_called()
        self.assertEqual(len(commands), 1)
        self.assertIn(
            ".video-only-",
            commands[0][commands[0].index("--output") + 1],
        )
        self.assertNotIn("--merge-output-format", commands[0])
        self.assertIn("[1/1] 正在处理：无声音视频", stdout.getvalue())
        self.assertNotIn("正在处理：完整视频", stdout.getvalue())
        subprocess_run.assert_called_once_with(
            ["/usr/bin/open", str(output_path)],
            check=False,
        )

    def test_audio_and_video_only_share_parts_without_combined_video(self):
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
                outputs=app.DownloadOutputs(False, "mp3", True),
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
            stack.enter_context(
                mock.patch.object(
                    app,
                    "convert_audio_sources",
                    return_value=(False, []),
                )
            )
            stack.enter_context(mock.patch.object(app.subprocess, "run"))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(app.main(), 0)

        self.assertEqual(len(commands), 2)
        self.assertIn("--print-to-file", commands[0])
        self.assertIn(
            ".video-only-",
            commands[1][commands[1].index("--output") + 1],
        )
        playlist_values = [
            command[command.index("--playlist-items") + 1]
            for command in commands
        ]
        self.assertEqual(playlist_values, ["1,3,4", "1,3,4"])
        rendered = stdout.getvalue()
        self.assertIn("[1/2] 正在取得独立 MP3", rendered)
        self.assertIn("[2/2] 正在处理：无声音视频", rendered)
        self.assertNotIn("正在处理：完整视频", rendered)

    def test_empty_plan_defense_runs_nothing_and_creates_no_output_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir, ExitStack() as stack:
            output_path = Path(temp_dir) / "must-not-exist"
            stack.enter_context(
                mock.patch.object(app, "OUTPUT_DIR", output_path)
            )
            (
                choose_quality,
                choose_combined_audio_quality,
            ) = self._patch_plan_inputs(
                stack,
                outputs=app.DownloadOutputs(False, "none", False),
                decision="start",
            )
            run_process = stack.enter_context(
                mock.patch.object(app, "run_process_with_pause")
            )
            combined_builder = stack.enter_context(
                mock.patch.object(app, "build_download_command")
            )
            audio_builder = stack.enter_context(
                mock.patch.object(app, "build_audio_source_command")
            )
            video_builder = stack.enter_context(
                mock.patch.object(app, "build_video_only_track_command")
            )
            subprocess_run = stack.enter_context(
                mock.patch.object(app.subprocess, "run")
            )

            self.assertEqual(app.main(), 1)

        choose_quality.assert_not_called()
        choose_combined_audio_quality.assert_not_called()
        run_process.assert_not_called()
        combined_builder.assert_not_called()
        audio_builder.assert_not_called()
        video_builder.assert_not_called()
        subprocess_run.assert_not_called()
        self.assertFalse(output_path.exists())


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
