# ORCA Studio

A desktop GUI for building, launching, and monitoring [ORCA](https://www.faccts.de/orca/) quantum-chemistry calculations on a SLURM cluster — from a SMILES string to a submitted job to a plotted IR/NMR spectrum, without leaving the window.

<!-- TODO: add a screenshot of the running app as the first visual.
     Suggested: the Calculations tab mid-run, or the Molecules tab showing a
     structure. Save it as docs/screenshot.png and it will render below. -->
![ORCA Studio](docs/screenshot.png)

---

## TL;DR

ORCA Studio turns the usual "write an input file, scp it, sbatch it, squeue it, scp the output back, parse it" loop into a tabbed GUI:

- **Draw molecules** from SMILES (RDKit → OpenBabel fallback) or import `.xyz`.
- **Define theory levels** once as reusable recipes (method + basis + variant).
- **Generate and submit** SLURM jobs into a tidy `calcs/<mol>/<category>/<type>/<method>/` tree; derive follow-up calculations (FREQ/NMR/SP) from a finished optimisation in one click.
- **Watch jobs live** — double-click a running job for a self-updating SCF / geometry-convergence plot.
- **Extract results** — energies, geometries, frequencies, NMR shieldings, thermochemistry — into a JSON + CSV report, and plot simulated IR and NMR spectra.

It is designed to run **on the cluster login node** and be displayed on your own machine through an X-forwarding SSH client (e.g. MobaXterm), so it calls `sbatch`/`squeue` directly and reads job output straight off the shared filesystem. It **also runs on a normal Windows/Linux/macOS PC** for the build / visualise / report parts (see [Running on a PC](#running-on-a-pc)).

---

## Installation

Both ways are *editable* installs (`pip install -e .`): the command points back at this folder, so the app updates in place whenever you `git pull` — **no reinstall, and don't move the folder afterwards** (that breaks the link).

### On a SLURM cluster (e.g. Lido) — via X-forwarding

Copy/clone this repository somewhere stable (e.g. `/work/<your_id>/orca-studio`), then from the repository root:

```bash
module load python          # REQUIRED — provides numpy's OpenBLAS (see note)
pip install --user -e .
orca-studio                 # launches; the window appears on your PC over X-forwarding
```

> **`module load python` is mandatory on the cluster.** The scientific stack
> (matplotlib → numpy) needs `libopenblas.so`, which that module autoloads.
> Without it the app fails at startup with
> `ImportError: libopenblas.so.0: cannot open shared object file`.
> Put `module load python` in your `~/.bashrc` so you never forget it.

If `~/.local/bin` isn't on your `PATH`, either add it or just launch with `python -m orca_studio`.

### On a Windows PC

No `module load` needed — the Windows wheels bundle their own libraries. From a terminal **opened in this folder** (the one with `pyproject.toml`):

```cmd
py -m pip install --user -e .
py -m orca_studio
```

- Use `py -m pip` (not bare `pip`) so it installs into the Python you actually launch with.
- The `orca-studio` command lands in a per-user `Scripts` folder that often isn't on `PATH`, so **`py -m orca_studio` is the reliable way to launch** (works from anywhere). pip prints that Scripts path during install if you'd rather add it to `PATH`.
- Keep the folder where it is (e.g. in your cloned repo); the editable install points at it.

<details>
<summary>Prefer an isolated virtual environment? (optional)</summary>

```cmd
py -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m orca_studio
```
On Linux/macOS use `source .venv/bin/activate` instead of the second line.
</details>

### Verify the environment

```bash
orca-studio --diagnose      # or: python -m orca_studio --diagnose
```

Reports whether RDKit / OpenBabel can generate coordinates on this machine — run it first if structure generation misbehaves.

Open a saved project straight away:

```bash
orca-studio myproject.json
```

---

## The five tabs

| Tab | What it does |
|-----|--------------|
| **Molecules** | Build 3D structures from SMILES (auto charge/multiplicity, optional "coord-gen SMILES" metal-swap trick), paste a whole list from ChemDraw, see a 2D depiction, double-click to open in Avogadro/molden. |
| **Recipes** | A searchable, sortable, favouritable library of ORCA input templates. A recipe = calc type (OPT/FREQ/NMR/…) + method label + optional variant + the template text. |
| **Calculations** | The job lifecycle in one place: plan calcs, **derive** follow-ups from finished ones (inherits the optimised geometry), build `.inp`/`.slurm`, submit via `sbatch`, refresh status via `squeue` (F5), double-click for a live plot. Right-click a finished FREQ/NMR for a simulated spectrum. |
| **Report** | Pick finished calculations and extractors (energy, geometry, trajectory, frequencies + IR, NMR shieldings, thermochemistry, dipole, HOMO–LUMO) and write a `<name>.json` + flat `<name>.csv` summary. |
| **⚗ Benchmark** | A bulk generator: fan a set of molecules out across many theory levels in a couple of clicks (see note). |

> **⚗ The Benchmark tab is experimental.** It works, but its design is the
> least settled part of the app and **may change substantially in future
> versions** as the workflow around large method/basis sweeps evolves. Treat it
> as a convenience for generating many calculations at once, not a stable API.

---

## Running on a PC

Once installed (see [Installation → On a Windows PC](#on-a-windows-pc)), the same app runs on a normal desktop; the cluster-only actions degrade gracefully:

- **Submit / Refresh status** need `sbatch`/`squeue`; off-cluster they report that SLURM isn't available. You can still build the `.inp`/`.slurm` files and run them however you like.
- **Avogadro** opens locally: double-click a molecule, point it at your `Avogadro2.exe` once, and it's remembered in `~/.orca_studio.json`.
- **Coordinate generation, recipes, spectra, and reports** all work the same.

A dedicated PC build that runs ORCA locally (instead of submitting to SLURM) is on the roadmap for a future version.

---

## Requirements

- **Python ≥ 3.9** (developed against the cluster's 3.9; avoids 3.10+ syntax).
- **Tkinter** — bundled with most Python builds.
- [`rdkit`](https://www.rdkit.org/) — coordinate generation and 2D depiction (OpenBabel is a fallback).
- [`openbabel-wheel`](https://pypi.org/project/openbabel-wheel/) — fallback coordinate generation.
- [`matplotlib`](https://matplotlib.org/) — live plots and spectra (needs numpy → on a cluster, see the `module load python` note).

All are pulled in automatically by the `pip install -e .` step above (Windows wheels need no compiler).

---

## Project layout

```
ACH-Orca-Studio/
├── pyproject.toml            # package metadata + the `orca-studio` console script
├── requirements.txt
├── orca_studio/
│   ├── __main__.py           # entry point + --diagnose / --help / project-path arg
│   ├── core/                 # pure logic, no GUI (unit-testable)
│   │   ├── coords.py         # SMILES → 3D XYZ (RDKit/OpenBabel), xyz I/O
│   │   ├── inputs.py         # recipes + .inp rendering
│   │   ├── slurm.py          # SLURM script templating
│   │   ├── slurm_runtime.py  # sbatch / squeue wrappers (graceful when absent)
│   │   ├── orca_parser.py    # parse ORCA 6 output (SCF, geometry, freqs, NMR, …)
│   │   ├── reporting.py      # result extractors → JSON + CSV
│   │   ├── spectra.py        # line-broadening math (no numpy needed)
│   │   ├── project.py        # project.json model (molecules, planned calcs)
│   │   └── config.py         # per-user config (~/.orca_studio.json)
│   ├── ui/                   # Tkinter widgets, one module per tab + helpers
│   └── data/
│       ├── slurm_template.sh # the SLURM submit template
│       └── recipes/*.json    # starter recipe library
└── LICENSE
```

<details>
<summary><b>How it works</b> (click to expand)</summary>

- **The SLURM template** copies the input to the compute node's local `/scratch`, runs ORCA there, and copies results back on exit. ORCA's stdout is wrapped in `stdbuf -oL` so the `.out` on the shared filesystem updates line-by-line *during* the run — that's what makes the live plots possible. The output streams to `<rundir>/<jobname>-<jobid>.out`.
- **Live monitoring** reads that `.out` directly (no SSH, no callbacks) and re-parses it on a timer. The parser is regex-based and was verified against real ORCA 6.0.1 output for OPT, single-point, FREQ, and NMR jobs.
- **Derived calculations** carry a `parent_id` and a `geometry_source` of `parent:<id>`; at build time the child reads the parent's optimised `<mol>.xyz`. A child can't be built until its parent has produced that geometry — a natural gate that mirrors the OPT → confirm → FREQ → SP/NMR workflow.
- **Spectra** are simulated by broadening the parsed stick lines (Lorentzian/Gaussian) — no `orca_mapspc` needed. The NMR window plots several molecules at once and highlights the one a peak belongs to on hover.
- **No outbound network code.** The app never opens a connection; it only calls local `sbatch`/`squeue` and reads local files. Everything reaches your screen through your own X-forwarding SSH client.

</details>

---

## Authorship and AI involvement

This project was conceived and directed by **[@p3rAsperaAdAstra](https://github.com/p3rAsperaAdAstra)** (Christian Nelle). It grew out of his own collection of ORCA/SLURM automation scripts (coordinate generation, input distribution, job launching, and the SLURM template), which embody the workflow and the directory conventions ORCA Studio is built around. Those scripts, the domain expertise, the design decisions, and the testing are his.

The **ORCA Studio application code was written by Claude (Anthropic's AI assistant) at the author's direction**, in May 2026, turning those scripts and the author's design choices into the tabbed GUI documented here. Output-parsing regexes and the workflow logic were validated against real ORCA 6.0.1 calculations during development.

This note is included for transparency about what is human-authored versus AI-authored. Nothing here claims the AI did more or less than it did.

---

## License

MIT — see [LICENSE](LICENSE). © 2026 Christian Nelle, Arbeitsgruppe Prof. Henke, Fakultät für Chemie und Chemische Biologie, Technische Universität Dortmund.
