# Frame Reuse Investigator

A standalone, resumable **画面复用调查工具** that traces a re-edited probe clip
back to the uncompressed AVI clips registered with the Frame Proxy Inspector
service. It maps continuous segments of the probe onto candidate source
footage and produces interactive evidence that links back to the *actual
decoded frames* on both sides.

It is deliberately separate from the production service:

- it lives in its own package (`reuse_investigator`) and its own data root
  (`REUSE_INVESTIGATOR_DATA_DIR`);
- it only **reads** the production AVI parser/decoder and PPM encoder; the
  production HTTP, service, and storage layers are never imported for writing
  and no production file is modified;
- a retrieval hit is never treated as a verified source.

## Why three stages instead of one similarity search

A single "find the nearest frame" step cannot satisfy the requirements. The
tool separates three concerns and records each one.

1. **Coarse retrieval (candidate only).** Each frame is reduced to an
   area-averaged 8×8 luma grid, thresholded bits, and mean-normalized color.
   Candidates pass a robust luma-grid mean-absolute-difference gate. These
   rows are explicitly candidates, never conclusions.
2. **Frame verification.** Every candidate is scored with normalized
   cross-correlation (geometry-tolerant spatial structure) and a
   mean-normalized color invariant (tolerant of color grading), plus a
   low-texture/flat guard. Flat frames have zero variance and cannot confirm
   anything.
3. **Temporal adjudication.** Per-source dynamic programming grows chains only
   when consecutive probe frames map to consecutive source frames, stepping
   `+1` (forward) or `-1` (reversed). Chains are then adjudicated so each
   probe frame has at most one winner; competing clips are retained as scored
   alternatives.

A chain is **confirmed** only when it is long enough, textured, strongly
correlated, and separated from the runner-up by a uniqueness margin.
Near-duplicate sources, flat footage, and short fragments stay
**ambiguous** with their candidates preserved.

## The three possible presentations

| Situation | What the report shows |
| --- | --- |
| Correct, unique match (forward, reversed, or repeated reuse) | `confirmed` chain with exact probe/source frame ranges, direction, NCC, color distance, and a side-by-side PPM of the two real frames |
| Retrieval hit that fails verification | frame disposition `rejected_candidate`; the interval is listed as unexplained with the reason that NCC/color verification failed |
| Cannot uniquely decide (near-duplicates / flat frames) or genuinely absent footage | `ambiguous` finding with retained alternative clips, or an `unexplained` interval (`no_candidate`); nothing is guessed |

## Memory and resource guarantees

- AVI files are opened read-only with `mmap`; frames are decoded **one at a
  time** during ingest and **two at a time** when rendering evidence. The
  whole library is never decoded into memory.
- Only compact signatures (a 64-cell grid + color means per frame) are held
  for matching, never decoded pixels.
- Verification is bounded by `retrieval_candidate_cap`, so the tool never runs
  the full all-pairs frame comparison (the demo measures 348 capped
  verifications versus 1900 naive pairs).
- A streaming check asserts live decoded-pixel memory stays below a two-frame
  budget.

## Incremental ingest, resume, and evidence validity

- Clips are ingested independently; new material never disturbs existing
  signatures.
- Each frame signature is appended to an fsynced `journal.ndjson`. If a run is
  interrupted, re-ingesting resumes/rebuilds to a complete, current state.
- Every signature set carries an analysis snapshot keyed by content digest,
  geometry, frame count, profile version, grid size, and sample stride.
  Stored evidence is reused **only while content and analysis conditions still
  match**; changed bytes invalidate it (the refresh path raises instead of
  reusing stale evidence).

## Requirements

- Python 3.11+, standard library only (no third-party packages).
- The production `frame_proxy` package on `PYTHONPATH` for the read-only AVI
  and PPM adapters.

## CLI

```bash
export PYTHONPATH=src

# 1. Generate the canonical re-edit demonstration set
python3 -m reuse_investigator demo-footage --output /tmp/reuse-demo

# 2. Register sources and the probe
python3 -m reuse_investigator ingest source-a /tmp/reuse-demo/source-a.avi --role source
python3 -m reuse_investigator ingest source-b /tmp/reuse-demo/source-b.avi --role source
python3 -m reuse_investigator ingest source-c /tmp/reuse-demo/source-c.avi --role source
python3 -m reuse_investigator ingest source-decoy /tmp/reuse-demo/source-decoy.avi --role source
python3 -m reuse_investigator ingest probe-edit /tmp/reuse-demo/probe-edit.avi --role probe

# 3. Investigate
python3 -m reuse_investigator investigate inv-demo probe-edit
python3 -m reuse_investigator report inv-demo

# 4. Open the interactive evidence viewer
python3 -m reuse_investigator serve --port 8871
#   http://127.0.0.1:8871/v1/investigations/inv-demo
```

Other commands: `list`, `refresh <clip_id>` (re-validate and resume/rebuild).

## Demonstration fixture design

The generated probe exercises every requirement in one short, deterministic
re-edit:

1. a uniquely attributable textured segment (forward reuse of `source-c`);
2. a reversed run and a repeated run of the same `source-a` segment;
3. a near-duplicate `source-a`/`source-b` region that cannot be uniquely
   attributed;
4. flat/static frames that must never confirm a source;
5. a look-alike decoy whose coarse luma retrieves candidates but whose shuffled
   spatial structure fails verification;
6. genuinely novel footage present in no source, which stays unexplained.

## Verification

```bash
# Build
python3 -m compileall -q src tools tests

# Focused unit tests (metrics, transform tolerance, chain adjudication)
PYTHONPATH=src python3 -m unittest discover -s tests

# End-to-end independently checkable harness
PYTHONPATH=src:. python3 tools/check_reuse_investigation.py
```

The end-to-end harness builds its own throwaway workspace and asserts the
correct match, the rejected retrieval hit, the non-unique/ambiguous case,
unexplained ranges, incremental/resumable ingest, evidence invalidation, and
the streaming memory bound.

## Data layout

```text
<REUSE_INVESTIGATOR_DATA_DIR>/
├── manifests/<clip_id>.json          # content identity + analysis conditions
├── signatures/<clip_id>/
│   ├── manifest.snapshot.json        # analysis key the journal depends on
│   ├── journal.ndjson                # one compact signature per line
│   └── complete.flag
└── evidence/<investigation_id>/
    ├── report.json
    └── frames/<probe>__<source>.ppm  # real-frame side-by-side evidence
```
