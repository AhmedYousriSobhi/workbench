"""Build stamps (issue 576, V-4): an image is not built again when its inputs hash and its image id match the last good build."""
import subprocess
from pathlib import Path

import pytest

from benchctl import stack


def cp(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, stdout=out, stderr=err)


@pytest.fixture
def tree(tmp_path):
    gen = tmp_path / "compose" / "generated"
    gen.mkdir(parents=True)
    for d, files in {"images/node-base": {"Dockerfile": "FROM debian\n", "entrypoint.sh": "a"},
                     "images/slurm-ctl": {"Dockerfile": "FROM workbench/node-base:dev\n"},
                     "components/bmc-farm": {"Dockerfile": "FROM python\n", "app.py": "1"},
                     "components/gpu-exporter-farm": {"Dockerfile": "FROM python\n", "x.py": "1"}}.items():
        for f, text in files.items():
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
            (tmp_path / d / f).write_text(text)
    services = {"cn001": {"build": "../../images/node-base", "image": "workbench/node-base:dev"},
                "slurmctld": {"build": "../../images/slurm-ctl", "image": "workbench/slurm-ctl:dev"},
                "bmc-farm": {"build": "../../components/bmc-farm", "image": "workbench/bmc-farm:dev"}}
    return tmp_path, gen / "docker-compose.yml", services


class Docker:
    """Stand-in for `docker image inspect`: ids change when an image is 'built'."""
    def __init__(self):
        self.ids, self.gen = {}, 0

    def build(self, *images):
        self.gen += 1
        self.ids.update({i: f"sha256:{self.gen}" for i in images})

    def __call__(self, cmd):
        img = cmd[-1]
        return cp(self.ids[img]) if img in self.ids else cp(rc=1)


def plan(tree, docker, force=False):
    _root, path, services = tree
    names = ["cn001", "slurmctld", "bmc-farm", stack.GPU_FARM[0]]
    h = stack.image_hashes(names, services, path, "cn001")
    return h, stack.stale_images(h, stack._stamps(path), docker, force)


def stamp(tree, docker, h, built):
    _, path, _ = tree
    docker.build(*[h[n][1] for n in built])
    stack._save_stamps(path, {**stack._stamps(path), **{n: {"hash": h[n][0], "image": h[n][1], "id": docker(["x", h[n][1]]).stdout} for n in built}})


def test_nothing_is_skipped_without_a_stamp(tree):
    _, stale = plan(tree, Docker())
    assert stale == ["cn001", "slurmctld", "bmc-farm", "gpu-exporter-farm"]


def test_unchanged_inputs_with_the_same_image_skip_the_build(tree):
    d = Docker()
    h, stale = plan(tree, d)
    stamp(tree, d, h, stale)
    assert plan(tree, d)[1] == []


def test_a_changed_file_rebuilds_only_that_image(tree):
    d = Docker()
    h, stale = plan(tree, d)
    stamp(tree, d, h, stale)
    (tree[0] / "components/bmc-farm/app.py").write_text("2")
    assert plan(tree, d)[1] == ["bmc-farm"]


def test_a_changed_node_base_rebuilds_the_images_built_from_it(tree):
    d = Docker()
    h, stale = plan(tree, d)
    stamp(tree, d, h, stale)
    (tree[0] / "images/node-base/entrypoint.sh").write_text("b")
    assert plan(tree, d)[1] == ["cn001", "slurmctld"]  # bmc-farm is not built FROM the base


def test_a_missing_or_replaced_image_rebuilds(tree):
    d = Docker()
    h, stale = plan(tree, d)
    stamp(tree, d, h, stale)
    del d.ids["workbench/bmc-farm:dev"]
    assert plan(tree, d)[1] == ["bmc-farm"]
    d.ids["workbench/bmc-farm:dev"] = "sha256:other"  # someone rebuilt or retagged it: the stamp no longer describes it
    assert plan(tree, d)[1] == ["bmc-farm"]


def test_force_build_rebuilds_everything(tree):
    d = Docker()
    h, stale = plan(tree, d)
    stamp(tree, d, h, stale)
    assert len(plan(tree, d, force=True)[1]) == 4


def test_a_missing_context_is_always_built(tree):
    _root, path, services = tree
    services["ghost"] = {"build": "../../images/ghost", "image": "x"}
    h = stack.image_hashes(["ghost"], services, path, "cn001")
    assert h["ghost"][0] is None and stack.stale_images(h, {}, Docker(), False) == ["ghost"]


def test_a_stand_in_runner_never_leaves_a_stamp(tree, monkeypatch):
    """The unit tests' runner answers every command with rc 0 and no output: no image id, so no stamp that a real `up` could trust."""
    assert stack.image_id("workbench/x", lambda c: cp()) == ""


def test_up_builds_unchanged_images_once(tree, monkeypatch):
    """Two `up`s with a runner that behaves like docker: the second builds nothing and says how many were unchanged."""
    import yaml
    _root, path, _ = tree
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    d, built = Docker(), []

    def runner(cmd):
        if "build" in cmd:
            built.append(cmd[cmd.index("build") + 1:])
            d.build(*{"cn001": ["workbench/node-base:dev"]}.get(cmd[cmd.index("build") + 1], []))
            return cp()
        if cmd[:3] == ["docker", "image", "inspect"]:
            return d(cmd)
        return cp()
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    msgs: list[str] = []
    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "_answering", lambda names, r, p, w: [])
    monkeypatch.setattr(stack, "COMPOSE_FILE", path)
    stack.up(sim, "min", runner=runner, say=msgs.append, available=12 * 1024**3, wait=1)
    assert any(m.startswith("images:") for m in msgs)
