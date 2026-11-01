# Progress tracking and diagrams: how it works and how to use it

## Where to look
- [docs/PROGRESS.md](../PROGRESS.md): the current picture. GitHub renders the Mermaid diagrams.
- `docs/progress.html`: the same content as one self-contained page. Open the file in any browser (it loads
  Mermaid from a CDN; no build step, no server). Use this one inside VS Code, whose Markdown preview does not
  draw Mermaid by default.

## What you will see
1. **Roadmap**: the nine phases as a flow, each box coloured by status with its percent built.
2. **As built**: the parts of the system and how they connect (who talks to whom), coloured the same way.
3. **Phase tables**: for every part, its status, *how it is built*, where the code is, and the PR.
4. **Next up** and **Known gaps**: what is coming and what is honestly still missing.

Status colours: green = done (merged and verified), blue = in an open PR, amber = partial, grey = not started.
"Percent built" counts done plus in-review items.

## How it stays true
The single source is [docs/progress.yaml](../progress.yaml). Nothing in `PROGRESS.md` or `progress.html` is
written by hand. After a change that moves a status:

1. Edit `docs/progress.yaml` (status, `how`, `where`, `pr`; add or move nodes and edges in `architecture`).
2. Run `python -m benchctl.progress` (set `PYTHONPATH` or `PYLIBS` as for the other tools).
3. Commit the yaml and both generated files together.

`./scripts/validate.sh` fails when the generated files are stale, and `tests/test_progress.py` checks that
statuses are valid, `where` paths exist for built items, and every diagram edge points at a real node.
Changing a status without regenerating therefore cannot get past the pre-commit gate.

## Rules of thumb
- Mark something **done** only after it was run for real (a test against the real thing, not only unit tests).
- Use **review** while its PR is open; flip to **done** when it merges.
- `legacy_pr_max` in `docs/progress.yaml` is the last PR number of the previous repository (before the move). Those numbers are shown as plain text; newer ones link to this repository.
- Write `how` for a reader who has not seen the code: the mechanism, not the file list.
- Put honest limits in **Known gaps** instead of leaving them out.

## Adding a diagram of your own
Mermaid is plain text. Add a function to `benchctl/progress.py` that returns Mermaid source, include it in both
`render_md` (inside a ```` ```mermaid ```` fence) and `render_html` (inside `<pre class="mermaid">`), and add
a structural check to `tests/test_progress.py`. Keep labels in double quotes and avoid raw `<` and `&`.
