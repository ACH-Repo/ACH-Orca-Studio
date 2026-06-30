# Gateway test projects

Self-contained projects for exercising the **Workflow** engine (branching,
multiple networks, merges, ZPVA, dependency chains) on the cluster for next to no
compute. Each uses the built-in **DEBUG** recipes (HF/STO-3G, 1 core — seconds per
job; for *mechanics*, not chemistry).

Molecules are stored as **pending SMILES** (no `.xyz` shipped), so each file is
self-contained — just copy the one `.json`.

## Use
1. Install this branch on the gateway (so the DEBUG recipes exist):
   `pip install --user --force-reinstall --no-deps "https://github.com/ACH-Repo/ACH-Orca-Workbench/archive/refs/heads/zpva-workflow-node.tar.gz"`
2. WinSCP a `.json` to a fresh folder on the gateway, open it in ORCA Workbench
   (File ▸ Open project).
3. **Molecules tab ▸ Generate Pending** — builds the `.xyz` from the SMILES (RDKit).
4. **Workflow tab** — the graph is pre-wired. Use **Generate only** (just create the
   calcs), **Run pipeline** (app babysits), or **Submit unattended** (SLURM
   dependency chain). Select a node first to scope to one network.
5. Set the partition first if needed: **Settings ▸ SLURM submit script…** and
   change `--partition=long` to your queue (e.g. `short`).

## The projects
- **two_networks.json** — two independent Molecules sources (water → OPT → FREQ;
  methane → OPT → NMR). Confirms each network only touches its own molecule, and
  that selecting one node scopes a run to that network.
- **branch_and_merge.json** — methanol → OPT → {FREQ, NMR}, both (plus the OPT's
  own results) merged into one **Report**. Confirms geometry fan-out and the
  results-merge at a Report.
- **zpva_demo.json** — fluoromethane → OPT → FREQ → **ZPVA**. Run OPT+FREQ, then on
  the ZPVA node click **Expand ZPVA** (reads the `.hess`, drops the displaced
  single-points), build+submit those, then **Assemble ZPVA**. Add isotopologues in
  the node (e.g. an H atom index `2:D`) after Generate Pending shows you the atom
  order.
- **uvvis_demo.json** — formaldehyde → OPT → **Property** (with a TD-DFT recipe) →
  Report. Vertical UV-Vis is just a property, so it's a Property node, not a
  special one. Run it, then right-click the finished TD-DFT calc ▸ **Plot UV-Vis
  spectrum** (or the node's **UV-Vis** button). Uses the cheap DEBUG TD-DFT recipe
  (TD-HF/STO-3G).
- **epr_demo.json** — methyl radical (an open-shell **doublet**, mult 2) → OPT →
  **Property** (with the DEBUG EPR recipe) → Report. EPR is the open-shell analogue
  of NMR, so again a Property node. Run it, then right-click the finished EPR calc ▸
  **Plot EPR spectrum** (or the node's **EPR** button) to see the simulated
  derivative spectrum (g-value + hyperfine). DEBUG numbers are meaningless (STO-3G);
  swap in `EPR g-tensor + hyperfine (B3LYP)` for real values.
