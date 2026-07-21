# Findings from the game 347 draft run (2026-07)

Game 347 — Minnesota Frost at Montréal Victoire, 2026-05-12, Game 5 of the
2026 Playoffs — was the first full game through the pipeline. The draft chart
(MIN 9, MTL 10 royal-road chances on 38 tagged of 43 shots on goal) prompted a
full review of the code and the run artifacts under `work/347/`. This file
records what was found, what has been fixed, and the exact path from the
current state to a publishable chart.

## State of the run

- 43 shots-on-goal candidates; 38 synced and tagged; 5 unmapped (sync gaps).
- 19/38 tagged royal-road true; zero manual review rows completed.
- `finalize` requires every denominator candidate tagged and every row
  manually reviewed before `render --publish` will produce an unwatermarked
  chart, so the five unmapped shots block publication outright.
- Calibration (`calibrate`, documented as preceding `tag`) was never run, so
  there is no measured precision/recall for the model's tags on this game.

## Fixed (PRs 1–5)

| PR | Fix | Measured effect on game 347 |
|----|-----|------------------------------|
| #1 | `sync` writes `sync/gaps.json` with bracketed re-scan windows for unmapped shots; `discover-anchors` merges instead of overwriting; run inference splits on broadcast cuts | The 5 unmapped shots get exact, cheap (1–2 call) recovery commands |
| #2 | Label collision/edge handling; home/away colors; arrows suppressed unless the drawn pass actually crosses the centre line | Labels legible and on the ice; MTL purple/MIN navy corrected; 12 of 19 misleading arrows removed |
| #3 | Flags split into errors vs advisories using rosters derived from the feed | Hard flags 37/38 → 26/38; the noise (15 teammate attributions, 15 adjacent zones, 6 multi-shot windows) is now advisory |
| #4 | `parse_clock` accepts scorebug tenths (`:52.9`, `17.9`) | 29 silently rejected anchors recovered → 367 running anchors (was 343); P1 covered to 0:07 remaining |
| #5 | Clip cache invalidated when offsets move; cached re-runs need no API key; `tag --limit` no longer clobbers the tagged CSV; `socket.timeout` retried on Py 3.9; empty anchor files refused | Prevents stale analysis after the PR 1 re-scan loop |

## Verified, not yet fixed

Ordered by expected impact. "Verified" means the failure was reproduced from
the run artifacts, not just asserted by review.

### 1. The +2s clip tail truncates real shots (clip window bias)

Feed event seconds and anchor clock values are both integer-truncated, and
the scorekeeper logs shots after they happen. Measured: blind verification
sees the shot at median 12.25s and piles up at 13.4–13.9s of a 14.0s clip
(three clips at 13.875s — 0.125s from falling outside). Several of the 13
"invalid clip" verdicts are this bias, not bad sync.

Recommendation: raise `clip.seconds_after` from 2.0 to ~5.0 (keep
`seconds_before` at 12), and update the blind prompt's "near the end"
expectation to match. Note this invalidates the clip cache by design (new
offsets ⇒ re-tag ⇒ ~76 Gemini calls for this game), so do it together with
the anchor re-scans, before the single full re-tag.

### 2. Exact clock matches land on stoppage-side sightings

A shot on goal freezes the clock at (or near) its own feed value. When the
scorebug shows that value across the stoppage, an "exact" anchor match can
place the clip at the whistle or the ensuing faceoff instead of live play.
In game 347, run-edge exact matches produced 5 of the 13 blind-invalid clips
(clips "ending at a faceoff" = mapped too early; "shot at the very
beginning" = mapped too late; all 3 goals got wrong windows this way).

Recommendation: in `map_clock_to_video`, detect when the exact-matched
anchor is the first/last/only anchor of its run and either derive the time
from the run's interior slope instead, or return a distinct
`exact_boundary` status that gets an asymmetric clip window and a review
flag.

### 3. Extrapolation ignores run boundaries

The extrapolation fallback picks the closest anchor by clock distance across
the whole period and assumes a 1:1 slope past run edges, so a shot near a
stoppage can map into the stoppage. Error is bounded by the stoppage length,
not by `max_extrapolation_seconds`. Recommendation: only extrapolate off the
run-edge anchor in the run's own direction, and only when the target clock
lies on that side.

### 4. The blind pass has no zone taxonomy or orientation

`blind_verification_prompt` asks for "a normalized shot zone" without
defining zone boundaries or what left/right mean from a broadcast camera.
The 22 zone mismatches measured on this game are therefore a property of the
prompt, not of sync quality (PR 3 downgraded adjacent-family disagreements
to advisories, but the underlying comparison stays weak). Recommendation:
define the taxonomy and orientation in the prompt (attacking team's
perspective), or drop blind zones from flagging entirely and rely on
shooter identity + timing.

### 5. The contextual tag echoes feed context

The contextual prompt hands the model the feed shooter number and feed zone,
then asks for `shooter_number` and `shot_zone` back — so `tag_shooter_number`
matching the feed is not independent confirmation (the two
`contextual_shooter_mismatch` rows are the interesting exceptions).
Treat those tag fields as context echoes, not evidence.

### 6. The royal-road rate is implausibly high pending review

19/38 (50%) of shots on goal tagged royal-road-true versus ~10–20% in
published NHL royal-road studies. The per-row rationales read plausibly and
the pass-geometry percentages are self-consistent (no crossing above the
circle tops), so this is not a mechanical bug — but it is exactly the
over-tagging pattern calibration and manual review exist to measure. Do not
quote the 9–10 headline until precision/recall are known.

### 7. Re-tagging with `--force` destroys manual review state in sidecars

`finalize` writes `manual_disposition`, `manual_notes`, and
`final_royal_road` into each clip sidecar; a later `tag --force` rebuilds
the audit dict from scratch and drops them. The CSVs survive, but the
sidecar audit trail loses the human labels. Recommendation: merge the
manual fields from an existing sidecar before overwriting.

### 8. `render --publish` trusts a stale validation report

`publishable` comes from `validation_report.json` with no freshness link to
the current tagged CSV: re-tagging after finalize leaves a stale
`final.csv` + report pair that still renders as publishable. Recommendation:
stamp the feed hash + tagged-CSV hash into the validation report at
`finalize` and have `render --publish` verify it.

### 9. Smaller confirmed items

- **OT length**: `overtime_period_seconds` defaults to 1200 (correct for
  playoff 20:00 OT, wrong for the regular season's 5:00). Set it per game in
  `game.json` until it is derived from schedule metadata.
- **Fenwick is a misnomer here**: the PWHL pxpverbose feed emits no
  `missed_shot` events, so `fenwick` currently equals `shots_on_goal` and
  `all_attempts` is SOG + blocks. Document or gate the universes.
- **Goal/shot dedup** compares raw string fields; a goal with a
  one-second-off `s` value would duplicate as a second candidate.
- **`run-game` silently ignores `--shot-universe`** when the config already
  exists with a different value.
- **Calibration is not wired into `run-game`** and nothing enforces the
  documented calibrate-before-tag order.
- **Resolver dates**: numeric slash dates in YouTube titles are parsed
  month-first only; day-first titles could mis-resolve (unverified against a
  real failing title — the title/date match on game 347 was correct).

## Runbook: from here to a publishable game 347 chart

1. Merge PRs 1–5 and re-run `sync` — it now reports `sync/gaps.json` with
   two or three bounded scan windows for the five unmapped shots.
2. Run the suggested `discover-anchors --start … --end … --yes` commands
   (a few Gemini calls); they merge into `anchors.csv`. Re-run `sync` and
   confirm `unmapped: 0` (the P1 0:02 shot may also need
   `clock.max_extrapolation_seconds` nudged or one manual anchor near the
   buzzer).
3. Decide the clip window (finding 1): if adopting `seconds_after: 5.0`, do
   it now so the full re-tag happens once (~86 calls for 43 shots).
4. `tag --yes` — only stale/new clips re-call Gemini after PR 5.
5. `calibrate` against a handful of known positives and hard negatives
   (watch the clips via the `review_url` links to pick them).
6. `review` → fill all 43 rows of `manual_review.csv` (the advisories
   column now says which flags have benign explanations) → `finalize` →
   `render --publish`.

The chart's headline numbers (MIN 9 / MTL 10) should be treated as
model-draft numbers until step 6 completes; given finding 6, expect them to
move.
