# Self-contained paper-figure artifact

This directory contains the source-only workflow needed to regenerate the ten
empirical figures in the EuroSys 2027 paper. The two architecture and timeline
diagrams are authored illustrations and are intentionally excluded.

All default paths are relative to this directory. Reproduction has two explicit
steps: prepare and verify the public trace inputs, then build the simulator,
preprocess the traces, train fresh models, run the experiments, and write the
plots to `output/figs/`:

```console
python3 download_traces.py
python3 reproduce.py
```

The script resolves paths from its own location, so it may also be invoked
from another working directory, for example `python3 artifact/reproduce.py`.
Completed downloads are reused. Use `--resume` to reuse completed computation
stages as well.

## Public inputs

`download_traces.py` is the only command that fetches inputs. `reproduce.py`
never downloads data: it requires the completed local trace tree and fails
with the missing paths if preparation has not run. The downloader uses these
publisher sources:

- Cloudflare 2025: <https://objects.research.cloudflare.com/@ikhor/cdn-traces/readme.md>
- Meta 2023: <https://s3.amazonaws.com/cache-datasets/index.html#cache_dataset_txt/2023_metaCDN/>
- Wikimedia 2019: <https://analytics.wikimedia.org/published/datasets/caching/2019/>

Running the downloader acknowledges each publisher's dataset terms, including
Cloudflare's CC BY-NC-SA 4.0 non-commercial research restriction.

The artifact-local input layout is:

| Directory | Published inputs | Simulator names |
| --- | --- | --- |
| `traces/cdn_cf25_csv/` | `106m105`, `106m106`, `243m12`, `243m13`, `411m264`, `411m325`, `472m378`, `472m379` | `cf_a` through `cf_h` |
| `traces/cdn_fb23_csv/` | `reag`, published `rnha` renamed to `rhna`, `rprn` | `fb_a` through `fb_c` |
| `traces/cdn_wm19_csv/` | assembled `t-all`, `u-all` | `wm_t`, `wm_u` |

Inputs use the `.csv.zst` suffix. Wikimedia daily gzip TSVs are projected to
`timestamp,key,size`; text days 01--20 and upload days 00--20 match the paper's
inputs. The downloader verifies a pinned SHA-256 checksum before accepting or
reusing each of the 13 final trace inputs, including the two deterministic
Wikimedia assemblies. Completed objects and partial transfers are reused.
Direct downloader use remains available:

```console
python3 download_traces.py --dry-run
python3 download_traces.py
```

`--trace-root PATH` can select an existing trace tree. Without that explicit
override, downloaded and generated trace files remain under this directory.

## Commands

```console
python3 reproduce.py --dry-run
python3 reproduce.py --smoke
python3 reproduce.py --resume
python3 reproduce.py --plots-only
```

The default is the full paper experiment. `--smoke` runs reduced prefixes and
a reduced simulation grid to check the machinery; it is not a reproduction of
the reported results. `--parallel N` controls simultaneous simulator
configurations. `--plots-only` regenerates plots from existing artifact-local
results, models, expiry events, and raw traces. `--force` deletes only generated
data for the selected mode and preserves downloaded public inputs.

## Outputs

All generated paths are local to this directory:

| Path | Contents |
| --- | --- |
| `traces/sim/` | Simulator-format traces with next-access metadata |
| `traces/expiry/` | Fresh expiry-event data used for model training and plots |
| `models/` | Fresh pickle and ONNX classifier models |
| `results/` | Baseline, oracle, ML sweep, and time-series measurements |
| `output/figs/` | Ten empirical paper figures |
| `output/manifest.json` | Byte sizes and SHA-256 hashes for those ten figures |
| `logs/` | Command output and failure details |
| `.work/` | Isolated Python environment, build tree, caches, and temporary data |

The generated figures are:

1. `ttl_cdf_by_key.png`
2. `miss_types_by_trace.png`
3. `expiry_population_ttl_dist.png`
4. `oracle_eligible.png`
5. `ml_tradeoff_combined.png`
6. `all_models_feature_importance.png`
7. `heuristic_ml_prc_combined.png`
8. `ml_tradeoff_by_eviction_avg.png`
9. `cross_dataset_auc_roc_heatmap.png`
10. `miss_over_time.png`

No diagram directory or diagram asset is created.

## Requirements and scale

Use Linux with Python 3.11--3.13, `curl`, `gzip`, GNU `cut`/`tail`, `uv`, a C++20
compiler, xmake, and the zstd command-line tools (`zstd` and `zstdcat`). The
included `.python-version` selects Python 3.12 for the cleanest locked setup.
The locked Python dependencies are installed below `.work/`; xmake obtains its
pinned C++ packages in an artifact-local cache. Both steps require network
access on a clean machine.

The public downloads total approximately 73 GiB and the formatted CSV inputs
occupy approximately 66 GiB compressed. Conversion and simulation require
substantial additional disk space, memory, and time because the full experiment
performs repeated passes over billions of requests. Start with low parallelism
on an unfamiliar machine.
