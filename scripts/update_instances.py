#!/usr/bin/env python3
"""Discover and verify Invidious instances, then write instances.json.

Only instances that pass both /api/v1/stats and a real video API stream
metadata check are published. This checks API stream metadata, not guaranteed
playback for every YouTube video or from every client network.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SOURCE_LIST = "https://raw.githubusercontent.com/kuru-bana/yt-data/refs/heads/main/invidious/data/invidious.json"
API_LIST = "https://api.invidious.io/instances.json?sort_by=health"
DOCS_LIST = "https://docs.invidious.io/instances/"
SELF_HOSTED = ["https://invidious.ritoge.com"]
TEST_VIDEO_IDS = [
    "aqz-KE-bpKQ",  # Big Buck Bunny
    "dQw4w9WgXcQ",
]
USER_AGENT = "invidious-watch/1.0 (+https://github.com/train233kei/invidious-watch)"
TIMEOUT = 12
MAX_INSTANCES = 250


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        body = response.read(5_000_000)
        return response.status, response.headers.get("Content-Type", ""), body


def normalize_base(value: str) -> str | None:
    value = html.unescape(str(value)).strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.hostname.endswith(".onion") or parsed.hostname.endswith(".i2p"):
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href", "")
        if href:
            self.links.append(href)


def discover() -> list[str]:
    found: list[str] = []

    # Primary candidate source supplied by the project owner. Its schema is
    # {"count": N, "instances": ["https://...", ...]}.
    try:
        _, _, body = get(SOURCE_LIST)
        data = json.loads(body)
        entries = data.get("instances", []) if isinstance(data, dict) else []
        if isinstance(entries, list):
            for item in entries:
                base = normalize_base(item)
                if base:
                    found.append(base)
        print(f"Primary source supplied {len(entries)} entries")
    except Exception as exc:
        print(f"Primary candidate source failed: {exc}")

    try:
        _, _, body = get(API_LIST)
        data = json.loads(body)
        if isinstance(data, list):
            for item in data:
                # API entries are commonly [base_url, metadata].
                if isinstance(item, list) and item:
                    metadata = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
                    if metadata.get("api") is False:
                        continue
                    base = normalize_base(item[0])
                elif isinstance(item, dict):
                    if item.get("api") is False:
                        continue
                    base = normalize_base(item.get("uri") or item.get("url") or "")
                else:
                    continue
                if base:
                    found.append(base)
    except Exception as exc:
        print(f"Instance API discovery failed: {exc}")

    # The official documentation list is a second source if the API list is down
    # or incomplete. Links are filtered to plausible Invidious hosts below.
    try:
        _, _, body = get(DOCS_LIST)
        parser = LinkParser()
        parser.feed(body.decode("utf-8", "replace"))
        for href in parser.links:
            base = normalize_base(urllib.parse.urljoin(DOCS_LIST, href))
            if not base:
                continue
            host = urllib.parse.urlsplit(base).hostname or ""
            if host and ("invidious" in host or host.startswith("inv.") or "youtube" in host):
                found.append(base)
    except Exception as exc:
        print(f"Official docs discovery failed: {exc}")

    found.extend(SELF_HOSTED)
    unique = []
    seen = set()
    for item in found:
        base = normalize_base(item)
        if base and base not in seen:
            seen.add(base)
            unique.append(base)
    return unique[:MAX_INSTANCES]


def check_instance(base: str) -> dict | None:
    started = time.monotonic()
    try:
        status, _, body = get(base + "/api/v1/stats")
        if status != 200:
            return None
        stats = json.loads(body)
        software = stats.get("software") or {}
        if software.get("name") not in (None, "invidious"):
            return None

        stream_video = None
        stream_count = 0
        errors = []
        for video_id in TEST_VIDEO_IDS:
            query = urllib.parse.urlencode({"fields": "videoId,formatStreams,adaptiveFormats,hlsUrl"})
            url = f"{base}/api/v1/videos/{video_id}?{query}"
            try:
                video_status, _, video_body = get(url)
                if video_status != 200:
                    errors.append(f"{video_id}:HTTP{video_status}")
                    continue
                video = json.loads(video_body)
                if video.get("videoId") != video_id:
                    errors.append(f"{video_id}:invalid-response")
                    continue
                formats = video.get("formatStreams") or []
                adaptive = video.get("adaptiveFormats") or []
                usable = [f for f in formats + adaptive if isinstance(f, dict) and str(f.get("url", "")).startswith("http")]
                if usable or video.get("hlsUrl"):
                    stream_video = video_id
                    stream_count = len(usable) + (1 if video.get("hlsUrl") else 0)
                    break
                errors.append(f"{video_id}:no-streams")
            except Exception as exc:
                errors.append(f"{video_id}:{type(exc).__name__}")

        if not stream_video:
            return None

        elapsed_ms = round((time.monotonic() - started) * 1000)
        playback = stats.get("playback") or {}
        return {
            "url": base,
            "latency_ms": elapsed_ms,
            "test_video_id": stream_video,
            "stream_count": stream_count,
            "version": software.get("version") or stats.get("version"),
            "playback_ratio": playback.get("ratio"),
        }
    except Exception as exc:
        print(f"Rejected {base}: {type(exc).__name__}: {exc}")
        return None


def main():
    candidates = discover()
    print(f"Discovered {len(candidates)} unique candidates")
    healthy = []
    for index, base in enumerate(candidates, 1):
        result = check_instance(base)
        if result:
            healthy.append(result)
            print(f"[{index}/{len(candidates)}] OK {base} ({result['latency_ms']} ms)")
        else:
            print(f"[{index}/{len(candidates)}] FAIL {base}")

    # Prefer low latency, then retain stable URL order for deterministic output.
    healthy.sort(key=lambda x: x["latency_ms"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output = {
        "generated_at": now,
        "check_interval_minutes": 60,
        "candidate_count": len(candidates),
        "healthy_count": len(healthy),
        "test_video_ids": TEST_VIDEO_IDS,
        "endpoints": {
            "video": {
                "working_instances": [item["url"] for item in healthy]
            }
        },
        "instances": healthy,
        "notice": "A passing check confirms stream metadata was returned for a test video; it does not guarantee playback of every video or from every client network."
    }
    path = Path("instances.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(healthy)} working instances to {path}")
    if not healthy:
        raise SystemExit("No instances passed the stream metadata check; refusing to publish an empty healthy list.")


if __name__ == "__main__":
    main()
