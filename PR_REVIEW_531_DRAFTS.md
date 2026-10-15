# #531 review comment drafts: emuemuJP's PRs #941-#952

Drafts for review, 2026-10-15. **Nothing has been posted.** No comment, review, merge or retarget has
been made on GitHub. Each section below holds the comment text ready to paste after you approve it.

Sources: the internal reviews, probe outputs and coordination notes in the session scratchpad
(`rev531/`). PR heads were re-checked today with `gh pr view` / `gh pr checks` and match the reviewed
heads (941 6907bbea2, 942 24dd5ad93, 943 fd0a60f3b, 944 15375f6b4, 945 7f2da8636, 946 11d68f5c5,
947 77d368235, 951 143f8ed63, 952 e7686b8db; develop-main ab294b461).

## 1. Overview

| PR | Title (short) | Verdict | Top issue | CI note | Depends on |
|---|---|---|---|---|---|
| #941 | ImageData shape from TIFF headers | Approve after small changes | Needs an equivalence test beyond plain stacks (`tifffile`/`imageio` are unpinned) | All green incl. test_backend | none (merges clean with #938) |
| #942 | Stream suite2p `mc_images` from the binary | Request changes | Iterator write loses BigTIFF: registration fails for movies over 4 GiB | All green; the unit test cannot see the BigTIFF case | none; lowers the rule peak only together with item 1 |
| #943 | suite2p EditRoiData keeps a movie reference | Approve after one test change | The new test passes with the change reverted (Copilot) | All green; wrapper line not run in CI (no suite2p) | none |
| #944 | Lazy ROI-edit movie; lccd / vacant ROI reference | Request changes | Reference can point outside the experiment: silent trace change or permanently failing commits | All green | none; base of #952 |
| #945 | suite2p mask stack on disk as `RoiStack` | Hold; request changes if the direction is accepted | Experiment copy keeps pointing at the source; rollback cannot unpickle the roi node or anything downstream | Lint only (stacked on #943) | #943; #531 item 2 (a)/(b) decision |
| #946 | CaImAn C-order memmap in batches | Approve after changes | Outputs identical, but the 200-frame loop writes the file 6.6x; needs a CaImAn version guard | All green; equivalence test skipped (no CaImAn) | none; base of #947 |
| #947 | Keep `caiman_mc`'s movie memory-mapped | Request changes | Same BigTIFF loss as #942: fails for float32 movies over 4 GiB | Lint only (stacked on #946) | #946, rebased on its final head |
| #951 | Fill `caiman_cnmf`'s memmap from the files | Approve after changes | Mismatched frame shapes across files are silently broadcast | All green; new test skipped (no CaImAn) | #939 first (conflict in `caiman_cnmf`) |
| #952 | CaImAn cnmf stores an input-movie reference | Request changes; hold behind #944 | #944's lifetime problems, now on CaImAn's input movie | Lint only; **isort fails** | #944; conflicts with #939 |

#939 (yours, draft): CI green, currently CONFLICTING with develop-main only in `caiman.yaml` (the same
`scipy<1.17` pin #953 already merged). #953 is merged.

## 2. Proposed merge order (recommendation only; merging and retargeting are yours)

**Track A: suite2p and API.** By file list, #939 shares no file with these PRs, so they can go in
parallel with Track B.
1. **#941** and **#943**, in either order, once their test changes land. Both are results-preserving
   and CI-green; #943 is rollback-safe (older builds read its pickles).
2. **#942**, after the explicit `bigtiff=` and the byte-bounded batch. No urgency: at the shipped
   `batch_size: 500` registration stays the rule peak until item 1 lands.
3. **#946**, after the row-block loop and the CaImAn version guard; then **#947**, rebased onto the
   final #946 head with the BigTIFF fix, the F-order read and the tests, and retargeted to develop-main
   so test_backend runs on it for the first time.

**Track B: CaImAn cnmf and ROI edit (with #939).**
4. **#939**: merge develop-main and take develop-main's `caiman.yaml` (drop the duplicate pin hunk).
   Keep `edit_roi_dtype`. Optional: the float32 cast in the CaImAn commit, which keeps ROI-edit traces
   bit-identical to CaImAn's copy (without it they differ by up to 5.8e-6 relative on a full-frame ROI,
   about 6e-8 on typical ROIs, `agent-editroi/logs/p952_cast.log`).
5. **#951**, after shape validation, rebased onto develop-main with #939: keep both helper blocks,
   **do not re-add `images = images.data`** (keeping both sides of that hunk silently undoes #951 and no
   test would fail), and derive the dtype for `edit_roi_dtype` from the TIFF headers.
6. **#944**, after the in-experiment rule, the lazy open, the missing-file error and the fallback fix.
7. **#952**, retargeted to develop-main after #944: `images_ref` inside the experiment, #939's
   `edit_roi_dtype` copy as the fallback outside it, float32 cast kept, isort fixed.

**Hold.** **#945** until the #531 item 2 decision. If it goes ahead: after #943, and only with
experiment-copy support, a rollback plan, atomic writes and a node-relative path.

**Separate.** A new PR for item 1 (`batch_size`) after the results comparison on representative data.

**Release notes.** Rollback limits: after #944 / #952 an older build fails ROI commits on lccd / vacant
ROI / CaImAn nodes run in between; after #945 it cannot unpickle the suite2p roi node or anything
downstream.

**Where this differs from the earlier plans, and why**
- findings_531.md section 6, stage 1 ("#941, #942, #943, #946 -> #947, small changes"): #942 and #947
  now each carry a HIGH (the BigTIFF loss makes the node fail over 4 GiB), and #946 needs a loop rewrite
  plus a version guard. The code fixes are small, but these are request-changes, not nits. Only #941
  and #943 are close to ready.
- Stage 2 ("scipy pin (#953 or #939), #951, #944 -> #952 after coordination"): #953 has merged, so the
  pin is settled and #939 drops its hunk. Stage 2 gave #939 no position; this plan puts it first, as
  939_coordination.md recommends. The new verdicts strengthen that: #944 and #952 now need a design
  change (where references are allowed), so they should not gate #939, which is CI-green and carries
  the NWB mask-order correctness fix.
- 939_coordination.md's alternative (#944 -> #952 -> #951 -> #939) assumed #944 / #952 would be fixed
  quickly. With HIGH lifetime findings on both, I would not use it.
- Stage 3 (#945 "after the direction decision and the SIGBUS fix"): the experiment-copy and rollback
  failures are now the bigger blockers; SIGBUS is one of five.

## 3. Comment drafts

### #941: Read ImageData's shape from TIFF headers

Edits vs draft: added the Copilot line; described the probe set precisely (18 TIFF kinds plus PNG and
list inputs).

```markdown
Thanks, this removes a real cost from `/run/result`, and the read counts in the description match the code. I compared develop-main's `ImageData` with this branch in the app image on 18 kinds of TIFF written by both tifffile versions (plain, ImageJ T / Z / TZCYX, OME and other two-series files, (1, Y, X), page-by-page without metadata, BigTIFF, RGB, zlib, tiled, float32), plus PNG fallbacks and lists (including mismatched shapes). Shape and `output_path` were identical everywhere except for a truncated TIFF, where develop-main raised and this branch reports the header's frame count.

Requested changes:

1. **Add a parametrized equivalence test beyond plain stacks.** `tifffile` and `imageio` are `"*"` in pyproject, so an upgrade that changes series semantics would go unnoticed. Suggested cases: ImageJ TZCYX, OME with two series, (1, Y, X) (it decides 3D vs JSON), multi-page without shape metadata, and a PNG that must take the fallback, each asserting `shape == data.shape` and the same `max_index`.
2. Optional: a `logger.debug` when `shape` falls back to the full read, and one docstring sentence saying that a truncated file reports its header frame count.

Copilot left no inline comments. I agree with its summary, except that the regression coverage needs item 1.
```

### #942: Write suite2p mc_images from the registered binary in batches

Edits vs draft: used the BigTIFF wording shared with #947; "so it is not served" became "so no successful
node references it" (the review left open whether experiment.yaml keeps old outputPaths); the closing
question now cites the PR description rather than "your table"; added the Copilot verdict.

```markdown
Thanks, this is a clear change, and byte identity holds below the BigTIFF threshold. With the real suite2p 0.10.3 `BinaryFile` and tifffile 2022.3.25 (the suite2p env pin), the file was SHA-identical for 1, 37, 401 and 3,000 frames with batch sizes that do not divide the count.

Requested changes:

1. **Pass `bigtiff=` explicitly (blocking).** `tifffile.imwrite` chooses BigTIFF from `data.nbytes`. A generator has no `nbytes`, so the choice is always classic TIFF, even with `shape=` and `dtype=`. develop-main passed an array, so files over `2**32 - 2**25` bytes became BigTIFF. Now a file between that threshold and 4 GiB is written as classic TIFF (no longer byte-identical), and above 4 GiB the write raises after the work is done. With this branch's `binary_to_tiff`, 2,034 x 1024 x 1024 int16 (4.27 GB) came out as classic TIFF, and 2,050 x 1024 x 1024 (4.30 GB) raised `data too large for standard TIFF file` after registration had finished; develop-main writes BigTIFF for both. At 512 x 512 that means 8,129 to 8,191 frames change format and 8,192 or more fail. `bigtiff=nframes * Ly * Lx * 2 > 2**32 - 2**25` restores byte identity (checked). Please also add a test on that decision (e.g. capture the kwargs passed to `tifffile.imwrite`) and correct the docstring. #947 has the same issue.
2. **Bound the batch in bytes, not frames.** suite2p's `BinaryFile.read` holds the raw buffer and the `astype` copy at the same time, so 200 frames of 2048 x 2048 are about 3.4 GB transient. For example, use `max(1, (64 << 20) // (Ly * Lx * 2))` frames.
3. Optional: write to a sibling temp file and `os.replace`. A failed write leaves a truncated `mc_images.tif` (8 bytes in the case above). The node gets an error pickle, so no successful node references the file, but the fix is cheap.

Copilot's inline comment (atomic replace): partly agree. The partial file is real, but develop-main's writer was not atomic either and no successful node points at it, so it is item 3, not a blocker.

Question: your description notes that at the shipped `batch_size: 500`, `register_binary` is still the rule's peak. Do you plan to land this with the item 1 change, or first on its own?
```

### #943: Keep a movie reference, not the movie, in suite2p's EditRoiData

Edits vs draft: "so I will correct #531 item 4" became an acknowledgement of the author's own correction
(it does not commit you to an edit); added the explicit Copilot verdict.

```markdown
Thanks, the change is correct as far as I can verify. suite2p's commit (`suite2p_edit_roi/commit.py`) reads traces from `ops["reg_file"]` and never touches `EditRoiData.images`; `EditROI` only carries it through. The reference points at `suite2p_file_convert`'s own TIFF copies inside the experiment, and an older build can still read the new pickles. The 1.525 GB drop also matches 512 x 512 x 2,908 x 2 bytes, so your note that #531 item 4's "~4 GB of disk" is really 1.5 GB is right.

One request before merge:

1. **Test the wrapper line itself.** The new test builds `EditRoiData(images=ImageData(...))` by hand and calls `commit_edit`, so it still passes with `.data` put back in `suite2p_roi`. `test_fluorescence_orientation.py::test_spike_deconv_passes_fluo_as_is` already stubs `suite2p` through `sys.modules`. The same approach can run `suite2p_roi` with fake `detection` / `extraction` / `classification` / `default_ops` and assert that `info["edit_roi_data"].images` is an `ImageData` with `path == ops["filelist"]`, with `ImageData.data` monkeypatched to raise so that a full read fails the test.

Nit: the two-line comment could be one line ("reference only: suite2p edits read ops['reg_file']").

Copilot's inline comment (the test passes with the change reverted): agree; that is item 1.
```

### #944: Open the ROI-edit movie lazily; store a reference in lccd and vacant ROI

Edits vs draft: tightened from about 60 to 40 lines; named the services where `OPTINIST_DIR` is
container-local (main, premium, background; the public service mounts EFS) instead of "the deployed app
services"; scoped the rollback to lccd / vacant ROI nodes (CaImAn references arrive with #952); said the
delete-only failure was reproduced; split the Copilot verdict into agree / overstated.

```markdown
Thanks for this. Opening the movie at commit time is the right direction, and the saving is real: on a 262 MB movie the commit worker for a typical ROI drops from 266 MB to about 4 MB, and the node pickle no longer carries the movie. `open_edit_images` returned the same values as `ImageData.data` for every TIFF kind I tried (tifffile, ImageJ, OME, big-endian, zlib, PIL multi-page).

The problem is the file's lifetime. Today the pickle holds a snapshot of the movie. After this change it holds a path, and nothing guarantees the file still exists, unchanged, when the user commits. Requested changes:

1. **Store a reference only when the movie is inside the node's experiment directory** (`OUTPUT_DIR/{ws}/{uid}/`). Otherwise keep the array, or store a fingerprint (shape, dtype, size, hash of a few frames) and refuse to commit on a mismatch. Upstream outputs cannot change under a live node, because a forced re-run deletes the downstream pickles first (`delete_dependencies`). Two cases fall outside:
   - **An input TIFF wired straight into lccd / vacant ROI.** A same-name upload rewrites it in place (`create_file` opens it with `"wb"`). In my test the next commit then succeeded with different traces. Deleting it (`delete_file`) breaks every later commit on the node.
   - **HDF5 / MAT / NWB inputs.** Their TIFF is the workspace cache `OUTPUT_DIR/{ws}/input_tiff/...` (`_tiff_cache_path`). That cache is outside the experiment, so it is never uploaded to S3 or restored by `download_experiment`. On the main, premium and background services `OPTINIST_DIR` is container-local, so the cache is gone after a redeploy or instance migration.
2. **Open the movie only when new traces need it**: rows past `num_committed`, or `images.shape[0]` when there are no traces yet. Today `open_edit_images` runs at the top of every commit, so a delete-only commit with the file missing fails with `FileNotFoundError` (reproduced for the lccd, vacant ROI and CaImAn commits).
3. **Fail with a clear message when the file is missing**, ideally checked when the edit session starts. Today status, add, merge and delete never touch the file, so the user does all the editing and then gets a generic 500 "Failed to commit Edit ROI".
4. **Avoid the second copy in the full-read fallback.** For a single path, `ImageData.data` runs `np.concatenate` on a one-element list. With a zlib-compressed 262 MB TIFF the commit peaked at 525 MB, against 265 MB on develop-main. `np.asarray(imageio.volread(path))` or `tifffile.imread(path, out="memmap")` avoids the copy. Please also catch only the not-memory-mappable `ValueError` and log a warning when falling back.
5. **Rollback.** A build without this PR fails every commit on lccd / vacant ROI nodes that ran with it (`TypeError: 'ImageData' object is not subscriptable`, or `AttributeError` when there are no committed traces). Please state this in the PR. Better still, ship the commit-side reader one release before the wrappers start storing references.
6. **PR text.** The lccd / vacant ROI rule peak does not move (1,051 MB vs 1,049 MB on a 262 MB float32 movie, because `LoadData` builds two float64 copies). Please quote the pickle, API and commit gains instead of the suite2p rule peak. The risk table's "Edit downloads the whole experiment first" does not cover these cases: `ensure_experiment_synced_for_edit` runs only on the status route, only when the sync status is not SUCCESS, and it cannot restore a deleted input or the `input_tiff` cache.
7. **Tests**: the fallback (a compressed TIFF), a missing file (the clear error), a delete-only commit with the file missing (succeeds after item 2), and the committed pickle keeping the `ImageData` reference.

Copilot's inline comment (fallback in `open_edit_images`): partly agree. It is right that the fallback reads the whole movie, for non-memory-mappable single files and for every multi-file reference, and that a single file is copied twice (item 4). It overstates the effect as bringing back the original spike: files written by OptiNiSt nodes memory-map, and API requests no longer unpickle the movie either way. Per-file streaming for multi-file references can be a follow-up.

Question: are there workflows where lccd or vacant ROI read a movie that is neither an upstream node's output nor an input file? I found none, but item 1 assumes it.
```

### #945: Keep suite2p's per-ROI mask stack on disk and reference it

Edits vs draft: "the gain is larger than described" became the verified fact (the stack also leaves
every downstream pickle; downstream memory is not measured); dropped the unverified examples for a
moved experiment ("local data moved into Docker") and cited `normalize_output_path` instead; "empty ROI
images" became "all-NaN ROI images"; named #531 item 2 for (a)/(b); added the CI note and explicit
Copilot verdicts.

```markdown
Thanks for the careful work. The stack and the all / cell / non-cell images match develop-main exactly (checked with the real suite2p 0.10.3 `ROI.to_array`, overlapping ROIs included). It also removes the stack from every downstream node's pickle, which the description does not mention: on develop-main the stack travels there through the merged `nwbfile`.

This is a third approach next to #531 item 2's options, (a) fill in place and (b) label images / sparse ROIs, so I would like to hold it until we decide the direction. The reference mechanism brings failure modes the codebase has no handling for. What I could reproduce:

1. **Experiment copy keeps pointing at the source.** The pickle holds the same `RoiStack` twice (`edit_roi_data.im` and the `nwbfile`'s `all_roi_img`). `__replace_ids_recursive` rebuilds the first into a full array and returns the second unchanged, so after the source experiment is deleted the copy's pickle fails to load (`FileNotFoundError`), although the copy reported success.
2. **Rollback.** A build without `RoiStack` cannot unpickle these pickles (`AttributeError: Can't get attribute 'RoiStack'`), and the reference travels into every downstream pickle. A rollback therefore makes the roi node and everything downstream unreadable (ROI edit, downstream re-runs, data filter, copy).
3. **Re-run truncates the mapped file.** `open_memmap(..., mode="w+")` on the existing file truncates the same inode. In my test, a process holding the old stack mapped was killed with SIGBUS. A temp file plus `os.replace` avoids this, and also avoids partial files.
4. **Absolute path.** If the experiment directory moves, the whole pickle fails to unpickle, not just the stack read. Elsewhere the repo stores output paths without `OUTPUT_DIR` (`normalize_output_path`).
5. **Write failures are swallowed.** The stack is written inside the `try` that logs and continues, so a failure publishes N traces with a 0-row `im` and all-NaN ROI images. The pattern predates this PR, but the PR puts disk I/O inside it.
6. **Orphaned file.** After an add or merge commit the pickle is full size again and nothing references `roi_mask_stack.npy`, which stays on disk and is still uploaded and copied.
7. **Behaviour change.** When every ROI is a cell (or none is), develop-main fails in `np.nanmax` on an empty selection, and this branch returns an all-NaN image. That is an improvement, but it should be stated and tested.

CI ran lint only (stacked base). Locally the five related test modules pass (37 tests), but none covers copy, rollback, re-run or relocation.

Copilot's inline comments: both valid. The experiment-copy one is item 1 (reproduced exactly); the swallowed write failure is item 5.

Questions:
- Can we hold this until the (a) / (b) decision? If a quick win is needed now, (a) is results-identical, with no format or rollback impact.
- If a reference is kept, could it be an explicit object with a node-relative path that opens lazily, written atomically, handled by experiment copy, and kept out of downstream `nwbfile`s?
- Could you measure post-process (whole.nwb), an ROI-edit commit and one downstream rule at the same limit?
```

### #946: Convert CaImAn's motion-corrected movie to C order in batches

Edits vs draft: the row-block snippet now matches the probed code (`np.asarray(..., dtype=np.float32)`);
the 19 GB figure for the #893 input is labelled as a model estimate (it was not measured); noted that
border_nan True / False take the existing fallback; added explicit Copilot verdicts.

```markdown
Thanks for this. Since the main concern was whether results change, I checked that first.

With CaImAn 1.11.4, `save_memmap_c_order` wrote the same file name and the same bytes as `caiman.save_memmap(order="C")` in 93 synthetic cases: non-square and 3D dims, T from 1 to 1234, NaN / inf / -0.0, several batch sizes. I also ran the real `caiman_mc` on two tutorial crops (1234 and 150 frames, 96x128): rigid with each `border_nan` setting (True / False take the existing fallback) and pw_rigid. The memmap and every output (`mc_images`, `meanImg`, `rois`, shifts) are identical to develop-main, and the temp dir is clean afterwards. So the results side looks right.

Requested changes:

1. **Write the C-order file in contiguous row blocks, not frame batches.** Each 200-frame batch writes 800 bytes into every pixel row, so each batch dirties nearly the whole file again. On 512x512x1454 float32 (1.5 GB) I measured 10.1 GB written (6.6x) with the current loop, against 1.0x with row blocks:
   `rows = max(1, min(d, budget // (4 * T)))`, then `out[p:p+rows] = np.asarray(source[p:p+rows], dtype=np.float32) + np.float32(0.0001) + np.float32(0.0)`.
   This gave byte-identical files against `caiman.save_memmap` in all 20 cases I tried. My write model predicts up to about 19 GB written for the #893 input (3 GB), not measured, on the host's shared gp3 volume.
2. **Bound the memory in bytes.** The row-block budget does this. With frame batches, one batch of a 4096x4096x100 stack is 6.25 GiB (one temporary at the peak; numpy elides the second). 1024-frame batches still measured 2.0x writes and cost 1 GiB per batch at 512x512, so I would not go that way.
3. **Guard against CaImAn upgrades.** The equivalence test is skipped in CI (no CaImAn in the app image), and CaImAn 1.13.0 already changed exactly the mirrored line: `+ np.float32(0.0001) + np.float32(add_to_movie)` became `+ np.float32(add_to_movie)` with a default of 0.0001. Please fall back to `caiman.save_memmap`, with a warning, when `caiman.__version__` is not 1.11.x.
4. **Test:** set `CAIMAN_TEMP` to `tmp_path` (the test currently writes into `~/caiman_data/temp`), compare full file names, and add a case where the budget forces several blocks.

Optional: log when falling back (border_nan True / False, several files), so that an OOM there does not look like this fix failing.

Copilot's inline comments:
- Frame-count bound: agree (items 1-2). The arithmetic is right; the peak is one batch.
- Temp files left on failure: partly agree. The leak is real but predates this PR: any exception after `motion_correct` already leaves the F-order memmap and the temp dir (I reproduced that on develop-main). The rename-after-flush part is already done. A `try/finally` in `caiman_mc` that calls `cleanup_caiman_byid_tempdir` would cover both, here or in a follow-up.

Question: on the #893 input at 5,982 MiB, could you add the conversion step's write volume (`write_bytes` from `/proc/self/io`) before and after the change to the PR body?
```

### #947: Keep caiman_mc's corrected movie memory-mapped

Edits vs draft: **removed "a full run had read over 190 GB"**: the evidence file
(`agent-caiman-mc/wa/run_512x512x1454_mem1g_reads.out`) lost that line and no other file holds the figure.
Used the BigTIFF wording shared with #942; added that the page-cache cliff is not a regression for inputs
that work today; "3.2 GB at 2048x2048" became 3,200 MiB (200 x 16 MiB float32 frames); replaced the inline
code block with a one-sentence description of the test double; added the CI note and Copilot verdict.

```markdown
Thanks. On the tutorial crops (rigid and pw_rigid, 1234 and 150 frames), `mc_images.tif`, `meanImg` and `rois` are byte-identical to develop-main, and your reasoning about the F-contiguous view and the mean holds. I found two problems with the streamed TIFF write, plus the test gap:

1. **BigTIFF is lost (same root cause as #942).** `tifffile.imwrite` chooses BigTIFF from `data.nbytes`. A generator has no `nbytes`, so the choice is always classic TIFF, even with `shape=` and `dtype=`. `ImageData(images)` passed an array, so files over `2**32 - 2**25` bytes became BigTIFF. Now a file between that threshold and 4 GiB is written as classic TIFF (no longer byte-identical), and above 4 GiB the write raises after the work is done. On a 1030x1024x1024 float32 movie (4.32 GB), `movie_to_tiff` raised `ValueError: data too large for non-BigTIFF file` and left a 4.3 GB partial file. At 512x512 float32 the format changes above 4,064 frames and the write fails from about 4,096 frames. Please pass `bigtiff=movie.nbytes > 2**32 - 2**25`, and add a test that monkeypatches `tifffile.imwrite` and checks for `bigtiff=True` above that size (such a test fails on this head).
2. **Read the frames from MotionCorrect's F-order file.** In the C-order (pixels x time) file one frame is spread over every page, so each frame copy touches the whole file. With the file out of the page cache at a 1 GB limit, copying one frame read 1.08x the file, two frames 2.07x and four frames 4.06x. Your run is fine because the 3 GB file fits in the page cache under 5,982 MiB. This is not a regression for inputs that work today (develop-main runs out of memory there instead), but it is where the memory goal stops being met. Writing the TIFF from `mc.mmap_file[0]` (frame-contiguous), with `np.float32(0.0001) + np.float32(0.0)` added per batch, gave byte-identical TIFFs in my check (3/3). The mean can stay on the C-order file, which it reads once. That constant then belongs under the same CaImAn version guard requested on #946. Please also bound the batch in bytes: 200 frames are 3,200 MiB at 2048x2048.
3. **Tests.** With `movie_to_tiff` changed to `imwrite(path, np.array(movie))`, both tests still pass. A test double that raises in `__array__` and rejects slices longer than `batch_size` passes on this head and fails on that mutant.
4. **Rebase.** This branch is based on #946's first commit. Once #946 changes, please rebase onto its final head and re-check byte identity. CI has run only lint so far (stacked base), so the tests first run in CI after the retarget.

Optional: `movie_to_tiff` copies `ImageData`'s path layout. A streaming write inside `ImageData` (array-like in, byte-bounded batches, same BigTIFF rule) would keep the two from drifting.

Copilot's inline comment (tests do not guard against full materialization): agree, confirmed by the mutation above; item 3 is its suggested test double.
```

### #951: Fill caiman_cnmf's memmap from the image files in batches

Edits vs draft: "reachable from the UI" is now hedged to match the review ("as far as I can see"); the
memory comparison is stated as measured (787 MB vs 0.1 MB after import); "we" became "I"; added the
explicit Copilot verdict.

```markdown
Thanks, this is a clean change. I reproduced byte identity with develop-main's `util_get_image_memmap(ImageData(paths).data)` at batch sizes 4 and 1024 on these inputs: float32, uint16 lists, uint8, int32 above 2^24, float64, big-endian, ImageJ, OME, zlib-compressed, PIL multi-page and multi-series files, a str path, and mixed-dtype lists. The step itself allocates essentially nothing: 0.1 MB of numpy heap per call once CaImAn is imported, against about 2x the file on develop-main (787 MB for a 394 MB float32 file).

Requested changes:

1. **Validate frame shapes before allocating.** develop-main raises in `np.concatenate`, but this branch accepts these silently:
   - a `(4, 1, 7)` file next to a `(5, 9, 7)` one gives a `(9, 9, 7)` memmap, and `(4, 9, 1)` does the same;
   - a 2D `(9, 7)` file in the list, at the default batch of 1024, gives 14 "frames", 9 of them the 2D image broadcast. At batch 4 the same input raises, so the outcome depends on the batch size.
   Please require every file to be 3D with `shape[1:] == dims`, raise a ValueError naming the file otherwise, and assert `t == T` after the fill loop. The image input node allows several files and I found no shape check upstream, so as far as I can see this is reachable from the UI.
2. **Make the test count in CI.** The module-level `importorskip("caiman")` skips the whole file in the app image, so CI never runs it. Stubbing `caiman.mmapping.prepare_shape` and `caiman.paths.memmap_frames_filename` in `sys.modules` would let it run there; both functions under test share the stubs, so byte identity remains a valid check. Please add a negative test for item 1.
3. **Coordination with #939** (milesAraya, same function). The two PRs conflict in `caiman_cnmf`: #939 adds `edit_dtype = edit_roi_dtype(images.dtype)` right after the `images = images.data` line that this PR removes. Whichever lands second must not resolve that hunk by keeping both sides, which would put the full-movie read back. I propose #939 first; this PR would then derive the dtype from the TIFF headers it already opens (`np.result_type` of each file's `series[0].dtype`). I will confirm the order before you rebase.

Note: no byte bound is needed on the batch here. Each batch is a view on the memory-mapped file, and the fill loop allocated nothing at any batch size I tried (1024, 512, 476 and 256 frames).

Copilot's inline comment (validate frame shapes): agree, confirmed by probe; item 1. A 2D file in the list is a more realistic trigger than `(frames, 1, width)`.
```

### #952: Store a reference to caiman_cnmf's input movie for ROI editing

Edits vs draft: **corrected "not worse than today"** for multi-file references. That holds for uint16
only; for float32 inputs the review infers about 2x develop-main (not measured). Removed "see the
coordination note" (an internal file the author cannot see); labelled the 262 MB no-cast figure as an
estimate (only the 787 MB is measured); wrote the exact `np.mean(..., dtype=np.float32)` expression from
the diff; added per-comment Copilot verdicts.

```markdown
Thanks. The float32 cast is correct, and I checked it beyond what the test covers: reference plus cast gives traces bit-identical to the former float32 copy for uint16, float32, int16 and uint8 inputs, with ROIs up to a full 128x128 frame. The current test uses ROIs of at most 15 pixels, too small for numpy's pairwise summation to differ. For a typical ROI on a 262 MB uint16 movie, the commit worker drops from 530 MB to 6 MB.

This PR stores CaImAn's *input* movie, so the lifetime points from #944 apply here as well. Requested changes:

1. **Store the reference only when the movie is inside the node's experiment directory** (the same rule as requested on #944). Otherwise keep a copy, or verify a fingerprint at commit time.
   - A `caiman_mc` output (input, `caiman_mc`, `caiman_cnmf`, as in the tutorial) is fine.
   - An input TIFF wired straight into `caiman_cnmf` can be deleted, or rewritten in place by a same-name upload; a rewrite silently changes new traces.
   - HDF5 / MAT / NWB inputs point at the workspace `input_tiff` cache, which is never uploaded to S3, so every commit on such a node fails after a redeploy or instance migration.
   - #939's own-dtype copy (`edit_roi_dtype`) could serve as that fallback.
2. **Fix isort.** The test import should be one line; the fixed line is 88 characters, so it still fits black.
3. **Test the wrappers.** Nothing runs the two `images_ref` lines: the pickle-size check builds `EditRoiData` directly. A stubbed run of both wrappers, as #944's test does for lccd, would cover them. The cast test does not need CaImAn either: reproducing the memmap layout (a `(pixels, T)` C-order `np.memmap` viewed as `(T, Y, X)` with order F) lets it run in CI. So far CI has run only lint on this stacked PR.
4. **Rollback.** develop-main's CaImAn commit fails on a reference (`TypeError: 'ImageData' object is not subscriptable`). Please note this together with #944's.
5. Optional: replace the cast copy with `np.mean(images[:, ~np.isnan(data.im[i])], axis=1, dtype=np.float32)`. It is bit-identical in all the cases above and avoids a second selected-pixel array. This only matters for very large ROIs: for a full-frame ROI on a uint16 movie I measured 787 MB with the cast, against roughly the 262 MB selected-pixel array without it (estimate).

Copilot's inline comments:
- 1st (cast keeps both arrays): partly agree. The extra copy is real but scales with ROI size, and the "3 GB plus 6 GB" figure overstates it. The suggested `dtype=np.float32` is item 5.
- 2nd and 3rd (multi-file references read in full): partly agree. There is no commit-time saving for multi-file references: they are read in full and concatenated. For uint16 that is about the same as develop-main's float32 pickle (525 MB vs 530 MB in my probe). For float32 inputs I would expect about 2x develop-main (inferred, not measured). Per-file streaming would be a good follow-up, especially for multisession inputs given as one file per session.
- 4th (the test does not cover the wrappers): agree; item 3.

Question: would you be happy to keep a compact copy (for example #939's `edit_roi_dtype`) as the fallback for movies outside the experiment, so that the reference becomes an optimisation rather than the only path?
```

## 4. Corrections to findings_531.md

Collected from the per-PR reviews; the evidence is in each review.

- **§2 #941**: "any doubt falls back to the full read" does not cover a truncated TIFF: its header parses,
  so the header frame count is reported where develop-main raised. The PR body's tifffile 2022.3.25
  cross-check is not the relevant environment; this code runs only in the API (2025.8.28).
- **§2 #942**: "BigTIFF is chosen from the declared size as before" is wrong: with an iterator `imwrite`
  never chooses BigTIFF. "Results: No (byte-identical), Risk: Low" holds only below `2**32 - 2**25` bytes;
  above 4 GiB the node fails. Risk is High until fixed. "2048² int16 x 200 = 1.6 GB": the transient is
  about twice that (raw buffer plus `astype` copy in `BinaryFile.read`).
- **§2 #943**: confirmed. Add: rollback is safe for this PR.
- **§2 #944**: "the fallback is not a regression" is wrong at the commit worker: a non-memory-mappable
  single file peaks at 2x the movie, against 1x on develop-main. "Results: No / Risk: Medium" misses the
  silent trace change on an in-place replacement and the permanent commit failure on a missing file.
  "The reference survives a copy" holds only for files inside the experiment. "lccd's rule peak hardly
  moves": confirmed (1,051 vs 1,049 MB on a 262 MB float32 movie).
- **§2 #945**: experiment copy is worse than "materialises the stack": the second reference keeps
  pointing at the source, so deleting the source breaks the copy, and the `.npy` is walked too. "Results:
  No" misses the all-cells / no-cells case, which changes from a rule failure to an all-NaN image. SIGBUS
  confirmed (exit -7); `.npy` synced like `.pkl` confirmed.
- **§2 #946**: rating "on target" should be "sound, changes needed": write amplification (6.6x measured)
  and a CaImAn version guard. The upgrade checklist alone is not enough, because CaImAn 1.13.0 already
  changed the mirrored line. Byte identity confirmed (93 function-level cases, 5 wrapper configs); the
  file also lands in the same place (`$CAIMAN_TEMP`) with the same cleanup.
- **§2 #947**: "on target / Low / To change: -" should be "request changes" (BigTIFF, page-cache cliff,
  tests). "caiman_mc 9.1 -> ~1.3 GB and completes" holds only while the C-order file fits in the page
  cache, and only up to 4 GiB of float32 movie.
- **§2 #951**: the 1024-frame batch is justified for the write pattern only; memory does not depend on
  the batch, so no byte bound is needed. "Reject mismatched frame shapes" is more serious than noted: a
  2D file in a list is accepted silently at the default batch.
- **§2 #952**: bit identity confirmed on realistic ROI sizes (the PR's test does not reach them). "Risk:
  Medium" omits that the reference is the *input* movie, so #944's failures apply. isort confirmed; no
  backend CI has run on it.
- **§3.3 rollback**: add #945, which is worse: the roi node and every downstream pickle fail to unpickle.
  #943 is safe.
- **§3.4 CI**: confirmed (#945, #947, #952 lint only; #946 and #951 tests skipped for lack of CaImAn).
- **§5 experiment copy walk**: confirmed, dtype preserved. Quote the native rate (about 1.23 M elements/s:
  about 10 min for #637's 1.5 GB movie, about 5 min per walk of the 2.94 GB mask stack). The 12k-25k
  elements/s figures in some probe logs come from amd64 emulation, so "hours" overstates it.
- **New**: on develop-main the suite2p mask stack is also pickled into every downstream node's pickle
  through the merged `nwbfile` (`Runner`). This matters for the item 2 (a) / (b) choice.
- **§6 plan**: #953 has merged (the pin is settled). #942 and #947 are not "small changes" until BigTIFF
  is fixed; #946 needs a loop rewrite and a guard.
- **Pasted notes (context, not findings_531.md)**: "#946: >= 1024-frame batches with a byte bound (+2.7 GB
  writes measured)". 1024 frames still measured 2.0x and conflicts with any byte bound under 1 GiB at
  512x512; row blocks give 1.0x. The +2.7 GB was not reproduced (measured +8.6 GB on a 1.5 GB file).
