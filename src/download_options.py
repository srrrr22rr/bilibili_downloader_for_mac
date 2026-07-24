#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Download-plan helpers for part selection and audio output conversion."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


AUDIO_MODE_LABELS = {
    "none": "不保留独立音频",
    "mp3": "MP3 V0（高质量有损，兼容性好）",
    "flac": "FLAC（仅平台实际提供原生 FLAC 时生成）",
}

AUDIO_SOURCE_SELECTORS = {
    # MP3 may start from the best available lossy source, but prefers FLAC to
    # avoid adding a second lossy generation when the account can access it.
    "mp3": "ba[acodec^=flac]/ba",
    # Strict FLAC intentionally has no slash fallback: AAC/Dolby must never be
    # relabeled as lossless merely because the destination suffix is .flac.
    "flac": "ba[acodec^=flac]",
}

AUDIO_SOURCE_OUTPUT = (
    "%(title).125B [%(id)s].audio-source-f%(format_id)s.%(ext)s"
)

AUDIO_MANIFEST_TEMPLATE = (
    'after_move:{"filepath":%(filepath)j,"acodec":%(acodec)j,'
    '"id":%(id)j,"title":%(title)j,'
    '"playlist_index":%(playlist_index)j}'
)


class SelectionError(ValueError):
    pass


class Part(NamedTuple):
    index: int
    title: str


class PartCatalog(NamedTuple):
    """Validated part metadata with canonical base/current URLs."""
    title: str
    parts: tuple[Part, ...]
    base_url: str
    current_url: str
    current_index: int
    kind_label: str = "分P"


class PartSelection(NamedTuple):
    """One current/all/custom selection using 1-based part indices."""
    catalog: PartCatalog
    indices: tuple[int, ...]
    mode: str

    @property
    def url(self) -> str:
        if self.mode == "current" or len(self.catalog.parts) <= 1:
            return self.catalog.current_url
        return self.catalog.base_url


class AudioSource(NamedTuple):
    """A fully moved source file recorded by yt-dlp's after_move hook."""
    path: Path
    codec: str
    video_id: str
    title: str
    playlist_index: Optional[int]


class AudioConversion(NamedTuple):
    """A source, atomic temporary output, destination, and FFmpeg command."""
    source: Path
    temporary: Path
    destination: Path
    command: list[str]


def current_part_from_url(url: str, default: int = 1) -> int:
    values = parse_qs(urlsplit(url).query).get("p", [])
    if not values:
        return default
    try:
        return int(values[-1])
    except (TypeError, ValueError):
        return default


def strip_part_query(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "p"
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def catalog_from_video_info(
    info: dict,
    requested_page: Optional[int],
) -> PartCatalog:
    bvid = str(info["bvid"])
    base_url = "https://www.bilibili.com/video/{}".format(bvid)
    raw_pages = info.get("pages") or []
    parts = tuple(
        Part(
            int(page.get("page") or index),
            str(page.get("part") or "P{}".format(index)).replace("\n", " "),
        )
        for index, page in enumerate(raw_pages, start=1)
    )
    if not parts:
        parts = (Part(1, str(info.get("title") or "当前视频")),)

    requested = requested_page or 1
    valid_indices = {part.index for part in parts}
    current_index = requested if requested in valid_indices else 1
    current_url = (
        "{}?p={}".format(base_url, current_index)
        if len(parts) > 1
        else base_url
    )
    return PartCatalog(
        title=str(info.get("title") or bvid).replace("\n", " "),
        parts=parts,
        base_url=base_url,
        current_url=current_url,
        current_index=current_index,
    )


def catalog_from_flat_json(payload: str, original_url: str) -> PartCatalog:
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SelectionError("无法解析分P信息") from exc

    entries = [entry for entry in (data.get("entries") or []) if entry]
    if not entries:
        return PartCatalog(
            title=str(data.get("title") or data.get("id") or "当前视频"),
            parts=(Part(1, "当前视频"),),
            base_url=strip_part_query(
                str(data.get("webpage_url") or original_url)
            ),
            current_url=str(data.get("webpage_url") or original_url),
            current_index=1,
        )

    parts = tuple(
        Part(
            index,
            str(entry.get("title") or "P{}".format(index)).replace("\n", " "),
        )
        for index, entry in enumerate(entries, start=1)
    )
    entry_urls = [
        str(entry.get("url"))
        for entry in entries
        if entry.get("url")
    ]
    canonical_url = entry_urls[0] if entry_urls else str(
        data.get("webpage_url") or original_url
    )
    base_url = strip_part_query(canonical_url)
    current_index = current_part_from_url(
        str(data.get("webpage_url") or original_url),
        current_part_from_url(original_url),
    )
    if not 1 <= current_index <= len(parts):
        current_index = 1
    current_url = (
        entry_urls[current_index - 1]
        if len(entry_urls) >= current_index
        else "{}?p={}".format(base_url, current_index)
    )
    return PartCatalog(
        title=str(data.get("title") or data.get("id") or "多P视频"),
        parts=parts,
        base_url=base_url,
        current_url=current_url,
        current_index=current_index,
    )


def parse_part_spec(value: str, total: int) -> tuple[int, ...]:
    """Parse inclusive, 1-based ranges such as ``1,3-5``."""
    normalized = (
        value.strip()
        .replace("，", ",")
        .replace("、", ",")
        .replace("—", "-")
        .replace("–", "-")
    )
    if not normalized:
        raise SelectionError("请选择至少一个分P")

    indices: list[int] = []
    seen: set[int] = set()
    for token in normalized.split(","):
        token = token.strip()
        if not token:
            raise SelectionError("分P编号之间不能有空项")
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if range_match:
            start, stop = (int(item) for item in range_match.groups())
            if start > stop:
                raise SelectionError("范围起点不能大于终点")
            values = range(start, stop + 1)
        elif token.isdigit():
            values = (int(token),)
        else:
            raise SelectionError("请使用类似 1,3-5 的编号格式")

        for index in values:
            if index < 1 or index > total:
                raise SelectionError(
                    "分P编号 {} 超出 1-{} 范围".format(index, total)
                )
            if index not in seen:
                indices.append(index)
                seen.add(index)
    return tuple(sorted(indices))


def compress_indices(indices: Sequence[int]) -> str:
    ordered = sorted(set(indices))
    if not ordered:
        return ""

    groups = []
    start = previous = ordered[0]
    for index in ordered[1:]:
        if index == previous + 1:
            previous = index
            continue
        groups.append(
            str(start) if start == previous else "{}-{}".format(start, previous)
        )
        start = previous = index
    groups.append(
        str(start) if start == previous else "{}-{}".format(start, previous)
    )
    return ",".join(groups)


def playlist_arguments(selection: PartSelection) -> list[str]:
    if len(selection.catalog.parts) <= 1 or selection.mode == "current":
        return ["--no-playlist"]
    return [
        "--yes-playlist",
        "--playlist-items",
        ",".join(str(index) for index in selection.indices),
    ]


def render_part_lines(
    catalog: PartCatalog,
    *,
    show_all: bool = False,
) -> list[str]:
    total = len(catalog.parts)
    if show_all or total <= 30:
        visible = list(catalog.parts)
        hidden = False
    else:
        visible = list(catalog.parts[:20]) + list(catalog.parts[-5:])
        hidden = True

    lines = [
        "  P{:<3} {}".format(part.index, part.title[:72])
        for part in visible
    ]
    if hidden:
        lines.insert(20, "  …… 中间 {} 个分P已折叠，输入 L 查看全部 ……".format(
            total - 25
        ))
    return lines


def choose_part_selection(
    catalog: PartCatalog,
    *,
    prompt: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> Optional[PartSelection]:
    total = len(catalog.parts)
    if total <= 1:
        write("\n检测结果：单P视频，无需选集。")
        return PartSelection(catalog, (1,), "current")

    write("\n检测到 {} 个{}：".format(total, catalog.kind_label))
    for line in render_part_lines(catalog):
        write(line)
    write(
        "\n选择方法：直接回车=当前 P{}；A=全部；N=全不选；"
        "也可输入 1,3-5；L=查看完整列表。".format(catalog.current_index)
    )

    while True:
        answer = prompt("请选择分P：").strip()
        lowered = answer.lower()
        if answer == "":
            return PartSelection(
                catalog,
                (catalog.current_index,),
                "current",
            )
        if lowered in {"a", "all", "*"} or answer in {"全部", "全选"}:
            return PartSelection(
                catalog,
                tuple(range(1, total + 1)),
                "all",
            )
        if lowered in {"n", "none", "0"} or answer in {"全不选", "不选"}:
            return None
        if lowered == "l" or answer in {"列表", "全部列表"}:
            for line in render_part_lines(catalog, show_all=True):
                write(line)
            continue
        try:
            indices = parse_part_spec(answer, total)
        except SelectionError as exc:
            write("[输入错误] {}".format(exc))
            continue
        mode = "all" if len(indices) == total else "custom"
        return PartSelection(catalog, indices, mode)


def build_audio_source_command(
    common_command: Sequence[str],
    url: str,
    audio_mode: str,
    playlist_args: Sequence[str],
    cache_dir: Path,
    manifest_path: Path,
) -> list[str]:
    """Build a source-audio download and append completed files to JSONL.

    ``after_move`` means incomplete ``.part`` files never enter the manifest.
    """
    if audio_mode not in AUDIO_SOURCE_SELECTORS:
        raise ValueError("unsupported audio mode: {}".format(audio_mode))
    return list(common_command) + [
        "--continue",
        "--part",
        "--format",
        AUDIO_SOURCE_SELECTORS[audio_mode],
        "--concurrent-fragments",
        "4",
        "--paths",
        str(cache_dir),
        "--output",
        AUDIO_SOURCE_OUTPUT,
        "--print-to-file",
        AUDIO_MANIFEST_TEMPLATE,
        str(manifest_path),
        "--no-write-info-json",
        "--no-write-comments",
        "--no-mark-watched",
        *playlist_args,
        "--",
        url,
    ]


def read_audio_manifest(manifest_path: Path) -> list[AudioSource]:
    """Read unique manifest entries whose files still exist."""
    if not manifest_path.is_file():
        return []

    sources = []
    seen = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            path = Path(item["filepath"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        raw_index = item.get("playlist_index")
        try:
            playlist_index = int(raw_index) if raw_index is not None else None
        except (TypeError, ValueError):
            playlist_index = None
        sources.append(
            AudioSource(
                path=path,
                codec=str(item.get("acodec") or "unknown").lower(),
                video_id=str(item.get("id") or ""),
                title=str(item.get("title") or ""),
                playlist_index=playlist_index,
            )
        )
    return sources


def _safe_codec_name(codec: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", codec).strip("-") or "unknown"


def build_audio_conversion(
    source: AudioSource,
    audio_mode: str,
    ffmpeg_path: str,
    output_dir: Path,
    cache_dir: Path,
) -> AudioConversion:
    """Build MP3 V0 or lossless-FLAC conversion into an atomic temp file.

    The caller is responsible for validating the source codec for strict
    FLAC, running the command, and renaming ``temporary`` only after success.
    """
    if audio_mode not in {"mp3", "flac"}:
        raise ValueError("unsupported audio mode: {}".format(audio_mode))

    marker = ".audio-source-"
    source_name = source.path.name
    base_name = (
        source_name.split(marker, 1)[0]
        if marker in source_name
        else source.path.stem
    )
    if audio_mode == "mp3":
        suffix = ".audio-MP3-V0-source-{}.mp3".format(
            _safe_codec_name(source.codec)
        )
    else:
        suffix = ".audio-FLAC-original.flac"
    destination = output_dir / "{}{}".format(base_name, suffix)
    temporary = cache_dir / ".{}.part.{}".format(
        destination.name,
        audio_mode,
    )

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source.path),
        "-map",
        "0:a:0",
        "-vn",
        "-map_metadata",
        "0",
    ]
    if audio_mode == "mp3":
        command.extend(
            [
                "-c:a",
                "libmp3lame",
                "-q:a",
                "0",
                "-compression_level:a",
                "0",
                "-ac",
                "2",
                "-id3v2_version",
                "3",
                "-f",
                "mp3",
            ]
        )
    else:
        command.extend(
            [
                "-c:a",
                "flac",
                "-compression_level:a",
                "8",
                "-f",
                "flac",
            ]
        )
    command.append(str(temporary))
    return AudioConversion(source.path, temporary, destination, command)
