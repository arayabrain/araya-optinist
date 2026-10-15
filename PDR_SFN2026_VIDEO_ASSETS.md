# PDR: SfN 2026 promo video, Araya-owned assets

Source of the request: the 素材リスト sheet of `絵コンテ_OptiNiSt_SfN2026.xlsx` (Drive), rows owned by Araya, plus the request message (items 1 to 12 below keep its numbering).
Related engineering work: issue #876 (reproduce published figures from independent labs' public data).

Dates from the brief sheet: submission to Yamane-sensei by **2026-10-18**, booth around 2026-11-18. Creator lock date and asset handover date are TBD, "work back from 10-18". This PDR assumes assets are handed over by **2026-10-15** so the creator has three days; confirm that with the requester.

---

## 0. Production plan, 2026-10-13 (decisions taken; supersedes sections 2, 6, 7)

Deliver images and videos in batches to the creator's Drive folder (`1vFRFMpeBFOlJsu9gpY-5aBeVx19gJkLc`); the creator needs image files, not documents. Local staging: `/Users/milesd/Claude/Projects/SfN2026-video-assets/deliver/<cut>/`.

### Decisions

| Topic | Decision |
|---|---|
| Cut 1 and cut 6 dataset | funamizu2016 raw TIFF `mouse23_2014-03-27_sound_1.tiff` (Zenodo 15238969): 2256 frames, 512 x 512, uint16, one channel, about 31 Hz |
| Processing | Suite2p in OptiNiSt (`suite2p_file_convert` -> `suite2p_registration` -> `suite2p_roi`), plus `correlation` for cut 6 |
| Cuts 8 and 9 analysis | sosa2025 place-field map (`sosa_position_binning.yaml`, 7 nodes) |
| Cut 3 figure on the shelf | sosa2025 Fig 2 (paper figure CC BY 4.0), the panel our cut 8/9 analysis reproduces |
| UI capture | Local OptiNiSt stack (same frontend as the cloud), 1920 x 1080 viewport at 2x device pixel ratio, browser chrome excluded |
| Figures | SVG from Save Fig plus a 2x PNG of each |
| Movie format | PNG sequences at 2048 x 2048 (4x source), 30 fps, movie and RGBA ROI-outline layer as separate sequences, plus an H.264 preview MP4 |

### Shot list

Status 2026-10-13 evening: every row below except 11a is produced and staged under `deliver/` (manifest in `deliver/README.txt`); 11a waits for the production record. Nothing uploaded to Drive yet. Local records: cut 6 f2598931, sosa aecffe4b, millman 0d1ec3cd, reproduced sosa 7bd74087 (bit-identical heatmap), funamizu def97b73 (workspace 405). Follow-up bug found on the way: Reproduce marks the position `data_slice` node as error with the message "data_slice success" (task chip filed).

| # | Cut | Deliverable | Source |
|---|---|---|---|
| 1a | 1 | `cut01_movie/` PNG sequence, about 10 s, motion-corrected, light temporal smoothing, contrast-stretched | Suite2p registered binary |
| 1b | 1 | `cut01_roi_outlines/` RGBA PNG sequence, outlines drawing on one by one | Suite2p `stat` / iscell from the same run |
| 1c | 1 | `cut01_preview.mp4` (movie + outlines composited) | 1a + 1b |
| 3a | 3 | sosa2025 Fig 2 crop, PNG | `sosa2025/figures/sosa2025_natneuro_Fig2.jpg` |
| 3b | 3, 4 | Workflow canvas, full graph, PNG 2x, plus one PNG per node-reveal step | Local app |
| 3c | 3 | `suite2p_roi` parameter panel, PNG 2x | Local app |
| 6a | 6 | Phone-size screens: parameter panel, value typed, RUN, progress (390 x 844 at 3x) | Local app |
| 6b | 6 | ROI map, fluorescence traces (10 to 20 cells), correlation matrix: SVG + PNG | Visualize > Save Fig on the funamizu run |
| 7a | 7 | Publish toggle, private and public states, PNG 2x | Local app Dataview |
| 8a | 8 | Public Dataview list, PNG 2x | Local app `/public` |
| 8b | 8 | Opened public record with its parameter dialog, PNG 2x | Local app |
| 9a | 9 | Public record with the Reproduce button, workflow loaded in a workspace, RUN, result | Local app |
| 9b | 9 | Original and reproduced place-field heatmaps, SVG + PNG | Visualize > Save Fig |
| 11a | 9, 11 | QR SVGs and short URL | Generated from the final URLs |
| T | all | `credits.txt` with every credit line | |

Credit lines:
- Cut 1, 6: "Data: Funamizu, Kuhn & Doya, Nat Neurosci 2016; Zenodo 15238969, CC BY 4.0. Motion-corrected and segmented in OptiNiSt."
- Cut 3, 8, 9: "Data and figure: Sosa, Plitt & Giocomo, Nat Neurosci 2025; DANDI:001361, CC BY 4.0. Reanalysed in OptiNiSt."

---

## 0b. Cut 3 / cut 9 figure: reproducing Sosa 2025 Fig 4d faithfully (PDR, 2026-10-13)

### What the paper panel is

Fig 4 (CC BY 4.0) panels a, b, d, e, g, h: one heatmap per subpopulation, rows = cells sorted by peak, columns = position on the track, two columns "Before switch" / "After switch". Fig 4d is the TR (track-relative) cells of mouse 4 and mouse 18. Methods (Sequence detection and quantification; Place cell identification):
- 450 cm track in 45 bins of 10 cm; frames with speed < 2 cm/s excluded; per trial, mean dF/F per bin (occupancy-normalised); then trial-averaged.
- Rows sorted by the peak of the trial average over the **odd** before-switch trials; the panel shows the **even** before-switch trials and all after-switch trials in that order (cross-validated sort).
- Each cell normalised to its mean activity within the session. Colour map magma, x axis in radians (0 cm = -pi, 450 cm = pi).
- TR / RR / non-RR remapping are subpopulations from the paper's remapping classification (Extended Data), applied before plotting.

### What our file supports (DANDI 001361, sub-m11 ses-03, checked 2026-10-13)

- The session **is a switch day**: identifier `Env1_LocationB_to_A`; rewards land at 200 to 235 cm on trials 0 to 29 and at 80 to 100 cm from trial 30 on (74 `Reward` events matched to `position`). 81 trials, 19818 frames at 15.5 Hz, 349 Suite2p cells.
- Frame-aligned 1D channels for everything needed: `position`, `speed`, `trial number`, `reward_zone`, `teleport`. 1867 frames are below 2 cm/s.

### What OptiNiSt does today, and the gaps

Today (integration branch): `covariate_binning` (n bins over a range, `sort_by_peak`, `iscell`), `condition_split` (keep frames whose covariate column is in a range, with `margin`), `data_slice` (`zscore` cell normalisation), HDF5 1D channels into behaviour ports (PR 929, **still open on develop-main**; the `fluo_from_hdf5` detour works without it). This gives the current cut 9 plot: all 349 cells, whole session, 40 bins, sorted by own peak. It already has the Fig 4d *shape* (one diagonal), which is why cut 3 and cut 9 read as the same figure today.

| Gap | Needed for | Change | Size |
|---|---|---|---|
| G1 | before/after split | none: `condition_split` on the `trial number` column, < 30 and >= 30, two branches | workflow only |
| G2 | speed exclusion | none: a first `condition_split` on the `speed` column (>= 2) feeding both branches; needs the behaviour table as one multi-column CSV (position, speed, trial, trial parity), which the usual prep script writes, format conversion only | prep script, 1 h |
| G3 | **shared row order** (after panel sorted by the before panel) | `covariate_binning` gets an optional input port `order_from: HeatMapData`; when connected the rows follow that heatmap's cell order (its index already carries cell ids) instead of its own peak | ~20 lines + test, half a day |
| G4 | cross-validated sort (odd trials sort, even trials shown) | with G3: a third binning on odd trials is the `order_from` source for the even-trial panel; trial parity is a CSV column from G2 | workflow only once G3 exists |
| G5 | per-cell normalisation to session mean | `covariate_binning` row normalisation option (`none`, `max`, `mean`), or `data_slice` gains `mean`; the paper divides by the cell's mean | ~10 lines + test, 1 h |
| G6 | TR / RR subpopulations | not feasible as nodes (needs the paper's remapping classifier); show **all** cells and say so | none, caption |
| G7 | magma colour map, radians axis | cosmetic; Visualize colour map is selectable per plot (check), axis stays cm | none |

Total for a faithful "all cells, before vs after switch, cross-validated sort" panel: about 2 working days including the numpy check, plus the usual release and four-service deploy before it can be published on production. Workflow size about 14 nodes.

### Options

1. **Ship as is.** Cut 3 shows Fig 4d (crop to the "Before switch" column if the creator wants one panel); cut 9 shows our sorted heatmap. Same form, honest differences (one session, all cells, whole session, blue-red). No code change, nothing blocks the 10-15 handover.
2. **Faithful panel (G3 + G5 + prep + workflow).** Cut 9 then shows a two-panel before/after figure that is visibly the paper's panel. About 2 days; the asset could follow in batch 2 while the creator works on everything else.
3. **Switch figure to Millman 2020 Fig 3c** (eLife, CC BY): numerically exact already, no code change, but a two-line plot that reads less well at booth distance, and it moves cuts 8/9 off the hippocampus story.

Recommendation: 1 for the 10-15 handover, and start 2 in parallel as the batch-2 upgrade; drop to 3 only if 2 misses and someone objects to the differences in 1.

## 0c. How close each paper is to one reproduced figure (checked 2026-10-13)

Checked by putting the paper panel (`40_TestData/Public-datasets/<paper>/figures/`) next
to our output (`optinist-screenshots-876/`, the local records, the numpy comparisons in
each README). "Numbers" means the node output was compared against an independent numpy
computation of the same quantity; "paper" means compared against the published values.

| Paper | Closest panel | Status | What matches | What does not | To close the gap |
|---|---|---|---|---|---|
| Sosa 2025 (Nat Neurosci, CC BY) | Fig 4d, TR cells sorted by peak position, before/after reward switch | **Reproduced in form.** Record `aecffe4b`, reproduce run bit-identical | A diagonal of place fields sorted by peak, 349 cells x 40 bins, same axis (track position), same sort | One session, all cells not TR cells, whole session not before/after, no speed exclusion, no cross-validated sort, min-max not mean normalisation, blue-red not magma | Section 0b: G1 + G2 need only workflow changes, G3 (`order_from` port) + G5 (mean normalisation) are the code changes, now in progress on `feature/binning-order-from-and-norm` |
| Millman 2020 (eLife, CC BY) | Fig 3c, L4 Sst contrast tuning, running vs stationary | **Reproduced in numbers.** Record `0d1ec3cd` | Node output equals numpy exactly; curve shape and magnitude agree with the paper (running 2.7 to 102.6 vs paper about 10 to 125 at 80 %; stationary 41.6 vs about 45) | One session, 16 ROIs (paper pools 63 cells, 8 experiments); run and stationary are in separate nodes so the UI shows two plots not one; y axis is per-frame mean (x 3000 for the paper's units); sem over frames not neurons; no blank marker on the same axis | A `data_concat` of the two curves gives the overlaid plot; units and sem are display only |
| Funamizu 2016 (Nat Neurosci, by permission) | Fig 3a middle/right: one neuron, trials by locomotion speed, time- vs distance-aligned | **Reproduced in numbers**, all 8 curves equal numpy; plus Fig 2c (four sorted heatmaps, record `def97b73`), 2b (traces + position), 3c (three conditions vs distance, 8e-8 of numpy) | 3a: time-aligned curves spread by speed and distance-aligned curves converge, as in the paper (half-max -1.97 to -1.10 s vs 19.9 to 15.7 cm). 2c: the peak-sorted sequence is there. 3c: both intermittent conditions above continuous far from the goal, all rise towards it | 2c shows all 502 cells (paper: task-relevant only, smoothed 500 ms), so the band is noisier; 3c x axis runs 0 to 58.6 cm (paper 58.6 to 0) and the z-score is recording-wide; no sem band; adjacent group colours are hard to tell apart in the TimeSeries plot | Feed the `task_relevant` iscell from `prep_cell_sets.py` into the 2c nodes (no code change); the rest is plotting |
| Patel 2026 (Nat Neurosci, CC BY) | Fig 3c left, CNMF-E contour map on the FOV | **Partial, numeric only.** `caiman_cnmfe` with the PR 939 defaults recovers 81 of the authors' 85 ROIs (recall 0.95) at precision 0.32 (252 accepted). No finished UI record yet: the earlier run was OOM-killed and the clean rerun is still pending | Cell positions | Over-segmentation (3x the authors' count), and the comparison is against an mPFC session, not the BLA session the panel shows; Fig 5b/c bout responses ran (`workflow_bouts.yaml`, equal to numpy) but 5 alcohol bouts give a null response, which is the paper's group-housed result but not a figure anyone can see | Rerun CNMF-E alone on the Docker VM (about 30 min, 5.8 GB peak) after PR 939 is in the stack; raise `min_SNR` / `rval_thr` toward the authors' count; overlay contours on the max projection |
| Zaki 2025 (Nature, CC BY) | Fig 5a, footprints over the max projection | **Not attempted.** `workflow.yaml` exists; nothing has run | | The FC movie is 8161 x 608 x 608 (3 GB uint8, 12 GB float32), above what the 8 GB local Docker VM survives (the 0.5 GB Patel movie already peaked at 5.8 GB) | Needs the deployed environment or a `data_slice` to a shorter window first; the authors' 783 footprints ship in the file for scoring |
| Kim 2024 (Nature, CC BY-NC-ND) | none | **Blocked by licence**, not by the pipeline | | NoDerivatives forbids showing the figure beside ours | A HitL vs HitR `svm` demo is possible without the figure |
| Yamane 2025 (our own tool paper) | Fig 7 ROI overlay | Runs from `Fig7_workflow.yaml` | | Reproducing our own paper proves nothing to the audience | Not for the video |

Verdict: three papers have a panel reproduced to the numbers (Sosa in form, Millman and
Funamizu numerically); Patel is a method match with no figure yet; Zaki has not run;
Kim cannot be shown. For the video, Sosa stays the lead, Millman and Funamizu are the
two records that make the public list real, and Patel is worth one more run only if a
1-photon example is wanted on screen.

## 1. Findings that change the plan

Checked against `origin/develop-main` (528442255) on 2026-10-08 unless stated otherwise. Nothing here was checked on the production UI yet; each needs one look there before it goes into a reply.

| ID | Finding | Impact |
|---|---|---|
| **F1** | **A viewer cannot reproduce someone else's public record into their own workspace.** The workspace `ReproduceButton` reproduces the user's *own* experiment. On the public Dataview, `publicDataviewReproduceWorkflow` (`GET /public/dataview/workflow/reproduce/...`) only loads the graph into the read-only `WorkflowDetailsView` (node table, parameter dialog, Close button). No copy, import or download action exists there. | Cut 9 ("tap Reproduce, the workflow loads into your workspace, RUN") cannot be recorded honestly today. The brief's "Facts verified" row also states this capability and needs correcting. See D3. |
| **F2** | **Node labels in the UI are the algorithm keys**, not product names: `suite2p_roi`, `caiman_cnmf` (or `caiman_cnmfe`), `lccd_cell_detection`, `pca`, `dpca`, `cca`. The sheet says "Suite2p, CaImAn, LCCD, PCA, dPCA, CCA". | Cut 4's note "node names exactly as in UI" and its headline cannot both hold. The creator can set the headline in product names over a screenshot that shows the keys. |
| **F3** | **Six nodes is not a valid graph.** `suite2p_roi` needs `suite2p_file_convert` upstream; all three detectors need an image input node; `cca` and `dpca` need a `behaviors_data` input. A real, connectable graph with those six algorithms is about 9 nodes. | Build the real 9-node graph and screenshot it; the creator crops or animates six of them. Do not fake edges. |
| **F4** | **Production lags develop-main.** Latest release is v1.1.11 (2026-10-05). #927 (import restores input paths), #932 (`covariate_binning`, `condition_split`) and #936 (lone-trace plot fix) merged 2026-10-08. #928, #929, #930, #938 are still open. Which image production runs was not checked from this session. | Any public analysis built on `covariate_binning` needs a release and a deploy to production before it can be published and recorded. Deploys must cycle all four ECS services. |
| **F5** | **Every candidate dataset is CC-BY-4.0.** Checked today via the DANDI API: 000718 (zaki), 001454 (patel), 000039 (millman), 001361 (sosa), 001605 (funamizu NWB), 001188 (kim). Zenodo 15238969 (funamizu raw TIFFs, OIST sample data) is also CC-BY-4.0. | CC-BY-4.0 permits commercial and promotional use with attribution and an indication of changes. The licence is not the deciding factor; the paper *figure* licence matters only if a paper figure is shown (kim: CC-BY-NC-ND, funamizu: not open). |
| **F6** | **Figure export already supports SVG.** Visualize > Save Fig offers png, svg, jpeg (`SaveFig.tsx`). PNG exports at on-screen size because `toImageButtonOptions` sets no width, height or scale. | "High resolution" (item 2) is met by exporting SVG. No code change. |
| **F7** | **Public deep link is a filter, not a page.** `/public` is outside the auth routes. `DataviewRecords` reads `uid`, `name`, `workspace_id` and similar from the query string, so `/public?uid=<uid>` should show a one-row list, but it does not open the record. Not yet tried on production. | The cut 9 QR can point to `/public?uid=<uid>`. Auto-opening the record when `uid` is given is a small optional change (fold into D3 if we build it). |
| **F8** | **The landing page has no demo.** `/` renders `LandingPage`; nothing in it mentions a demo, and it has no i18n. | The end card's "Try the demo" has no target. Either define one (the public record from D2 is the natural demo) or change the call to action. |
| **F9** | **No workspace sharing exists**, and the repo holds only PNG logos (`frontend/public/static/optinist_logo.png`, `araya_logo.png`). | Item 12 must be set up by logging in as the account itself. Items 9 and 10 cannot come from the repo. |

---

## 2. Decisions needed first (①)

### D1. Which dataset

Requirements from the storyboard: cut 1 needs a **raw movie** (the description says two-photon) with an ROI overlay produced in OptiNiSt; cut 6 needs an ROI map, traces and a correlation matrix; the credit must fit on screen.

| Dataset | Raw movie | Lab | Fit | Risk |
|---|---|---|---|---|
| **funamizu2016** | Two-photon, 512 x 512, 10 TIFFs of about 1.19 GB (Zenodo 15238969) | Doya lab, OIST | Only two-photon movie in the set. Suite2p is robust on two-photon. Fig 2b, 2c, 3a, 3c already reproduced and checked against numpy (#876). | Not an independent lab. Brief forbids attaching OIST to the cloud version; a data credit shown in cloud cuts 6 to 9 sits close to that line. Paper figure is not openly licensed (never show it). Whether a TIFF matches the NWB session is not verified. |
| **zaki2025** | One-photon miniscope, 608 x 608, 3 GB | Cai lab, Mount Sinai | Independent, *Nature*, the headline target of #876 | CNMF-E on 3 GB is hours single-threaded (`n_processes = 1`, #888) and the 0.5 GB patel movie already ran out of memory locally. One-photon footage looks murkier on a big monitor. |
| patel2026 | One-photon, 194 x 317 | Tye lab, Salk | Independent, best CNMF-E match | Low resolution for a hook shot; CNMF-E currently blocked by memory and a SciPy 1.17 error; stored pixel masks are scrambled. |
| sosa2025, millman2020 | None (traces only) | Stanford, Allen | Independent, small, fast | Cannot supply cut 1. |

**Recommendation:** funamizu2016 for cut 1 (the hook has no cloud tag, so the OIST rule is not touched) and for the cut 6 figures, **subject to one question at the Yamane-sensei hearing**: may OIST-lab data appear in the cloud cuts with a data credit that does not name OIST? If the answer is no, cut 6 and cuts 8 to 9 move to sosa2025 (D2), and funamizu stays in cut 1 only.

Credit line template (CC-BY-4.0 needs author, source, licence and an indication of changes):

> Data: Funamizu et al., *Nat Neurosci* 2016; DANDI:001605 and Zenodo 15238969, CC BY 4.0. Processed in OptiNiSt.

If the creator puts a real published figure in cut 3 ("one published figure lifts off the shelf"), it must be from a CC-BY paper (sosa, zaki, millman), never funamizu or kim.

### D2. Which public analysis for cuts 8 and 9

Must be: independent data, light enough to rerun on camera, visually obvious that two results are identical, built only from nodes that are on production by recording day.

| Candidate | Nodes | Production ready? | Notes |
|---|---|---|---|
| **sosa2025 place-field map** (Fig 2a form) | `covariate_binning` sorted by peak, `condition_split`, `data_slice` | After a release containing #932. The local run also used #929 (1D position into a behaviour port, open); the `fluo_from_hdf5` workaround avoids #929 but needs a re-check | Independent (Giocomo lab), 93 MB NWB, runs in minutes, the sorted diagonal reads instantly from a distance |
| funamizu2016 Fig 2c | 3 x `eta` + `covariate_binning`, prep CSV | After a release containing #932 (eta `sort_by_peak` is on develop-main via #937) | Strong, already verified, but carries the D1 OIST question |
| millman2020 Fig 3c | 13 nodes incl. `data_slice` `mean_timeseries` | Needs #928 (open) or the unverified undeclared-handle edge | Line plot, less striking; more moving parts |

**Recommendation:** sosa2025 place-field map. It keeps the proof shot on an unconnected lab regardless of the D1 answer.

### D3. How cut 9 is made true (from F1)

| Option | What | Size | Honest? |
|---|---|---|---|
| **a. Build "Reproduce into my workspace"** on the public record | Logged-in viewer taps Reproduce in `WorkflowDetailsView`; backend copies the published workflow and its input files (already pulled locally by `sync_experiment_input_files` for viewing) into the viewer's workspace and reuses the existing reproduce path. Optionally auto-open the record on `/public?uid=` (F7) | Medium: about 2 to 3 days plus review, release, deploy | Yes, and the QR then lands visitors on a button that exists |
| b. Download workflow YAML from the public record, then Import | Public endpoint for the YAML plus a button; import already restores paths (#927), but the viewer uploads the data from DANDI themselves | Small | Yes, but not "one tap"; cut 9 copy must change |
| c. Record the publisher reproducing their own record | Existing button | None | **No.** "Re-run it yourself" implies another user |
| d. Rewrite cut 9 | "Open the shared workflow and its exact settings" | None | Yes, but loses the proof shot |

**Recommendation:** a. Cut 9 is "the proof shot for the whole video", and the cut 9 QR sends booth visitors to the real page. File it as its own issue today; if it cannot be released by about 10-14, fall back to b and tell the creator the cut 9 copy changes.

---

## 3. Assets that depend on the dataset (②)

**1. Cut 1: raw movie with ROI overlay, image sequence with alpha**
1. Spike first (half a day): match a `long_data.zip` TIFF to its session, run `suite2p_file_convert` + `suite2p_roi` in Araya OptiNiSt (dev cloud), confirm the ROI count and that the run fits the plan limits.
2. Export the ROI masks from the output NWB. A short script outside the repo renders two sequences at the monitor's native resolution (open decision 7 in the brief): the motion-corrected movie (8 to 10 s, contrast-stretched) and an ROI-outline layer as RGBA PNGs, with an outline-draw-on order the creator can animate.
3. Deliver: movie sequence, overlay sequence, frame rate, credit line.

**2. Figure exports: ROI map, fluorescence traces, correlation matrix (cuts 6, 9)**
1. From the same run, add a `correlation` node on the Suite2p fluorescence.
2. Export each plot as SVG (F6). Pick a 10 to 20 cell subset for traces so they read on a monitor. Cut 9's figure is the D2 result (place-field heatmap), exported as SVG from both the original and the reproduced run.

**3. One real public analysis (cuts 8, 9)**
1. Ship D3 and a release containing #927, #932, #936 (and #938 if merged) to production; cycle all four ECS services and verify each task digest.
2. In a dedicated Araya account on production: upload the sosa NWB, import the workflow YAML, run, publish the record.
3. From a second, unrelated account: open `/public?uid=<uid>`, Reproduce, Run. Screen-record at the monitor resolution with the cursor visible. Export both result figures as SVG and confirm they are identical (numbers, not just looks).
4. Record the public URL for item 11. This also closes the #876 task "publish a record and verify Reproduce from a second, unrelated account".

**4. Public Dataview screenshot (cut 8)**
1. Count real published records on production. The brief allows a list "only with a real count of entries".
2. If the count is thin, publish the other #876 reproductions (millman Fig 3c, funamizu Fig 2c, sosa) as genuine records rather than placeholders. Each must be real work, credited.
3. Screenshot the list and the opened record's parameter dialog (also covers item 6, cut 8).

## 4. Assets that do not need the dataset (③)

Shoot these on production so they match what visitors will see. If D3 or the release changes the UI, re-shoot after the deploy.

**5. Workflow canvas (cuts 3, 4).** Build the 9-node graph from F3 with real edges, tidy layout, full-screen canvas, hide panels. Export at 2x device pixel ratio. Tell the creator the exact labels shown (F2).
**6. Parameter panels (cuts 3, 8).** Cut 3: `suite2p_roi` parameter panel. Cut 8: the D2 analysis's parameter dialog from the public record.
**7. Publish toggle (cut 7).** The publish on/off control on a record in Dataview; capture both states.
**8. Mobile (cut 6).** First check whether the 9/14 phone screenshots show the parameter panel and RUN clearly (not located from this session). If they do, deliver them for a flat device frame, which the sheet allows. If not, capture at a phone viewport (390 x 844): open a parameter, type a value, tap RUN, progress. Whether the workspace UI is usable at phone width is not verified; if it is not, the "even your phone" claim needs a word with the requester before cut 6 is built on it.

## 5. Logos, URLs, account (④)

**9. OptiNiSt logo (vector):** 岩谷さん. Usage rules in a commercial promo go to the Yamane-sensei hearing.
**10. Araya logo (vector):** 宮田さん.
**11. URLs, QR, short URL**
- Cut 9: `https://www.araya-optinist.com/public?uid=<uid>` (F7; confirm on production).
- Cut 11: the English landing page `https://www.araya-optinist.com/`. Resolve F8 first: either the end-card CTA becomes "Try it free" (sign-up), or "demo" means the cut 9 public record.
- Short URL: prefer a path on our own domain (for example `araya-optinist.com/sfn`) over a third-party shortener: no vendor expiry, on-brand, and the domain is already short. Needs a redirect; how the frontend is hosted (CloudFront or ALB rule vs a frontend route) was not checked here. Add a UTM parameter on the target so booth traffic is countable. **Lock by 10-09**, since it appears from cut 2 onward.
- QR codes generated by Araya from the final URLs, error correction M or higher, delivered as SVG.

**12. Account for 大塚さん with a demo workflow**
Create a dedicated account (admin `POST /admin/users`, or `/register`), log in as it, import the cut 4 workflow and the D2 workflow, run both so outputs exist on first login. Hand credentials over privately, not on Slack. Decide the plan tier (runs need compute; free-tier limits apply). Whether admin-created users get a set-password email was not checked.

---

## 6. Schedule (proposed, handover 10-15)

Superseded on 2026-10-13 by the batches in section 0; kept for the record.

| Date | Work | Owner |
|---|---|---|
| 10-08 Thu | PDR; send D1, D2, D3 and the short-URL question to the requester | Miles |
| 10-09 Fri | Decisions agreed; short URL locked; file the D3 issue; cut 1 spike; shoot items 5, 7 and the cut 3 half of 6 | Miles |
| 10-09 to 10-13 | Build D3 (PR, review); render item 1; figure exports (item 2) | Miles |
| 10-13 to 10-14 | Release (#927, #932, #936, D3, #938 if merged) and deploy to production | user acks and deploys |
| 10-14 | Publish the analysis, record cut 9 from a second account, Dataview screenshots (items 3, 4, cut 8 half of 6); set up item 12; generate QR | Miles |
| 10-15 | Hand over all Araya assets with credit lines and a manifest | Miles |
| by 10-18 | Submission to Yamane-sensei | requester |

If D3 slips past 10-14, hand over everything else on 10-15 and deliver cut 9 under option b, with the copy change flagged to the creator.

## 7. Open questions for the requester

1. Asset handover date: is 10-15 right, or does the 10-18 submission only need an animatic with placeholders for cut 9?
2. Yamane-sensei hearing: may OIST-lab data (funamizu2016) appear in the cloud cuts with a credit that does not name OIST?
3. D3: agree to build "Reproduce into my workspace" before recording, with option b as the fallback?
4. Cut 4: OK to show the real 9-node graph with algorithm-key labels, headline in product names?
5. End card: what does "Try the demo" point to?
6. Monitor native resolution (brief open decision 7): needed before rendering item 1.
7. Brief "Facts verified" row: correct the Reproduce sentence (F1).
