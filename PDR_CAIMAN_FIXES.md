# PDR: CaImAn fixes still needed

Trigger: reproducing Patel et al. 2026 (DANDI 001454) with `caiman_cnmfe` for issue #876, on 2026-10-08.
Checked against `origin/develop-main` 528442255 unless stated otherwise.

Test movie: `sub-Cohort13b-M3-2_ses-20221221T142324_behavior+image+ophys.nwb`, `acquisition/OnePhotonSeries/data`, (39616, 97, 133) uint8 at 10 Hz. That is 0.5 GB as uint8 and 2.0 GB as float32. Local docker stack: 7.75 GB, 4 CPUs. Host: 16 GB.

Work already in flight elsewhere:

| Where | What | State |
|---|---|---|
| `claude/elastic-rubin-2d4d0d` (worktree `.claude/worktrees/wonderful-sammet-543f76`) | chunked `correlation_image` replacing `caiman.local_correlations`, `EditRoiData` in the source dtype, `scipy<1.17` | uncommitted, session "Cut caiman_cnmfe peak memory on long 1p movies" |
| separate session (user, 2026-10-08) | SciPy 1.17 fix (C1) | in progress |
| `fix/caiman-1-12-2-bump` (c2646767b, local only) | CaImAn 1.12.2, drops dead mc params | not pushed. Its blockers #918 and #920 are both merged now |

---

## 1. Findings

| ID | Finding | Evidence | Covered by |
|---|---|---|---|
| **C1** | **CaImAn 1.11.4 deconvolution fails under SciPy 1.17.** `estimate_time_constant` passes 2D arrays to `scipy.linalg.toeplitz`. SciPy 1.17 treats N-D input as a batch, so `np.linalg.lstsq` gets a 3D matrix. | In the fresh env (SciPy 1.17.1, numpy 1.26.4): `estimate_time_constant(y, p=1)` raises `LinAlgError: 3-dimensional array given`, `p=2` raises a broadcast `ValueError`, `constrained_foopsi(y, p=1)` raises. The Patel run died in `fit -> merge_comps -> merge_iteration -> constrained_foopsi`. Both caiman envs on this host resolved SciPy 1.17.1. CaImAn 1.12.2 fixes it upstream (`toeplitz(c=np.ravel(...), r=np.ravel(...))`). | separate session. `scipy<1.17` verified to resolve: caiman 1.11.4, scipy 1.16.3, numpy 1.26.4, tensorflow 2.15.0 |
| **C2** | **CNMF-E without patches runs out of memory on an ordinary 1p session.** The default `rf: null` runs the whole FOV at once, and CaImAn's corr_pnr init holds several full-movie float32 arrays. | Default-style run (rf null): SIGKILL about 5 min in, at 7.75 GB. Same movie with `rf: 32, stride: 16`: about 3.5 to 4.4 GB RSS, got through every patch. | partly: C5 makes patches the default; elastic-rubin covers only the end-of-run costs (C3, C4) |
| **C3** | `caiman_cnmf` ends with `local_correlations(mmap_images.transpose(1, 2, 0))`, which builds `w_mov = (Y - mean) / std` and a product array over the whole movie: about 4 GB peak here. | caiman 1.11.4 `summary_images.local_correlations` | elastic-rubin (`correlation_image`, test equal to 1e-12) |
| **C4** | `EditRoiData(mmap_images, im)` puts the float32 memmap view in the output dict that is pickled to `caiman_cnmfe.pkl`. For this movie that is about 2.0 GB of float32 written to disk, and possibly held twice while pickling. | code reading; pickle size not yet measured (no successful run yet) | elastic-rubin (stores uint8, 0.5 GB) |
| **C5** | **The `caiman_cnmfe` defaults are 2p-style and cap the FOV at 10 cells.** `K: 10` with `rf: null`: CaImAn passes `K` as `max_number` to `greedyROI_corr` (`initialize_components`), so the whole FOV yields at most 10 neurons. Other defaults also differ from CaImAn's own 1p demo. | `params/caiman_cnmfe.yaml` against CaImAn 1.11.4 `demos/general/params_demo_pipeline_cnmfE.json` (table below) | none |
| **C6** | **The NWB `image_mask` of every CaImAn ROI is reshaped in the wrong order.** CaImAn numbers pixels in Fortran order (`cnmf.fit`: `Yr = np.transpose(np.reshape(images, (T, -1), order='F'))`). The display path agrees (`get_roi` uses `order="F"`). But the NWB `roi_list` uses `A.T[comp].T.toarray().reshape(dims)`, which is C order. Non-square FOVs get scrambled masks; square FOVs get transposed masks. After an Edit ROI commit, `caiman_edit_roi/utils.py` writes `image_mask` from `edit_roi_data.im` (correct). So the same NWB field is wrong before an edit and right after it. Same pattern in `cnmf_multisession.py` (`spatial_filtered.T[i].T.toarray().reshape(dims)`). | Code reading and CaImAn's convention; the Patel run's NWB will be checked with `score_footprints.py` (it detects the order). The masks stored in DANDI 001454 are scrambled by the same kind of mistake (verified: re-reading their index in Fortran order gives compact cells). Introduced in 629be1b52 ("caiman CNMFのNWB保存機能を追加"), first seen in a 2021-12 sample notebook (d97e8bc9f). | none |
| **C7** | **The CaImAn frame rate comes from the NWB settings (default 30), not from the input.** `fr = nwbfile.get("imaging_plane", {}).get("imaging_rate", 30)`. A 10 Hz 1p movie is analysed as 30 Hz unless the user edits NWB settings. The quality check's event window (`decay_time * fr`) is then 3x too long. | `cnmf.py`, `cnmf_multisession.py`; the Patel source NWB states `OnePhotonSeries` rate 10.0 | none |
| **C8** | **The first CaImAn run after any `caiman.yaml` change builds a new conda env, about 35 min, with no feedback.** The node shows "running" throughout. The production Dockerfile does not pre-build snakemake envs, so each host resolves packages at its first CaImAn run. That is how C1 reaches users without any code change. | local: env build 16:40 to 17:17 for an `hdmf<4` line, again for `scipy<1.17`. `studio/config/docker/Dockerfile` has no env pre-build | none (infra) |
| **C9** | `use_online` (OnACID, PR #332, 2024) is exposed for `caiman_cnmf` but not `caiman_cnmfe`. | `params/caiman_cnmf.yaml` has `use_online: False`; `caiman_cnmfe.yaml` has no such key | listed in the elastic-rubin session's brief; OnACID support for the ring model in 1.11.4 not checked |

C5 defaults against CaImAn's 1p demo:

| Parameter | OptiNiSt `caiman_cnmfe.yaml` | CaImAn 1.11.4 1p demo |
|---|---|---|
| `init_params/K` | 10 | null (per-patch cap from patch size) |
| `patch_params/rf`, `stride` | null, 6 | 40, 20 |
| `init_params/nb` | 2 | 0 (exact ring background) |
| `preprocess_params/p` | 2 | 1 |
| `init_params/gSig` / `gSiz` | [4, 4] / [13, 13] | [3, 3] / [13, 13] |
| `merge_params/merge_thr` | 0.85 | 0.7 |
| `advanced/init_params/min_corr` / `min_pnr` | 0.85 / 10 | 0.8 / 10 |
| `advanced/init_params/ring_size_factor` | 1.5 | 1.4 |
| `advanced/patch_params/del_duplicates` | False | True |
| `advanced/patch_params/low_rank_background` | False | null |
| `advanced/quality_evaluation_params/use_cnn` | True | False (the CNN is trained on 2p data) |
| `advanced/quality_evaluation_params/rval_thr` | 0.8 | 0.85 |
| `min_SNR`, `decay_time`, `tsub`, `ssub_B` | 2.5, 0.4, 2, 2 | same |

---

## 2. Decisions needed

### D1. C1: pin now, or bump now
- **Pin `scipy<1.17`** (one line in `caiman.yaml`), keeping CaImAn 1.11.4. Verified to resolve. Smallest change; every host then rebuilds once (C8).
- **Land the 1.12.2 bump** (`fix/caiman-1-12-2-bump`), which contains the upstream fix. Larger: it changes parameters, so it needs the 1.12.2 re-checks noted when #918 merged (regenerate `caiman_cnmfparams_groups.json`).

**Recommendation:** pin now; the bump follows as its own PR. #925 later moves to CaImAn 1.13 anyway.

### D2. C5: change the `caiman_cnmfe` defaults to CaImAn's 1p demo values
Saved workflows carry their full parameter tree, so new defaults affect only newly added nodes. That needs confirming for workflows saved before #920 dropped retired keys. The tutorial and any CNMF-E e2e expectations will change.

**Recommendation:** yes, the demo values, including patches. `caiman_cnmfe` exists for 1p data, and with `K: 10` and no patches the defaults are wrong for any real 1p session (C2, C5).

### D3. C6: fix the order only, or also flag old files
NWB files already exported keep wrong masks; re-running fixes them. **Recommendation:** fix both writers and say so in the release notes; no migration.

### D4. C7: warn, or read the rate from the input
- Warn: log a warning when the input's own rate (NWB `rate` attribute, when present) differs from the NWB-settings rate.
- Read: use the input's rate when the NWB setting was left at its default.

**Recommendation:** warning first (small, no behaviour change). Reading the rate is a separate change touching every wrapper that uses `imaging_rate`: suite2p `roi.py`, `lccd_detection.py`, `vacant_roi.py` and the edit-ROI utils use the same default 30.

---

## 3. Plan

| PR | Covers | Files | Tests | Risk |
|---|---|---|---|---|
| **A** `fix/caiman-scipy-pin` | C1 | `caiman/conda/caiman.yaml` | smoke check in the built env: `estimate_time_constant` on a synthetic trace for p=1 and p=2 | Low: one pin; one env rebuild per host |
| **B** elastic-rubin | C3, C4 | `caiman/cnmf.py`, new `test_caiman_correlation_image.py` | equality with caiman's `local_correlations`; measure peak RSS and pickle size on the Patel movie before and after | Low to medium: `EditRoiData` consumers must accept a non-float32 movie (Edit ROI add and merge recompute traces from it) |
| **C** `fix/caiman-cnmfe-1p-defaults` | C5, C2 | `caiman/params/caiman_cnmfe.yaml`, `docs/specifications/algorithm_nodes.md`, tutorial sample if it uses CNMF-E | params router test (defaults load); one CNMF-E run on a small 1p movie | Medium: changes default results for new nodes |
| **D** `fix/caiman-nwb-mask-order` | C6 | `caiman/cnmf.py` (`component_outputs` `roi_list`), `caiman/cnmf_multisession.py` | unit test with a non-square `dims` and a one-pixel component at a known (row, col): the NWB `image_mask` must equal the `get_roi` mask | Low: output-only, NWB consumers get correct masks |
| **E** `fix/caiman-frame-rate-warning` | C7 | `caiman/cnmf.py`, `cnmf_multisession.py` | warning logged when the rates differ | Low |
| later | C8 | Dockerfile env pre-build, or an explicit lock file per env | image build | Medium (image size, build time) |
| later | C9 | expose `use_online` for CNMF-E if OnACID supports the ring model | | |

Order: A first (it unblocks every CaImAn run in a fresh env), then D (small, independent), B, C, E. The 1.12.2 bump after A.

---

## 4. Verification on the Patel movie

Workflow `40_TestData/Public-datasets/patel2026/` (generated by `make_patel_cnmfe_yaml.py`): `rf` 32, `stride` 16, `K` null, `nb` 0, `p` 1, `gSig` [3, 3], `gSiz` [13, 13], `merge_thr` 0.7, `min_corr` 0.8, `ring_size_factor` 1.4, `use_cnn` false, `min_SNR` 3, `rval_thr` 0.85, NWB `imaging_rate` 10.

| Run | Env | Result |
|---|---|---|
| 399 | caiman 1.11.4, scipy 1.17.1, `rf` null | SIGKILL (OOM) about 5 min into CNMF-E |
| 400 | same env, `rf` 32 | patches done (31 min), then `LinAlgError` in `merge_comps` (C1) |
| 402 | `scipy<1.17` (1.16.3) | SIGKILL 25 min in, still inside the patch stage that run 400 finished. Not a result: a global OOM in the 8 GB Docker VM while another session's CaImAn memory harness ran alongside (two of its processes, 4.7 GB each, were killed in the 5 min before). Backend container peak about 5.6 GiB, CaImAn process 3.2 GB at the kill. Rerun alone. |
| 407/4a20f2fa (2026-10-13) | integration branch with #939 (defaults: `min_SNR` 2.5, no other override), Docker VM to itself | success: CNMF-E node 25 min, backend container peak 6.5 GB (idle 1.0 GB); 267 components, 252 accepted; `score_footprints.py`: masks compact as stored (C6 fixed), 81 of 85 stored ROIs matched, precision 0.32, median IoU 0.63, trace r 0.78. Details in `40_TestData/Public-datasets/patel2026/README.md` |

`score_footprints.py` (same folder) matches OptiNiSt footprints to the authors' 85 stored ROIs. It reads the stored masks with the Fortran-order fix that DANDI 001454 needs, and reports which order OptiNiSt's NWB masks are in.

---

## 5. Open questions
1. Do deployed hosts already hold a caiman env built before SciPy 1.17 (so C1 has not reached users yet), or a newer one? Not checkable from this session; needs ECS Exec or a log of the first CaImAn run per host.
2. For D2: does any shipped tutorial or e2e test depend on the current CNMF-E defaults?
3. For PR B: do the Edit ROI add and merge paths work with a uint8 `EditRoiData.images`?
