---
applyTo: "studio/app/optinist/wrappers/**,studio/app/optinist/dataclass/**,studio/app/common/dataclass/**,studio/app/common/core/rules/**,studio/app/common/core/workflow/**"
---

# Algorithm wrappers, dataclasses and saved workflows

- A new wrapper function must be in its package's `*_wrapper_dict` with the right `conda_name`, merged in `studio/app/optinist/wrappers/__init__.py`, with a default params YAML under `<package>/params/`. An unregistered function silently does not appear in the UI.
- Wrapper, rule and dataclass code runs inside conda envs, not the API process, and every env imports `studio/app/common/dataclass/` and `studio/app/common/core/rules/`. It must run on the lowest Python pinned in any `studio/app/optinist/wrappers/*/conda/*.yaml`: check the pins, then flag newer syntax (`match`, runtime `X | Y` unions, parenthesised context managers). Never import `fastapi`, `snakemake_executor` or `snakemake.api` from this code.
- Conda env YAMLs pin versions. A version bump in one env must not break code shared with another env.
- Use the existing dataclasses (`ImageData`, `TimeSeriesData`, `FluoData`, `RoiData`, `IscellData`) for inputs and outputs, not raw arrays or dicts.
- Check the axis order a change assumes: (roi, time) vs (time, roi) between in-memory data and NWB has caused real bugs. Ask for a test that pins the shape.
- Saved workflows: renaming or removing a key in a default params YAML, or changing it between scalar and group, breaks users' saved workflows in one of two ways. `check_types` drops a retired or reshaped key with only a log warning, so the saved value is lost; `has_outdated_shape` makes the whole saved workflow fail with a 422 when the tree looks like an older layout. Ask how existing saved values migrate.
- A change to the stored workflow or snakemake YAML format must still load existing files.
- Memory: peak matters, not total. Flag holding input and output movies at once, a `.copy()` or `astype()` of an already-fresh array, and large intermediates kept past their last use. A new pool, `n_processes` or worker count must be sized against memory per worker, not CPU count.
- An OOM kill is a SIGKILL with no traceback. Flag code that swallows a subprocess exit code or signal, or reports a killed run without a log line.
