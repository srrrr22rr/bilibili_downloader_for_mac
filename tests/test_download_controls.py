#!/usr/bin/env python3

import signal
import subprocess
import unittest
from pathlib import Path

import download_controls as controls


class FakeProcess:
    def __init__(self, wait_results):
        self.pid = 43210
        self.returncode = None
        self.wait_results = list(wait_results)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        result = self.wait_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result


class SidecarCommandTests(unittest.TestCase):
    def test_video_only_matches_selected_quality_and_has_no_audio_selector(self):
        command = controls.build_video_only_command(
            ["yt-dlp"],
            "https://www.bilibili.com/video/BV1cb411V7Lm",
            "4",
            ["--yes-playlist", "--playlist-items", "1,3"],
            Path("/tmp/output"),
        )
        self.assertEqual(
            command[command.index("--format") + 1],
            "bv[height<=720]",
        )
        self.assertIn(".video-only-", command[command.index("--output") + 1])
        self.assertIn("--yes-playlist", command)
        self.assertIn("--playlist-items", command)
        self.assertEqual(command[-2], "--")


class PauseTests(unittest.TestCase):
    def test_control_c_pauses_then_enter_resumes(self):
        process = FakeProcess([KeyboardInterrupt(), 0])
        signals = []
        messages = []

        result = controls.run_process_with_pause(
            ["yt-dlp", "URL"],
            prompt=lambda _: "",
            write=messages.append,
            popen_factory=lambda *args, **kwargs: process,
            signal_group=lambda pid, sig: signals.append((pid, sig)),
        )

        self.assertEqual(result, controls.ProcessResult(0, False))
        self.assertEqual(
            [item[1] for item in signals],
            [signal.SIGSTOP, signal.SIGCONT],
        )
        self.assertTrue(any("[继续]" in message for message in messages))

    def test_paused_job_can_exit_gracefully(self):
        process = FakeProcess([KeyboardInterrupt(), 130])
        signals = []

        result = controls.run_process_with_pause(
            ["yt-dlp", "URL"],
            prompt=lambda _: "y",
            write=lambda _: None,
            popen_factory=lambda *args, **kwargs: process,
            signal_group=lambda pid, sig: signals.append((pid, sig)),
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(
            [item[1] for item in signals],
            [signal.SIGSTOP, signal.SIGCONT, signal.SIGINT],
        )

    def test_eof_while_paused_exits_instead_of_leaving_stopped_process(self):
        process = FakeProcess([KeyboardInterrupt(), 130])
        signals = []

        def eof_prompt(_):
            raise EOFError

        result = controls.run_process_with_pause(
            ["yt-dlp", "URL"],
            prompt=eof_prompt,
            write=lambda _: None,
            popen_factory=lambda *args, **kwargs: process,
            signal_group=lambda pid, sig: signals.append((pid, sig)),
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(
            [item[1] for item in signals],
            [signal.SIGSTOP, signal.SIGCONT, signal.SIGINT],
        )

    def test_stubborn_process_escalates_to_sigkill(self):
        process = FakeProcess(
            [
                subprocess.TimeoutExpired("yt-dlp", 8),
                subprocess.TimeoutExpired("yt-dlp", 3),
                137,
            ]
        )
        signals = []

        returncode = controls.terminate_process_group(
            process,
            paused=False,
            signal_group=lambda pid, sig: signals.append((pid, sig)),
        )

        self.assertEqual(returncode, 137)
        self.assertEqual(
            [item[1] for item in signals],
            [signal.SIGINT, signal.SIGTERM, signal.SIGKILL],
        )


if __name__ == "__main__":
    unittest.main()
