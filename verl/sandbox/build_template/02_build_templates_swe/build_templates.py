"""Build E2B templates for every image in the given dataset files, so training never builds one on the fly.

What a template contains (unless --plain is given):
  1. the task's Docker image;
  2. tmux installed (INSTALL_TMUX below);
  3. for swe_rebench tasks, the git repository in /testbed reset to the task's base_commit, with remotes, refs and
     history removed (GIT_CLEAN_HISTORY below, the same script the task runs when it starts, which then has nothing
     left to do);
  4. the snapshot is taken only once `tmux -V` works.
Measured on 2026-11-26 with 128 sandboxes starting at once: 85-95 s per sandbox with plain templates, about 40 s
with these. All 1650 templates (swe_rebench_filtered_1150 + swe_bench_verified) were built this way that day.

Template names follow uni_agent.sandbox.e2b._ensure_template (2 vCPU / 4096 MB), so uni-agent finds them by itself.
The run is resumable: only images without a `ready` template are built. A failed build leaves a template in
buildStatus=error that still owns the name (uni-agent then believes it exists), so it is deleted before a rebuild
and right after a failure.

Setup:   pip install e2b==2.64.0 pandas==3.1.1 pyarrow==26.0.1      (Python 3.12)
         cp env.example env && chmod 600 env, then fill it in. `env` is git-ignored.
Usage:   python build_templates.py --check                 count ready / failed / missing per dataset, build nothing
         python build_templates.py --check --missing       ... and list the images that are missing or failed
         python build_templates.py                         build what is missing, one at a time
         python build_templates.py --shard 0/4             run this with 0/4 .. 3/4 in four terminals (or under
                                                           nohup / systemd-run) to build four at a time

Docker Hub: the E2B server pulls the images itself and ignores any `docker login` done on that machine; an account
is only used if it travels with the build request, which is what DOCKERHUB_USERNAME / DOCKERHUB_TOKEN in `env` do.
Without them pulls are anonymous (100 per hour): run a single process with --min-interval 40, about 18 h for 1650
images. With an account, four processes built all 1650 in about 3 h. The E2B API here is plain HTTP, so use a
revocable access token, never the account password.
"""
import argparse, concurrent.futures, hashlib, json, os, pathlib, re, shlex, sys, time, urllib.request

import e2b
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
CPU, MEM = 2, 4096  # template size; part of the template name, must match what uni-agent asks for
DATA = "/shared_nfs/yuhan/data"
DEFAULT_PARQUETS = [f"{DATA}/swe_rebench_filtered_1150.parquet", f"{DATA}/swe_bench_verified.parquet"]

# Both scripts are copied verbatim from uni-agent (revision 5536c72, 2026-11-25): _INSTALL_TMUX in
# uni_agent/tools/shell.py and _GIT_CLEAN_HISTORY in uni_agent/tasks/swe_rebench/task.py. The pre-cleaned repository
# only saves time if it is cleaned exactly the way the task cleans it, so after a uni-agent upgrade run
# `--check --uni-agent <checkout>`: it fails if these copies have drifted.
INSTALL_TMUX = r"""
if command -v apt-get >/dev/null 2>&1; then
  # A start attempt that timed out leaves its apt-get running and holding the apt lock; wait for it, then reuse
  # its result instead of failing on the lock.
  for _ in $(seq 1 120); do pgrep -x apt-get >/dev/null 2>&1 || pgrep -x dpkg >/dev/null 2>&1 || break; sleep 2; done
  command -v tmux >/dev/null 2>&1 && exit 0
  DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tmux
elif command -v dnf >/dev/null 2>&1; then dnf install -y tmux
elif command -v yum >/dev/null 2>&1; then yum install -y tmux
elif command -v apk >/dev/null 2>&1; then apk add --no-cache tmux
elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm tmux
elif command -v zypper >/dev/null 2>&1; then zypper install -y -n tmux
else
  echo "no supported package manager to install tmux" >&2
  exit 127
fi
""".strip()

GIT_CLEAN_HISTORY = r"""
set -euo pipefail

test -n "${BASE_COMMIT:-}"
base_commit="$(git rev-parse --verify "${BASE_COMMIT}^{commit}")"

git reset --hard "$base_commit"
git clean -ffd
git checkout --detach "$base_commit"

for remote in $(git remote); do
    git remote remove "$remote"
done
git for-each-ref --format='delete %(refname)' | git update-ref --stdin

git reflog expire --expire=now --expire-unreachable=now --all
git gc --prune=now
git prune --expire=now

test "$(git rev-parse HEAD)" = "$base_commit"
test -z "$(git remote)"
test -z "$(git for-each-ref --format='%(refname)')"
test "$(git rev-list --count --all --reflog --not HEAD)" = "0"
test -z "$(git status --porcelain --untracked-files=all)"
""".strip()

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("parquets", nargs="*", default=DEFAULT_PARQUETS, help="dataset files; default: swe_rebench_filtered_1150 and swe_bench_verified")
ap.add_argument("--check", action="store_true", help="only count ready / failed / missing templates per dataset, build nothing")
ap.add_argument("--missing", action="store_true", help="with --check: also list the images that are missing or failed")
ap.add_argument("--env-file", default=str(HERE / "env"), help="KEY=VALUE file with E2B_API_URL, E2B_API_KEY and optionally DOCKERHUB_USERNAME / DOCKERHUB_TOKEN (default: env next to this script)")
ap.add_argument("--plain", action="store_true", help="plain image only: no tmux, no git clean-up (how uni-agent builds on the fly)")
ap.add_argument("--shard", default="0/1", help="i/n: build only every n-th image starting at i, to run n builders in parallel")
ap.add_argument("--limit", type=int, default=0, help="build at most this many images (for a quick test)")
ap.add_argument("--min-interval", type=float, default=5, help="seconds between two build requests of this process; use 40 without a Docker Hub login")
ap.add_argument("--build-timeout", type=float, default=900, help="give up on one build after this many seconds")
ap.add_argument("--backoff", default="300,600,1200,1800", help="seconds to wait before each retry of a failed image")
ap.add_argument("--stop-streak", type=int, default=5, help="stop the run after this many images in a row gave up")
ap.add_argument("--name-suffix", default="", help="append to every template name to try a build without touching the real templates; write it as --name-suffix=-selftest")
ap.add_argument("--uni-agent", metavar="DIR", help="uni-agent checkout: fail if INSTALL_TMUX / GIT_CLEAN_HISTORY below differ from its source")
args = ap.parse_args()


def log(*a):
    print(time.strftime("%m-%d %H:%M:%S", time.gmtime()), *a, flush=True)


# Settings come from the env file as a whole. Variables already exported in the shell are left alone; if the shell
# exports E2B_API_URL, no E2B_* value is taken from the file, so a key never goes to a server it was not meant for.
if os.path.exists(args.env_file):
    shell_has_server = bool(os.environ.get("E2B_API_URL"))
    for line in open(args.env_file).read().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = (x.strip() for x in line.split("=", 1))
            if not (shell_has_server and key.startswith("E2B_")):
                os.environ.setdefault(key, value.strip('"').strip("'"))
_missing = [k for k in ("E2B_API_URL", "E2B_API_KEY") if not os.environ.get(k)]
if _missing:
    raise SystemExit(f"missing {', '.join(_missing)}: copy env.example to env and fill it in")

if args.uni_agent:
    for local, path, const in [(INSTALL_TMUX, "uni_agent/tools/shell.py", "_INSTALL_TMUX"), (GIT_CLEAN_HISTORY, "uni_agent/tasks/swe_rebench/task.py", "_GIT_CLEAN_HISTORY")]:
        found = re.search(const + r' = r?"""(.*?)"""', open(os.path.join(args.uni_agent, path)).read(), re.S)
        if not found or found.group(1).strip() != local:
            raise SystemExit(f"out of date: {const} in {path} differs from the copy in this script. Update the copy, then rebuild the templates.")
    log("INSTALL_TMUX and GIT_CLEAN_HISTORY match the uni-agent source")


def name_of(image):
    """Template name for an image. Same rule as uni_agent.sandbox.e2b._ensure_template, so uni-agent finds it."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", image.lower()).strip("-_")[:64] or "image"
    return f"uni-agent-{slug}-{CPU}c-{MEM}mb-{hashlib.sha256(image.encode()).hexdigest()[:12]}"


def api(method, path):
    req = urllib.request.Request(os.environ["E2B_API_URL"].rstrip("/") + path, method=method, headers={"X-API-KEY": os.environ["E2B_API_KEY"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def template_state():
    """name -> (templateID, buildStatus). Names may carry a "team/" prefix on some servers; it is dropped."""
    for _ in range(5):
        try:
            return {n.split("/")[-1]: (t["templateID"], t.get("buildStatus")) for t in json.loads(api("GET", "/templates")) for n in t.get("names") or []}
        except Exception as exc:  # noqa: BLE001
            log(f"list templates failed ({type(exc).__name__}), retrying"); time.sleep(20)
    raise SystemExit("cannot list templates")


def drop_errored(name, state=None):
    tid, status = (state or template_state()).get(name, (None, None))
    if tid and status != "ready" and name.startswith("uni-agent-"):
        try:
            api("DELETE", f"/templates/{tid}"); return True
        except Exception as exc:  # noqa: BLE001
            log(f"could not delete errored template {name}: {type(exc).__name__}")
    return False


BASE = {}  # image -> base_commit, swe_rebench tasks only


def tname(image):
    return name_of(image) + args.name_suffix


def template_for(image):
    # The E2B server pulls the image itself and ignores any `docker login` done on that machine: a Docker Hub
    # account is only used if it travels with the build request. E2B then wants the registry spelled out.
    user, token = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
    head = image.split("/")[0]
    on_hub = "/" not in image or not ("." in head or ":" in head or head == "localhost")
    if user and token and on_hub:
        tpl = e2b.Template().from_image("docker.io/" + image, username=user, password=token)
    else:
        tpl = e2b.Template().from_image(image)
    if args.plain:
        return tpl
    tpl = tpl.run_cmd("bash -c " + shlex.quote(INSTALL_TMUX), user="root")
    ready = "tmux -V"
    if image in BASE:
        tpl = tpl.run_cmd(f"cd /testbed && export BASE_COMMIT={shlex.quote(BASE[image])} && bash -c " + shlex.quote(GIT_CLEAN_HISTORY), user="root")
        ready += " && test -d /testbed/.git"
    return tpl.set_ready_cmd(ready)


if not re.fullmatch(r"\d+/[1-9]\d*", args.shard) or int(args.shard.split("/")[0]) >= int(args.shard.split("/")[1]):
    raise SystemExit("--shard must be i/n with 0 <= i < n, e.g. 0/4")
_i, _n = (int(x) for x in args.shard.split("/"))
state = template_state()
images = []
for p in args.parquets:
    tasks = [x["tools_kwargs"]["task"] for x in pd.read_parquet(p, columns=["extra_info"])["extra_info"]]
    BASE.update({x["sandbox"]["image"].strip(): x["metadata"]["base_commit"].strip() for x in tasks if x["name"] == "swe_rebench"})
    im = sorted({x["sandbox"]["image"].strip() for x in tasks})
    ok_ = [i for i in im if state.get(tname(i), (None, None))[1] == "ready"]
    bad = [i for i in im if tname(i) in state and i not in ok_]
    gone = [i for i in im if tname(i) not in state]
    log(f"{os.path.basename(p)}: {len(tasks)} tasks, {len(im)} images: ready {len(ok_)}, failed {len(bad)}, missing {len(gone)}")
    if args.check and args.missing:
        print("".join(f"    missing: {i}\n" for i in gone) + "".join(f"    failed:  {i}\n" for i in bad), end="", flush=True)
    images += [i for i in im if i not in images]
log(f"server has {len(state)} templates in total, {sum(s == 'ready' for _, s in state.values())} ready")
images = images[_i::_n]
ready = [i for i in images if state.get(tname(i), (None, None))[1] == "ready"]
errored = [i for i in images if tname(i) in state and state[tname(i)][1] != "ready"]
todo = [i for i in images if i not in set(ready)]
log(f"this run ({args.shard}): {len(images)} images: ready {len(ready)}, failed {len(errored)}, never built {len(todo) - len(errored)}")
if args.check:
    sys.exit(0)
n = sum(drop_errored(tname(i), state) for i in errored)
if errored:
    log(f"deleted {n} errored templates")
todo = todo[:args.limit] if args.limit else todo
if not todo:
    sys.exit(0)
log("Docker Hub pulls: " + ("account " + os.environ["DOCKERHUB_USERNAME"] if os.environ.get("DOCKERHUB_USERNAME") and os.environ.get("DOCKERHUB_TOKEN") else "anonymous (100 pulls/h, use --min-interval 40)"))

POOL = concurrent.futures.ThreadPoolExecutor(64)
BACKOFF = [int(x) for x in args.backoff.split(",") if x]
ok = gave_up = streak = 0
t0 = time.time(); last = 0.0
for k, image in enumerate(todo, 1):
    name = tname(image); err = None
    attempt = -1; limited = 0
    while attempt < len(BACKOFF):
        attempt += 1
        time.sleep(max(0.0, last + args.min_interval - time.time())); last = t = time.time()
        try:
            fut = POOL.submit(e2b.Template.build, template_for(image), name, cpu_count=CPU, memory_mb=MEM, on_build_logs=None)
            fut.result(timeout=args.build_timeout); err = None  # a hung build is abandoned (its thread stays behind) and retried
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:160]} ... {str(exc)[-200:]}"
        if err is None and template_state().get(name, (None, None))[1] == "ready":
            ok += 1; streak = 0; log(f"ok   [{k}/{len(todo)}] {image}  {time.time() - t:.0f}s"); break
        drop_errored(name)
        if err and re.search(r"http 429|TOOMANYREQUESTS|pull rate limit", err) and limited < 8:
            # Docker Hub limit: wait it out, this does not use up a retry
            limited += 1; attempt -= 1; log(f"rate-limited, waiting 900 s [{k}/{len(todo)}] {image}"); time.sleep(900); continue
        if attempt < len(BACKOFF):
            log(f"retry in {BACKOFF[attempt]} s [{k}/{len(todo)}] {image}  {err or 'not ready after build'}"); time.sleep(BACKOFF[attempt])
    else:
        gave_up += 1; streak += 1; log(f"GAVE UP [{k}/{len(todo)}] {image}  {err}")
        if streak >= args.stop_streak:
            log(f"STOP: {streak} images in a row gave up"); break
    if k % 25 == 0:
        log(f"progress: built {ok}, gave up {gave_up}, left {len(todo) - k}, {(time.time() - t0) / 3600:.1f} h elapsed")
s = template_state()
log(f"DONE: built {ok}, gave up {gave_up}; ready now {sum(s.get(tname(i), (None, None))[1] == 'ready' for i in images)}/{len(images)}")
