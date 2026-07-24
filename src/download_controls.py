#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Helpers for pausable yt-dlp jobs and optional source tracks."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Callable, NamedTuple, Sequence


VIDEO_ONLY_SELECTORS = {
    "1": "bv",
    "2": "bv[height<=2160]",
    "3": "bv[height<=1080]",
    "4": "bv[height<=720]",
    "5": "bv[height<=480]",
}

VIDEO_ONLY_OUTPUT = (
    "%(title).140B [%(id)s].video-only-f%(format_id)s.%(ext)s"
)


class ProcessResult(NamedTuple):
    returncode: int
    cancelled: bool


def build_video_only_command(
    common_command: Sequence[str],
    url: str,
    quality_key: str,
    playlist_args: Sequence[str],
    output_dir: Path,
) -> list[str]:
    """Build a separate video-only source-track download."""
    try:
        selector = VIDEO_ONLY_SELECTORS[quality_key]
    except KeyError as exc:
        raise ValueError("unknown quality key: {}".format(quality_key)) from exc
    return list(common_command) + [
        "--continue",
        "--part",
        "--format",
        selector,
        "--concurrent-fragments",
        "4",
        "--paths",
        str(output_dir),
        "--output",
        VIDEO_ONLY_OUTPUT,
        "--no-write-info-json",
        "--no-write-comments",
        "--no-mark-watched",
        *playlist_args,
        "--",
        url,
    ]


def _signal_process_group(
    process: subprocess.Popen,
    sig: signal.Signals,
    signal_group: Callable[[int, int], None],
) -> bool:
    if process.poll() is not None:
        return False
    try:
        signal_group(process.pid, sig)
    except ProcessLookupError:
        return False
    return True


def terminate_process_group(
    process: subprocess.Popen,
    *,
    paused: bool,
    signal_group: Callable[[int, int], None] = os.killpg,
) -> int:
    """Stop a child process group with CONT → INT → TERM → KILL.

    A paused process must receive SIGCONT before graceful signals can run
    cleanup handlers. SIGKILL is only the final fallback after bounded waits.
    """
    if process.poll() is not None:
        return int(process.returncode or 0)

    if paused:
        _signal_process_group(process, signal.SIGCONT, signal_group)

    for sig, timeout in ((signal.SIGINT, 8), (signal.SIGTERM, 3)):
        if not _signal_process_group(process, sig, signal_group):
            break
        try:
            return int(process.wait(timeout=timeout))
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            continue

    if process.poll() is None:
        _signal_process_group(process, signal.SIGKILL, signal_group)
    try:
        return int(process.wait(timeout=3))
    except subprocess.TimeoutExpired:
        return 130


def _ask_exit_while_paused(
    prompt: Callable[[str], str],
    write: Callable[[str], None],
) -> bool:
    write("\n[已暂停] 当前下载进程及其合并子进程均已暂停。")
    while True:
        try:
            answer = prompt(
                "是否退出本次任务？输入 Y 退出；直接回车继续"
                "（保持此提示不操作即持续暂停）："
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return True
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        write("请输入 Y 退出，或直接回车继续。")


def run_process_with_pause(
    command: Sequence[str],
    *,
    prompt: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    signal_group: Callable[[int, int], None] = os.killpg,
) -> ProcessResult:
    """Run a macOS/POSIX process group with interactive pause semantics.

    The parent receives Control+C, stops the full yt-dlp/FFmpeg process group,
    and asks whether to resume or terminate. ``cancelled`` is true only when
    the user chose to exit from that paused state.
    """
    process = popen_factory(
        list(command),
        # Child tools must not consume answers intended for this frontend.
        stdin=subprocess.DEVNULL,
        # The child PID becomes its process-group ID, so killpg also reaches
        # merger/converter subprocesses spawned by yt-dlp.
        start_new_session=True,
    )
    paused = False

    try:
        while True:
            try:
                return ProcessResult(int(process.wait()), False)
            except KeyboardInterrupt:
                if not _signal_process_group(
                    process,
                    signal.SIGSTOP,
                    signal_group,
                ):
                    return ProcessResult(int(process.wait()), False)
                paused = True

                if _ask_exit_while_paused(prompt, write):
                    returncode = terminate_process_group(
                        process,
                        paused=True,
                        signal_group=signal_group,
                    )
                    return ProcessResult(returncode, True)

                if not _signal_process_group(
                    process,
                    signal.SIGCONT,
                    signal_group,
                ):
                    return ProcessResult(int(process.wait()), False)
                paused = False
                write("[继续] 下载任务已恢复。再次按 Control+C 可暂停。")
    except BaseException:
        if process.poll() is None:
            terminate_process_group(
                process,
                paused=paused,
                signal_group=signal_group,
            )
        raise
