"""The read-only Kubernetes calls a monitoring client makes over its SSH session, in this repository's own words (issue 762, ADR 0084).

One catalogue, three users: the live conformance test (`tests/real/test_k8s_reads_real.py`) runs each read from the k-login as
the cluster-wide viewer and as the namespace-limited user and checks the outcome and the shape of the answer; the unit tests
keep the catalogue read-only and in step with `docs/guides/client-testing.md`; the Teleport path (issue 763) can run the same
reads through `tsh ssh`. Nothing here copies a client's command text: each read is "what a Kubernetes monitor needs to ask".

An outcome is one word: `yes` or `no` (what `kubectl auth can-i` prints), `ok` (exit 0), `forbidden` (RBAC refused it),
`not-found` (the API path is not served), `served` or `not-served` (an API group has resources or has none), `error`."""
import json
import re
from dataclasses import dataclass

LIMITED_NS = "team-a"        # the one namespace the limited user may read (k8s/fixtures/30-rbac-viewer.yaml)
OTHER_NS = "team-b"
ROLES = ("viewer", "limited")
KUBECONFIG = {"viewer": None, "limited": "limited.yaml"}   # file under ~/.kube; None is the default `config`
GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"
GFD_LABELS = ("nvidia.com/gpu.present", "nvidia.com/gpu.count", GPU_PRODUCT_LABEL, "nvidia.com/mig.strategy", "nvidia.com/cuda.driver.major")
POOL_LABEL = "pool"
# Kinds the viewer role may read that the client does not (tests/test_k8sreads.py: a new viewer rule needs a read or a name here)
NOT_READ_BY_CLIENT = {"services", "configmaps", "replicasets", "statefulsets", "cronjobs", "customresourcedefinitions", "selfsubjectaccessreviews",
                      "selfsubjectrulesreviews"}
READ_VERBS = {("get",), ("describe",), ("logs",), ("auth", "can-i"), ("config", "view"), ("api-resources",)}
LOG_LIMIT_BYTES = 1048576  # the cap a log read asks for (this repository's figure; a client chooses its own)
LOG_ARGS = ("--tail=500", "--timestamps", f"--limit-bytes={LOG_LIMIT_BYTES}")
TIMESTAMPED = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z \S"  # what --timestamps puts before each line (RFC 3339)


@dataclass(frozen=True)
class Read:
    key: str                    # stable name, used in test ids and in the guide
    what: str                   # one line, for the guide
    args: tuple[str, ...]       # the kubectl arguments
    needs: str                  # what the user must be allowed to do (for the guide)
    viewer: tuple[str, ...]     # acceptable outcomes as the cluster-wide viewer
    limited: tuple[str, ...]    # acceptable outcomes as the namespace-limited user
    signal: bool = False        # a "workload signal": the client reads it only when that option is on


def _can_i(key: str, what: str, args: str, viewer: str, limited: str, signal: bool = False) -> Read:
    return Read(key, what, ("auth", "can-i", *args.split()), "nothing (anyone may ask what they may do)", (viewer,), (limited,), signal)


def _signal_reads() -> list[Read]:
    kinds = [("deployments", "deployments.apps"), ("jobs", "jobs.batch"), ("pvcs", "persistentvolumeclaims"), ("daemonsets", "daemonsets.apps"),
             ("resourcequotas", "resourcequotas"), ("hpas", "horizontalpodautoscalers.autoscaling"), ("clusterqueues", "clusterqueues.kueue.x-k8s.io")]
    out = []
    for short, res in kinds:
        out.append(_can_i(f"can-i-list-{short}", f"may it list {res} cluster-wide", f"list {res} -A" if "kueue" not in res else f"list {res}", "yes", "no", True))
        out.append(_can_i(f"can-i-get-{short}", f"may it get {res}", f"get {res}" if "kueue" in res else f"get {res} -n {LIMITED_NS}", "yes", "no", True))
        out.append(Read(f"get-{short}", f"list {res} (workload signal)", ("get", res, *(() if "kueue" in res else ("-A",)), "-o", "json"),
                        f"list on {res}", ("ok",), ("forbidden",), True))
    out.append(_can_i("can-i-list-localqueues", "may it list Kueue LocalQueues in its namespace", f"list localqueues.kueue.x-k8s.io -n {LIMITED_NS}", "yes", "yes", True))
    out.append(Read("get-localqueues", "list Kueue LocalQueues in one namespace", ("get", "localqueues.kueue.x-k8s.io", "-n", LIMITED_NS, "-o", "json"),
                    "list on localqueues in that namespace", ("ok",), ("ok",), True))
    return out


READS: tuple[Read, ...] = (
    _can_i("can-i-list-nodes", "may it list nodes", "list nodes", "yes", "no"),
    _can_i("can-i-list-pods-all", "may it list pods in every namespace", "list pods -A", "yes", "no"),
    _can_i("can-i-list-events-all", "may it list events in every namespace", "list events -A", "yes", "no"),
    _can_i("can-i-list-pods-ns", "may it list pods in one namespace", f"list pods -n {LIMITED_NS}", "yes", "yes"),
    _can_i("can-i-list-events-ns", "may it list events in one namespace", f"list events -n {LIMITED_NS}", "yes", "yes"),
    _can_i("can-i-list-pods-other-ns", "may it list pods in another namespace", f"list pods -n {OTHER_NS}", "yes", "no"),
    _can_i("can-i-list-pods-kubeconfig-ns", "may it list pods in the kubeconfig's own namespace", "list pods", "yes", "yes"),
    _can_i("can-i-list-namespaces", "may it list namespaces", "list namespaces", "yes", "no"),
    _can_i("can-i-get-pod-logs", "may it read pod logs", f"get pods --subresource=log -n {LIMITED_NS}", "yes", "no"),
    Read("config-view", "which context and namespace the kubeconfig uses", ("config", "view", "--minify", "-o", "json"),
         "nothing (reads the local file)", ("ok",), ("ok",)),
    Read("readyz", "API server readiness, one line per check", ("get", "--raw", "/readyz?verbose"),
         "the public-info role every authenticated user has", ("ok",), ("ok",)),
    Read("version", "the API server's version", ("get", "--raw", "/version"), "the same role", ("ok",), ("ok",)),
    Read("nodes", "every node: conditions, GPU capacity, labels, kubelet version", ("get", "nodes", "-o", "json"),
         "list on nodes (cluster-wide)", ("ok",), ("forbidden",)),
    Read("metrics-probe", "is the metrics API served (one item)", ("get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes?limit=1"),
         "list on nodes in metrics.k8s.io", ("ok",), ("forbidden",)),
    Read("node-usage", "CPU and memory use of every node (metrics-server, issue 382)", ("get", "nodes.metrics.k8s.io", "-o", "json"),
         "list on nodes in metrics.k8s.io", ("ok",), ("forbidden",)),
    Read("pods-all", "pods in every namespace that are not finished, in chunks", ("get", "pods", "-A", "--field-selector=status.phase!=Succeeded", "--chunk-size=500", "-o", "json"),
         "list on pods (cluster-wide)", ("ok",), ("forbidden",)),
    Read("pods-ns", "pods in one namespace, in chunks", ("get", "pods", "-n", LIMITED_NS, "--chunk-size=500", "-o", "json"),
         "list on pods in that namespace", ("ok",), ("ok",)),
    Read("pods-count", "how many pods, from one item and `remainingItemCount`", ("get", "--raw", "/api/v1/pods?limit=1"),
         "list on pods (cluster-wide)", ("ok",), ("forbidden",)),
    Read("pods-count-ns", "the same for one namespace", ("get", "--raw", f"/api/v1/namespaces/{LIMITED_NS}/pods?limit=1"),
         "list on pods in that namespace", ("ok",), ("ok",)),
    Read("events-warning", "Warning events in every namespace", ("get", "events", "-A", "--field-selector=type=Warning", "-o", "json"),
         "list on events (cluster-wide)", ("ok",), ("forbidden",)),
    Read("events-warning-ns", "Warning events in one namespace", ("get", "events", "-n", LIMITED_NS, "--field-selector=type=Warning", "-o", "json"),
         "list on events in that namespace", ("ok",), ("ok",)),
    Read("api-group-kueue", "is the Kueue group served", ("api-resources", "--api-group=kueue.x-k8s.io", "--no-headers", "-o", "name"),
         "discovery (every authenticated user)", ("served",), ("served",), True),
    Read("api-group-volcano", "is the Volcano group served", ("api-resources", "--api-group=scheduling.volcano.sh", "--no-headers", "-o", "name"),
         "discovery (every authenticated user)", ("served",), ("served",), True),
    Read("get-volcano-queues", "list Volcano Queues (workload signal)", ("get", "queues.scheduling.volcano.sh", "-o", "json"),
         "list on queues (cluster-scoped)", ("ok",), ("forbidden",), True),
    Read("get-volcano-podgroups", "list Volcano PodGroups in one namespace", ("get", "podgroups.scheduling.volcano.sh", "-n", LIMITED_NS, "-o", "json"),
         "list on podgroups in that namespace", ("ok",), ("ok",), True),
    Read("api-group-kubeflow", "is the Kubeflow group served (not yet: issue 390)", ("api-resources", "--api-group=kubeflow.org", "--no-headers", "-o", "name"),
         "discovery", ("not-served",), ("not-served",), True),
    Read("describe-pod", "what the user sees when it opens a pod", ("describe", "pod", "sized", "-n", LIMITED_NS),
         "get on pods and list on events in that namespace", ("ok",), ("ok",)),
    # Logs (issue 760): KWOK serves canned kubelet log files (k8s/kwok/logs.yaml). The limited user's role has pods and events, not
    # pods/log, so it is refused in its own namespace: the refusal is the subresource, not the namespace
    Read("pod-logs", "the last lines of a pod's log, with timestamps and a byte cap", ("logs", "sized", "-n", LIMITED_NS, *LOG_ARGS),
         "get on pods/log in that namespace", ("ok",), ("forbidden",)),
    Read("pod-logs-previous", "the log of the run before, for a crash-looping pod", ("logs", "crashloop", "-n", OTHER_NS, "--previous", *LOG_ARGS),
         "get on pods/log in that namespace", ("ok",), ("forbidden",)),
    *_signal_reads(),
)
BY_KEY = {r.key: r for r in READS}


def classify(read: Read, returncode: int, stdout: str, stderr: str) -> str:
    """The outcome word of one run (see the module text)."""
    if read.args[:2] == ("auth", "can-i"):
        word = stdout.strip().split("\n")[0].strip() if stdout.strip() else ""
        return word if word in ("yes", "no") else "error"
    if read.args[0] == "api-resources":
        return "served" if returncode == 0 and any(ln.strip() for ln in stdout.splitlines()) else "not-served"
    if returncode == 0:
        return "ok"
    low = stderr.lower()
    if "forbidden" in low:
        return "forbidden"
    if "notfound" in low or "not found" in low or "could not find the requested resource" in low:
        return "not-found"
    return "error"


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def shape_problems(read: Read, role: str, stdout: str, api_version: str) -> list[str]:
    """What is wrong with the answer of a read that succeeded (empty when it has the shape the client parses)."""
    key, bad = read.key, []
    if key == "readyz":
        if "readyz check passed" not in stdout:
            bad.append("no 'readyz check passed' line")
    elif key == "version":
        v = (_json(stdout) or {}).get("gitVersion")
        if v != api_version:
            bad.append(f"gitVersion {v!r}, expected {api_version!r}")
    elif key == "config-view":
        cfg = _json(stdout) or {}
        ctx = (cfg.get("contexts") or [{}])[0].get("context", {})
        if not cfg.get("current-context") or "cluster" not in ctx:
            bad.append("no current context with a cluster")
        if role == "limited" and ctx.get("namespace") != LIMITED_NS:
            bad.append(f"limited namespace is {ctx.get('namespace')!r}, expected {LIMITED_NS!r}")
        if any(u.get("user", {}).get("token", "REDACTED") != "REDACTED" for u in cfg.get("users", [])):
            bad.append("a token is printed")
    elif key == "nodes":
        bad += _node_problems(_json(stdout) or {}, api_version)
    elif key == "pods-count":
        meta = (_json(stdout) or {}).get("metadata", {})
        if not isinstance(meta.get("remainingItemCount"), int) or meta["remainingItemCount"] < 1:
            bad.append(f"metadata.remainingItemCount is {meta.get('remainingItemCount')!r}, expected a count of the pods not returned")
    elif key in ("pods-all", "pods-ns", "pods-count-ns"):
        items = (_json(stdout) or {}).get("items")
        if not items:
            bad.append("no pods in the list")
    elif key.startswith("events-warning"):
        items = (_json(stdout) or {}).get("items")
        if items is None or any(e.get("type") != "Warning" for e in items):
            bad.append("not a list of Warning events")
    elif key == "node-usage":
        items = (_json(stdout) or {}).get("items") or []
        if not items or any(not {"cpu", "memory"} <= set(i.get("usage") or {}) for i in items):
            bad.append("not a list of nodes with cpu and memory usage")
    elif key in ("pod-logs", "pod-logs-previous"):
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines or not all(re.match(TIMESTAMPED, ln) for ln in lines):
            bad.append("not log lines that each start with a timestamp")
        if len(stdout.encode()) > LOG_LIMIT_BYTES:
            bad.append("more bytes than --limit-bytes allows")
        if key == "pod-logs-previous" and "failing" not in stdout:
            bad.append("not the previous run's output")
    elif key == "describe-pod":
        if "Name:" not in stdout or "sized" not in stdout:
            bad.append("no pod description")
    elif key.startswith("get-") and "items" not in (_json(stdout) or {}):
        bad.append("not a list")
    return bad


def _node_problems(doc: dict, api_version: str) -> list[str]:
    bad, items = [], doc.get("items") or []
    if not items:
        return ["no nodes"]
    for n in items:
        name, st, lab = n["metadata"]["name"], n.get("status", {}), n["metadata"].get("labels", {})
        if not any(c["type"] == "Ready" for c in st.get("conditions", [])):
            bad.append(f"{name}: no Ready condition")
        kv = st.get("nodeInfo", {}).get("kubeletVersion")
        if kv != api_version:
            bad.append(f"{name}: kubeletVersion {kv!r}, expected the API server's {api_version!r} (a client parses vX.Y for skew)")
        if POOL_LABEL not in lab:
            bad.append(f"{name}: no {POOL_LABEL!r} label")
        cap, alloc = st.get("capacity", {}), st.get("allocatable", {})
        if lab.get("nvidia.com/gpu.present") == "true":
            miss = [lb for lb in GFD_LABELS if lb not in lab]
            if miss:
                bad.append(f"{name}: missing GPU Feature Discovery labels {miss}")
            if not (cap.get("nvidia.com/gpu") and cap.get("nvidia.com/gpu") == alloc.get("nvidia.com/gpu") == lab.get("nvidia.com/gpu.count")):
                bad.append(f"{name}: nvidia.com/gpu capacity {cap.get('nvidia.com/gpu')!r}, allocatable {alloc.get('nvidia.com/gpu')!r}, label count {lab.get('nvidia.com/gpu.count')!r} do not agree")
        elif "nvidia.com/gpu" in cap:
            bad.append(f"{name}: a node without the GPU label advertises GPUs")
    return bad


def render_table() -> str:
    """The guide's table of reads (markdown), so the doc and the catalogue cannot drift."""
    rows = ["| Read | What it asks | Needs | Viewer | Limited |", "|---|---|---|---|---|"]
    for r in READS:
        rows.append(f"| `{r.key}`{' (signal)' if r.signal else ''} | {r.what}: `kubectl {' '.join(r.args)}` | {r.needs} | {' or '.join(r.viewer)} | {' or '.join(r.limited)} |")
    return "\n".join(rows)
