# Using ORCA Studio

ORCA Studio is organised as a row of tabs that follow the natural order of a
calculation — draw a molecule, pick a level of theory, run it, read the results.

## The tabs

| Tab | What it does |
|-----|--------------|
| **Molecules** | Build 3D structures from SMILES (auto charge/multiplicity, optional "coord-gen SMILES" metal-swap trick), paste a whole list from ChemDraw, see a 2D depiction, double-click to open in Avogadro/molden. |
| **Recipes** | A searchable, sortable, favouritable library of ORCA input templates. A recipe = calc type (OPT/FREQ/NMR/…) + method label + optional variant + the template text. |
| **Calculations** | The job lifecycle in one place: plan calcs, **derive** follow-ups from finished ones (inherits the optimised geometry), build `.inp`/`.slurm`, submit via `sbatch` (or **Run locally** on a PC), refresh status via `squeue` (F5), double-click for a live plot. Right-click a finished FREQ/NMR for a simulated spectrum. |
| **Report** | Pick finished calculations and extractors (energy, geometry, trajectory, frequencies + IR, NMR shieldings, thermochemistry, dipole, HOMO–LUMO) and write a `<name>.json` + flat `<name>.csv` summary. |
| **Benchmark** | A bulk generator: fan a set of molecules out across many theory levels in a couple of clicks. |
| **Workflow** | A visual node-graph pipeline editor: wire `Molecules → Optimize → Frequencies → Condition → Property → Report`, then **Run pipeline** to build, launch, and advance each step automatically — including conditional branches. |

!!! note "Benchmark and Workflow are still evolving"
    Both work, but they are the least settled parts of the app and may change
    substantially in future versions. Treat them as conveniences, not a stable
    API.

## The Workflow tab, in brief

Instead of clicking OPT → derive FREQ → check → derive NMR by hand, you draw the
recipe once as a graph and let the app run it:

```
 Molecules ──▶ Optimize ──▶ Frequencies ──▶ Condition ──▶ Property ──▶ Report
                                            (no imag. freq?)   (NMR/SP)
```

- **Run pipeline** runs it live — the app drives each step and must stay open.
- **▶▶ Submit unattended** (cluster only) hands the whole pipeline to SLURM as a
  dependency chain, so it runs with no GUI; you can close ORCA Studio and your
  SSH session.
- **Generate only** creates the planned calculations without launching them.

The full node reference, editing shortcuts, conditional branching, resume
behaviour, and merged reports are documented in the
[Workflow section of the README](https://github.com/ACH-Repo/ACH-Orca-Studio#the-workflow-tab).

## Running on a PC

The same app runs on a normal desktop; the cluster-only actions adapt
automatically. **Run locally** replaces Submit when `sbatch` isn't found (point
it at your local `orca` executable once), cores are capped to your CPU, and
coordinate generation, recipes, spectra, and reports all work the same. See
[Running on a PC](https://github.com/ACH-Repo/ACH-Orca-Studio#running-on-a-pc)
in the README for details.
