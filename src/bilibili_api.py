#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Best-effort public metadata compatibility for the macOS frontend.

Media discovery and downloads do not use this module; they are delegated to
yt-dlp. These web endpoints are not a stable public SDK and may change.
"""

import os
import re
from urllib.parse import parse_qs, urlparse

import requests


VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BilibiliAPIError(RuntimeError):
    pass


def _headers(referer=None):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer

    cookie = os.environ.get("BILIBILI_COOKIE")
    sessdata = os.environ.get("BILIBILI_SESSDATA")
    if cookie:
        headers["Cookie"] = cookie
    elif sessdata:
        headers["Cookie"] = "SESSDATA={}".format(sessdata)
    return headers


def _json_response(response, action):
    try:
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BilibiliAPIError("{}失败：{}".format(action, exc))

    if payload.get("code") != 0:
        raise BilibiliAPIError(
            "{}失败：{} ({})".format(
                action,
                payload.get("message", "未知错误"),
                payload.get("code", "unknown"),
            )
        )
    return payload


def parse_video_input(value):
    """Return API lookup parameters and an optional requested part number."""
    value = value.strip()
    if not value:
        raise BilibiliAPIError("视频编号或链接不能为空")

    parsed = urlparse(value if "://" in value else "")
    page_values = parse_qs(parsed.query).get("p", [])
    try:
        requested_page = int(page_values[0]) if page_values else None
    except ValueError:
        raise BilibiliAPIError("分P参数 p 必须是数字")

    if value.isdigit():
        return {"aid": value}, requested_page

    av_match = re.search(r"(?:^|/|\\b)av(\d+)", value, re.IGNORECASE)
    if av_match:
        return {"aid": av_match.group(1)}, requested_page

    bv_match = re.search(r"(BV[0-9A-Za-z]+)", value, re.IGNORECASE)
    if bv_match:
        bvid = "BV" + bv_match.group(1)[2:]
        return {"bvid": bvid}, requested_page

    raise BilibiliAPIError(
        "无法识别该地址；请输入 AV 号、BV 号或完整的 Bilibili 视频链接"
    )


def get_video_info(value):
    params, requested_page = parse_video_input(value)
    try:
        response = requests.get(
            VIEW_API,
            params=params,
            headers=_headers(),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise BilibiliAPIError("获取视频信息失败：{}".format(exc))
    payload = _json_response(response, "获取视频信息")
    return payload["data"], requested_page


def build_video_page_url(bvid, page=None):
    url = "https://www.bilibili.com/video/{}".format(bvid)
    if page is not None:
        return "{}/?p={}".format(url, page)
    return url


def get_play_urls(bvid, cid, quality, referer):
    """Legacy helper retained for old scripts; the macOS app never calls it."""
    try:
        requested_quality = int(quality)
    except (TypeError, ValueError):
        raise BilibiliAPIError("清晰度参数必须是数字")

    params = {
        "bvid": bvid,
        "cid": cid,
        "qn": requested_quality,
        "fnval": 0,
        "fnver": 0,
        "fourk": 1,
    }
    try:
        response = requests.get(
            PLAYURL_API,
            params=params,
            headers=_headers(referer),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise BilibiliAPIError("获取播放地址失败：{}".format(exc))
    payload = _json_response(response, "获取播放地址")
    data = payload["data"]
    urls = [item["url"] for item in data.get("durl", []) if item.get("url")]
    if not urls:
        raise BilibiliAPIError("接口未返回可下载的视频地址")
    return urls, data.get("quality"), data.get("format", "")
