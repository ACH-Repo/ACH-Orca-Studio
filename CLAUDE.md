# ORCA Workbench — context for Claude (read this first)

If you're an AI assistant picking up work on this repo: **read this whole file
first**, then you're oriented. It's the durable memory of *why* things are the
way they are. Keep it updated when you finish meaningful work.

> Maintainer: Christian Nelle (AG Henke, TU Dortmund). PyPI package: `orca-workbench`.
> Repo: `ACH-Repo/ACH-Orca-Workbench`. License MIT.

## What this is
A **Tkinter desktop GUI** for building, submitting, monitoring, and analysing
**ORCA** quantum-chemistry calculations on a **SLURM** cluster (TU Dortmund's
LiDO3). The user runs the GUI *on the cluster gateway* over a remote desktop, so
the app calls `sbatch`/`squeue` directly. Tabs: Molecules · Recipes ·
Calculations · Report · Benchmark · Workflow.

## The golden architectural rule
**Core logic lives in `orca_workbench/core/` and is UI-free, with I/O injected;
the Tkinter code in `orca_workbench/ui/` is a thin shell over it.** This is why
features are unit-testable with no cluster and no display. Follow it for every
new feature:
- pure function/module in `core/`, network/`subprocess`/`squeue` passed in or
  mockable (see `core/discovery.py`, `core/resolve.py`, `core/headless.py`);
- `ui/` just wires widgets to that core;
- tests in `tests/` mock HTTP/sbatch/ORCA so `python -m pytest tests/ -q` runs
  anywhere (currently ~58 tests, all offline).

## Feature tiers (the `--simple` lens)
"Core" = the feature set loaded under the `--simple` flag (originally a load-time
optimisation for slow connections). **That load-time rationale is now largely
moot**: the real cause of sluggishness was MobaXterm's X11-forwarding chattiness,
fixed by switching to **ThinLinc** (framebuffer streaming). Tiers still guide
*dependency/optionality*: keep optional features (e.g. the web name-resolver)
off the core path and gracefully degradable.

## Environment (LiDO3 gateway `gw02`, confirmed 2026-06-22)
- Python **3.9** on the gateway (write 3.9-compatible code: no `match`, no bare
  `X | Y` runtime unions). Dev machine (Windows) is 3.10.
- Remote access: **ThinLinc** for the GUI (not MobaXterm anymore), **WinSCP**
  for file transfer. Both are in the LiDO First Contact handout.
- **Do NOT run ORCA on the gateway** — it's sanctioned (account blocked). Always
  `sbatch`. The slurm template stages to node-local `/scratch` and copies back.
- 3D viewer on the gateway: **molden only** (`module molden/5.9`) — no
  PyMOL/VMD/Ovito. molden animates multi-frame `.xyz` (trajectories).
- Web: PubChem (`pug/...`) and OPSIN (`opsin.ch.cam.ac.uk`) are reachable with
  verified TLS, no proxy. RDKit present (2024.03.2); Java absent (so no OPSIN jar).
- Light on-demand web requests are policy-OK (the handout sanctions gateway
  *compute*, not network; `pip`/`conda` downloads are documented).

## Conventions
- **Branch off `main`** for each feature; merge when tested. Commits end with a
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
- Install a branch on the gateway to test:
  `pip install --user --force-reinstall --no-deps "https://github.com/ACH-Repo/ACH-Orca-Workbench/archive/refs/heads/<branch>.tar.gz"`
- **ASCII-only button labels** — ThinLinc's font stack lacks glyphs like ▶ ■ →
  (they render as literal `▶`). Use `>`, `>>`, `->`, `...`. (Greek σ/δ and
  cm⁻¹ in plot labels are semantic; left as-is unless they break.)
- **No SSH/SFTP/paramiko/credentials in any Windows-side code.** The app runs on
  the cluster; that's where it calls SLURM. Develop locally, ship via WinSCP.
- App-level settings live in `core/config.py` (`~/.orca_workbench.json`),
  project state in `core/project.py` (a project.json with **relative** paths).

## Recently built (all merged to `main` unless noted)
- **Job discovery/import + submit throttle** — Detect jobs / Import calcs
  buttons, open-time auto-prompt, `submit_delay_ms` (default 100 ms, Settings ▸
  SLURM submission delay) so bulk `sbatch` doesn't trip the rate limit.
  (`core/discovery.py`, `core/slurm_runtime.py`)
- **Add molecule by name** — web resolver (OPSIN→PubChem→autocomplete), RDKit
  salt-strip, depiction-confirm, provenance to comment, "Test connection"
  button. Shortcut **Ctrl+Shift+N**. (`core/resolve.py`, `ResolveNameDialog`)
- **Gradient report extractor** — max/RMS/norm from `.engrad`. (`core/reporting.py`)
- **Headless run** — `orca-workbench --run FILE.inp`: sbatch on a login node,
  else `orca FILE > FILE.out`. (`core/headless.py`)
- **Right-click a finished OPT** → open optimised geometry / trajectory movie
  (via shared `open_xyz_3d`, → molden on gateway). (`ui/calculations_tab.py`,
  `ui/molecules_tab.py`)
- **Import/recipe improvements** (branch `import-and-recipes-improvements`) —
  (a) file-imported structures carry `Molecule.coords_locked` so SMILES
  generation can't overwrite the original geometry (refused with a note; delete +
  re-add to use SMILES); recovered jobs lock too when an xyz was found.
  (b) `.smi/.smiles/.csv` SMILES-list files import as *pending* molecules to
  generate (`coords.read_smiles_file`, reusing `parse_smiles_list`).
  (c) write-only/input-deck formats (e.g. `.acesin`) get a last-resort
  `Label x y z` salvage (`coords.heuristic_atoms_from_text`, flagged "VERIFY").
  (d) **multiple recipe directories**: `Project.recipe_dirs` (the `<builtin>`
  sentinel keeps project.json portable), `inputs.load_recipes_from_dirs` (global
  name dedup), Recipes menu *Add*/*Manage* dirs, and the tab groups recipes under
  per-folder dividers while search/sort span all of them.
- **UI polish** — Ctrl+Shift+N, ASCII button labels, Calc/Recipes scrollbars.
- **Plotter overhaul** (`ui/spectra.py`) — IR now stacks multiple molecules like
  NMR (right-click several finished FREQ); both windows share ONE hover-driven
  structure side panel (`_StructurePanel`) instead of per-trace thumbnails; NMR
  x-range opens with a sensible minimum span (not zoomed to one peak); a
  **Maximize** button on both; on-hover sticks skipped in stacked IR.

## Open work / TODO
- Optional: PyMOL support for a *local* (Windows) machine (gateway has molden only).
- The plotter overhaul (below) was a blind refactor (no GUI here) — worth a visual
  pass on the gateway: stacked IR, the shared hover structure panel, NMR margins,
  maximize button. Ping if anything renders oddly in ThinLinc.

## Origin / sibling project
This app grew out of a **ZPVA** study (¹⁹F isotope shifts of deuterated
4-fluoroimidazole) in `C:\Users\chris\Documents\Claude\Fim_NMR\` on the dev
machine. Stage 1 concluded: deuteration gives position-resolved, additive ¹⁹F
shifts (48–351 ppb, all above the 0.03 ppm floor). The reusable tooling from it
(`hess_tools`, ORCA parsers, job inventory) was deliberately *not* pulled into
this app — no in-app consumer yet.
