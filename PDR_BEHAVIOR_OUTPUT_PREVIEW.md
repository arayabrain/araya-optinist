# PDR: behaviour output previews and the "set orientation once" rule

Date: 2026-10-14. Scope: the blank or useless previews of behaviour-typed node
outputs on the workflow page and the Data View page, seen on the local docker
runs of feature/876-reproduce-integration (sosa run 406/efb64faf, 403/144e55fe,
404/ccdcfa9c).

## 1. What users expect

1. Every output that can be drawn is drawn: a behaviour table shows one line
   per column (position, speed, trial number) over time, in both the workflow
   output dialog and the Data View "Workflow Outputs" dialog.
2. Orientation is decided once, at the data source, and then sticks. Users
   should not re-declare "which axis is time" on every downstream node, and
   a wrong guess should be visible in the input node's preview, not at the
   end of a 25 minute run.

## 2. Facts (verified in code and in the saved run folders)

- In memory, behaviour data is time-major: BehaviorData and CsvData are
  (time, columns). This is documented for data_slice in
  docs/specifications/algorithm_nodes.md, assumed by all seven behaviour
  consumers (eta, covariate_binning, condition_split, cca, glm, lda, svm all
  default `transpose_y: False`), produced by the HDF5 1D cast
  (`_cast_behavior_inputs` in runner.py gives a (time, 1) column), and
  matches NWB TimeSeries, which is time first.
- The JSON preview saver, `TimeSeriesData.save_json`, writes one record per
  row of axis 0 and treats axis 1 as time. BehaviorData inherits it unchanged.
  A (19818, 2) position and speed table is therefore saved as 19818 records
  of 2 points. Recorded shapes in experiment.yaml of run 406:
  concatenated_data 19818 x 2 and 19818 x 3, sliced_data 19810 x 3,
  behaviors_data 17946 x 3 and smaller. Fluo-oriented outputs (349 x time,
  349 x 45 bins) are right.
- The preview endpoint `inittimedata` returns every record id with one point
  each, and TimeSeriesPlot builds one Plotly trace per record. Twenty
  thousand traces is a blank, frozen plot. The Data View dialog renders the
  same component for every item, so the whole dialog stalls.
- The only input-side orientation control is the CSV node's Transpose toggle
  (CsvParamSettingDialog, applied in `FileWriter.csv`). It shows the table
  in the chosen orientation before the run. HDF5 and MAT nodes have no such
  toggle; 1D datasets are cast to a time column automatically.
- `TimeSeriesData.__init__` derives the default index and cell numbers from
  the (rows, time) layout, so a BehaviorData built without an explicit index
  gets an index of length columns and cell numbers of length time. PR 948's
  data_concat column stack then "repairs" the index to `arange(columns)`
  (565a993b6, pinned by `test_behaviour_column_concat_rebuilds_the_index`),
  which is the wrong axis for a time-major table.
- Introducing commits: the saver is bd469fd42 (2023, fine while only fluo
  outputs existed); a30eb8faf and e82c1a059 (PR 928) fixed the time-major
  contract by type; 3f86c4cc8 (PR 932) added condition_split, the first node
  that routinely emits BehaviorData; 24cdc8f5c (PR 948) made data_concat
  produce a (time, columns) table from 1D inputs.

## 3. Options

### A. Workflow-side workaround: transpose at the CSV node, `transpose_y` on every consumer, data_concat `axis: 0`

- Pro: no code.
- Con: not available for HDF5 1D inputs (the sosa chain), so it does not
  cover the failing runs at all. Every consumer must be re-toggled, which is
  the opposite of "set once". It contradicts the documented contract, and a
  forgotten toggle fails at the assertion inside the node after the run.
- Verdict: reject.

### B. Transpose inside `BehaviorData.save_json` only

- Pro: one override, every behaviour-emitting node fixed at once, HDF5 and
  CSV paths both covered.
- Con: incomplete on its own. The inherited constructor still builds the
  default index and cell numbers from the wrong axis, data_concat's index
  rebuild then produces an index of length columns, and the transposed save
  raises a pandas length mismatch. It also leaves 1D behaviour (a column
  sliced out with an integer spec) as (1, time).
- Verdict: right place, but it needs the constructor too. See B'.

### B'. BehaviorData owns its layout (recommended)

BehaviorData knows it is time-major and takes responsibility for everything
that depends on that: default index (length time, from axis 0), default cell
numbers (one per column), 1D input becomes a (time, 1) column, `save_json`
writes one record per column with the time index as x, `output_path` reports
`max_index` as the column count and `data_shape` as (columns, time). The
data_concat index rebuild is restricted to the (rows, time) types so it stops
rewriting behaviour indexes.

- Pro: the type already is the contract (PR 928), so this closes the one gap
  where the contract was not honoured. Smallest diff that makes the Data View
  and workflow previews correct for every current and future behaviour
  output, including workflows already saved with default parameters.
  Consumers, loaders, pickles from old runs, saved workflow YAML and the
  NWB writer are untouched. "Set once" is already true for the default path
  (HDF5 cast, CSV with rows as time); this makes the preview agree with it.
- Con: still relies on the type. A user who ticks CSV Transpose on a file
  that already has rows as time gets a (columns, time) array in memory, and
  every downstream node and the preview will treat columns as time. That is
  the same failure the node assertions produce today, and the CSV node's
  preview table shows it before the run. Old run folders keep their wrong
  JSON until the record is re-run or reproduced; the saver only runs at run
  time.
- Verdict: do this.

### C. Carry an explicit orientation attribute on every dataclass

A `time_axis` attribute set by the loaders (CSV toggle, HDF5 cast),
propagated by data_slice, data_concat and condition_split, read by all seven
consumers in place of `transpose_y`, and by the saver.

- Pro: the strongest "set once" story. `transpose_y` would become an
  override with default "infer", and odd files would be handled at the source.
- Con: wide blast radius for a problem the type already answers: two loaders,
  three data utils, seven consumers, the saver, pickled outputs from old runs
  without the attribute, saved workflows carrying explicit `transpose_y`
  values, and docs for every node. Once B' is in, the attribute would equal
  "is this a BehaviorData" on every path that exists, so it is an abstraction
  with one value. If per-node transpose toggles prove to confuse users later,
  this is the follow-up, and B' is a prerequisite for it, not wasted work.
- Verdict: not now.

### D. Heuristic in the saver: treat the longer axis as time

- Pro: a few lines.
- Con: silently wrong for short recordings with many channels, impossible
  to reason about from the UI, and it would mask orientation mistakes that
  the node assertions currently surface.
- Verdict: reject.

### E. Preview behaviour outputs as a table (csv type) instead of a time series

- Pro: tables are an honest view of mixed columns such as trial number.
- Con: users asked for line plots of position and speed; the csv endpoint
  reads only from the input directory; it would need a new output type and
  plot plumbing, and it does not fix the orientation that eta and the others
  consume. Could be a later option on the Visualize box, not the fix.
- Verdict: reject as the fix.

### F. Guard the time-series preview against thousands of records

Cap the traces the init endpoint returns, or draw the first N and a count.

- Pro: protects any output type from freezing the dialog.
- Con: it hides the symptom and does not make behaviour outputs correct.
  Outputs with hundreds of ROIs already render in this regime, and once B'
  is in, no current node produces thousands of records.
- Verdict: not now. Revisit if a real output with thousands of records
  appears.

## 4. Why B' beats the others

B' is the only option that fixes the failing runs (HDF5 inputs, so A is out),
does it in one place (unlike A and C), keeps the orientation decision at the
source where the user can see it (unlike D, which guesses), and keeps the
preview a plot (unlike E). C would be the next step if the per-node
`transpose_y` toggles turn out to be a real source of confusion, and B' is
the piece C would need anyway.

## 5. Plan

Branch `fix/behavior-output-orientation` from develop-main (the saver bug
predates the integration branch). The data_concat guard belongs to PR 948's
code, so it goes as one commit on `feature/binning-order-from-and-norm`.
Both then merge into feature/876-reproduce-integration for the re-run.

1. `studio/app/optinist/dataclass/behavior.py`
   - Constructor: accept 1D as a (time, 1) column; default index
     `arange(shape[0])`; cell numbers `range(shape[1])`.
   - `save_json`: transpose data and std to (columns, time) and call the
     parent, or build the records directly with the time index as x.
   - `output_path`: `max_index` = columns, `data_shape` = (columns, time).
   - Record ids stay "0".."n-1" (CsvData does not keep header names).
2. `studio/app/optinist/wrappers/data_utils/data_concat.py` (on PR 948):
   restrict the index rebuild to TimeSeriesData that is not BehaviorData, and
   change `test_behaviour_column_concat_rebuilds_the_index` to expect an index
   of length time.
3. Tests, in `studio/tests/app/optinist/wrappers/` next to the existing
   BehaviorData tests:
   - (time, columns) with an explicit time index saves `columns` records,
     each of length `time`, with the index as x; std follows.
   - 1D input saves one record of length `time`.
   - condition_split and data_slice outputs on a (50, 3) table save 3
     records (extend the existing contract tests, which already build these
     inputs).
   - `output_path` reports `data_shape` (columns, time).
4. Docs: one sentence in docs/gui_guide/workflow.md under the CSV node's
   transpose bullet: rows must be time; use Transpose when the file has one
   row per channel. Nothing else changes for users.
5. Verification on the local stack: re-run the sosa 16-node YAML
   (`SfN2026-video-assets/workflows/production/sosa2025_fig4d_before_after_switch.yaml`)
   through the existing Playwright run spec, check experiment.yaml shapes read
   (2, 19818), (3, 19810), (3, 17946) and so on, open the workflow output
   dialog and the Data View "Workflow Outputs" dialog, and screenshot the
   behaviour items. Run the wrappers test directory in the test image.

## 5b. HDF5 and MAT inputs: Transpose toggle and Behaviour role (added 2026-10-14, revised 2026-10-15)

### Why the toggle alone is not enough

`FileWriter.hdf5` and `FileWriter.mat` both end in `get_info_from_array_data`,
which types a dataset by rank alone (`dataclass_for_rank`: 1D IscellData, 2D
FluoData, 3D ImageData) and leaves 2D as stored. The CSV family carries a
role in the node kind (Fluo node and Behaviour node are both csv underneath,
and `FileWriter.csv` files them under NWB TIMESERIES or BEHAVIOR by node
type). HDF5 and MAT have no role at all, so a 2D behaviour table from either
format is a FluoData object for its whole life:

- `data_slice` and `data_concat` have untyped `BaseData` ports. They take
  the time axis from the type (`_time_axis`, PR 928), so a (time, columns)
  table typed FluoData is sliced and averaged as (rows, time). The sosa
  chain goes HDF5 to data_concat to data_slice to condition_split, so this
  is the main path, not an edge case.
- After B', the saver also goes by type, so the preview stays one record
  per time sample for that table.
- The runner's `_cast_behavior_inputs` (PR 929) casts 1D arrays at
  BehaviorData-annotated ports, which fixes typed consumers only, and only
  for 1D.

A Transpose toggle fixes stored orientation, not the type. Both are
properties of the source, and both have to be declared there.

### Options for the type gap

- **Extend the runner cast to 2D at BehaviorData ports.** Rejected: every
  BehaviorData-port consumer (eta, covariate_binning, condition_split, cca,
  glm, lda, svm, dpca) reads `.data` directly and never inspects the type,
  so a 2D cast changes nothing there, and it never reaches the BaseData
  ports where the problem is.
- **Infer the type from the downstream edge at rule-build time.** Rejected:
  one input can feed several consumers, black connectors feed any port, and
  input rules are built without edges.
- **Make data_slice and data_concat orientation-agnostic.** Rejected: PR 928
  made the type the contract on purpose, and a (time, columns) table has no
  self-evident time axis.
- **Declare the role on the input node (recommended).** The HDF5 and MAT
  nodes get a "Behaviour data" choice next to Transpose. The loader then
  builds BehaviorData from the start, so every downstream path, typed or
  untyped, and the saver see the right type without any per-node setting.
  This is the same model the CSV family already uses, and the same
  set-once principle as the Transpose toggle. It also lets the flow chart
  colour the node's handle as BehaviorData and the run-time validator check
  the wiring, instead of relying on a black connector and a cast the user
  cannot see.

### Design

Two controls in the shared structure dialog (`StructureItemSelectDialog`,
used by both nodes through `BaseStructuredFileNode`), stored in the node's
`param` like the CSV node:

- `transpose` (bool, default false): flip a 2D dataset as it is loaded.
  Ignored for 3D and for NWB fluorescence that the `rois` sibling already
  oriented (the rule `fluo_from_hdf5.yaml` already states). When applied,
  the loader sets `nwb_oriented = True`, the flag `fluo_from_hdf5` and
  `roi_fluo_from_hdf5` already honour, so there is no double flip.
- `dataType` ("auto" | "behavior", default "auto"): "auto" is today's rank
  rule. "behavior" makes a 1D dataset a (time, 1) BehaviorData and a 2D
  dataset a BehaviorData as loaded (after `transpose`), filed under NWB
  BEHAVIOR like the CSV behaviour node. A 3D dataset with "behavior" is
  refused by the run-time validator before the run starts.

The order is transpose first, then role, so "stored channel-major, used as
behaviour" is `transpose: true, dataType: behavior`, and the dataset shape
printed in the dialog lets the user check the result before running. The
runner's 1D cast stays for saved workflows that predate the role.

### Steps (in addition to section 5)

6. Backend, `studio/app/common/core/rules/file_writer.py`: in
   `get_info_from_array_data`, apply `rule_config.params.get("transpose")`
   to 2D data not already NWB-oriented and set `nwb_oriented`; when
   `params.get("dataType") == "behavior"` and `ndim <= 2`, build BehaviorData
   (1D as a column) and file it under NWB BEHAVIOR. Input params already
   reach the rule through `set_params(self._node.data.param)` for every
   input node, and `NodeData.param` is a plain dict, so no schema change.
7. Backend, `studio/app/common/core/workflow/workflow_input_validator.py`:
   `produced` becomes BehaviorData when the role is set; a 3D dataset with
   the behaviour role raises `WorkflowValidationError` with the shape.
8. Frontend state: add `transpose` and `dataType` to the HDF5 and MATLAB
   `defaultParam` in `fileTypes.config.ts` and to their param types in
   `InputNodeType.ts`. In `InputNodeSlice.ts`, the import/reproduce matcher
   merges saved params over defaults for CSV only (`mergeKnownParams`);
   extend that branch to HDF5 and MATLAB so both settings survive import,
   reproduce and Data View reproduce. The run payload
   (`InputNodePostData.param`) already carries params for every input node.
9. Frontend UI: a Transpose checkbox and a "Behaviour data" choice in
   `StructureItemSelectDialog`, dispatched with the same action pattern as
   `CsvParamSettingDialog`. `handleTypeForRank` takes the role and returns
   "BehaviorData" for rank 1 or 2 when it is set, so the handle colour and
   the `title` tooltip show the declared type.
10. Tests: `test_file_writer_hdf5.py` gains 2D transpose, 3D ignored,
    NWB-oriented ignored, 1D behaviour and 2D behaviour cases, with a MAT
    twin for the 2D cases; the validator test gains the 3D-behaviour
    refusal; `test_data_slice_contract.py` gains a BehaviorData-from-loader
    case to pin the time axis; a frontend test that an imported YAML with
    `param.transpose` and `param.dataType` on an HDF5 node keeps both.
11. Docs: the HDF5 and Matlab bullets in docs/gui_guide/workflow.md get the
    two settings with one sentence each: Transpose flips a 2D dataset, and
    is ignored for 3D and for NWB fluorescence oriented on load; Behaviour
    data means rows are time, as for the CSV behaviour node.

### Risk added

| Area | Risk | Notes |
|---|---|---|
| HDF5 and MAT loader | Low | Both settings default to today's behaviour, so existing workflows load byte-identically. |
| fluo_from_hdf5 interaction | Low | The `nwb_oriented` flag already exists for exactly this; `test_fluo_from_hdf5_does_not_undo_the_orientation` is the template. |
| Reproduce | Medium | Forgetting the `mergeKnownParams` extension drops both settings silently on reproduce; the frontend test above guards it. |
| Validator | Low | Only adds a refusal for a combination that cannot work. |

## 6. Not done, and why

- No change to `transpose_y` or `transpose_x` on the seven consumers; the
  defaults already match the contract. Removing the toggles is option C.
- No migration of JSON in existing run folders; re-run or reproduce.
- No cap on trace count in the preview (option F).
- No header names on behaviour traces; CsvData drops them at load time.
  Worth a separate small change if users ask for named lines.

## 7. Risks

| Area | Risk | Notes |
|---|---|---|
| BehaviorData constructor | Medium | Default index and cell numbers change axis; every behaviour consumer indexes `.data` directly, not `.index`, so behaviour is unchanged, but the data_slice index-length search and the data_concat rebuild both read `.index` and are covered by the tests above. |
| JSON preview | Low | Only behaviour outputs change shape; fluo and timeseries outputs are untouched. |
| Old pickles | Low | Reproduce re-runs the node, so the new saver applies; no attribute is read from old pickles. |

## 8. Orientation regression matrix (added 2026-10-15)

Purpose: one parametrised test layer that feeds the same canonical data into
every input kind, in every orientation and setting the UI allows, through
every node that depends on an axis order, and checks numbers, types and the
preview contract. It is the regression guard for any future change to
dimensions, transposes, casts or savers.

### Principle: outputs follow the type contract

- FluoData and TimeSeriesData are (rows, time). BehaviorData and CsvData are
  (time, columns). IscellData is 1D. ImageData is (frames, y, x).
  ScatterData is stored (2, points). BarData and HeatMapData index rows.
- `transpose`, `transpose_x` and `transpose_y` describe how to read the
  input. They never change what an output means, so a node's outputs are
  always in the contract orientation for their type.
- The preview contract for any time-series-like output: the saved record
  count equals the non-time axis, every record's x is the time index, and
  `output_path.max_index` and `data_shape` describe the same thing.

### Oracle

Each consumer row computes its expected numbers once with numpy from the
canonical array, then runs the node for each (orientation, setting) pair the
row lists and asserts `np.allclose` against that one reference, plus output
types and the preview contract. A helper yields the pairs from a canonical
table, so adding an orientation later is one entry, not a new test.

### Two code decisions the matrix enforces

- `condition_split` returns `behaviors_data` time-major regardless of
  `transpose_y`. Today it transposes the kept rows back to the input
  orientation, which with `transpose_y: True` yields a (columns, time)
  BehaviorData that violates the contract and previews wrongly after B'.
  `neural_data` already comes back (cells, time) with the default
  `transpose_x: True`.
- `data_transpose` of a BehaviorData or CsvData returns TimeSeriesData,
  because the result is no longer time-major. Other types keep their type,
  and the test pins the resulting orientation.

### Rows

Lane: P = pytest in the backend test image, J = Jest, E = end-to-end on the
local stack (verification after the agent, not in its automated scope).
Optional-library rows use `pytest.importorskip` and record the skip.

| ID | Source and setting | Path | Assert | Lane |
|---|---|---|---|---|
| L1 | CSV (time, cols), transpose off | FileWriter.csv | CsvData (time, cols); NWB TIMESERIES for csv, BEHAVIOR for behaviour node type | P |
| L2 | CSV (cols, time), transpose on | FileWriter.csv | CsvData (time, cols), equal to L1 | P |
| L3 | CSV one column; CSV one row with transpose on | FileWriter.csv | (time, 1) both ways | P |
| L4 | CSV with setHeader | FileWriter.csv | header row dropped, shape (time, cols) | P |
| L5 | HDF5 1D, auto | FileWriter.hdf5 then runner cast at a BehaviorData port | IscellData at load; (time, 1) BehaviorData at the port | P |
| L6 | HDF5 1D, dataType behavior | FileWriter.hdf5 | BehaviorData (time, 1); NWB BEHAVIOR; nwb_oriented unset | P |
| L7 | HDF5 2D stored (time, cols), auto | FileWriter.hdf5 | FluoData as stored; nwb_oriented False | P |
| L8 | HDF5 2D stored (cols, time), transpose on | FileWriter.hdf5 | (time, cols); nwb_oriented True | P |
| L9 | HDF5 2D (time, cols), dataType behavior | FileWriter.hdf5 | BehaviorData (time, cols); NWB BEHAVIOR | P |
| L10 | HDF5 2D (cols, time), transpose on + behavior | FileWriter.hdf5 | BehaviorData (time, cols), equal to L9 | P |
| L11 | NWB fluorescence (time, roi) with rois sibling, transpose on | FileWriter.hdf5 | (roi, time) from the sibling rule; toggle ignored and logged | P |
| L12 | HDF5 3D, transpose on; 3D + behavior | FileWriter.hdf5; validator | ImageData untouched; validator refuses 3D + behavior with the shape | P |
| L13 | MAT twins of L5 to L10 (column vector squeezes to 1D) | FileWriter.mat | same as the HDF5 rows | P |
| L14 | behavior role wired to an ImageData port | validator | refused before the run | P |
| C1 | fluo_from_hdf5: plain (time, roi) transpose True; NWB-oriented input; L8 input | wrapper | (roi, time) in all three; no double flip | P |
| C2 | roi_fluo_from_hdf5 with the same three inputs | wrapper | same as C1 | P |
| C3 | data_slice on FluoData (cells, time): time slice, integer cell, cell_normalization, mean_normalization | wrapper | numbers equal numpy; mean_timeseries length; index follows time | P |
| C4 | data_slice on BehaviorData (time, cols): time slice, column slice, integer column collapse | wrapper | sliced on axis 0; index follows time; no mean; collapse gives (time, 1) | P |
| C5 | data_slice on CsvData, TimeSeriesData, HeatMapData, BarData, ScatterData | wrapper | type kept; orientation per contract | P |
| C6 | data_concat: Behavior+Behavior axis 1 and axis 0; Fluo+Fluo axis 0 and 1; 1D+1D stack; Iscell; Bar; HeatMap; Scatter; std concatenate and compute | wrapper | shapes, index length and continuity, output type, std axis | P |
| C7 | data_transpose on each type | wrapper | BehaviorData and CsvData become TimeSeriesData (cols, time); others keep type; preview records follow the output type | P |
| C8 | condition_split: fluo (cells, time) + behaviour (time, cols) defaults; same data with transpose_y True on a (cols, time) behaviour; modulo; margin | wrapper | kept rows equal numpy; behaviors_data time-major in both; neural_data (cells, kept) | P |
| C9 | covariate_binning: defaults; transpose_y True on flipped behaviour; iscell subset; order_from | wrapper | (cells, bins) equal numpy; heatmap rows match mean rows | P |
| C10 | eta: four transpose_x and transpose_y combinations on flipped inputs | wrapper | mean, mean_trace, heatmap equal the single reference | P |
| C11 | cca, glm, lda, svm, dpca_fit, granger, pca, tsne: defaults on contract inputs and the flipped inputs with toggles | wrapper | no shape assertion; results equal across orientations where deterministic | P (importorskip) |
| C12 | correlation, cross_correlation on (cells, time) | wrapper | transpose False default is right for FluoData | P |
| S1 | TimeSeriesData and FluoData (rows, time) | save_json, output_path | records = rows; x = index; data_shape and max_index agree | P |
| S2 | BehaviorData (time, cols) with std | save_json, output_path | records = cols; x = time index; std transposed; data_shape (cols, time); max_index cols | P |
| S3 | BehaviorData 1D | save_json | one record of length time | P |
| S4 | CsvData | save_json | current contract pinned (rows as records) | P |
| S5 | HeatMapData, BarData, ScatterData, IscellData, ImageData, RoiData | save_json, output_path | record count and shape per type pinned | P |
| S6 | saved BehaviorData dir through `/inittimedata` and `/timedata` | router | keys = columns; xrange = time; one full record | P |
| S7 | sosa shapes from a run: concat (2, 19818), slice (3, 19810), split (3, 17946) | experiment.yaml | recorded data_shape is (columns, time) | E |
| F1 | handleTypeForRank with dataType behavior on rank 1 and 2; auto unchanged | FlowChartUtils | "BehaviorData"; existing cases unchanged | J |
| F2 | import and reproduce of a YAML with transpose and dataType on HDF5 and MAT nodes | InputNodeSlice | both kept; CSV merge unchanged; unknown keys dropped | J |
| F3 | run payload for HDF5 and MAT nodes | Run payload builder | param carries both settings | J |
| E1 | sosa 16-node YAML on the local stack | workflow output dialog, Data View outputs dialog | behaviour items draw 2 or 3 lines; screenshots | E |
| E2 | funamizu CSV workflow (transpose on, transpose_y on) | same dialogs | unchanged numbers and plots | E |
| E3 | patel CNMF-E run | same dialogs | unchanged (no behaviour outputs) | E |

### Where the tests live

- Backend: one new module per group under `studio/tests/app/optinist/wrappers/`
  (`test_orientation_matrix.py` for C rows, `test_dataclass_preview_contract.py`
  for S rows), `studio/tests/app/common/core/rules/test_file_writer_hdf5.py`
  and a new `test_file_writer_mat.py` for L rows, the existing
  `test_workflow_input_validator.py` for L12 and L14, and the existing
  router tests for S6. Existing tests that pin the old behaviour
  (`test_behaviour_column_concat_rebuilds_the_index`, the condition_split
  orientation cases) are updated, not duplicated.
- Frontend: `FlowChartUtils.test.ts` and a new `InputNodeSlice.test.ts`
  next to the slice.
