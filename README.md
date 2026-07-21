# pwhl-shot-tracking

Python proof of concept for the Royal-Road Shot-Map plan. It ingests a PWHL
HockeyTech play-by-play feed, maps game clocks to a public YouTube VOD, classifies
each candidate with Gemini, and renders a high-resolution PNG. A publishable chart
requires full-game manual review; a watermarked model-only draft does not.

The implementation uses only the Python 3.9+ standard library. Do not install
anything to run it.

## Quick start: run one game and view the draft shot chart

From the `pwhl-shot-tracking` directory, run one command:

```sh
./pwhl-shot-tracking run-game --api-key 'YOUR_GOOGLE_AI_STUDIO_KEY' --url 'https://www.youtube.com/watch?v=PUBLIC_PWHL_GAME' --open
```

That command runs the complete default pipeline and opens `shot-chart.png`. The
chart is marked **DRAFT** because it uses Gemini's labels before human review.

Only the key and public game URL are required:

- the HockeyTech game ID and VOD duration are resolved automatically;
- `fetch` downloads the official event/shot coordinates.
- Gemini maps the broadcast clock and makes two tagging calls per synchronized
  shot;
- the result is rendered to `shot-chart.png`.

For a cheaper plumbing test, add `--limit 5`; the resulting chart will contain
only those tagged shots. Anchor and clip audits are saved under
`work/<game-id>/`, and completed shot tags are cached on reruns.

Security note: a value passed through `--api-key` can appear in shell history and
local process listings. It is used only for the current process and is never
written to `game.json`, CSV output, or audit files.

## What is implemented

- Configurable candidate denominator: shots on goal, Fenwick, or all attempts.
- Automatic HockeyTech game-ID resolution from the YouTube title, publication
  date, and live PWHL schedules, with ranked evidence and ambiguity refusal.
- Cached raw HockeyTech response, normalized candidate CSV, and feed hash.
- Active-clock-run synchronization that never interpolates across a whistle.
- Manual sync-anchor import or Gemini scorebug-anchor discovery.
- Contextual royal-road tagging and a separate blind clip/sync verification pass.
- Structured output with normalized shot zones and optional pass geometry.
- Per-clip audit sidecars containing model, API surface, prompt/schema versions,
  source, feed hash, offsets, FPS, media resolution, requests, raw responses,
  retries, and manual disposition.
- Automatic shooter, zone, timing, and clip-validity checks, split into
  hard error flags and benign-explanation advisories (teammate attribution,
  adjacent broadcast zones, multi-shot clip windows) using rosters derived
  from the play-by-play feed.
- Calibration against both known positives and hard negatives.
- Full-candidate manual review, precision/recall thresholds, and a publish guard.
- Dependency-free 1800×1050 PNG rendering. Arrows appear only when Gemini reports
  medium/high geometry confidence; otherwise the graphic uses honest markers.

## Inputs and credentials

No secrets are written to disk. A live game run needs:

1. A **public** PWHL YouTube VOD URL. Gemini YouTube ingestion does not accept
   private or unlisted videos.
2. `GEMINI_API_KEY`, or comma-separated `GEMINI_API_KEYS`.

The game ID is resolved automatically. The only credential is the Gemini key.
The HockeyTech feed key in the example configuration is the public site feed key
documented by the PWHL Data Reference.

## Game resolution and advanced workflow

`init` reads public YouTube metadata, searches the relevant HockeyTech schedules,
and stores the selected game plus ranked matching evidence in `game.json`. It
requires both teams, a strong date match, and a clear lead over the second-ranked
candidate. It refuses ambiguous links instead of guessing.

To inspect resolution without creating a configuration:

```sh
python3 -m pwhl_shot_tracking resolve-game \
  --video-url 'https://www.youtube.com/watch?v=PUBLIC_VIDEO_ID'
```

If a broadcaster uses an unusual title or publishes the VOD long after the game,
look up the ID manually and pass `--game-id 123` as an explicit override.

`shots_on_goal` is the default denominator. Use `fenwick` for unblocked attempts
or `all_attempts` to include blocks. Do not change this after review begins.

### Clock synchronization

For manual anchors, copy `examples/anchors.example.csv`, record broadcast
time-remaining values and VOD seconds, then run:

```sh
python3 -m pwhl_shot_tracking sync \
  --config game.json \
  --anchors anchors.csv
```

Every `run_id` must identify one uninterrupted active-clock run. If `run_id` is
blank, the program infers boundaries from period changes, repeated clocks, and
video-time gaps.

Gemini can instead discover anchors in chunks. The default end time is the VOD
duration detected by `init`:

```sh
export GEMINI_API_KEY='...'

python3 -m pwhl_shot_tracking discover-anchors \
  --config game.json \
  --chunk-seconds 300 \
  --yes

python3 -m pwhl_shot_tracking sync --config game.json
```

Inspect `work/<game>/sync/anchors.csv` and spot-check generated `review_url`
values in `shots_synced.csv` before paying to tag the game.

### Calibration and tagging

First replace the IDs in `examples/calibration.example.csv` with known positive
and hard-negative shots:

```sh
python3 -m pwhl_shot_tracking calibrate \
  --config game.json \
  --selection calibration.csv \
  --yes

python3 -m pwhl_shot_tracking tag --config game.json --yes
```

Both commands show or guard their maximum call count. Completed clip sidecars are
cached; reruns do not call Gemini unless `--force` is used. `--limit N` is useful
for a small smoke test.

The pinned API contract is:

- model: `gemini-3.1-pro-preview`
- surface: GenerateContent REST
- clipping: `videoMetadata.startOffset` / `endOffset`
- sampling: `videoMetadata.fps`
- structured output: `generationConfig.responseJsonSchema`

GenerateContent is used because the current video API exposes per-request clipping
and custom FPS there. Audio is processed with the video automatically. The +2s
tail intentionally gives passer attribution low recall, so unknown passers remain
null and are not part of the headline.

### Review, validation, and rendering

```sh
python3 -m pwhl_shot_tracking review --config game.json
```

Open `work/<game>/manual_review.csv`. For every row:

- set `manual_royal_road` to `true` or `false`;
- set `manual_disposition` to `confirmed` or `corrected`;
- add notes when resolving a flag.

Then:

```sh
python3 -m pwhl_shot_tracking finalize --config game.json
python3 -m pwhl_shot_tracking render --config game.json
python3 -m pwhl_shot_tracking render --config game.json --publish
```

Before `finalize`, the normal render command reads model tags and produces a
watermarked draft. After `finalize`, it reads the manually reconciled rows.
`--publish` refuses to render until all candidates are reviewed and
precision/recall meet the configured thresholds (defaults: 0.85/0.80).

Use `python3 -m pwhl_shot_tracking doctor --config game.json` to inspect state
without making network calls.

## Runtime data

All generated data stays under the configured `work_dir`:

```text
feed/raw.json                 cached source response
feed/metadata.json            source URL, hash, and coordinate coverage
shots.csv                     normalized denominator
sync/anchors.csv              raw/discovered scorebug anchors
sync/anchors_normalized.csv   active-run assignments
shots_synced.csv              VOD offsets and review links
clips/<shot_id>.json          complete Gemini audit sidecar
royal_road_<game>.csv         feed + model + automatic flags
manual_review.csv             human labels
final.csv                     reconciled result
validation_report.json        precision, recall, thresholds
royal_road_<game>.png         final or watermarked draft
```

## Tests

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
```

The tests are offline and do not use a Gemini key.

## Current limitations

- Gemini scorebug discovery still needs human inspection; exact sync determines
  whether every downstream result is meaningful.
- The public feed is unofficial and may change. The raw response and hash make
  a run reproducible.
- Broadcast-view geometry is approximate. Low-confidence geometry is never drawn.
- This is a single-game research prototype, not player-tracking ground truth.

References: [Gemini video understanding](https://ai.google.dev/gemini-api/docs/generate-content/video-understanding),
[Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output),
[Gemini 3.1 Pro Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview),
and [PWHL Data Reference](https://github.com/IsabelleLefebvre97/PWHL-Data-Reference).
