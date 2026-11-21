# Invidious Watch

A GitHub Actions-powered list of Invidious instances that pass an API and real media-stream byte probe.

## Output

Raw JSON:

`https://raw.githubusercontent.com/train233kei/invidious-watch/main/instances.json`

The file preserves the VIDOOR-compatible field:

`endpoints.video.working_instances`

It also includes the generation time, candidate/healthy counts, latency, version, test video ID, and details from the verified stream response.

## How it works

The workflow runs hourly (at minute 17 UTC) and can also be started manually from **Actions → Update Invidious instances → Run workflow**.

1. Collect candidates from the project owner's supplied instance list.
2. Supplement discovery with the Invidious instances API and official documentation.
3. Add the self-hosted instance `https://invidious.ritoge.com` as a candidate.
4. Check `/api/v1/stats`.
5. Request video metadata for known public test videos.
6. Prefer returned muxed stream URLs, then try adaptive stream URLs.
7. Send a small HTTP byte-range request to the actual media URL and require a successful response with non-empty media bytes. HLS is checked by fetching and validating its manifest.
8. Publish only instances that pass the real stream probe, sorted by measured latency.

The stream probe reads at most 1 KiB per attempted stream. No external secrets are required; GitHub Actions uses the repository-scoped `GITHUB_TOKEN` permission to commit the generated JSON.

## What "working" means

A passing instance has, at check time and from the GitHub Actions runner:

- Responded to the Invidious stats API.
- Returned metadata for a known test video.
- Returned a stream URL that actually delivered bytes (or a valid HLS manifest).

This is stronger than checking only whether the API or homepage responds. It can detect cases where an instance returns video metadata but YouTube's media URL is blocked or unavailable from the runner.

## Limitations

- This is a point-in-time test from GitHub Actions' network. It cannot guarantee playback from Render, a user's network, or every other IP address.
- A successful test video does not guarantee that every YouTube video works. Region, age, bot checks, PO Tokens, and per-video restrictions can differ.
- YouTube may rate-limit or block an instance between hourly checks.
- A public instance can disappear or change behavior at any time.
- If all candidates fail, the workflow fails and refuses to replace the last known-good `instances.json` with an empty list.

## Run manually

Open the repository's **Actions** tab, choose **Update Invidious instances**, then select **Run workflow**.
