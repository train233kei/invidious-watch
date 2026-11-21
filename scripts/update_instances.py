#!/usr/bin/env python3
"""Discover Invidious instances and verify that their stream URLs deliver bytes.

A passing check requires stats, video metadata, and an actual HTTP byte-range
request to a returned media URL. This is still a point-in-time probe, not a
guarantee that every video or viewer network will work.
"""
from __future__ import annotations

import html
import json
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
TEST_VIDEO_IDS = ["aqz-KE-bpKQ", "dQw4w9WgXcQ"]
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
TIMEOUT = 12
STREAM_TIMEOUT = 10
MAX_INSTANCES = 250
MAX_STREAMS_TO_PROBE = 4


def get(url: str, headers: dict | None = None):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,*/*",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read(5_000_000)


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
        if tag.lower() == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.links.append(href)


def discover() -> list[str]:
    found: list[str] = []
    try:
        _, _, body = get(SOURCE_LIST)
        data = json.loads(body)
        entries = data.get("instances", []) if isinstance(data, dict) else []
        if isinstance(entries, list):
            found.extend(base for item in entries if (base := normalize_base(item)))
        print(f"Primary source supplied {len(entries)} entries")
    except Exception as exc:
        print(f"Primary candidate source failed: {type(exc).__name__}: {exc}")

    try:
        _, _, body = get(API_LIST)
        data = json.loads(body)
        if isinstance(data, list):
            for item in data:
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
        print(f"Instance API discovery failed: {type(exc).__name__}: {exc}")

    try:
        _, _, body = get(DOCS_LIST)
        parser = LinkParser()
        parser.feed(body.decode("utf-8", "replace"))
        for href in parser.links:
            base = normalize_base(urllib.parse.urljoin(DOCS_LIST, href))
            if base:
                host = urllib.parse.urlsplit(base).hostname or ""
                if "invidious" in host or host.startswith("inv.") or "youtube" in host:
                    found.append(base)
    except Exception as exc:
        print(f"Official docs discovery failed: {type(exc).__name__}: {exc}")

    found.extend(SELF_HOSTED)
    unique, seen = [], set()
    for item in found:
        base = normalize_base(item)
        if base and base not in seen:
            seen.add(base)
            unique.append(base)
    return unique[:MAX_INSTANCES]


def probe_stream(url: str) -> dict | None:
    """Fetch only the first 1 KiB; verify the media URL actually serves bytes."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Range": "bytes=0-1023",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=STREAM_TIMEOUT) as response:
            status = response.status
            content_type = (response.headers.get("Content-Type") or "").lower()
            sample = response.read(1024)
            if status not in (200, 206) or not sample:
                return None
            if any(kind in content_type for kind in ("text/html", "application/json", "text/plain")):
                return None
            return {
                "http_status": status,
                "bytes_received": len(sample),
                "content_type": content_type or None,
            }
    except urllib.error.HTTPError as exc:
        print(f"    stream probe HTTP {exc.code}")
    except Exception as exc:
        print(f"    stream probe {type(exc).__name__}: {exc}")
    return None


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

        for video_id in TEST_VIDEO_IDS:
            query = urllib.parse.urlencode({
                "fields": "videoId,formatStreams,adaptiveFormats,hlsUrl"
            })
            try:
                video_status, _, video_body = get(f"{base}/api/v1/videos/{video_id}?{query}")
                if video_status != 200:
                    print(f"  {base} {video_id}: video API HTTP {video_status}")
                    continue
                video = json.loads(video_body)
                if video.get("videoId") != video_id:
                    print(f"  {base} {video_id}: invalid video API response")
                    continue

                formats = video.get("formatStreams") or []
                adaptive = video.get("adaptiveFormats") or []
                candidates = []
                # Prefer muxed streams; then try audio/video adaptive URLs.
                for item in formats + adaptive:
                    if isinstance(item, dict) and item.get("url", "").startswith("https://"):
                        candidates.append((item["url"], item.get("type") or item.get("qualityLabel") or "stream"))
                for stream_url, stream_type in candidates[:MAX_STREAMS_TO_PROBE]:
                    result = probe_stream(stream_url)
                    if result:
                        elapsed_ms = round((time.monotonic() - started) * 1000)
                        playback = stats.get("playback") or {}
                        return {
                            "url": base,
                            "latency_ms": elapsed_ms,
                            "test_video_id": video_id,
                            "stream_count": len(candidates) + (1 if video.get("hlsUrl") else 0),
                            "verified_stream": {
                                **result,
                                "format": str(stream_type)[:80],
                            },
                            "version": software.get("version") or stats.get("version"),
                            "playback_ratio": playback.get("ratio"),
                        }
                if video.get("hlsUrl"):
                    # HLS manifests are text, so inspect the manifest itself separately.
                    try:
                        hls_status, hls_type, hls_body = get(
                            video["hlsUrl"], {"Accept": "application/vnd.apple.mpegurl,*/*"}
                        )
                        if hls_status == 200 and hls_body and (
                            b"#EXTM3U" in hls_body[:2048] or "mpegurl" in hls_type.lower()
                        ):
                            elapsed_ms = round((time.monotonic() - started) * 1000)
                            playback = stats.get("playback") or {}
                            return {
                                "url": base,
                                "latency_ms": elapsed_ms,
                                "test_video_id": video_id,
                                "stream_count": len(candidates) + 1,
                                "verified_stream": {
                                    "http_status": hls_status,
                                    "bytes_received": min(len(hls_body), 1024),
                                    "content_type": hls_type or None,
                                    "format": "hls-manifest",
                                },
                                "version": software.get("version") or stats.get("version"),
                                "playback_ratio": playback.get("ratio"),
                            }
                    except Exception as exc:
                        print(f"    HLS probe {type(exc).__name__}: {exc}")
                print(f"  {base} {video_id}: metadata returned but no stream URL passed byte probe")
            except Exception as exc:
                print(f"  {base} {video_id}: {type(exc).__name__}: {exc}")

        return None
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
            print(
                f"[{index}/{len(candidates)}] OK {base} "
                f"({result['latency_ms']} ms, {result['verified_stream']['bytes_received']} bytes)"
            )
        else:
            print(f"[{index}/{len(candidates)}] FAIL {base}")

    healthy.sort(key=lambda item: item["latency_ms"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    output = {
        "generated_at": now,
        "check_interval_minutes": 60,
        "candidate_count": len(candidates),
        "healthy_count": len(healthy),
        "test_video_ids": TEST_VIDEO_IDS,
        "check_level": "api_metadata_and_real_stream_byte_probe",
        "endpoints": {
            "video": {"working_instances": [item["url"] for item in healthy]}
        },
        "instances": healthy,
        "notice": (
            "A passing check confirms that a test video's media URL returned bytes "
            "from the GitHub Actions runner at check time. It does not guarantee "
            "every video or playback from every client network."
        ),
    }
    Path("instances.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(healthy)} verified instances to instances.json")
    if not healthy:
        raise SystemExit(
            "No instances passed the real stream byte probe; refusing to publish an empty healthy list."
        )


if __name__ == "__main__":
    main()
