# PDR: ROI contours, event shading and a dF/F step

Trigger: the Patel 2026 Fig 3c stand-in (patel2026 run 407/4a20f2fa, 2026-10-13) and the Sosa 2025
Fig 4d panel (PR #948). Both look wrong next to the papers for reasons the current nodes and plots
cannot fix. Checked against `feature/876-reproduce-integration` at aeff9dedb (develop-main plus #939,
#948, #949). Papers read in full, including Methods, figure legends, Extended Data legends and the
supplementary items listed on the Nature pages.

---

## 1. What the papers specify

### Patel et al. 2026, Nat Neurosci, doi:10.1038/s41593-026-02413-x

Supplementary material on the article page: Reporting Summary (software list only: Inscopix Data
Processing Software, MATLAB 2025b, Fiji, SLEAP, Prism 10; no CNMF-E version), Supplementary Videos 1 and
2 (SLEAP pose tracking), Supplementary Tables 1 and 2 (slice electrophysiology), Source Data xlsx
(statistics behind Figs 1 to 6 and Extended Data 1 to 9). Every imaging-analysis rule is in the
main-text Methods ("Calcium imaging analysis", "Lick microstructure analysis"):

| Step | Paper | OptiNiSt today |
|---|---|---|
| Preprocessing | IDAS: 4x spatial downsample, 10 Hz, crop, motion-correct to the first frame, 8-bit TIFF | the DANDI movie is already this (39616 x 97 x 133 uint8 at 10 Hz); `caiman_mc` not needed |
| Extraction | CNMF-E "without non-negative constraints on temporal components to preserve negative transients"; every neuron visually curated | `caiman_cnmfe` keeps non-negativity (CaImAn has no switch); curation = Edit ROI |
| Trace unit | ΔF/F0 from CNMF-E (the baseline mean is "the mean ΔF/F0 during the baseline") | node emits CaImAn `C`, never ΔF/F0 |
| Bouts | >= 2 licks within 1 s, onset at the first lick; imaging uses only bouts >= 3 s apart | `prep_lick_events_csv.py`; no node turns lick times into a per-frame column |
| Per-bout z-score | Z = (F(t) - F_m) / s.d. with F_m and s.d. from a 3 or 5 s pre-bout baseline, averaged over a matched number of bouts per condition per neuron, then over neurons | `eta` averages raw windows; z-score is recording-wide via `data_slice` |
| Responsive cell | Wilcoxon signed-rank, or mean Z of +-1.98 from onset to 3 s; "excited" and "inhibited" proportions (Fig 6, ED 8) | nothing |
| Clustering | agglomerative, Ward linkage, Euclidean, on each neuron's mean Z trace; threshold = highest value giving one non-responsive cluster (Fig 3d/e, ED 5e/f) | nothing (issue #889) |
| Decoding | pseudo-simultaneous population across mice, linear SVM, k-fold (Fig 3g/h, ED 6b) | `svm` node, single session only |
| Cross-session | CellReg aligns CNMF-E footprints to a reference session | `caiman_cnmf_multisession` (CaImAn `register_multisession`) |
| Fig 3c left | numbered cell contours on the projection image, four example cells circled in colour | opaque filled ROI blobs, random colours, square aspect |
| Fig 3c right | four dF/F traces, each on its own a.u. scale, grey bands at alcohol licks and blue bands at water licks, 50 s bar | z-scored stacked traces on a frames axis, licks drawn as two extra rows |
| Fig 5b-d, ED 5d/f | mean +- s.e.m. Z trace from -3 to +3 s with a dashed onset line, inset mean bar | `eta` mean_trace with sem band; onset at x = 0 (ZeroLine) |
| ED 5e | per-neuron Z heatmaps (-3 to +3 s) alcohol and water side by side, rows sorted by cluster | `eta` mean_heatmap sorted by peak; no cluster sort |

### Sosa et al. 2025, Nat Neurosci (Fig 2a, 4d)

"To compute the ΔF/F (dF/F) for each ROI, baseline fluorescence was calculated within each trial
independently using a maximin procedure with a 20 s sliding window ... dF/F was then calculated for
each cell as the fluorescence minus the baseline, divided by the absolute value of the baseline, then
smoothed with a two-sample (~0.129 s) s.d. Gaussian kernel." Deconvolution with OASIS as in Suite2p.
Binning: "the mean dF/F or deconvolved activity" per 10 cm bin; Fig 4d style panels use dF/F.
The NWB carries `Fluorescence` (raw Suite2p F), `Neuropil` and `Deconvolved` per cell.

---

## 2. What the code does now

### 2a. ROI overlay (`frontend/src/components/Workspace/Visualize/Plot/ImagePlot.tsx`)

- Two Plotly `heatmap` traces in one chart: trace 0 `name: "images"`, trace 1 `name: "roi"` with
  `z = roi ids` (NaN background served as null), a discrete `colorscale` with one stop per id, colours
  from `getRoiColor` (jet, `(|id| * 9) % 200`), `zsmooth: false`, `showscale: false`.
- No `contour` trace and no `layout.shapes` anywhere in `frontend/src`.
- "Roi Alpha" is stored (`setImageItemRoiAlpha`, default 1.0) but never read: commit de83bea7b
  ("fix roiAlpha", 2025-02-18) replaced `rgba2hex(rgba, roiAlpha)` with `rgba2hex(rgba, 1)`. So the
  blobs are always opaque.
- No `yaxis.scaleanchor`; the chart sits in a square box (`INIT_WIDTH_ROI`), so a 97 x 133 field is
  drawn square.
- "Show ROI labels" adds `layout.annotations` at each ROI centroid.
- Edit ROI depends on the trace layout: `onChartClick` acts when `point.curveNumber >= 1 && point.z >= 0`;
  box select (`selectingImageArea`) reads `roiData[y][x]` from the store, not the trace; add ROI is an
  HTML overlay. The ROI JSON comes from `GET /api/visualizations/image/{path}` (`RoiData.save_json`,
  `write_as_split`, one 2D array of 0-based ids).

### 2b. TimeSeries plot (`Plot/TimeSeriesPlot.tsx`, helpers `timeSeriesStack.ts`, `timeSeriesAnnotations.ts`, `timeSeriesRange.ts`)

- One scatter per record; with the STD switch off, `stackedTrace` z-scores each trace and stacks it:
  `(v - mean) / (span * std + 1e-10) + index`. That is why one large transient flattens the others.
- "Range Unit" `frames` or `time` (divides by `selectFrameRate`); legend select; ZeroLine on both axes.
- Nothing draws vertical bands or event lines (`shapes`, `vrect` absent). The only overlay is the
  `cell: N` annotation at the end of each trace.
- "Link to box" links ROI selection between an image box and a timeseries box; it does not sync x ranges.
- Data: `inittimedata` / `timedata/{path}?index=` / `alltimedata` routes over the chunked
  `chunk_N.json` layout (`TimeSeriesChunkHandler`); `plot-meta.json` holds only `xlabel`, `ylabel`,
  `title`, `xaxis_type`, `yaxis_type`.

### 2c. Baselines and dF/F in the wrappers

- `data_slice`: `cell_normalization` is `none` or `zscore` (`_zscore`, nan-aware, constant row to 0),
  applied along `_time_axis` for FluoData and TimeSeriesData, drops `std`/`sem`.
- `lccd_detect`: the only dF/F in the repo. `f0 = percentile(trace[k - f0_frames : k + f0_frames], f0_percentile)`,
  `dff = (F - f0) / f0`, Python double loop over cells and frames, NaN within `f0_frames` of either end,
  output `dff` undeclared in the return annotation (issue #931). `vacant_roi.yaml` carries the same
  two params unused.
- suite2p: `suite2p_spike_deconv` calls `extraction.preprocess` (maximin / constant / prctile
  baseline, `win_baseline` 60, `sig_baseline` 10, `prctile_baseline` 8) on `F - neucoeff * Fneu` as
  input to OASIS; the baselined trace is not output. `suite2p_roi` outputs raw `F`; `Fneu` stays in ops.
- caiman: `C` only. CaImAn 1.11.4 has `caiman.source_extraction.cnmf.utilities.detrend_df_f(A, b, C, f, YrA=None, quantileMin=8, frames_window=500, flag_auto=True, use_fast=False, detrend_only=False)`, unused.
- Events: no node or dataclass holds onset lists or intervals. `eta.calc_trigger` thresholds a
  per-frame column (`up`, `down`, `cross`), keeps the modal event length, and `calc_trigger_average`
  stacks raw windows; onsets are not written out.

---

## 3. Design

Rule applied: smallest change that makes the panel honest, in the place every workflow already
passes through. No new dataclass, no new node unless an option on an existing one cannot carry it.

### A. ROI contours (frontend only)

Recommendation: an **"Outline" switch** on the image item, next to "Show ROI labels".

- A pure helper `outlineRoi(roi: (number | null)[][]) -> (number | null)[][]` keeps a pixel's id
  only where a 4-neighbour holds a different id (or the edge of the field); everything else becomes
  null. The existing ROI heatmap trace draws it: same colours, same ids, no new trace, so
  `onChartClick` (curveNumber 1, z = id) still works on the outline pixels, and box select is unchanged
  because it reads the full array from the store. One-pixel contours on a 97 x 133 field are what the
  paper's panel shows at that resolution.
- Interior clicks when outlined: `point.z` is null, so `onChartClick` falls back to
  `roiDataState[y][x]` (the same lookup `selectingImageArea` uses). Two lines.
- **Restore Roi Alpha**: read `selectImageItemRoiAlpha` and pass it to `rgba2hex`. de83bea7b dropped it
  without a reason in the message; the control has been dead since. If a reason surfaces (Edit ROI
  colours were tuned for opaque), apply alpha only when no edit is in progress.
- **Aspect**: `yaxis: { scaleanchor: "x", scaleratio: 1 }` on the image chart so non-square fields keep
  their shape. The square box stays; the plot letterboxes inside it.
- Rejected: Plotly `contour` traces (one per ROI, 252 traces on this run, and the click and select
  handlers assume two traces); backend-side outline (the frontend already has the full array, and
  Edit ROI needs the filled ids in the store anyway).
- Example-cell circles (Fig 3c's four coloured rings): not built. "Show ROI labels" plus the outline
  and Legend-select highlighting (unselected ROIs already drop to 0.3 alpha) cover the panel.

### B. Event shading (frontend only)

Recommendation: a **"Shade events from box (#)"** select on the timeseries item, like "Link to box".

- The user picks another timeseries box that shows a behaviour column (the lick rows, a cue column,
  the `condition_split` mask). A pure helper `eventSpans(x: number[], y: number[], threshold = 0.5)`
  returns `[x0, x1]` runs where `y > threshold` (same rule as `eta.calc_trigger` "up"). The box's
  x unit (frames or time) is applied to both.
- Spans become `layout.shapes` of type `rect`, `xref: "x"`, `yref: "paper"`, `y0: 0`, `y1: 1`,
  `layer: "below"`, `line.width: 0`, `fillcolor` = that record's `getRoiColor` at opacity 0.25. One
  colour per source record, so alcohol and water licks shade differently as in Fig 3c.
- Source data: the linked box's records come from `getTimeSeriesAllDataApi` for that path (exists),
  taking every drawn record of the source box. If nothing is drawn, no shapes.
- Guard: merge touching runs; cap at 500 shapes per source record with a one-line warning in the
  editor, since Plotly slows past a few thousand shapes. A 1 Hz lick channel over 40 min stays far below.
- Not built: onset ticks for `eta` (x = 0 is the onset and ZeroLine already marks it), events from
  intervals (no interval data exists in a run), x-range sync between boxes.
- Rejected: a node that emits intervals (new dataclass, new JSON, new plot type) and shading inside
  `data_concat` outputs (the plot cannot tell a lick row from a cell).

### C. dF/F step (backend)

Recommendation: **`cell_normalization: dff` on `data_slice`** with three parameters, and a declared
`dff` output on the CaImAn nodes from their own ΔF/F0.

C1. `data_slice` (`studio/app/optinist/wrappers/data_utils/data_slice.py`, `params/data_slice.yaml`):

| Param | Default | Meaning |
|---|---|---|
| `cell_normalization` | `none` | gains `dff` |
| `f0_method` | `percentile` | `percentile` (LCCD, CaImAn `detrend_df_f` style) or `maximin` (Suite2p, Sosa) |
| `f0_window_frames` | `0` | sliding window length; `0` = the whole recording |
| `f0_percentile` | `8` | percentile for `percentile`; `maximin` uses a Gaussian of `window / 6` frames then min then max filters of `window`, the Suite2p recipe with `sig_baseline = win_baseline / 6` |

Formula: `dff = (F - F0) / |F0|` (Sosa's wording; LCCD divides by `f0` without abs, which flips the
sign on a negative baseline). F0 is `scipy.ndimage.percentile_filter(F, f0_percentile, size=(1, window), mode="nearest")`
or `maximum_filter1d(minimum_filter1d(gaussian_filter1d(F, sigma), window), window)`; both are a
few lines, vectorised over cells, no Python loop. A baseline of exactly 0 is left as NaN with a
warning (raw F is never 0; CaImAn `C` can be 0 for a silent component). `std` and `sem` are dropped
as for `zscore`. Only FluoData and TimeSeriesData with a time axis qualify, same guard as `zscore`.

Why here: every reproduction already has a `data_slice` in front of the analysis node, the
normalisation option and its guards exist, and the docs section is the one users read. A `dff` node
would add a node to wire and a second place for the same guards.

Sosa's "within each trial" baseline is not reproduced by a sliding window over the whole recording;
with 80 trials of about 15 s, a 20 s window is close. Per-trial baselines need trial boundaries,
which `condition_split` can provide only by splitting the recording. Documented as a deviation.
Sosa's 2-sample Gaussian smoothing: not added (no node smooths; a `smooth_sigma_frames` param on
`data_slice` is the natural follow-up if a panel needs it).

C2. `caiman_cnmf` / `caiman_cnmfe` / `cnmf_multisession`: a declared `dff` output from
`detrend_df_f(A, b, C, f, YrA, quantileMin=8, frames_window=500)` on the accepted-plus-rejected
component set, written as `FluoData(dff, file_name="dff")` next to `fluorescence`. This is the
paper's own unit for Patel and Zaki (both CNMF-E). Two params, `dff_quantile_min` and
`dff_frames_window`, under `advanced`. The call needs `A`, `b`, `C`, `f`, `YrA` which
`component_outputs` already has; memory cost is one `(components, frames)` float32 array.

C3. Not built: Suite2p dF/F from `F - neucoeff * Fneu` (needs `Fneu` as a port or a param on `suite2p_roi`;
#924 rewrites that node), replacing LCCD's loop with the shared helper (do it when #931 declares
`dff`; the result changes at the edges because LCCD leaves NaN there).

### D. Per-bout baseline z-score in `eta` (the Patel measure)

Listed because the paper's Fig 5 numbers cannot be reproduced without it and it is three lines:
a `baseline_window` param (frames before onset; default 0 = off). When set, each extracted window is
z-scored against the mean and s.d. of the `baseline_window` samples immediately before the onset
(`windows[:, abs(pre_event) - baseline_window : abs(pre_event)]`; `calc_trigger_average` already holds
the `(events, time, cells)` array). Windows whose baseline s.d. is 0 or within 1e-6 of its magnitude
become NaN for that cell and are skipped by nan-aware statistics; the default path keeps the exact
old arithmetic so its outputs stay bit-identical, float32 included. Matched bout counts per condition (the paper
subsamples to the smaller class) stay manual: `data_slice` on the event column, or the user's choice.

---

## 4. Plan: one PR

Branch `feature/dff-outline-event-shading` on develop-main. Title: "Add dF/F, ROI outlines and
event shading for figure panels". One PR because the five parts are only useful together: the
panels in section 5 need the new unit (C), the new view (A, B) and the paper's measure (D) in the
same build, and each part is small enough that a combined diff stays reviewable. Five commits, one
per part, so a reviewer can read them separately and a bad part can be dropped without a rebase of
the rest.

| Commit | Covers | Files |
|---|---|---|
| `feature: add dff cell normalisation to data_slice` | C1 | `studio/app/optinist/wrappers/data_utils/data_slice.py`, `params/data_slice.yaml`, `docs/specifications/algorithm_nodes.md` |
| `feature: output caiman dff from detrend_df_f` | C2 | `studio/app/optinist/wrappers/caiman/cnmf.py` (`component_outputs`), `cnmfe.py` (annotation), `cnmf_multisession.py`, the three caiman yamls, docs |
| `feature: add baseline_window to eta` | D | `studio/app/optinist/wrappers/optinist/basic_neural_analysis/eta.py`, `params/eta.yaml`, docs |
| `feature: outline ROIs and restore roi alpha in the image plot` | A | `frontend/src/components/Workspace/Visualize/Plot/ImagePlot.tsx`, new `Plot/roiOutline.ts`, `Editor/ImageItemEditor.tsx`, `store/slice/VisualizeItem/VisualizeItemSlice.ts` and selectors |
| `feature: shade events in the time series plot from a linked box` | B | `Plot/TimeSeriesPlot.tsx`, new `Plot/eventSpans.ts`, `VisualizeItem.tsx` (select), `VisualizeItemSlice.ts`, `VisualizeContext.tsx` |

Order inside the branch: C1, C2, D (backend, change the numbers), then A, B (frontend, change the
view). The backend commits can be re-run on the reproductions before the frontend ones land.

Tests, all in the same PR:

| Area | Test | Pins |
|---|---|---|
| data_slice | `studio/tests/app/optinist/wrappers/test_data_slice.py` | percentile F0 vs a numpy reference; maximin vs `suite2p.extraction.dcnv.preprocess` on a synthetic trace (1e-6); `f0_window_frames 0` = whole-recording percentile; zero baseline -> NaN with a warning; BehaviorData untouched with a warning; `std`/`sem` dropped; the existing contract test unchanged |
| caiman | new `studio/tests/app/optinist/wrappers/test_caiman_dff.py` | two-component synthetic (A, b, C, f) equals `detrend_df_f`; `test_algolist.py` sees the new `dff` port on all three nodes |
| eta | `studio/tests/app/optinist/wrappers/test_eta.py` | window z-scored against its own first `baseline_window` samples equals numpy; zero-s.d. window skipped; default 0 leaves every existing test green |
| roiOutline | `frontend/src/components/Workspace/Visualize/Plot/__tests__/roiOutline.test.ts` | interior pixel null, edge pixel kept, one-pixel ROI kept, field-border pixel kept, null input stays null |
| eventSpans | `Plot/__tests__/eventSpans.test.ts` | threshold, touching runs merged, run that reaches the end, cap with the warning flag |
| selectors | `store/slice/VisualizeItem/__tests__/VisualizeItemSelectors.test.ts` | outline flag and shade-source link default and set |

Risk:

| Area | Risk | Notes |
|---|---|---|
| `data_slice` `dff` | Low | New option, default `none`; saved workflows without the keys load the defaults |
| caiman `dff` output | Low | Additive output; runs only in the caiman env; one extra `(components, frames)` float32 array |
| `eta` `baseline_window` | Low | Default 0 is the current behaviour |
| ImagePlot outline and alpha | Medium | Roi Alpha was deliberately pinned to 1 in de83bea7b with no stated reason; Edit ROI click and box select must be re-tested with Outline on and off (manual testcases below) |
| TimeSeries shapes | Low | Additive `layout.shapes`; capped at 500 spans per source record |

Manual testcases for the PR body:

- Miles, Visualize on the patel run: image box `images (cnmfe)` with `cell_roi`, Outline on, Roi
  Alpha 0.6; expected: one-pixel coloured contours over the correlation image, field drawn 133 wide by
  97 high, labels on request.
- Miles, same box: click an ROI interior and an ROI edge with Outline on; expected: both select the
  ROI and highlight the linked trace; box select with drag select on still lists the ROIs inside.
- Miles, Edit ROI with Outline on: merge two ROIs, delete one, cancel; expected: same result as with
  Outline off.
- Miles, timeseries box `concatenated_data (traces_licks)` with rows 178, 94, 219, 194 drawn, and a
  second box showing rows 267 and 268; "Shade events from box" set to the second box; expected: grey
  and a second colour bands behind the traces at the lick frames, in time units when Range Unit is time.
- Miles, Workflow: `data_slice` on `caiman_cnmfe.dff` with `cell_normalization dff` left `none`, `eta`
  with `baseline_window 30`; expected: the run is green and the population mean trace is flat before
  onset (baseline z about 0).
- `pytest studio/tests/app/optinist/wrappers/test_data_slice.py studio/tests/app/optinist/wrappers/test_eta.py studio/tests/app/optinist/wrappers/test_caiman_dff.py studio/tests/app/common/routers/test_algolist.py` and `yarn --cwd frontend test --watchAll=false src/components/Workspace/Visualize src/store/slice/VisualizeItem` -- all pass

---

## 5. Verification on the reproductions

- **Patel** (`40_TestData/Public-datasets/patel2026/workflow_cnmfe_bouts.yaml`): re-run with
  `caiman_cnmfe.dff` into `data_slice` (`zscore` as now) and `eta` with `baseline_window 30` (3 s at
  10 Hz). Expected: population mean alcohol response stays near 0 for this group-housed mouse; the
  per-cell values compare to the previous recording-z numbers at r about 0.9 (the README's numpy check
  of the paper's method gave 0.90 / 0.91). Fig 3c panel: Outline on, Roi Alpha 0.6, labels on; traces
  box shaded from the lick box, Range Unit time, STD on so each trace keeps its own scale.
- **Sosa** (`sosa2025_fig4d_before_after_switch.yaml`): insert `cell_normalization: dff`,
  `f0_method: maximin`, `f0_window_frames: 310` (20 s at 15.5 Hz) before the splits; expected: the
  mean-normalised heatmap spans well beyond 0.8 to 1.7 and the min-max render is no longer needed.
  Compare the binned dF/F of one cell against numpy with the same filters (1e-6).
- **LCCD** regression: unchanged until #931.
- Both re-runs are the PR's Evidence section (screenshots of the two panels before and after).

---

## 6. Open questions

1. Roi Alpha: was de83bea7b fixing a real Edit ROI colour problem? The commit has no body and no
   linked issue. If so, alpha applies only outside edit mode.
2. `maximin` sigma: Suite2p takes `sig_baseline` as its own parameter (default 10 frames at
   `win_baseline` 60 s). Fixing it to `window / 6` keeps three params; expose it if someone needs
   Suite2p-exact numbers.
3. Should `dff` on `data_slice` also accept the Sosa `Neuropil` series for `F - 0.7 Fneu`? That needs a
   second port; out of scope here, and `data_concat` cannot subtract.
4. Event shading from an `eta`-detected onset list would need the onsets written out (a small JSON
   next to `mean`); only worth it if someone wants onset ticks on raw traces rather than bands.
