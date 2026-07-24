#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path

import download_options as options


class CatalogTests(unittest.TestCase):
    def test_catalog_from_video_info_keeps_titles_and_current_part(self):
        catalog = options.catalog_from_video_info(
            {
                "bvid": "BV1xW411E739",
                "title": "多P示例",
                "pages": [
                    {"page": 1, "part": "第一段"},
                    {"page": 2, "part": "第二段"},
                    {"page": 3, "part": "第三段"},
                ],
            },
            2,
        )
        self.assertEqual(catalog.current_index, 2)
        self.assertEqual(catalog.parts[1].title, "第二段")
        self.assertEqual(
            catalog.base_url,
            "https://www.bilibili.com/video/BV1xW411E739",
        )
        self.assertTrue(catalog.current_url.endswith("?p=2"))

    def test_flat_catalog_uses_canonical_entry_url(self):
        payload = json.dumps(
            {
                "id": "BV1xW411E739",
                "title": "示例",
                "entries": [
                    {
                        "url": "https://www.bilibili.com/video/BV1xW411E739?p=1"
                    },
                    {
                        "url": "https://www.bilibili.com/video/BV1xW411E739?p=2"
                    },
                ],
            }
        )
        catalog = options.catalog_from_flat_json(
            payload,
            "https://b23.tv/example",
        )
        self.assertEqual(len(catalog.parts), 2)
        self.assertNotIn("p=", catalog.base_url)


class PartSelectionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = options.PartCatalog(
            "示例",
            tuple(options.Part(i, "P{}".format(i)) for i in range(1, 7)),
            "https://www.bilibili.com/video/BV1xW411E739",
            "https://www.bilibili.com/video/BV1xW411E739?p=2",
            2,
        )

    def test_parses_numbers_ranges_chinese_comma_and_deduplicates(self):
        self.assertEqual(
            options.parse_part_spec("1，3-5,5", 6),
            (1, 3, 4, 5),
        )
        self.assertEqual(options.compress_indices((1, 3, 4, 5)), "1,3-5")

    def test_rejects_out_of_range_and_descending_range(self):
        for value in ("0", "7", "5-3", "1,,2", "abc"):
            with self.assertRaises(options.SelectionError):
                options.parse_part_spec(value, 6)

    def test_current_all_custom_and_none_choices(self):
        current = options.choose_part_selection(
            self.catalog,
            prompt=lambda _: "",
            write=lambda _: None,
        )
        self.assertEqual(current.indices, (2,))
        self.assertEqual(current.mode, "current")
        self.assertEqual(options.playlist_arguments(current), ["--no-playlist"])

        all_parts = options.choose_part_selection(
            self.catalog,
            prompt=lambda _: "A",
            write=lambda _: None,
        )
        self.assertEqual(all_parts.indices, (1, 2, 3, 4, 5, 6))
        self.assertIn("--playlist-items", options.playlist_arguments(all_parts))

        custom = options.choose_part_selection(
            self.catalog,
            prompt=lambda _: "1,3-4",
            write=lambda _: None,
        )
        self.assertEqual(custom.indices, (1, 3, 4))
        self.assertEqual(custom.url, self.catalog.base_url)

        none = options.choose_part_selection(
            self.catalog,
            prompt=lambda _: "N",
            write=lambda _: None,
        )
        self.assertIsNone(none)

    def test_removes_every_p_query_but_preserves_other_parameters(self):
        self.assertEqual(
            options.strip_part_query(
                "https://www.bilibili.com/video/BV1?p=2&foo=x&p=3"
            ),
            "https://www.bilibili.com/video/BV1?foo=x",
        )


class AudioTests(unittest.TestCase):
    def test_mp3_source_prefers_flac_then_falls_back(self):
        command = options.build_audio_source_command(
            ["yt-dlp"],
            "https://www.bilibili.com/video/BV1xW411E739",
            "mp3",
            ["--yes-playlist", "--playlist-items", "1,3"],
            Path("/tmp/cache"),
            Path("/tmp/cache/manifest.jsonl"),
        )
        self.assertEqual(
            command[command.index("--format") + 1],
            "ba[acodec^=flac]/ba",
        )
        self.assertIn("--print-to-file", command)
        self.assertIn("--playlist-items", command)

    def test_flac_source_is_strict_and_never_falls_back_to_lossy(self):
        command = options.build_audio_source_command(
            ["yt-dlp"],
            "https://www.bilibili.com/video/BV1xW411E739",
            "flac",
            ["--no-playlist"],
            Path("/tmp/cache"),
            Path("/tmp/cache/manifest.jsonl"),
        )
        selector = command[command.index("--format") + 1]
        self.assertEqual(selector, "ba[acodec^=flac]")
        self.assertNotIn("/", selector)
        self.assertNotIn("--extract-audio", command)

    def test_manifest_and_conversion_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / (
                "标题 [BV1_p1].audio-source-f30251.m4a"
            )
            source_path.write_bytes(b"source")
            manifest = root / "manifest.jsonl"
            manifest.write_text(
                json.dumps(
                    {
                        "filepath": str(source_path),
                        "acodec": "flac",
                        "id": "BV1_p1",
                        "title": "标题",
                        "playlist_index": 1,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            source = options.read_audio_manifest(manifest)[0]
            mp3 = options.build_audio_conversion(
                source,
                "mp3",
                "/usr/local/bin/ffmpeg",
                root,
                root,
            )
            self.assertIn("libmp3lame", mp3.command)
            self.assertIn("-q:a", mp3.command)
            self.assertTrue(mp3.destination.name.endswith(".mp3"))

            flac = options.build_audio_conversion(
                source,
                "flac",
                "/usr/local/bin/ffmpeg",
                root,
                root,
            )
            self.assertIn("flac", flac.command)
            self.assertTrue(flac.destination.name.endswith(".flac"))
            self.assertIn("FLAC-original", flac.destination.name)


if __name__ == "__main__":
    unittest.main()
