(algorithm_nodes)=
Algorithm Nodes
=================

OptiNiSt includes a variety of third-party calcium (Ca<sup>2+</sup>) imaging software packages for processing video data and extracting cell ROI information. OptiNiSt also includes a selection of data analysis tools. Each step of data processing and analysis are contained within a Node, which can be mixed-and-matched. Here we summarise the usage and requirements of each node.

#### Data Processing

##### [CaImAn](https://caiman.readthedocs.io/en/latest/)

###### caiman_mc

- **Description:** Motion Correction ([NoRMCorre](https://caiman.readthedocs.io/en/latest/CaImAn_features_and_references.html#normcorre)). The function will perform either rigid or piecewise rigid motion correction depending on the attribute self.pw_rigid and will perform high pass spatial filtering for determining the motion (used in 1p data) if the attribute self.gSig_filt is not None.
- **Input:** ImageData (Tiff), HDF5
- **Output:** mc_images (ImageData), meanImg (ImageData: mean of the motion-corrected movie), refImg (ImageData: the template frames were aligned to, high-pass filtered when gSig_filt is set)
- **Parameters:**
  - **border_nan** [true, false, copy, default: copy]: flag for allowing NaN in the boundaries. True allows NaN, whereas ‘copy’ copies the value of the nearest data point.
  - **g_Sig_filt**　[int or None, default: None]:　 Size of kernel for high-pass spatial filtering in 1P data. If None no spatial filtering is performed.
  - **is3D** [bool, default: False]: Flag for 3D recordings for motion correction
  - **max_deviation_rigid** [int, default: 3]: Maximum deviation in pixels between rigid shifts and shifts of individual patches
  - **max_shifts** [(int, int), default: (6,6)]: Maximum shifts per dimension in pixels.
  - **min_mov** [float or None, default: None]: Minimum value of movie, used to adjust the brightness or offset. If None it gets computed.
  - **niter_rig** [int, default: 1]: Number of iterations rigid motion correction.
  - **nonneg_movie** [bool, default: True]: Ensures that the minimum value in the motion-corrected movie is non-negative by subtracting the minimum value if it's negative.
  - **num_frames_split** [int, default: 80]: Splits movie every x frames for parallel processing
  - **overlaps** [(int, int), default: (32, 32)]: Overlap between patches in pixels in piecewise-rigid motion correction.
  - **pw_rigid** [ bool, default: False]: flag for performing piecewise-rigid motion correction.
  - **splits_els** [int, default: 14]: Number of splits across time for pw-rigid registration.
  - **splits_rig** [int, default: 14]: Number of splits across time for rigid registration
  - **shifts_opencv** [bool, default: True]: Flag for applying shifts using cubic interpolation (otherwise FFT).
  - **strides** [(int, int), default: (96, 96)]: How often to start a new patch in pw-rigid registration. Size of each patch will be strides + overlaps
  - **upsample_factor_grid** [int, default: 4]:, Motion field upsampling factor during FFT shifts.
  - **use_cuda** [bool, default: False]: Flag for using a GPU.
  - **advanced** (See [CaImAn documentation](https://caiman.readthedocs.io/en/latest/core_functions.html#))

###### caiman_cnmf

- **Description:** Constrained nonnegative matrix factorization, find ROIs 2P data
- **Input:**　 Image (Tiff), HDF5
- **Output:** FluoData (fluorescence, dff), IsCellData, meanImg (ImageData: mean of the input movie)
- **Parameters:**
  - **decay_time** [float, default: 0.4] Length of typical transient in seconds. Dependent on fluorescent indicator dynamics.
  - **use_online** Provides faster processing and reduced memory requirements, slight reduction in accuracy. Good for large data.
  - **do_refit** Refit reprocesses data after an initial fit, refining results by using the centres of components from the initial fit as seeds for another iteration.
  - **K** [int, default: 10]: Number of components to be found, which bounds how many cells can be found. With rf None (the default) it counts components for the whole field of view, so the node returns at most K; with rf set it counts them per patch.
  - **gSig** [default: (4,4)]: Standard deviation of neuron size along x and y.
  - **ssub** [float, default: 2]: Spatial downsampling factor.
  - **tsub** [float, default 1, no downsampling]: temporal downsampling factor recommended for long datasets.
  - **nb** [int, default: 2]: Number of global background components (noise complexity).
  - **method_init** [‘greedy_roi’,’corr_pnr’, default: ‘greedy_roi’]: Use ‘greedy_roi’ for standard 2P, ‘corr_pnr’ for 1p (CNMF-E).
  - **roi_thr** [scalar between 0 and 1, default: 0.9]: Energy threshold for computing contours
  - **p** [int, default: 2]: Order of the autoregression model.
  - **rf** [int or None, default: None]: Half-size of patch in pixels.
    - None or 0 processes the whole field of view at once. With only_init (default true, under advanced > patch_params) the node then runs CaImAn's initialisation only: greedy ROI and HALS, no CNMF iterations (do_refit adds them). Its peak memory is about 13 bytes x pixels x frames plus about 1 GB, in the initialisation's seed search (ssub 2): about 11 GB for 512 x 512 x 2,900 frames, twice that for twice the frames.
    - A number fits each patch of (2 x rf + 1) x (2 x rf + 1) pixels on its own, so the fit takes about 16 bytes x (2 x rf + 1)^2 x frames per patch plus about 1.5 GB, whatever the field of view. K then counts per patch, so the node finds many more components (about 1,250 to 1,600 on a 512 x 512 movie at CaImAn's 2p demo density of one per 144 pixels, e.g. rf 15, stride 6, K 4, or rf 32, stride 16, K 16), deconvolves their traces (p) and returns different results.
    - With many components, patch mode can need more memory than None: each component is kept as a dense image mask, so the outputs need about 30 bytes x components x pixels (about 12 GB for 1,500 components at 512 x 512) and about 8 MB of disk per component at 512 x 512. CaImAn's CNN evaluation (use_cnn, under advanced > quality_evaluation_params) also holds about 8 bytes x components x pixels until it finishes.
  - **stride** [int or 0, default: 6]: Overlap between neighboring patches in pixels. Used to optimize memory consumption and parallelizing computations, as it allows for the processing of data in smaller segments while maintaining some continuity between them.
  - **merge_thr** [float, default: 0.85]: Trace correlation threshold for merging two components. A higher threshold value means that only components with very similar activity traces will be merged.
  - **n_processes** [int, default: 1, under advanced > patch_params]: CaImAn worker processes. The value is clamped to the CPUs available to the process minus one, so on a 2-CPU instance it always resolves to 1. Memory, not CPU, is the ceiling: each worker holds its own slice of the movie. With `rf: None` (the default) the whole field of view is one patch, so extra workers only parallelise preprocessing and residual computation; set `rf` as well to parallelise the patch-level fit.
  - **dff** (output): CaImAn's own ΔF/F from `detrend_df_f(A, b, C, f, YrA)`: (F - F0) / (B0 + F0), where F0 is a running baseline of the trace at a percentile CaImAn picks per trace and B0 the same baseline of the background under the component. If the fit has no `b` and `f` (the ring background, `nb: 0`), B0 comes from the ring model's static background `b0`; its ring fluctuations `W` are not included. A fit with no background at all gives F - F0 only, with a warning. Rows are in the same order as fluorescence. A ROI added or merged with Edit ROI has no CaImAn ΔF/F, so its dff row is NaN. dff is not written to the NWB file. An invalid frames_window stops the node before CaImAn runs; if CaImAn itself fails, the node still finishes and dff is all NaN with the error in the log. It adds run time: about 1.5 to 4 minutes for 267 components x 39,616 frames single-threaded with frames_window 500.
  - **frames_window** [int of at least 2, or null, default: 500, under advanced > dff_params]: Running baseline window in frames. null, or a window longer than the recording, uses one baseline for the whole recording and is the fast option. From the parameter form, which cannot set null, enter a window larger than the recording's frame count instead.
  - **advanced** (See [CaImAn documentation](https://caiman.readthedocs.io/en/latest/core_functions.html#))

###### caiman_cnmfe

- **Description:** Constrained nonnegative matrix factorization-endoscope, find ROIs for 1P data.
- **Input:**　 Image (Tiff), HDF5
- **Output:** FluoData (fluorescence, dff), IsCellData, meanImg (ImageData: mean of the input movie)
- **Parameters:**
  - Additional information on CNMF-E can be found on [Inscopix github](https://github.com/inscopix/inscopix-cnmfe?tab=readme-ov-file#algorithm-parameters)
  - The defaults follow CaImAn's own 1p demo (`demos/general/params_demo_pipeline_cnmfE.json`), except rf and stride, which are smaller so a long movie fits in about 5 GB. Under advanced, `use_cnn` is off because CaImAn's CNN classifier is trained on 2p data.
  - **decay_time** [float, default: 0.4] Length of typical transient in seconds. Dependent on fluorescent indicator dynamics
  - **do_refit** Refit reprocesses data after an initial fit, refining results by using the centres of components from the initial fit as seeds for another iteration.
  - **K [int or None, default: None]: Number of components to be found. This determines how many cells can be found** (per patch or whole FOV depending on whether rf=None). None lets CaImAn cap it at a fifth of the searched pixels.
  - **gSig** [default: (3,3)]: Standard deviation of neuron size along x and y.
  - **ssub** [float, default: 1]: Spatial downsampling factor.
  - **tsub** [float, default: 2]: Temporal downsampling factor for initialisation, recommended for long datasets.
  - **nb** [int, default: 0]: Background model. 0 is CNMF-E's exact ring model, which patch mode needs; a positive value approximates the background with that many low-rank components.
  - **roi_thr** [scalar between 0 and 1, default: 0.9]: Energy threshold for computing contours
  - **p** [int, default: 1]: Order of the autoregression model.
  - **rf** [int or None, default: 32]: Half-size of patch in pixels. Each patch is initialised on its own, so peak memory is about 24 bytes x (2 x rf + 1)^2 x frames plus about 1 GB: about 5 GB for rf 32 on a 40,000-frame movie. Halve rf for a movie four times longer. This holds for n_processes 1 (more workers multiply the per-patch term by their count) and a field of view up to about 140 x 140 pixels; above that the ring-background step after the patches needs about 5 bytes x pixels x frames whatever rf is, so crop or spatially downsample the movie first. 0 (None in a workflow file) processes the whole field of view at once, which holds about six float32 copies of the movie (more than 13 GB for 97 x 133 pixels x 40,000 frames) and only suits short movies.
  - **stride** [int, default: 16]: Overlap between neighboring patches in pixels. Keep it at least gSiz.
  - **merge_thr** [float, default: 0.7]: Trace correlation threshold for merging two components. A higher threshold value means that only components with very similar activity traces will be merged.
  - **dff** (output): CaImAn's own ΔF/F from `detrend_df_f(A, b, C, f, YrA)`: (F - F0) / (B0 + F0), where F0 is a running baseline of the trace at a percentile CaImAn picks per trace and B0 the same baseline of the background under the component. The ring model (`nb: 0`) leaves no `b` and `f`, so B0 comes from its static background `b0`; the ring fluctuations `W` are not included. Rows are in the same order as fluorescence. A ROI added or merged with Edit ROI has no CaImAn ΔF/F, so its dff row is NaN. dff is not written to the NWB file. An invalid frames_window stops the node before CaImAn runs; if CaImAn itself fails, the node still finishes and dff is all NaN with the error in the log. It adds run time: about 1.5 to 4 minutes for 267 components x 39,616 frames single-threaded with frames_window 500.
  - **frames_window** [int of at least 2, or null, default: 500, under advanced > dff_params]: Running baseline window in frames. null, or a window longer than the recording, uses one baseline for the whole recording and is the fast option. From the parameter form, which cannot set null, enter a window larger than the recording's frame count instead.

###### caiman_cnmf_multisession

- **Description:** Keep union of components found in multiple sessions
- **Input:**　 Image (Tiff), HDF5
- **Output:** FluoData (fluorescence, dff), IsCellData, meanImg (ImageData: mean of the input movie)
- **Parameters:** Same as caiman_cnmf with the addition of:
  - **K**, **ssub**, **p** [default: 4, 1, 1]: as in caiman_cnmf. With rf None, K counts components for each session's whole field of view. At ssub 1 each session's fit needs about 33 bytes x pixels x the session's frames plus about 1 GB: about 14 GB per session for two sessions of 512 x 512 x 1,450 frames. In patch mode CaImAn's session registration holds both sessions' footprints as dense images, about 30 bytes x components per session x pixels: about 12 GB at 512 x 512 with rf 15, stride 6 and K 4, half that with K 2. Bound K when setting rf.
  - n_reg_files
  - reg_file_rate
  - **session_lengths** [list of int, default: empty]: Frames per session. If set, the input movie is split at these boundaries instead of into n_reg_files equal parts (use when sessions have unequal length). The lengths must sum exactly to the number of frames; otherwise the run fails with a message naming both numbers.
  - **align_flag** [bool, default: true]: Align the templates before matching
  - **max_thr** [scalar, default 0]: Max threshold parameter before binarization.
  - **use_opt_flow** [bool, default: true]: Use dense optical flow to align templates
  - **thresh_cost** [scalar, default: 0.7]: Maximum distance considered.
  - **max_dist** [scalar, default: 10]: Maximum distance between centroids.
  - **enclosed_thr** [float or none, default: None]: If not None, set distance to at most the specified value when ground truth is a subset of inferred.
  - **fluorescence** (output): each session's CaImAn `C` trace of the union cell, concatenated in time. A union cell with no match in a session (possible with reg_file_rate below 1) has NaN fluorescence for that session, also in the NWB file.
  - **ROIs** (cell_roi, Edit ROI and the NWB ROI table): each union cell's footprint is the one from the first session it was matched in. With reg_file_rate 1 every kept cell is in the first session, so all footprints come from it.
  - **dff** (output): as in caiman_cnmf, computed per session with frames_window and concatenated in time like fluorescence, so each session has its own baseline. A union cell with no match in a session (possible with reg_file_rate below 1) has NaN dff for that session. With `nb: 0` the denominator uses each session's static ring background `b0` (not its `W` fluctuations). Not written to the NWB file.

##### [Suite2p](https://suite2p.readthedocs.io/en/latest/)

suite2p 1.1.0. Workflows saved with an earlier OptiNiSt carry suite2p parameter names that no longer exist: delete each suite2p node and add it again before running (the parameter panel cannot restore groups it does not show). A run with stale names fails: suite2p_registration and suite2p_spike_deconv with a `Workflow yaml error`, suite2p_roi with `parameters predate suite2p 1.1.0` after the log warns about each dropped group. A node saved without a key the yaml has since gained takes the yaml's default for it.

###### suite2p_file_convert

- **Description:** Convert to int16 and write suite2p's binary movie (`data.bin`)
  - Integer TIFFs with one frame per page, stored in frame order, are read in place, in batches. That includes integer HDF5 data.
  - float32 TIFFs, including float32 / float64 HDF5 data, are first normalised to int16 into a copy in the node directory, also in batches.
  - Everything else is first copied whole into the node directory: several series, RGB pages, ImageJ hyperstacks, OME-TIFFs whose frames are stored out of order, other float types (float64 / float16 TIFFs, float16 HDF5 data), and a file that needs a different TIFF reader from the first file.
  - An input file rewritten or deleted while it is read (for example, uploaded again under the same name) fails the node; run the workflow again.
- **Input:** Image (Tiff), HDF5
- **Output:** Suite2pData
- **Parameters:**
  - **force_sktiff** [bool, default: False]: Whether to use scikit-image for reading in tiffs
  - **batch_size** [int, default: 500]: How many frames are converted to the binary at a time. Registration and extraction have their own batch sizes.

###### suite2p_registration

- **Description:** Motion correction. suite2p computes its own reference image from `nimg_init` frames and registers the binary written by suite2p_file_convert in place, so re-running only this node registers an already registered movie. The 1P pre-filtering of suite2p 0.10.3 (`1Preg`, `spatial_hp_reg`, `pre_smooth`) was removed upstream; raise `smooth_sigma` and `spatial_taper` for 1P data instead.
- **Input:** Suite2pData
- **Output:** ops (Suite2pData), mc_images (ImageData), meanImg (ImageData: mean of the registered movie; suite2p_file_convert's meanImg is the unregistered one), refImg (ImageData: the reference frames were aligned to)
- **Parameters:**

  - **torch_device** [string, default: cpu]: `cpu`, `cuda` or `mps`. Falls back to cpu when the device is unavailable.

  - **run:**

    - **do_regmetrics** [bool, default: False]: Whether to compute the registration quality metrics (regPC, tPC, regDX) by PCA over the registered movie. QC only: they are kept in the ops output and not shown in the app. Skipped below 1500 frames. The PCA reads up to 2,000 frames, or 5,000 when the movie has at least 5,000 frames and neither side exceeds 700 pixels, and its memory can set the run's peak. Measured at 512 × 512 it adds about 3 GB at 2,908 frames and about 10 GB at 5,816 frames.

  - **registration:**

    - **batch_size** [int, default: 100]: How many frames to register at a time. Peak memory grows with it. The results also depend on it while it is below `nimg_init`: suite2p 1.1.0 ranks the reference candidates by the last batch only ([MouseLand/suite2p#1276](https://github.com/MouseLand/suite2p/issues/1276)), so changing the batch size changes the reference image and the registration. Keep the default unless memory requires a smaller batch.
    - **nimg_init** [int, default: 400]: How many frames to use to compute the reference image.
    - **smooth_sigma** [float, default: 1.15]: Standard deviation in pixels of the gaussian used to smooth the phase correlation between the reference image and the frame which is being registered. A value of >4 is recommended for one-photon recordings (with a 512x512 pixel FOV).
    - **smooth_sigma_time** [float, default: 0]: Standard deviation in time frames of the gaussian used to smooth the data before phase correlation is computed. Must stay 0: suite2p 1.1.0's time smoothing fails inside registration for any positive value, so the node refuses it with a message.
    - **maxregshift** [float, default: 0.1]: Maximum shift as a fraction of the frame size. If the frame is Ly pixels x Lx pixels, then the maximum pixel shift in pixels will be max(Ly,Lx) \* maxregshift.
    - **th_badframes** [float, default: 1.0]: Involved with setting threshold for excluding frames for cropping. Set this smaller to exclude more frames.
    - **norm_frames** [bool, default: True]: Normalize frames when detecting shifts.
    - **subpixel** [int, default: 10]: Precision of the subpixel registration (1/subpixel steps).
    - **spatial_taper** [float, default: 3.45]: Slope in pixels of the sigmoid that fades the frame edges before phase correlation; its midpoint sits 2 x this value in from each edge. Larger may help 1P recordings. suite2p 0.10.3 used its `spatial_taper` (default 40) only with `1Preg` on and otherwise tapered with 3 x `smooth_sigma`, which is 3.45, the new default: carrying an old 1P value of 40 forward attenuates most of a small frame.
    - **do_bidiphase** [bool, default: False]: Whether to estimate the bidirectional phase offset from the data.
    - **bidiphase** [int, default: 0]: Bidirectional phase offset in pixels, applied when it is not estimated.
    - **reg_tif** [bool, default: False]: Whether or not to write the registered binary to tiff files, one file per registration batch
    - **nonrigid** [bool, default: True]: Whether to use nonrigid registration, which splits the field of view into blocks and computes registration offsets in each block separately.
    - **block_size** [list of int, default: [128, 128]]: Size of blocks for non-rigid registration, in pixels. HIGHLY recommend keeping this a power of 2 and/or 3 (e.g. 128, 256, 384, etc) for efficient fft. Must be smaller than the frame along both axes: with a single block suite2p 1.1.0 cannot interpolate the block shifts, so the node then registers rigidly and logs a warning.
    - **snr_thresh** [float, default: 1.2]: How big the phase correlation peak has to be relative to the noise in the phase correlation map for the block shift to be accepted. In low SNR recordings like one-photon, I’d recommend a larger value like 1.5, so that block shifts are only accepted if there is significant SNR in the phase correlation.
    - **maxregshiftNR** [int, default: 5]: Maximum shift in pixels of a block relative to the rigid shift.

###### suite2p_roi

- **Description:** ROI detection 2P or 1P, signal extraction and cell classification. The frame rate is the NWB setting `imaging_rate`.
- **Input:** Suite2pData
- **Output:** Suite2pData, FluoData, IsCellData
- **Parameters:**

  - **tau** [float, default 1.0]: The timescale of the sensor (in seconds). Sets the detection bin size together with the frame rate, and the deconvolution kernel of suite2p_spike_deconv. The kernel is fixed to have this decay and is not fit to the data.

    - 0.7 for GCaMP6f
    - 1.0 for GCaMP6m
    - 1.25-1.5 for GCaMP6s

  - **diameter** [list of int, default: [12, 12]]: Expected cell diameter in pixels (y, x). Normalises the pixel distances behind each ROI's compactness and aspect-ratio statistics (classifier inputs), and sets the cell size sourcery looks for. Must be two numbers.

  - **detection:**

    - **algorithm** [string, default: sparsery]: `sparsery` (suite2p's default detector) or `sourcery` (the older detector). suite2p's cellpose mode is not available here.
    - **highpass_time** [int, default: 100]: Running mean subtraction across time with a window of this many bins. Values of less than 10 are recommended for 1P data where there are often large full-field changes in brightness.
    - **nbins** [int, default: 5000]: Maximum number of binned frames to use for ROI detection. A bin is `tau` × the frame rate in frames, or the number of frames ÷ `nbins` when that is larger.
      - Detection's memory grows with the number of binned frames: about 4.6 MB per binned frame at 512 × 512.
      - At 30 Hz and `tau` 1.0, detection becomes the run's peak beyond about 10 minutes of recording. From there it adds about 0.28 GB per minute, up to the 5,000-frame cap (about 83 minutes).
      - A lower `nbins` bounds that memory on long recordings, but it widens the bin and changes the detected ROIs. On a 13-minute recording, `nbins` 500 and 250 kept a half and a third of the ROIs.
    - **denoise** [bool, default: False]: PCA-denoise the binned movie before detection.
      - It changes the detected ROIs substantially: measured at 512 × 512, it found 1.5 to 2.3 times as many, and kept about half of the ROIs found without it.
      - Its memory grows with the number of binned frames: about +0.5 GB on an 8-minute recording at 30 Hz.
    - **block_size** [list of int, default: [64, 64]]: Block size for denoising, in pixels.
    - **threshold_scaling** [float, default: 1.0]: Controls the threshold at which to detect ROIs (how much the ROIs have to stand out from the noise to be detected). If higher, then fewer ROIs will be detected, and if lower, more ROIs will be detected.
    - **max_overlap** [float, default: 0.75]: Overlapping ROIs are allowed during cell detection. After detection, ROIs with more than this fraction of their pixels overlapping with other ROIs will be discarded. Must be below 1: ROI editing needs the overlap information suite2p only computes then.
    - **soma_crop** [bool, default: True]: Crop dendrites for cell classification stats like compactness.
    - **npix_norm_min** [float, default: 0.0], **npix_norm_max** [float, default: 100]: Discard ROIs whose soma area, relative to the median soma area of the first 100 detected ROIs, falls outside this range.
    - **sparsery_settings:**
      - **spatial_scale** [int, default: 0]: If set to 0, then the algorithm determines the optimal scale of the recording in pixels automatically. If it seems off, set it yourself to the following values: 1 (=6 pixels), 2 (=12 pixels), 3 (=24 pixels), or 4 (=48 pixels).
      - **highpass_neuropil** [int, default: 25]: Window for spatial high-pass filtering for neuropil subtraction before ROI detection takes place.
      - **max_ROIs** [int, default: 5000]: Stop detection after this many ROIs.
      - **active_percentile** [float, default: 0.0]: Above 0, each ROI's active frames are those over the lower of the usual threshold (from `threshold_scaling`) and this percentile (0 to 100) of its temporal projection. A small positive value admits more frames; raising it moves the cutoff up towards the usual threshold and admits fewer. 0 keeps the usual threshold.
    - **sourcery_settings:**
      - **max_iterations** [int, default: 20]: How many iterations over which to extract cells - usually stops before due to the threshold_scaling criterion. In suite2p 0.10.3 this also capped sparsery at 250 x `max_iterations` ROIs; in 1.1.0 `max_ROIs` caps sparsery and this key is sourcery's alone.
      - **connected** [bool, default: True]: Require each ROI to be a connected region.
      - **smooth_masks** [bool, default: False]: Smooth the ROI masks.

  - **extraction:**

    - **allow_overlap** [bool, default: False]: Whether or not to extract signals from pixels which belong to two ROIs. By default, any pixels which belong to two ROIs (overlapping pixels) are excluded from the computation of the ROI trace.
    - **inner_neuropil_radius** [int, default: 2]: Number of pixels to keep between the ROI and the neuropil donut.
    - **min_neuropil_pixels** [int, default: 350]: Minimum number of pixels used to compute neuropil for each cell.
    - **snr_threshold** [float, default: 0.0]: Discard ROIs whose trace SNR is below this and extract again. 0 keeps all ROIs.
    - **batch_size** [int, default: 500]: Frames per extraction batch.

  - **classification:**

    - **preclassify** [float, default: 0.0]: Apply classifier before signal extraction with probability threshold of “preclassify”. If this is set to 0.0, then all detected ROIs are kept and signals are computed.
    - **use_builtin_classifier** [bool, default: False]: Use suite2p's built-in classifier even when a user classifier exists in the server's home directory.

  The iscell output keeps suite2p's first column (cell or not); the probability column is not stored.

###### suite2p_spike_deconv

- **Description:** Estimates spikes from the extracted fluorescence (OASIS non-negative deconvolution), with the `tau` and frame rate of the suite2p_roi node
- **Input:** Suite2pData
- **Output:** Suite2pData, SpikingActivityData
- **Parameters:**
  - **extraction:**
    - **neuropil_coefficient** [float, default: 0.7]: Neuropil coefficient for all ROIs. Used to subtract neuropil contamination from cell fluorescence.
    - **batch_size** [int, default: 500]: Traces per preprocessing and deconvolution batch.
  - **dcnv_preprocess:**
    - **baseline** [string, 'maximin', 'constant' or 'prctile', default: maximin]: Baseline of each trace to be subtracted from each cell.
      - maximin: computes a moving baseline by filtering the data with a Gaussian of width sig_baseline frames, and then minimum filtering with a window of win_baseline \* fs, and then maximum filtering with the same window.
      - constant: computes a constant baseline by filtering with a Gaussian of width sig_baseline frames and then taking the minimum value of this filtered trace.
      - prctile: computes a constant baseline by taking the prctile_baseline percentile of the trace.
    - **win_baseline** [float, default: 60.0]: Window for maximin filter in seconds
    - **sig_baseline** [float, default: 10.0]: Gaussian filter width in frames (not seconds), used before maximin filtering or taking the minimum value of the trace (baseline = 'maximin' or 'constant').
    - **prctile_baseline** [float, default: 8.0]: Percentile of the trace to use as baseline if baseline = 'prctile'.

##### [LCCD](https://doi.org/10.1016/j.neures.2022.02.008)

###### lccd_detect

- **Description:** Low Computational-cost Cell Detection
- **Input:** ImageData
- **Output:** FluoData, IsCellData
- **Parameters:**

  - **blob_detector:** Initial step that identifies potential cells or "blobs" in each frame of the video.

    - **filtersize1** [int (filtersize1 <= Time / 2), default: 100]: Size of the larger temporal filter for smoothing.
    - **filtersize2** [int (filtersize2 < filtersize1), default: 4]: Size of the smaller temporal filter for edge detection.
    - **sigma** [float, smaller than T & filtersize2 < filtersize1, default: 1.25]: Standard deviation for the Gaussian filter.
    - **fsize** [int (odd number recommended), default: 9]: Size of the 2D Gaussian filter for spatial filtering.
    - **min_area** [int (min_area < max_area < X * Y), default: 16]: Define the acceptable size range for detected blobs.
    - **max_area** [int, (max_area < X * Y), default: 100]: Define the acceptable size range for detected blobs.
    - **sparse** [bool, default: False]: Determines whether to use sparse matrix operations.

  - **roi_integration:** Takes blobs detected across all frames and integrates them into coherent ROI that represent consistent cell locations over time.

    - **overlap_threshold** [float (0 < overlap_threshold < 1), default: 0.25]: Threshold for determining significant overlap between ROIs
    - **min_area** [int (min_area < max_area < X * Y), default: 16]: Define the acceptable size range for integrated ROIs.
    - **max_area** [int (max_area < X * Y), default: 100]: Maximum acceptable size for integrated ROIs.
    - **sparse** [bool, default: False]: Determines whether to use sparse matrix operations.

  - **lccd:**

    - **frame_divider** [int (Time / frame_divider >= 2), default: 100]: Divides the time dimension into segments for faster processing.

  - **dff:**
    - **f0_frames** [int (0, f0_frames, Time), default: 100]: Number of frames used to calculate the baseline fluorescence.
    - **f0_percentile** [float (0 <= f0_percentile <= 100), default: 8]: Percentile used for calculating the baseline fluorescence.

#### OptiNiSt (Analysis)

##### Basic neural analysis

###### ETA (Event-Triggered Average)

- **Description:** Calculates the average neural response around specific events in behavioral data.
- **Input:** FluoData, BehaviorData, IsCellData (optional)
  - **Neural data (X) and behavior data (Y) must have the same number of time points: X.shape[0] == Y.shape[0].**
- **Output:** mean (TimeSeriesData), mean_trace (TimeSeriesData: average across cells with an error band), mean_heatmap (HeatMapData), nwbfile
- **Parameters:**
  - **transpose_x** [bool, default: true]: Whether to transpose the neural data.
  - **transpose_y** [bool, default: false]: Whether to transpose the behaviour data.
  - **event_col_index** [int, default 1]: Index of column in behavioral data to use for event detection.
  - **trigger_type** ['up', 'down', 'cross', default: 'up']:
    - 'up' detects transitions 0 to trigger_threshold. The window runs from `pre_event` frames before the onset to `post_event` frames after the end of the event. Events whose length differs from the most common length are dropped and logged.
    - 'down' detects transitions trigger_threshold to 0. Here the event is the below-threshold run, so the window runs from `pre_event` frames before the falling edge to `post_event` frames after the next rising edge, and the same length rule applies to that run. The run length is the gap to the next event, so irregularly spaced events are dropped and the window is as long as the most common gap.
    - 'cross' detects both up and down transitions and averages them together with a fixed window, `pre_event` frames before each edge to `post_event` frames after it. The event length is 0 and no event is dropped for its length.
  - **trigger_threshold** [float, default 0.5]: Threshold value for trigger detection
  - **pre_event** [int, default: -10]: Number of time points before the trigger to include. The sign is ignored; -10 and 10 both mean 10 frames before the onset.
  - **post_event** [int, default: 10]: Number of time points after the end of the trigger to include. May be negative to end the window inside the event, as long as abs(pre_event) + event length + post_event is at least 1.
  - **baseline_window** [int, default: 0]: Number of frames immediately before the onset used as each event's own baseline. When above 0, each event window is z-scored per cell as (F - baseline mean) / baseline s.d. before averaging, as in Patel et al. 2026 (a 3 s pre-bout baseline at 10 Hz is 30). Must be 0 or a whole number from 2 to abs(pre_event), and the window must reach the onset (event length + post_event at least 0). A baseline that is constant for a cell (s.d. at most 1e-6 of its largest absolute value) or has no finite sample skips that event for that cell only, and the skipped pairs are logged with their cell numbers. mean, std and sem then use the remaining events, and a cell with no event left is NaN in `mean` and left out of `mean_trace`. With the option on, NaN samples in the input are excluded per time point, from the baseline and from the averages, instead of making the average NaN. 0 averages the raw windows.
  - **mean_trace_band** ['std', 'sem', default: 'sem']: Error band for the mean_trace output, computed across cells of the per-cell event-triggered means. Note the per-cell mean output's own std/sem are computed across events, not cells. The plot's STD toggle shows whichever band is selected, so with `sem` it draws the sem.
  - **sort_by_peak** [bool, default: false]: Order the `mean_heatmap` rows by the time of each cell's peak in the averaged trace, earliest peak in the bottom row (the heatmap draws its first row at the bottom). Ties keep cell order, and a flat cell or one whose average is NaN throughout sorts with the earliest. The sort key is the same average that is plotted, so cells with no response also fall on the diagonal. Row labels keep the original cell numbers; `mean` is not reordered. `covariate_binning` defaults this to true.
  - `mean_heatmap` scales each row to 0..1 over its finite values, so a NaN time point in a cell's average is blank and the rest of that row still shows; a row that is NaN throughout stays blank.
  - Events whose window would cross the start or end of the recording are dropped and logged. `num_sample` in the NWB output is the number of events extracted after these drops, before any per-cell `baseline_window` skipping.

###### covariate_binning

- **Description:** Bins neural activity by a continuous behavioural covariate, such as track position or distance to a goal, and averages each cell's activity within each bin.
- **Input:** FluoData, BehaviorData, IsCellData (optional), HeatMapData `order_from` (optional)
  - **Neural data (X) and behavior data (Y) must have the same number of time points: X.shape[0] == Y.shape[0].**
  - A 1D behaviour dataset (for example one column loaded from HDF5) is treated as a single column, so use `event_col_index: 0` for it.
  - `order_from`: another node's heatmap, typically the `mean_heatmap` of a `covariate_binning` on a different part of the same recording. When connected, the rows of this node's `mean_heatmap` follow that heatmap's row labels (cell numbers), so a before and an after panel share one order, and `sort_by_peak` is ignored. Every cell binned here must have a row in the reference, so use the same `iscell` in both nodes; extra rows in the reference are ignored.
- **Output:** mean (TimeSeriesData, cells x bins, with std and sem, x axis is the bin centres), mean_heatmap (HeatMapData), nwbfile
  - mean_heatmap is the mean scaled per cell as set by `heatmap_normalization`, one row per cell, labelled with the cell number. Empty bins are left blank.
  - The NWB output stores mean, std, sem, `bin_centers` and `num_sample`, the number of time samples in each bin.
- **Parameters:**
  - **transpose_x** [bool, default: true]: Whether to transpose the neural data.
  - **transpose_y** [bool, default: false]: Whether to transpose the behaviour data.
  - **event_col_index** [int, default: 1]: Index of the behaviour column to bin by.
  - **n_bins** [int, default: 20]: Number of equal-width bins.
  - **use_data_range** [bool, default: true]: Bin from the minimum to the maximum of the covariate. When false, bin from `bin_min` to `bin_max`.
  - **bin_min** [float, default: 0.0]: Lower edge of the first bin, used when `use_data_range` is false.
  - **bin_max** [float, default: 1.0]: Upper edge of the last bin, used when `use_data_range` is false.
  - **sort_by_peak** [bool, default: true]: Order the heatmap rows by the bin of each cell's peak, lowest bin in the bottom row, with the same tie and flat-cell rules as `eta`. Only the heatmap is reordered. Ignored when `order_from` is connected.
  - **heatmap_normalization** ['minmax', 'mean', 'none', default: 'minmax']: How each row of `mean_heatmap` is scaled. `minmax` maps each cell's binned means to 0..1. `mean` divides each cell's binned means by that cell's mean activity over the samples that were binned (NaN samples excluded), the convention of place-field papers that show activity as a multiple of the session mean; it needs non-negative activity such as raw fluorescence, dF/F or deconvolved spikes, and a cell whose mean is not positive (for example after z-scoring) is blanked with a warning. `none` plots the binned means as they are. The `mean` output and the NWB file always hold the unscaled means, and the peak used by `sort_by_peak` is taken from the min-max scaled row, so the order does not depend on this choice.
  - Every time sample whose covariate lies in the range is used; samples outside it, NaN or infinite are excluded, and with `use_data_range` the range spans the finite values only. No trial or event filtering is applied, so std and sem are over time samples, which are usually autocorrelated, not over trials. A NaN in a cell's fluorescence makes that cell's mean NaN for the bin it falls in, as in ETA.

###### condition_split

- **Description:** Keeps only the time samples where a behaviour column meets a condition, so that a downstream node such as ETA runs on the matching part of the recording only.
- **Input:** FluoData, BehaviorData
  - **Neural data (X) and behavior data (Y) must have the same number of time points: X.shape[0] == Y.shape[0].**
  - A 1D behaviour dataset (for example one column loaded from HDF5) is treated as a single column, so use `event_col_index: 0` for it.
- **Output:** neural_data (FluoData), behaviors_data (BehaviorData)
  - neural_data keeps the orientation of its input; behaviors_data is always (time, columns), so a downstream node reads it with `transpose_y: false`.
- **Parameters:**
  - **transpose_x** [bool, default: true]: Whether to transpose the neural data.
  - **transpose_y** [bool, default: false]: Whether to transpose the behaviour data.
  - **event_col_index** [int, default: 1]: Index of the behaviour column the condition is tested on.
  - **condition** ['greater', 'less', 'equal', 'between', default: 'greater']:
    - 'greater' keeps samples with value > threshold.
    - 'less' keeps samples with value < threshold.
    - 'equal' keeps samples with value exactly equal to threshold, for example a trial type or zone ID.
    - 'between' keeps samples with threshold <= value <= threshold_upper.
  - **threshold** [float, default: 0.5]: Value the column is compared with.
  - **threshold_upper** [float, default: 1.0]: Upper bound, used by 'between' only.
  - **margin** [int, default: 0]: Also drop kept samples within this many samples of a rejected one. Use it when the imaging frames lead or lag the behaviour stream by a frame or two, so the first frames of a rejected stretch are not kept by mistake (for example 2 for Sosa et al. 2025).
  - **modulo** [float, default: 0]: When greater than 0, the condition is tested on the remainder of the column divided by this value instead of the column itself. With a trial-number column, `modulo: 2` and `equal` with `threshold: 1` keeps the odd-numbered trials and `threshold: 0` the even ones, which is how a cross-validated sort (order from one half, display the other) is built without a parity column. For a column that is not exactly integer, test the remainder with 'between' (0.5 to 1.5 for odd) rather than 'equal'. A column that codes the inter-trial interval as 0 or -1 puts those samples into one half, so split off `trial > 0` with a 'greater' node first.
  - Kept samples are joined in their original order. Nothing else is filtered: there is no trial detection, and a downstream window can span the join between two kept stretches that were not adjacent in the recording. The number of kept samples and stretches is logged.

##### Dimensionality Reduction

###### [CCA](https://scikit-learn.org/stable/modules/generated/sklearn.cross_decomposition.CCA.html) (Canonical Correlation Analysis)

- **Description:** Finds linear combinations of variables in two datasets that have maximum correlation with each other
- **Input:** FluoData (neural activity), BehaviorData (behavioral variables), IsCellData (optional)
  - **Neural data (X) and behavior data (Y) must have the same number of time points: X.shape[0] == Y.shape[0].**
  - **Y must be a single column after selecting the target_index.**
- **Output:** ScatterData (projected data), BarData (correlation coefficients).
- **Parameters:**

  - **I/O:**

    - **target_index** [int (columns start from 0), default 1]: Index of the target behavioral variable.
    - **transpose_x** [bool, default: True]: Whether to transpose the neural data matrix.
    - **transpose_y** [bool, default: False]: Whether to transpose the behavior data matrix.
    - **standard_x_mean** [bool, default: True]: Whether to standardize X by subtracting mean.
    - **standard_x_std** [bool, default: True]: Whether to standardize X by dividing by std.
    - **standard_y_mea** [bool, default: True]: Whether to standardize Y by subtracting mean.
    - **standard_y_std** [bool, default: True]: Whether to standardize Y by dividing by std.

  - **CCA:**
    - **n_components** [int, 1 <= n_components <= min(n_features, n_samples), default: 2]: Number of components to keep.
    - **scale** [bool, default: True]: Whether to scale the data to unit variance before performing CCA. Ensures that all variables contribute equally to the analysis.
    - **max_iter** [int, default: 500]: Maximum number of iterations of the power method.
    - **tol** [float, default: 0.000001]: Convergence tolerance for the power method. Will stop iterating when the change between iterations is less than this value.
    - **copy** [bool, default: True]: Whether to copy X and Y before fitting and transforming. Ensures that the original data is not modified during the CCA process.

###### [PCA](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html)

- **Description:** Dimensionality-reduction, linear reduction in components while retaining max variance.
- **Input:** FluoData (neural activity) , IsCellData (optional)
  - **Neural data shape should be (timepoints, ROI)**
- **Output:** ScatterData (projected data), BarData (explained variance ratios)
- **Parameters:**

  - **I/O:**

    - **transpose** [bool, default: True]: Whether to transpose the neural data matrix.
    - **standard_mean** [bool, default: True]: Whether to standardize X by subtracting mean.
    - **standard_std** [bool, default: True]: Whether to standardize X by dividing by std.

  - **PCA:**

    - **n_components** [int, float, default: None]: Number of components to keep. If int, it specifies the exact number of components. If float between 0 and 1, it specifies the proportion of variance to be retained.

  - **advanced:**
    - See [SciKit link](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html) for more information.
    - copy=True, whiten=False, svd_solver='auto', tol=0.0, iterated_power='auto'
    <!-- check which other scikit can be made simpler by splitting to advanced-->

###### [dPCA](https://github.com/machenslab/dPCA) (demixed Principal Component Analysis)

- **Description:** Decompose trial-averaged neural activity around behavioral events into components related to time and to each task condition (demixed PCA).
- **Input:** FluoData (neural activity), BehaviorData (behavioral variables), IsCellData (optional)
  - **Neural data should have shape (n_neurons, n_timepoints)** (the default `transpose: True` turns it into time x cells).
  - **Behavior data should have shape (n_timepoints, n_behavioral_variables)** and the same number of time points as the neural data.
  - **Trigger column:** thresholded per frame; a trial starts at the first frame above (or below) threshold after each transition. A signal already above threshold at frame 0 counts as an 'up' trigger, as in ETA.
  - **Feature columns:** read at the trigger frame and treated as categorical conditions (a few discrete levels each). Every combination of levels must occur at least once. Continuous columns (many distinct values) and constant columns are rejected. Unbalanced trial counts (a condition with fewer than 3 trials, or a 10x spread) are averaged as they are and logged as a warning; the per-condition counts are stored in the NWB file.
  - **Trials whose window leaves the recording are dropped** with a warning; conditions with unequal trial counts are averaged over all their trials.
- **Output:** HeatMapData per selected marginalization and component: rows are the condition combinations (in level order), columns are frames relative to the trigger. The NWB file also stores the explained variance ratio per marginalization and the level values of each feature.
- **Parameters:**

  - **I/O:**

    - **transpose** [bool, default: True]: Whether to transpose the neural data matrix to (time, cells).
    - **standard_mean** [bool, default: True]: Whether to standardize by subtracting mean.
    - **standard_std** [bool, default: True]: Whether to standardize by dividing by std.
    - **trigger_column** [int, default: 1]: Column index in behavior data for event triggers.
    - **trigger_type** ['up', 'down', 'cross', default: 'up']:
      - 'up' detects transitions from below to above trigger_threshold
      - 'down' detects transitions from above to below trigger_threshold
      - 'cross' detects either transition (the feature values at up and down edges are usually different, so prefer 'up' or 'down').
    - **trigger_threshold** [float, default: 0.5]: Threshold value for trigger detection.
    - **trigger_duration** [list of 2 ints, default: [-10, 10]]: Frames before (negative) and after the trigger to include; before < after.
    - **feature_columns** [list of ints, default: [0, 2]]: Columns in behavior data used as conditions for the dPCA analysis (at least one, usually 1 to 3). The default pair is the only full-factorial design in the sample behavior CSV and is heavily unbalanced, so the defaults are a smoke test rather than an analysis.

  - **dPCA:**

    - **labels** [str, default: 'tbc']: One character per data axis: time first, then one per feature column, so its length must be 1 + number of feature columns. Marginalizations are referred to by subsets of these characters (e.g. 'tb').
    - **regularizer** [float >= 0, default: 0]: If > 0, the regularization weight is regularizer\*var(data). Helps prevent overfitting. The library's 'auto' search is not supported.
    - **n_components** [int, default: 8]: Number of components kept in every marginalization. Must not exceed the number of cells.
    - **n_iter** [int, default: 0]: Number of iterations for the randomized SVD solver (sklearn).
    - **seed** [int, default: 0]: Random seed for the SVD solver; the same seed gives the same components on the same data.

  - **Plot:**
    - **figure_components** [list of ints, default: [0, 1]]: Which dPCA components to plot; each must be below n_components.
    - **figure_features** [list of str, default: ['t', 'b', 'c', 'tbc']]: Which marginalizations to plot; each must be a subset of `labels`.

- **Errors:** the node fails before fitting with a message naming the problem when the time axes differ (check `transpose`), a column index is out of range, `labels` has the wrong length, `regularizer` is negative or 'auto', a feature column is constant or continuous, a level combination has no trials, no trigger is found, or a plot selection is outside `n_components` or the marginalizations.

###### [TSNE](https://scikit-learn.org/stable/modules/generated/sklearn.manifold.TSNE.html) (t-distributed stochastic neighbor embedding)

- **Description:** Reduces dimensionality while preserving local structure of high-dimensional data.
- **Input:** FluoData (neural activity), IsCellData (optional)
  - **Neural data should have shape (n_neurons, n_timepoints).**
- **Output:** ScatterData (low-dimensional embedding)
- **Parameters:**

  - **I/O:**

    - **transpose** [bool, default: True]: Whether to transpose the neural data matrix.
    - **standard_mean** [bool, default: True]: Whether to standardize X by subtracting mean.
    - **standard_std** [bool, default: True]: Whether to standardize X by dividing by std.

  - **TSNE**

    - **n_components** [int, default: 2]: Dimension of the embedded space, i.e., the number of dimensions in the output. Most commonly set to 2 or 3 for visualization purposes.
    - **perplexity** [float, 5 <= perplexity <= n_samples - 1, default: 30]: Related to the number of nearest neighbors used. It can be thought of as a guess about the number of close neighbors each point has. Perplexity balances attention between local and global aspects of the data. Typical values range between 5 and 50
    - **early_exaggeration** [float, default: 12]: Controls how tight natural clusters are in the embedded space
    - **learning_rate** [float or 'auto', default: 'auto']: The learning rate for t-SNE

  - **advanced:**
    - See [SciKit link](https://scikit-learn.org/stable/modules/generated/sklearn.manifold.TSNE.html) for more information.
    - n_iter: 1000, n_iter_without_progress: 300, min_grad_norm: 0.0000001, metric: 'euclidean', init: 'pca', random_state: 0, method: 'barnes_hut', angle: 0.5

##### Neural population analysis

###### [GLM](https://www.statsmodels.org/stable/glm.html) (Generalized Linear Model)

- **Description:** Generalized Linear Model. Extends linear regression to allow for non-normal distributions of the response variable. Can model relationships between neural activity and behavioral variables.
- **Input:** FluoData (neural activity), BehaviorData (behavioral variables), IsCellData (optional)
- **Output:** BarData (model parameters), ScatterData (actual vs predicted values), HTMLData (model summary statistics)
- **Parameters:**

  - **I/O:**

    - **target_index** [int (columns start from 0), default 1]: Column index of behavioral variable to model.
    - **transpose_x** [bool, default: True]: Whether to transpose the neural data matrix.
    - **transpose_y** [bool, default: False]: Whether to transpose the behavior data matrix.
    - **standard_x_mean** [bool, default: True]: Standardize X by subtracting mean.
    - **standard_x_std\*** [bool, default: True]: Standardize X by dividing by std.
    - **standard_y_mean** [bool, default: True]: Standardize Y by subtracting mean.
    - **standard_y_std** [bool, default: False]: Standardize Y by dividing by std.

  - **GLM:**
    - **family** [str ('Binomial', 'Gamma', 'Gaussian', 'InverseGaussian', 'NegativeBinomial', 'Poisson', 'Tweedie'), default: 'Gaussian']: Distribution family
    - **link** [str ('CDFLink', 'CLogLog', 'LogLog', 'LogC', 'Log', 'Logit', 'NegativeBinomial', 'Power', 'Cauchy', 'Identity', 'InversePower', 'InverseSquared', 'Probit'), default: 'log']: Link function
    - **add_constant** [bool, default: False]: Add a constant term to the model.
    - **offset** [Array or None]: An offset to be included in the model. If provided, must be an array whose length is the number of rows in exog.
    - **exposure** [Array or None]: Log(exposure) will be added to the linear prediction in the model. Exposure is only valid if the log link is used. If provided, it must be an array with the same length as endog.
    - **missing** [str (‘none', ‘drop’, ‘raise’), default: 'raise']: If ‘none’, no nan checking is done. If ‘drop’, any observations with nans are dropped. If ‘raise’, an error is raised. Default is ‘none’.

###### [LDA](https://scikit-learn.org/stable/modules/generated/sklearn.discriminant_analysis.LinearDiscriminantAnalysis.html) (Linear Discriminant Analysis)

- **Description:** Finds linear combinations of features that best separate classes. Can be used for dimensionality reduction and classification of neural data, by projecting input to the most discriminative directions. The model fits a Gaussian density to each class, assuming that all classes share the same covariance matrix.
- **Input:** FluoData (neural activity), BehaviorData (class labels), IsCellData (optional)
  - **Neural data (X) and behavior data (Y) must have the same number of time points: X.shape[0] == Y.shape[0].**
  - **To predict 2 classes, use binary, for 3+ classes use int, e.g. 0,1,2,3**
- **Output:** BarData (cross-validation scores)
- **Parameters:**

  - **I/O:**

    - **target_index** [int (columns start from 0), default 1]: Index of target behavioral variable to use as class labels.
    - **transpose_x** [bool, default: True]: Whether to transpose the neural data matrix.
    - **transpose_y** [bool, default: False]: Whether to transpose the behavior data matrix.
    - **standard_x_mean** [bool, default: True]: Standardize X by subtracting mean.
    - **standard_x_std\*** [bool, default: True]: Standardize X by dividing by std.

  - **cross_validation:**

    - **n_splits** [int, default: 5]: Number of folds for cross-validation.
    - **shuffle** [bool, default: False]: Whether to shuffle the data before splitting into batches.

  - **LDA:**
    - **solver** ['svd', 'lsqr', 'eigen', default: 'svd']: Solver to use.
      - 'svd': Singular value decomposition, doesn't compute the covariance matrix, therefore this solver is recommended for data with a large number of features.
      - 'lsqr': Least squares solution. Can be combined with shrinkage.
      - 'eigen': Eigenvalue decomposition. Can be combined with shrinkage.
    - **shrinkage** shrinkage [float or 'auto', default: None]: Shrinkage parameter, between 0 and 1. Only available if solver is 'lsqr' or 'eigen'.
    - **priors** [array-like of shape (n_classes,), default: None]: Class priors. By default, the class proportions are inferred from the training data.
    - **n_components** :
    - **store_covariance**:
    - **tol** : 0.0001\*
    - **covariance_estimator**:

###### [SVM](https://scikit-learn.org/stable/modules/svm.html#svm) (Support Vector Machine)

- **Description:** Finds a hyperplane that best separates classes in high-dimensional space. Can be used for classification of neural states. It can handle non-linear relationships through kernel functions and is effective for both linear and non-linear classification tasks.
- **Input:** FluoData (neural activity), BehaviorData (class labels), IsCellData (optional)
- **Output:** BarData (cross-validation scores)
- **Parameters:**

  - **I/O:**
  - **target_index** [int (columns start from 0), default 1]: Index of target behavioral variable to use as class labels.
  - **transpose_x** [bool, default: True]: Whether to transpose the neural data matrix.
  - **transpose_y** [bool, default: False]: Whether to transpose the behavior data matrix.
  - **standard_x_mean** [bool, default: True]: Standardize X by subtracting mean.
  - **standard_x_std\*** [bool, default: True]: Standardize X by dividing by std.

  - **Grid Search**: Find optimal parameters by comparing models within specified range
    (Otherwise specify yourself in SVM parameters)

    - **use_grid_search** [bool, default: True]: Whether to perform grid search for hyperparameter optimization.
    - **params_to_grid_search**: Parameters and their ranges to search during grid search:
      - **C** [list of float, default: [0.001, 0.01, 0.1]]: Range of regularization parameter (inverse of regularization strength).
      - **kernel** [list of str (‘linear’, ‘poly’, ‘rbf’, ‘sigmoid’), default: ['linear', 'rbf']]: Kernel types to test; (See SVM below for more info).
      - **degree** [list of int, default: [2,3]]: Polynomial degree for 'poly' kernel. Degree of the polynomial kernel function. Must be non-negative. Ignored by all other kernels.
      - **gamma** [list of str (‘scale’, ‘auto’) or float, default: ['scale', 'auto']]: Kernel coefficient for 'rbf', 'poly' and 'sigmoid'; (See SVM below for more info).
      - **coef0** [list of float, default: [0.0]]: Independent term in kernel function. It is only significant in ‘poly’ and ‘sigmoid’.
      - **shrinking** [list of bool, default: [True]]: Whether to use the shrinking heuristic; (See SVM below for more info).
      - **tol** [list of float, default: [0.001]]: Tolerance for stopping criterion.
      - **decision_function_shape** [list of str (‘ovo’, ‘ovr’), default: ['ovr']]: Decision function shape ('ovr' for one-vs-rest, 'ovo' for one-vs-one); (See SVM below for more info).

  - **Cross Validation:**

    - **n_splits** [int, default: 5]: Number of folds in k-fold cross-validation.
    - **shuffle** [bool, default: True]: Whether to shuffle the data before splitting.

  - **SVM**:
    - **C** [float, default: 1.0]: Regularization parameter (inverse of regularization strength). Must be strictly positive. The penalty is a squared L2 penalty.
    - **kernel** [str (‘linear’, ‘poly’, ‘rbf’, ‘sigmoid’), default: 'rbf']: Kernel type to be used in the algorithm. For an intuitive visualization of different kernel types see [Plot classification boundaries with different SVM Kernels](https://scikit-learn.org/stable/auto_examples/svm/plot_svm_kernels.html#sphx-glr-auto-examples-svm-plot-svm-kernels-py).
      - 'linear': Linear kernel, equivalent to no transformation.
      - 'poly': Polynomial kernel, allows for curved decision boundaries.
      - 'rbf': Radial Basis Function kernel, creates circular/elliptical decision boundaries.
      - 'sigmoid': Sigmoid kernel, similar to a neural network.
    - **degree** [int, default: 3]: Polynomial degree for 'poly' kernel. Degree of the polynomial kernel function. Must be non-negative. Ignored by all other kernels.
    - **gamma** [str (‘scale’, ‘auto’) or float, default: 'scale']: Kernel coefficient for 'rbf', 'poly' and 'sigmoid'.
      - 'scale' (default) is passed then it uses 1 / (n_features \* X.var()) as value of gamma.
      - ‘auto’, uses 1 / n_features.
      - float, must be non-negative.
    - **coef0** [float, default: [0.0]]: Independent term in kernel function. It is only significant in ‘poly’ and ‘sigmoid’.
    - **shrinking** [bool, default: True]: Whether to use the shrinking heuristic. If the number of iterations is large, then shrinking can shorten the training time. However, if you loosely solve the optimization problem (e.g., by using a large stopping tolerance), the code without using shrinking may be much faster.
    - **probability** [bool, default: False]: Whether to enable probability estimates. This must be enabled prior to calling fit, will slow down that method as it internally uses 5-fold cross-validation, and predict_proba may be inconsistent with predict.
    - **tol** [float, default: 0.001]: Tolerance for stopping criterion.
    - **class_weight** [dict or 'balanced', default: None]: Weights associated with classes. Set the parameter C of class i to class*weight[i]\_C for SVC. If not given, all classes are supposed to have weight one. The “balanced” mode uses the values of y to automatically adjust weights inversely proportional to class frequencies in the input data as n_samples / (n_classes * np.bincount(y)).
    - **max_iter** [int, default: -1]: Hard limit on iterations within solver, or -1 for no limit.
    - **decision_function_shape** [str (‘ovo’, ‘ovr’), default: 'ovr']: Decision function shape ('ovr' for one-vs-rest, 'ovo' for one-vs-one). Defines the shape of the decision function for multi-class classification. It can be 'ovr' (one-vs-rest) for a (n_samples, n_classes) shape or 'ovo' (one-vs-one) for the original (n_samples, n_classes \* (n_classes - 1) / 2) shape. Internally, models are always trained using 'ovo', and 'ovr' is derived from it. This parameter is not applicable to binary classification.
    - **break_ties** [bool, default: False]: If true, decision_function_shape='ovr', and number of classes > 2, predict will break ties according to the confidence values of decision_function; otherwise the first class among the tied classes is returned. Please note that breaking ties comes at a relatively high computational cost compared to a simple predict.

##### Neural Decoding

###### [correlation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pearsonr.html)

- **Description:** Calculates pairwise correlations between neural signals.
- **Input:** FluoData (neural activity), IsCellData (optional)
- **Neural data shape should be (ROI, timepoints)**
- **Output:** HeatMapData (correlation matrix)
- **Parameters:**
- **transpose** [bool, default: False]: Whether to transpose the neural data matrix.

###### [cross_correlation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.correlate.html)

- **Description:** Estimate the similarity between pairs of neural signals as a function of a specified time-lag.
- **Input:** FluoData (neural activity), IsCellData (optional)
  - **Neural data shape should be (ROI, timepoints)**
- **Output:** TimeSeriesData (cross-correlation values for each pair)
- **Parameters:**
- **transpose** [bool, default: False]: Whether to transpose the neural data matrix.
- **lags** [int, default: 10]: Maximum number of time lags to compute correlations for.
- **method** [str (‘auto’, ‘direct’, ‘fft’), default 'auto']: Method for correlation calculation.
  - direct: The correlation is determined directly from sums, the definition of correlation.
  - fft: The Fast Fourier Transform is used to perform the correlation more quickly.
  - auto: Automatically chooses direct or Fourier method based on an estimate of which is faster.
- **shuffle_sample_number** [int, default: 100]: Determines how many times the data is shuffled to create the baseline distribution for testing.
- **shuffle_confidence_interval** [float, default: 0.95]: determines the confidence level used when calculating the interval of this baseline distribution.

###### [Granger](https://www.statsmodels.org/dev/generated/statsmodels.tsa.stattools.grangercausalitytests.html) (Granger causality test)

- **Description:** Performs Granger causality tests to assess potential causal relationships between neural signals.
- **Input:** FluoData (neural activity), IsCellData (optional)
  - **Neural data shape should be (ROI, timepoints)**
- **Output:** HeatMapData (Granger F-values), ScatterData (Granger F-values)
- **Parameters:**
- **I/O:**

  - **transpose** [bool, default: True]: Whether to transpose the neural data matrix.
  - **standard_mean** [bool, default: True]: Whether to standardize X by subtracting mean.
  - **standard_std** [bool, default: True]: Whether to standardize X by dividing by std.

- **Granger:**

  - **Granger_maxlag** [int, default: 1 (no lag)]: If an integer, computes the test for all lags up to maxlag. If an iterable int e.g.[1,3,5], computes the tests only for the lags in maxlag.
  - **Granger_addconst** [bool]: Whether to add a constant term to the model.
  - **use_adfuller_test** [bool, default: ]: Whether to perform Augmented Dickey-Fuller test for stationarity. Determines if the time series is suitable for Granger causality analysis. See[Augmented Dickey-Fuller test documentation](https://www.statsmodels.org/dev/generated/statsmodels.tsa.stattools.adfuller.html)
  - **use_coint_test** [bool]: Whether to perform cointegration test. Checks if non-stationary variables are cointegrated, which can affect the interpretation of Granger causality. See[cointegration test documentation](https://www.statsmodels.org/dev/generated/statsmodels.tsa.stattools.coint.html)

  - **adfuller**: The Augmented Dickey-Fuller test can be used to test for a unit root in a univariate process in the presence of serial correlation.

    - **maxlag** [int or None]: Maximum lag order for the test, default value of 12\*(nobs/100)^{1/4} is used when None.
    - **regression** [str ('c', 'ct','ctt','n'), default: 'c']: Type of regression to use.
      - “c” : constant only (default).
      - “ct” : constant and trend.
      - “ctt” : constant, and linear and quadratic trend.
      - “n” : no constant, no trend.
    - **autolag** [str (“AIC”, “BIC”, “t-stat”, None), default: 'AIC']: Method to use for automatically determining the lag order.
      - If “AIC” (default) or “BIC”, then the number of lags is chosen to minimize the corresponding information criterion.
      - “t-stat” based choice of maxlag. Starts with maxlag and drops a lag until the t-statistic on the last lag length is significant using a 5%-sized test.
      - If None, then the number of included lags is set to maxlag.
    - regresults [bool, default: False]: If True, returns the full regression results.
    - store ?
    - regresults ?

  - **coint**: The null hypothesis is no cointegration. Variables in y0 and y1 are assumed to be integrated of order 1, I(1).Uses the augmented Engle-Granger two-step cointegration test. Constant or trend is included in 1st stage regression, i.e. in cointegrating equation.
    - **trend** [str ('c', 'ct','ctt','n'), default: 'c']: The trend term included in regression for cointegrating equation. - “c” : constant only (default). - “ct” : constant and trend. - “ctt” : constant, and linear and quadratic trend. - “n” : no constant, no trend.
    - **maxlag:** Argument for adfuller, largest or given number of lags; see above.
    - **autolag:** Argument for adfuller, lag selection criterion; see above.

#### OptiNiSt (Utils)

###### data_slice

- **Description:** Slices data along specified dimensions, e.g. to trim or downsample a time series so its length matches another node's input.
- **Input:** BehaviorData, CsvData, FluoData, ImageData, RoiData, IscellData, TimeSeriesData, BarData, HeatMapData, or ScatterData
- **Output:** sliced_data, the same type as the input (CsvData is returned as BehaviorData). For FluoData and TimeSeriesData inputs a mean_timeseries plot (the mean over every axis but time) is also produced.
- **Parameters:**
  - **slice_dims** [list of str, default: empty]: One slice spec per dimension, in the input's axis order: FluoData (cells, time); BehaviorData and CsvData (time, columns); ImageData (frames, y, x). Each spec can be:
    - `:` or `all`: keep the entire dimension
    - `start:stop`: range slice (Python semantics, `stop` excluded)
    - `start:stop:step`: strided slice, e.g. `0:8152:2` keeps every 2nd sample of the first 8152
    - `squeeze`: remove a dimension of size 1
    - a non-negative integer: select a single index. The dimension is removed, except that FluoData and BehaviorData outputs stay 2-D, e.g. FluoData with `1, :` gives shape (1, time). On the time axis of BehaviorData and CsvData, an integer or `squeeze` keeps the axis one sample long, e.g. `5, :` gives shape (1, columns).
  - In the GUI, blank entries are dropped, so always write `:` for a kept dimension, e.g. `:, 0:8152:2` to keep every cell of a FluoData input and downsample time, or `0:8152:2, :` for BehaviorData.
  - Unparsable specs, `squeeze` on a dimension of size > 1, and out-of-range integer indices keep the entire dimension and log a warning; a slice that selects no elements logs a warning, and a `start:stop:step` spec that appears to be in `start:step:stop` order logs a corrected hint.
  - **cell_normalization** [str, default: `none`]: `none`, `zscore` or `dff`. `zscore` z-scores each row of a FluoData or TimeSeriesData input along its time axis before the mean is taken, so no single high-amplitude cell dominates mean_timeseries; a constant row becomes zeros. `dff` converts each row to `(F - F0) / |F0|` with the baseline F0 set by the `f0_` parameters below. `dff` is meant for raw fluorescence (Suite2p `F`, LCCD traces); CaImAn `C` and deconvolved traces have baselines at or near 0 and should not be fed to it. Samples where `|F0|` is at most 1e-6 times the row's range become NaN with a warning. mean_timeseries averages each sample over the rows that are not NaN there, so it is NaN only where every row is, and rows that end up entirely NaN are reported with a warning. Applied to sliced_data too; its `std` and `sem` are dropped because their units no longer match. Other inputs, and slices that remove the time axis, are left unchanged with a warning. Non-finite samples (NaN, inf) come out NaN.
  - **f0_method** [str, default: `percentile`]: baseline for `dff`. `percentile` takes the `f0_percentile` percentile (linear interpolation, NaN samples ignored) of each row over the whole recording or over a centred sliding window, with the edges padded by the first and last sample. `maximin` is Suite2p's `extraction.dcnv.preprocess` recipe with `f0_window_frames` as `win_baseline * fs` and `f0_sigma_frames` as `sig_baseline`: a Gaussian of s.d. `f0_sigma_frames`, then a minimum and a maximum filter of `f0_window_frames`; rows with NaN samples use a NaN-aware Gaussian and rolling windows truncated at the edges. Sosa et al. 2025 use maximin with a 20 s window and give no sigma: set `f0_window_frames` to 20 s times the frame rate, e.g. 310 at 15.5 Hz.
  - **f0_window_frames** [int, default: 0]: sliding window length in frames. `0` uses the whole recording, which `percentile` supports and `maximin` rejects. A window at least as long as the trace is clamped to the trace length with a warning (for `percentile` that is the whole recording).
  - **f0_percentile** [float, default: 8]: percentile (0 to 100) used by `percentile`.
  - **f0_sigma_frames** [float, default: 10]: Gaussian s.d. in frames used by `maximin`, must be finite and > 0, and a value above the trace length is clamped to it with a warning; 10 is Suite2p's `sig_baseline` default.
  - **mean_normalization** [str, default: `none`]: `none`, `zscore` or `minmax`. Applied only to the mean_timeseries output. A NaN sample stays NaN, a constant trace becomes zeros, and an entirely non-finite trace is left unchanged with a warning.

###### microscope_to_img

- **Description:** Data type conversion: Converts microscope data to image data.
- **Input:** MicroscopeData
- **Output:** ImageData
- **Parameters:**
- **ch:** [int, default: 0] Channel to extract from the microscope data.

(fluo-from-hdf5)=

###### fluo_from_hdf5

- **Description:** Extracts fluorescence data from HDF5 file and transposes for visualization
- **Input:** FluoData, HDF5Data
  - If using optinist nwb format:
    - processing/ophys/suite2p_roi_UNIQUE-ID/Fluorescence/data
    - processing/ophys/caiman_cnmf_UNIQUE-ID/Fluorescence/data
- **Output:** FluoData
- **Parameters:**
  - **transpose** [bool, default: True]: Whether to transpose the neural data matrix.

###### roi_from_hdf5

- **Description:** Extracts ROI data from HDF5 file and prepares it for visualization
- **Input:** ImageData, HDF5Data, IscellData
  - If using optinist nwb format:
    - cell_img:
      - processing/optinist/suite2p_roi_UNIQUE-ID_all_roi_label_img/data
      - processing/optinist/caiman_cnmf_UNIQUE-ID_all_roi_label_img/data
    - iscell:
      - processing/ophys/ImageSegmentation/suite2p_roi_UNIQUE-ID/iscell
      - processing/ophys/ImageSegmentation/caiman_cnmf_UNIQUE-ID/iscell
- **Output:** all_roi (RoiData); with an iscell input also iscell (IscellData), cell_roi and non_cell_roi (RoiData)
- `all_roi_label_img` is the all-ROI label image (one layer; each pixel holds the highest ROI covering it). cell_roi and non_cell_roi take the pixels whose ROI is in the group, so where a ROI of the other group lies on top, the pixel is empty. Files written by earlier releases hold one layer per ROI under `all_roi_img` instead, which is split by iscell as before.

###### roi_fluo_from_hdf5

- **Description:** Extracts ROI data and fluorescence data from HDF5 file and prepares it for visualization
- **Input:** ImageData, FluoData, HDF5Data, IscellData
  - If using optinist nwb format:
    - cell_img:
      - processing/optinist/suite2p_roi_UNIQUE-ID_all_roi_label_img/data
      - processing/optinist/caiman_cnmf_UNIQUE-ID_all_roi_label_img/data
    - iscell:
      - processing/ophys/ImageSegmentation/suite2p_roi_UNIQUE-ID/iscell
      - processing/ophys/ImageSegmentation/caiman_cnmf_UNIQUE-ID/iscell
    - fluorescence
      - processing/ophys/suite2p_roi_UNIQUE-ID/Fluorescence/data
      - processing/ophys/caiman_cnmf_UNIQUE-ID/Fluorescence/data
- **Output:** all_roi (RoiData), fluorescence (FluoData); with an iscell input also iscell (IscellData), cell_roi and non_cell_roi (RoiData)
- `all_roi_label_img`, and `all_roi_img` of earlier files: read as by roi_from_hdf5.
- **Parameters:**
  - **transpose** [bool, default: True]: Whether to transpose the neural data matrix.


###### vacant_roi

- **Description:** Prepared for manual ROI editing in VISUALIZE field. The average fluorescence of the added ROI area will be calculated.
- **Input:** ImageData
  - If using optinist nwb format:
    - cell_img:
      - processing/optinist/vacant_roi_UNIQUE-ID_all_roi_label_img/data
    - fluorescence
      - processing/ophys/vacant_roi_UNIQUE-ID/Fluorescence/data
- **Output** FluoData, IscellData
- **Parameters:**
  - **f0_frames** [int (0, f0_frames, Time), default: 100]: Number of frames used to calculate the baseline fluorescence.
  - **f0_percentile** [float (0 <= f0_percentile <= 100), default: 8]: Percentile used for calculating the baseline fluorescence.

#### Maintenance
- **Description:** For debugging purposes, OptiNiSt provides setup_conda_* nodes. These nodes do not contain any analysis or processing, and are used to setup conda environments for each algorithm in advance.

- **Input:** ImageData (setup_conda_mouse2p_image.tiff)
- **Output:** None

#### Custom
- **Description:** Example minimal working node provided as an example for users to [add their own algorithm](Add-your-algorithm).
- **Input:** ImageData
- **Output:** FluoData, ImageData, HeatMapData
- **Parameters:**
  - **window_size** [int, default: 10]: Example
  - **custom_params_nested**
      - **threshold** [int, default: 0.5]: Example
