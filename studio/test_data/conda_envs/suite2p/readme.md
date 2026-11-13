- `suite2p.yaml` is a fixture for `studio/tests/app/common/core/snakemake/test_smk_utils.py`.
  It is a conda env file for snakemake to hash; no environment is built from it.
- It previously sat beside two marker directories, one per platform, because snakemake
  names an env directory after a hash of its own **absolute path** -- so a committed
  marker is only findable from the checkout that produced it. The tests now build their
  own marker under `tmp_path` instead, and those directories were removed.
