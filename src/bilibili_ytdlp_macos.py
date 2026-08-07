#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Interactive macOS front end for user-authorized Bilibili downloads.

The frontend delegates media discovery and transfer to the bundled official
yt-dlp executable. It never writes browser cookies to a cookies.txt file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from bilibili_api import BilibiliAPIError, get_bangumi_info, get_video_info
from download_controls import (
    build_video_only_command,
    run_process_with_pause,
)
from download_options import (
    AUDIO_MODE_LABELS,
    Part,
    PartCatalog,
    PartSelection,
    SelectionError,
    build_audio_conversion,
    build_audio_source_command,
    catalog_from_bangumi_info,
    catalog_from_flat_json,
    catalog_from_video_info,
    choose_part_selection,
    compress_indices,
    current_part_from_url,
    playlist_arguments,
    read_audio_manifest,
    strip_part_query,
)
from runtime_compat import configure_ffmpeg


APP_NAME = "b站downloader"
FROZEN = bool(getattr(sys, "frozen", False))
ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
YTDLP_PATH = ROOT_DIR / "bin" / "yt-dlp"
OUTPUT_DIR = Path(
    os.environ.get(
        "BILIBILI_DOWNLOADER_OUTPUT_DIR",
        str(
            Path.home() / "Downloads" / APP_NAME
            if FROZEN
            else ROOT_DIR / "bilibili_video"
        ),
    )
).expanduser()
CACHE_ROOT = Path(
    os.environ.get(
        "BILIBILI_DOWNLOADER_CACHE_DIR",
        str(
            Path.home() / "Library" / "Caches" / APP_NAME
            if FROZEN
            else OUTPUT_DIR / ".bilibili-downloader-cache"
        ),
    )
).expanduser()
LOGIN_URL = "https://passport.bilibili.com/login"

BROWSER_SPECS = (
    {
        "id": "chrome",
        "label": "Google Chrome（推荐）",
        "app": "Google Chrome",
        "path": Path("/Applications/Google Chrome.app"),
    },
    {
        "id": "safari",
        "label": "Safari（可能需要给 Terminal 完全磁盘访问权限）",
        "app": "Safari",
        "path": Path("/Applications/Safari.app"),
    },
)

QUALITY_CHOICES = {
    "1": {
        "label": "最高可用画质（推荐）",
        "video_format": "bv",
        "fallback_format": "b",
    },
    "2": {
        "label": "最高不超过 4K",
        "video_format": "bv[height<=2160]",
        "fallback_format": "b[height<=2160]",
    },
    "3": {
        "label": "最高不超过 1080P",
        "video_format": "bv[height<=1080]",
        "fallback_format": "b[height<=1080]",
    },
    "4": {
        "label": "最高不超过 720P",
        "video_format": "bv[height<=720]",
        "fallback_format": "b[height<=720]",
    },
    "5": {
        "label": "最高不超过 480P",
        "video_format": "bv[height<=480]",
        "fallback_format": "b[height<=480]",
    },
}

COMBINED_AUDIO_QUALITY_CHOICES = {
    "1": {
        "label": "最高可用音质（推荐）",
        "format_ids": None,
    },
    "2": {
        "label": "AAC 最高不超过平台 192K 档（可回退 132K/64K）",
        "format_ids": ("30280", "30232", "30216"),
    },
    "3": {
        "label": "AAC 最高不超过平台 132K 档（可回退 64K）",
        "format_ids": ("30232", "30216"),
    },
    "4": {
        "label": "AAC 平台 64K 档",
        "format_ids": ("30216",),
    },
    "5": {
        "label": "杜比音频（严格；视频和账号必须实际提供）",
        "format_ids": ("30250",),
    },
    "6": {
        "label": "Hi-Res 无损 FLAC（严格；视频和账号必须实际提供）",
        "format_ids": ("30251",),
    },
}


class UserInputError(ValueError):
    pass


class DownloadOutputs(NamedTuple):
    """The independently selectable files produced by one download plan."""

    combined_video: bool
    audio_mode: str
    video_only: bool

    @property
    def has_any(self) -> bool:
        return (
            self.combined_video
            or self.audio_mode != "none"
            or self.video_only
        )


def normalize_bilibili_url(value: str) -> str:
    """Extract and validate an official Bilibili video reference.

    The clipboard text may include a title before the first HTTP(S) URL.
    Only official Bilibili/b23 hosts and supported video paths survive the
    validation below; arbitrary URLs and embedded credentials are rejected.
    """
    value = value.strip()
    if not value:
        raise UserInputError("视频编号或链接不能为空")

    copied_url = re.search(r"https?://[^\s]+", value, re.IGNORECASE)
    if copied_url:
        value = copied_url.group(0).rstrip(
            ".,!?;:，。！？；：、）)]}】》>\"'"
        )

    if value.isdigit():
        return "https://www.bilibili.com/video/av{}".format(value)

    av_match = re.fullmatch(r"av(\d+)", value, re.IGNORECASE)
    if av_match:
        return "https://www.bilibili.com/video/av{}".format(av_match.group(1))

    bv_match = re.fullmatch(r"BV([0-9A-Za-z]{10})", value, re.IGNORECASE)
    if bv_match:
        return "https://www.bilibili.com/video/BV{}".format(bv_match.group(1))

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UserInputError("请输入 AV 号、BV 号或完整的 Bilibili 链接")
    if parsed.username or parsed.password:
        raise UserInputError("链接中不能包含用户名或密码")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"b23.tv", "www.b23.tv"}:
        if not parsed.path or parsed.path == "/":
            raise UserInputError("Bilibili 短链接不完整")
        return value

    is_bilibili = hostname == "bilibili.com" or hostname.endswith(".bilibili.com")
    allowed_path = (
        parsed.path.startswith("/video/")
        or parsed.path.startswith("/bangumi/play/")
    )
    if not is_bilibili or not allowed_path:
        raise UserInputError("只支持官方 Bilibili 视频、番剧或 b23.tv 分享链接")
    return value


def available_browsers() -> list[dict[str, object]]:
    return [spec for spec in BROWSER_SPECS if spec["path"].exists()]


def build_common_command(
    browser_id: Optional[str],
    ffmpeg_path: str,
) -> list[str]:
    """Build the deterministic, privacy-conscious yt-dlp command prefix.

    User config files, plug-ins, and remote components are disabled so a
    packaged release behaves the same on a clean Mac. Browser cookies are
    passed directly to yt-dlp only when the user selects that browser.
    """
    command = [
        str(YTDLP_PATH),
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--no-cache-dir",
        "--ffmpeg-location",
        ffmpeg_path,
        "--newline",
    ]
    if browser_id:
        command.extend(["--cookies-from-browser", browser_id])
    else:
        command.append("--no-cookies-from-browser")
    return command


def build_list_formats_command(
    url: str,
    browser_id: Optional[str],
    ffmpeg_path: str,
    playlist_args: Sequence[str] = ("--no-playlist",),
) -> list[str]:
    return build_common_command(browser_id, ffmpeg_path) + [
        "--list-formats",
        *playlist_args,
        "--",
        url,
    ]


def build_download_command(
    url: str,
    browser_id: Optional[str],
    quality_key: str,
    audio_quality_key: str,
    playlist_args: Sequence[str],
    ffmpeg_path: str,
) -> list[str]:
    format_selector = build_combined_format_selector(
        quality_key,
        audio_quality_key,
    )
    return build_common_command(browser_id, ffmpeg_path) + [
        "--continue",
        "--part",
        "--format",
        format_selector,
        "--merge-output-format",
        "mp4/mkv",
        "--concurrent-fragments",
        "4",
        "--paths",
        str(OUTPUT_DIR),
        "--output",
        "%(title).150B [%(id)s].video-f%(format_id)s.%(ext)s",
        "--no-write-info-json",
        "--no-write-comments",
        "--no-mark-watched",
        *playlist_args,
        "--",
        url,
    ]


def build_combined_format_selector(
    quality_key: str,
    audio_quality_key: str,
) -> str:
    """Combine one video ceiling with the selected embedded audio tier.

    Standard AAC tiers fall back only to the explicitly documented lower
    tiers. Dolby and Hi-Res choices are strict so an unavailable special
    track never turns into ordinary AAC without telling the user.
    """
    try:
        quality = QUALITY_CHOICES[quality_key]
    except KeyError as exc:
        raise ValueError("unknown quality key: {}".format(quality_key)) from exc
    try:
        audio_quality = COMBINED_AUDIO_QUALITY_CHOICES[audio_quality_key]
    except KeyError as exc:
        raise ValueError(
            "unknown combined audio quality key: {}".format(audio_quality_key)
        ) from exc

    video_format = str(quality["video_format"])
    format_ids = audio_quality["format_ids"]
    if format_ids is None:
        return "{}+ba/{}".format(
            video_format,
            quality["fallback_format"],
        )
    return "/".join(
        "{}+{}".format(video_format, format_id)
        for format_id in format_ids
    )


def build_video_only_track_command(
    url: str,
    browser_id: Optional[str],
    quality_key: str,
    playlist_args: Sequence[str],
    ffmpeg_path: str,
) -> list[str]:
    return build_video_only_command(
        build_common_command(browser_id, ffmpeg_path),
        url,
        quality_key,
        playlist_args,
        OUTPUT_DIR,
    )


def build_part_probe_command(
    url: str,
    browser_id: Optional[str],
    ffmpeg_path: str,
    *,
    as_playlist: bool,
) -> list[str]:
    playlist_option = "--yes-playlist" if as_playlist else "--no-playlist"
    command = build_common_command(browser_id, ffmpeg_path)
    if as_playlist:
        command.append("--flat-playlist")
    return command + [
        "--dump-single-json",
        playlist_option,
        "--",
        url,
    ]


def _run_json_probe(
    command: Sequence[str],
) -> Tuple[Optional[dict], str]:
    """Run a metadata probe without letting diagnostic text pollute JSON."""
    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None, result.stderr.strip()
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return None, result.stderr.strip() or "yt-dlp 未返回可解析的元数据"
    if not isinstance(payload, dict):
        return None, "yt-dlp 返回了意外的元数据格式"
    return payload, ""


def _bvid_from_metadata(data: dict) -> Optional[str]:
    for key in ("bvid", "id", "display_id", "webpage_url", "original_url"):
        match = re.search(r"(BV[0-9A-Za-z]{10})", str(data.get(key) or ""))
        if match:
            return match.group(1)
    return None


def _current_part_from_metadata(data: dict, original_url: str) -> int:
    for key in ("webpage_url", "original_url"):
        index = current_part_from_url(str(data.get(key) or ""), 0)
        if index > 0:
            return index

    identifier = str(data.get("id") or "")
    id_match = re.search(r"_p(\d+)$", identifier, re.IGNORECASE)
    if id_match:
        return int(id_match.group(1))

    try:
        playlist_index = int(data.get("playlist_index"))
    except (TypeError, ValueError):
        playlist_index = 0
    if playlist_index > 0:
        return playlist_index
    return current_part_from_url(original_url)


def fetch_part_catalog(
    url: str,
    browser_id: Optional[str],
    ffmpeg_path: str,
) -> PartCatalog:
    """Resolve a URL into a best-effort catalog of titled parts.

    Normal BV/AV URLs first use public page metadata for fast titled results.
    Short links are canonicalized with yt-dlp, then a flat playlist probe is
    used as a fallback. Failure of every metadata route deliberately degrades
    to one current item instead of inventing playlist indices.
    """
    print("\n正在读取视频标题和分P/分集信息……")
    direct_error = ""
    if "/bangumi/play/" in urlsplit(url).path:
        try:
            info, requested_episode_id = get_bangumi_info(url)
            return catalog_from_bangumi_info(info, requested_episode_id)
        except (
            BilibiliAPIError,
            KeyError,
            SelectionError,
            TypeError,
            ValueError,
        ) as exc:
            direct_error = str(exc)

    try:
        info, requested_page = get_video_info(url)
        return catalog_from_video_info(info, requested_page)
    except (BilibiliAPIError, KeyError, TypeError, ValueError) as exc:
        if not direct_error:
            direct_error = str(exc)

    current_data, current_error = _run_json_probe(
        build_part_probe_command(
            url,
            browser_id,
            ffmpeg_path,
            as_playlist=False,
        )
    )
    if current_data:
        canonical_url = str(
            current_data.get("webpage_url")
            or current_data.get("original_url")
            or url
        )
        if "/bangumi/play/" in urlsplit(canonical_url).path:
            try:
                info, requested_episode_id = get_bangumi_info(canonical_url)
                return catalog_from_bangumi_info(info, requested_episode_id)
            except (
                BilibiliAPIError,
                KeyError,
                SelectionError,
                TypeError,
                ValueError,
            ):
                pass

        bvid = _bvid_from_metadata(current_data)
        if bvid:
            try:
                info, _ = get_video_info(bvid)
                return catalog_from_video_info(
                    info,
                    _current_part_from_metadata(current_data, url),
                )
            except (BilibiliAPIError, KeyError, TypeError, ValueError):
                pass

    canonical_url = url
    if current_data:
        canonical_url = str(
            current_data.get("webpage_url")
            or current_data.get("original_url")
            or url
        )
    playlist_data, playlist_error = _run_json_probe(
        build_part_probe_command(
            strip_part_query(canonical_url),
            browser_id,
            ffmpeg_path,
            as_playlist=True,
        )
    )
    if playlist_data:
        try:
            catalog = catalog_from_flat_json(
                json.dumps(playlist_data, ensure_ascii=False),
                canonical_url,
            )
            if "/bangumi/play/" in catalog.base_url:
                return catalog._replace(kind_label="分集")
            return catalog
        except SelectionError:
            pass

    details = playlist_error or current_error or direct_error
    if details:
        print("[提示] 完整分P信息读取失败，将按当前视频处理：{}".format(
            details.splitlines()[-1][:180]
        ))
    return PartCatalog(
        title=str((current_data or {}).get("title") or "当前视频"),
        parts=(Part(1, "当前视频"),),
        base_url=strip_part_query(canonical_url),
        current_url=canonical_url,
        current_index=1,
    )


def choose_login_source() -> Optional[dict[str, object]]:
    browsers = available_browsers()
    print("\n登录状态来源：")
    for index, browser in enumerate(browsers, start=1):
        print("  {}. {}".format(index, browser["label"]))
    anonymous_index = len(browsers) + 1
    print("  {}. 匿名下载（通常只有低清晰度）".format(anonymous_index))
    print("  0. 退出")

    while True:
        choice = input("请选择：").strip()
        if choice == "0":
            raise KeyboardInterrupt
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(browsers):
                return browsers[index - 1]
            if index == anonymous_index:
                return None
        print("请输入菜单中的数字。")


def offer_browser_login(browser: dict[str, object]) -> None:
    print(
        "\n下载器只会在当前进程中临时读取 {} 的 B 站 Cookie，"
        "不会导出或保存 Cookie。".format(browser["app"])
    )
    action = input(
        "已在该浏览器登录可直接回车；输入 L 打开 B 站官方登录页："
    ).strip().lower()
    if action != "l":
        return

    result = subprocess.run(
        ["/usr/bin/open", "-a", str(browser["app"]), LOGIN_URL],
        check=False,
    )
    if result.returncode != 0:
        print("无法打开浏览器，请手动访问：{}".format(LOGIN_URL))
    input("完成登录后回到此窗口，按回车继续：")


def prompt_video_url() -> str:
    while True:
        value = input(
            "\n请输入 AV号、BV号或 B站视频链接"
            "（可直接粘贴带标题的分享文字）："
        )
        try:
            return normalize_bilibili_url(value)
        except UserInputError as exc:
            print("[输入错误] {}".format(exc))


def choose_quality(*, prompt=input, write=print) -> str:
    write("\n视频画质选择：")
    for key, quality in QUALITY_CHOICES.items():
        write("  {}. {}".format(key, quality["label"]))
    write("  6. 查看当前登录状态可用的全部格式")

    while True:
        choice = prompt("请选择：").strip()
        if choice in QUALITY_CHOICES or choice == "6":
            return choice
        write("请输入菜单中的数字。")


def choose_combined_audio_quality(*, prompt=input, write=print) -> str:
    write("\n完整视频音质选择（这是视频内的声音）：")
    for key, quality in COMBINED_AUDIO_QUALITY_CHOICES.items():
        write("  {}. {}".format(key, quality["label"]))
    write("  7. 查看当前登录状态可用的全部格式")
    write(
        "  说明：平台码率档位是约值，实际码率以格式列表为准；"
        "此选择不会改变独立 MP3/FLAC 文件。"
    )

    while True:
        choice = prompt("请选择：").strip()
        if choice == "":
            return "1"
        if choice in COMBINED_AUDIO_QUALITY_CHOICES or choice == "7":
            return choice
        write("请输入 1 至 7，或直接回车选择最高可用音质。")


def prompt_yes_no(
    message: str,
    *,
    default: bool = False,
    prompt=input,
    write=print,
) -> bool:
    while True:
        answer = prompt(message).strip().lower()
        if answer == "":
            return default
        if answer in {"n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        write("请输入 Y 或 N；也可以直接回车使用默认选择。")


def choose_audio_mode(*, prompt=input, write=print) -> str:
    write("\n是否下载独立音频文件（只有声音、没有画面）？")
    write("  0. 不下载（默认）")
    write("  1. {}".format(AUDIO_MODE_LABELS["mp3"]))
    write("  2. {}".format(AUDIO_MODE_LABELS["flac"]))
    write(
        "  说明：FLAC 只接受平台实际提供的原生 FLAC/Hi-Res 音轨；"
        "若不可用会明确跳过，不会把 AAC 等有损音频伪装成无损。"
    )
    choices = {"": "none", "0": "none", "1": "mp3", "2": "flac"}
    while True:
        answer = prompt("请选择：").strip().lower()
        if answer in choices:
            return choices[answer]
        if answer in {"mp3", "flac"}:
            return answer
        write("请输入 0、1、2，或直接回车选择不下载。")


def choose_output_plan(*, prompt=input, write=print) -> DownloadOutputs:
    """Choose one or more real output types and reject an empty plan."""
    while True:
        write("\n文件输出选择（可以只选一种，也可以自由组合）：")
        combined_video = prompt_yes_no(
            "是否下载完整视频（有画面、有声音）？"
            "直接回车下载，输入 N 不下载：",
            default=True,
            prompt=prompt,
            write=write,
        )
        audio_mode = choose_audio_mode(prompt=prompt, write=write)
        video_only = prompt_yes_no(
            "\n是否下载无声音视频（只有画面）？"
            "输入 Y 下载，直接回车不下载：",
            prompt=prompt,
            write=write,
        )
        outputs = DownloadOutputs(
            combined_video,
            audio_mode,
            video_only,
        )
        if outputs.has_any:
            return outputs
        write(
            "\n[选择无效] 完整视频、独立音频和无声音视频不能全部不选，"
            "请至少选择一种输出。"
        )


def selection_summary(selection: PartSelection) -> str:
    total = len(selection.catalog.parts)
    if total <= 1:
        return (
            "单集"
            if selection.catalog.kind_label == "分集"
            else "单P视频"
        )
    if selection.mode == "current":
        part = next(
            (
                item
                for item in selection.catalog.parts
                if item.index == selection.catalog.current_index
            ),
            selection.catalog.parts[0],
        )
        if selection.catalog.kind_label == "分集":
            return "当前第{}集：{}".format(part.index, part.title)
        return "当前 P{}：{}".format(part.index, part.title)
    if selection.mode == "all":
        return "全部 {} 个{}".format(total, selection.catalog.kind_label)
    if selection.catalog.kind_label == "分集":
        return "第{}集（共 {} 集）".format(
            compress_indices(selection.indices),
            len(selection.indices),
        )
    return "P{}（共 {} 个）".format(
        compress_indices(selection.indices),
        len(selection.indices),
    )


def confirm_download_plan(
    selection: PartSelection,
    browser_label: str,
    quality_key: Optional[str],
    combined_audio_quality_key: Optional[str],
    outputs: DownloadOutputs,
    *,
    prompt=input,
    write=print,
) -> str:
    if outputs.audio_mode not in AUDIO_MODE_LABELS:
        raise ValueError("unsupported audio mode: {}".format(outputs.audio_mode))
    if not outputs.has_any:
        raise ValueError("download plan must contain at least one output")
    needs_video_quality = outputs.combined_video or outputs.video_only
    if needs_video_quality and quality_key not in QUALITY_CHOICES:
        raise ValueError("video output requires a quality choice")
    if (
        outputs.combined_video
        and combined_audio_quality_key not in COMBINED_AUDIO_QUALITY_CHOICES
    ):
        raise ValueError("combined video requires an audio quality choice")
    if not outputs.combined_video and combined_audio_quality_key is not None:
        raise ValueError("audio quality choice requires combined video output")

    write("\n" + "=" * 58)
    write("请确认下载计划")
    write("  视频：{}".format(selection.catalog.title))
    write("  链接：{}".format(selection.url))
    write(
        "  {}：{}".format(
            selection.catalog.kind_label,
            selection_summary(selection),
        )
    )
    write("  登录：{}".format(browser_label))
    write(
        "  画质：{}".format(
            QUALITY_CHOICES[quality_key]["label"]
            if needs_video_quality
            else "不适用（仅下载音频）"
        )
    )
    write(
        "  完整视频音质：{}".format(
            COMBINED_AUDIO_QUALITY_CHOICES[combined_audio_quality_key]["label"]
            if outputs.combined_video
            else "不适用（未选择完整视频）"
        )
    )
    write(
        "  完整视频：{}".format(
            "下载" if outputs.combined_video else "不下载"
        )
    )
    write("  无画面音频：{}".format(AUDIO_MODE_LABELS[outputs.audio_mode]))
    write(
        "  无声音视频：{}".format(
            "下载" if outputs.video_only else "不下载"
        )
    )
    write("  保存位置：{}".format(OUTPUT_DIR))
    write("=" * 58)
    selected_output_count = sum(
        (
            int(outputs.combined_video),
            int(outputs.audio_mode != "none"),
            int(outputs.video_only),
        )
    )
    if selected_output_count > 1:
        write("多种输出会增加下载流量、磁盘占用和处理时间。")
    write("直接回车开始；输入 R 重新选择；输入 Q 取消并退出。")

    while True:
        answer = prompt("你的选择：").strip().lower()
        if answer in {"", "s", "start", "开始"}:
            return "start"
        if answer in {"r", "redo", "重选", "重新选择"}:
            return "redo"
        if answer in {"q", "quit", "退出", "取消"}:
            return "quit"
        write("请直接回车开始，输入 R 重新选择，或输入 Q 退出。")


def audio_cache_paths(
    selection: PartSelection,
    audio_mode: str,
) -> Tuple[Path, Path]:
    """Return an isolated cache for one URL, part set, and audio mode.

    A stable hash allows retries to find their source audio while preventing
    different selections from sharing or deleting each other's temporary
    files. Frozen builds keep caches outside the signed application bundle.
    """
    identity = json.dumps(
        {
            "url": selection.url,
            "parts": selection.indices,
            "mode": audio_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    cache_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    cache_dir = CACHE_ROOT / cache_key
    return cache_dir, cache_dir / "audio-sources.jsonl"


def _path_is_inside(path: Path, directory: Path) -> bool:
    """Guard every cache deletion against a tampered manifest path."""
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def convert_audio_sources(
    audio_mode: str,
    ffmpeg_path: str,
    cache_dir: Path,
    manifest_path: Path,
) -> Tuple[bool, list[str]]:
    """Convert safe cached sources; return ``(cancelled, failures)``.

    Manifest paths are treated as untrusted until confirmed inside cache_dir.
    MP3 uses LAME V0. Strict FLAC accepts only a FLAC source and performs a
    lossless re-encode into a standard FLAC file; it is not byte-for-byte
    preservation of the platform response. Temporary conversion output is
    deleted on failure/cancel, while source audio remains available to retry.
    """
    sources = read_audio_manifest(manifest_path)
    if not sources:
        return False, ["未取得可处理的音频源"]

    failures = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(sources, start=1):
        label = source.title or source.video_id or source.path.name
        if not _path_is_inside(source.path, cache_dir):
            failures.append("{}（缓存路径异常）".format(label))
            continue
        if audio_mode == "flac" and not source.codec.startswith("flac"):
            failures.append("{}（源音轨不是 FLAC）".format(label))
            continue

        conversion = build_audio_conversion(
            source,
            audio_mode,
            ffmpeg_path,
            OUTPUT_DIR,
            cache_dir,
        )
        if (
            conversion.destination.is_file()
            and conversion.destination.stat().st_size > 0
        ):
            print(
                "[音频 {}/{}] 已存在，跳过：{}".format(
                    index,
                    len(sources),
                    conversion.destination.name,
                )
            )
            _unlink_if_present(source.path)
            continue

        _unlink_if_present(conversion.temporary)
        print(
            "\n[音频 {}/{}] 正在生成 {}：{}\n".format(
                index,
                len(sources),
                "MP3 V0" if audio_mode == "mp3" else "无损 FLAC",
                conversion.destination.name,
            )
        )
        result = run_process_with_pause(conversion.command)
        if result.cancelled:
            _unlink_if_present(conversion.temporary)
            return True, failures
        if (
            result.returncode != 0
            or not conversion.temporary.is_file()
            or conversion.temporary.stat().st_size == 0
        ):
            _unlink_if_present(conversion.temporary)
            failures.append(label)
            continue

        conversion.temporary.replace(conversion.destination)
        _unlink_if_present(source.path)

    if not failures:
        _unlink_if_present(manifest_path)
        try:
            cache_dir.rmdir()
            cache_dir.parent.rmdir()
        except OSError:
            pass
    return False, failures


def show_failure_hint(browser_id: Optional[str]) -> None:
    if browser_id == "chrome":
        print(
            "\n[提示] 请确认 Chrome 中已登录 B 站。首次读取登录态时，"
            "macOS 可能询问是否允许访问“Chrome Safe Storage”钥匙串。"
        )
    elif browser_id == "safari":
        print(
            "\n[提示] 请确认 Safari 中已登录 B 站。若出现 Operation not "
            "permitted，请在“系统设置 → 隐私与安全性 → 完全磁盘访问”中"
            "允许 Terminal，或改用 Chrome。"
        )
    else:
        print("\n[提示] 当前为匿名模式；登录后通常可以看到更多画质。")
    print("登录只能解锁该账号本身有权观看的画质。")


def ensure_runtime() -> str:
    if not YTDLP_PATH.is_file() or not os.access(YTDLP_PATH, os.X_OK):
        raise RuntimeError(
            "缺少可执行的 bin/yt-dlp，请重新运行 setup_macos.command"
        )
    return configure_ffmpeg()


def show_available_formats(
    selection: PartSelection,
    browser_id: Optional[str],
    ffmpeg_path: str,
    menu_label: str,
) -> None:
    if (
        len(selection.catalog.parts) > 1
        and selection.mode != "current"
    ):
        preview_index = selection.indices[0]
        preview_label = (
            "第{}集".format(preview_index)
            if selection.catalog.kind_label == "分集"
            else "P{}".format(preview_index)
        )
        playlist_args = [
            "--yes-playlist",
            "--playlist-items",
            str(preview_index),
        ]
        print(
            "\n正在读取首个已选项（{}）可用的视频与音频格式……".format(
                preview_label
            )
        )
        print("[提示] 不同分P/分集实际提供的格式可能不同。\n")
    else:
        playlist_args = ["--no-playlist"]
        print("\n正在读取当前所选视频可用的视频与音频格式……\n")
    result = subprocess.run(
        build_list_formats_command(
            selection.url,
            browser_id,
            ffmpeg_path,
            playlist_args,
        ),
        check=False,
    )
    if result.returncode != 0:
        show_failure_hint(browser_id)
    input("\n查看完成，按回车返回{}菜单：".format(menu_label))


def run_self_test() -> int:
    """Print a non-network runtime report for release-package verification."""
    ffmpeg_path = ensure_runtime()
    yt_dlp_version = subprocess.run(
        [str(YTDLP_PATH), "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    ffmpeg_version = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-version"],
        check=False,
        capture_output=True,
        text=True,
    )
    report = {
        "frozen": FROZEN,
        "resource_root": str(ROOT_DIR),
        "yt_dlp": str(YTDLP_PATH),
        "yt_dlp_version": yt_dlp_version.stdout.splitlines()[0]
        if yt_dlp_version.stdout
        else "",
        "ffmpeg": ffmpeg_path,
        "ffmpeg_version": ffmpeg_version.stdout.splitlines()[0]
        if ffmpeg_version.stdout
        else "",
        "output_dir": str(OUTPUT_DIR),
        "cache_root": str(CACHE_ROOT),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (
        yt_dlp_version.returncode != 0
        or ffmpeg_version.returncode != 0
        or not FROZEN
    ):
        return 1
    return 0


def main() -> int:
    print("=" * 58)
    print("{} · 浏览器登录态高清版".format(APP_NAME))
    print("Cookie 不会复制到文件，也不会保存到项目中。")
    print("=" * 58)

    try:
        ffmpeg_path = ensure_runtime()
        browser = choose_login_source()
        browser_id = str(browser["id"]) if browser else None
        if browser:
            offer_browser_login(browser)

        while True:
            url = prompt_video_url()
            catalog = fetch_part_catalog(url, browser_id, ffmpeg_path)
            choose_another_url = False

            while True:
                selection = choose_part_selection(catalog)
                if selection is None:
                    print("\n已全不选。本链接不会启动任何下载，请重新输入链接。")
                    choose_another_url = True
                    break

                outputs = choose_output_plan()
                quality_key = None
                combined_audio_quality_key = None
                if outputs.combined_video or outputs.video_only:
                    quality_key = choose_quality()
                    while quality_key == "6":
                        show_available_formats(
                            selection,
                            browser_id,
                            ffmpeg_path,
                            "视频画质",
                        )
                        quality_key = choose_quality()
                if outputs.combined_video:
                    combined_audio_quality_key = (
                        choose_combined_audio_quality()
                    )
                    while combined_audio_quality_key == "7":
                        show_available_formats(
                            selection,
                            browser_id,
                            ffmpeg_path,
                            "完整视频音质",
                        )
                        combined_audio_quality_key = (
                            choose_combined_audio_quality()
                        )

                decision = confirm_download_plan(
                    selection,
                    str(browser["app"]) if browser else "匿名",
                    quality_key,
                    combined_audio_quality_key,
                    outputs,
                )
                if decision == "redo":
                    print("\n好的，重新选择本视频的下载方案。")
                    continue
                if decision == "quit":
                    print("\n已取消，未启动下载。")
                    return 0
                break

            if choose_another_url:
                continue
            break

        task_total = sum(
            (
                int(outputs.combined_video),
                int(outputs.audio_mode != "none"),
                int(outputs.video_only),
            )
        )
        if task_total <= 0:
            raise RuntimeError("下载计划没有选择任何输出")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        selected_playlist_args = playlist_arguments(selection)
        print("\n已确认，开始执行下载计划。")
        print(
            "下载过程中按 Control+C 可暂停；暂停后会询问继续还是退出。"
        )

        task_index = 0
        failures = []

        if outputs.combined_video:
            task_index += 1
            assert quality_key is not None
            assert combined_audio_quality_key is not None
            print("\n[{}/{}] 正在处理：完整视频\n".format(
                task_index,
                task_total,
            ))
            result = run_process_with_pause(
                build_download_command(
                    selection.url,
                    browser_id,
                    quality_key,
                    combined_audio_quality_key,
                    selected_playlist_args,
                    ffmpeg_path,
                )
            )
            if result.cancelled:
                print(
                    "\n已按你的选择退出。未完成的 .part 文件会保留，"
                    "下次使用相同链接和选项可继续下载。"
                )
                return 0
            if result.returncode != 0:
                failures.append("完整视频")
                if task_total > 1:
                    print(
                        "\n完整视频下载失败；"
                        "将继续处理其他已经选择的输出。"
                    )
                else:
                    print("\n完整视频下载失败。")

        if outputs.audio_mode != "none":
            task_index += 1
            audio_mode = outputs.audio_mode
            cache_dir, manifest_path = audio_cache_paths(
                selection,
                audio_mode,
            )
            cache_dir.mkdir(parents=True, exist_ok=True)
            print(
                "\n[{}/{}] 正在取得独立{}所需的源音轨……\n".format(
                    task_index,
                    task_total,
                    " MP3" if audio_mode == "mp3" else " FLAC",
                )
            )
            audio_download = run_process_with_pause(
                build_audio_source_command(
                    build_common_command(browser_id, ffmpeg_path),
                    selection.url,
                    audio_mode,
                    selected_playlist_args,
                    cache_dir,
                    manifest_path,
                )
            )
            if audio_download.cancelled:
                print(
                    "\n已按你的选择退出。未完成的 .part 文件会保留，"
                    "已下载的音频源会保留供下次继续。"
                )
                return 0

            audio_sources = read_audio_manifest(manifest_path)
            audio_cancelled, audio_failures = convert_audio_sources(
                audio_mode,
                ffmpeg_path,
                cache_dir,
                manifest_path,
            )
            if audio_cancelled:
                print(
                    "\n已按你的选择退出。音频源已保留，"
                    "下次可重新生成输出文件。"
                )
                return 0

            expected_audio_count = len(selection.indices)
            missing_audio_count = max(
                0,
                expected_audio_count - len(audio_sources),
            )
            if (
                audio_download.returncode != 0
                or audio_failures
                or missing_audio_count
            ):
                if audio_mode == "flac":
                    print(
                        "\n[提示] 部分或全部所选分P未向当前账号提供"
                        "平台原生 FLAC，因此没有为这些分P生成“假无损”文件。"
                    )
                    failures.append("独立 FLAC")
                else:
                    print(
                        "\n[提示] 部分独立 MP3 未能生成；"
                        "已成功的文件仍然保留。"
                    )
                    failures.append("独立 MP3")

        if outputs.video_only:
            task_index += 1
            assert quality_key is not None
            print("\n[{}/{}] 正在处理：无声音视频\n".format(
                task_index,
                task_total,
            ))
            video_only_result = run_process_with_pause(
                build_video_only_track_command(
                    selection.url,
                    browser_id,
                    quality_key,
                    selected_playlist_args,
                    ffmpeg_path,
                )
            )
            if video_only_result.cancelled:
                print(
                    "\n已按你的选择退出。未完成的 .part 文件会保留，"
                    "下次使用相同链接和选项可继续下载。"
                )
                return 0
            if video_only_result.returncode != 0:
                failures.append("无声音视频")
                print("\n无声音视频下载失败，其他已完成文件不受影响。")

        if failures:
            print(
                "\n以下所选输出未能全部完成：{}".format(
                    "、".join(failures)
                )
            )
            show_failure_hint(browser_id)
            subprocess.run(["/usr/bin/open", str(OUTPUT_DIR)], check=False)
            return 1

        print("\n全部所选文件下载完成，保存在：{}".format(OUTPUT_DIR))
        subprocess.run(["/usr/bin/open", str(OUTPUT_DIR)], check=False)
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")
        return 0
    except RuntimeError as exc:
        print("\n[运行环境错误] {}".format(exc))
        return 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(run_self_test())
    sys.exit(main())
