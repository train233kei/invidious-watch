# Invidious Watch

A GitHub Actions-powered, automatically refreshed list of Invidious instances that pass a real API stream-metadata check.

## Output

Raw JSON:

`https://raw.githubusercontent.com/train233kei/invidious-watch/main/instances.json`

The file preserves the VIDOOR-compatible field:

`endpoints.video.working_instances`

It also includes `generated_at`, candidate/healthy counts, measured latency, version, and the test video that returned stream metadata.

## How it works

The workflow runs hourly (at minute 17 UTC) and can also be started manually from **Actions → Update Invidious instances → Run workflow**.

1. Collect candidates from the Invidious instances API.
2. Use the official Invidious instances documentation as a secondary discovery source.
3. Add the self-hosted instance `https://invidious.ritoge.com`.
4. Check `/api/v1/stats`.
5. Request `/api/v1/videos/:id` for known public test videos and require at least one HTTP stream URL in `formatStreams` or `adaptiveFormats`, or an HLS URL.
6. Publish only candidates that pass both checks, sorted by measured latency.

No external secrets are required. GitHub Actions uses the repository-scoped `GITHUB_TOKEN` permission to commit the generated JSON.

## Important limitations

- A passing check means the instance returned stream metadata for at least one test video at check time. It does **not** guarantee every YouTube video will work, or that playback from every client/network will succeed.
- YouTube may rate-limit or block instances between hourly checks.
- A public instance can disappear or change behavior at any time. Prefer instances you operate or trust.
- The workflow refuses to overwrite the list with an empty healthy set if all candidates fail.

## Run manually

Open the repository's **Actions** tab, choose **Update Invidious instances**, then select **Run workflow**.
