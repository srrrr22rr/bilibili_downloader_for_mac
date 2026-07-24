#!/usr/bin/env python3

import unittest
from unittest import mock

import bilibili_api as api


class BangumiInputTests(unittest.TestCase):
    def test_parses_episode_and_season_play_urls(self):
        self.assertEqual(
            api.parse_bangumi_input(
                "https://www.bilibili.com/bangumi/play/ep251076/"
                "?share_source=copy_web"
            ),
            ({"ep_id": "251076"}, 251076),
        )
        self.assertEqual(
            api.parse_bangumi_input(
                "https://www.bilibili.com/bangumi/play/ss25733"
            ),
            ({"season_id": "25733"}, None),
        )

    def test_rejects_a_normal_video_as_bangumi(self):
        with self.assertRaises(api.BilibiliAPIError):
            api.parse_bangumi_input(
                "https://www.bilibili.com/video/BV1nW411U7JQ"
            )

    def test_fetches_public_season_catalog_by_episode_id(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": 0,
            "message": "success",
            "result": {
                "season_id": 25733,
                "title": "青春猪头少年不会梦到兔女郎学姐",
                "episodes": [{"id": 251076, "title": "1"}],
            },
        }
        url = "https://www.bilibili.com/bangumi/play/ep251076"

        with mock.patch.object(
            api.requests,
            "get",
            return_value=response,
        ) as request:
            result, requested_episode_id = api.get_bangumi_info(url)

        self.assertEqual(result["season_id"], 25733)
        self.assertEqual(requested_episode_id, 251076)
        request.assert_called_once_with(
            api.BANGUMI_SEASON_API,
            params={"ep_id": "251076"},
            headers=api._headers(url),
            timeout=20,
        )


if __name__ == "__main__":
    unittest.main()
