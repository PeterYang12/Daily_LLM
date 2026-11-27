# Build E2B templates for the SWE datasets

Builds one E2B template per task image, so training never builds one on the fly. Each template is the task's Docker image with tmux installed and, for swe_rebench, the git repository in `/testbed` already reset to the task's start commit.

## Scope

This script is for the SWE datasets only. It has been used on two:

| Dataset | Images | What is added to the image |
|---|---|---|
| `swe_rebench_filtered_1150` | 1150 | tmux, git reset to the start commit |
| `swe_bench_verified` | 500 | tmux |

It expects the uni-agent parquet format, where each row carries the task's Docker image in `extra_info.tools_kwargs.task.sandbox.image`. Other kinds of tasks need their own build script.

## Setup

```bash
pip install e2b==2.64.0 pandas==3.1.1 pyarrow==26.0.1
cp env.example env && chmod 600 env    # then fill in the E2B address and key
```

## Check

```bash
python build_templates.py --check              # ready / failed / missing per dataset
python build_templates.py --check --missing    # also list the missing images
```

## Build

```bash
python build_templates.py                      # build what is missing, one at a time
```

To build four at a time, run these in four terminals:

```bash
python build_templates.py --shard 0/4
python build_templates.py --shard 1/4
python build_templates.py --shard 2/4
python build_templates.py --shard 3/4
```

The run can be stopped and restarted at any time: only images without a ready template are built.

## Datasets

By default the script reads `swe_rebench_filtered_1150.parquet` and `swe_bench_verified.parquet` from `/shared_nfs/yuhan/data/`. To build for other SWE datasets in the same format, pass the parquet files:

```bash
python build_templates.py /path/to/a.parquet /path/to/b.parquet
```

## Docker Hub account

Without `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` in `env`, images are pulled anonymously, which is limited to 100 pulls per hour. In that case run a single process with `--min-interval 40`.

## Other options

| Option | Effect |
|---|---|
| `--plain` | Build the plain image: no tmux, no git reset |
| `--limit N` | Build at most N images |
| `--uni-agent DIR` | Check that the tmux and git scripts in this file still match the uni-agent source |

`python build_templates.py --help` lists the rest.
