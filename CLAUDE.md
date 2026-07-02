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
Calculations · Report · Workflow.

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
- **There IS a real ORCA 6.0.1 on the dev machine: `C:\ORCA_6.0.1`** (`orca.exe`,
  `orca_plot.exe`, `orca_2mkl.exe`) — same major version as the gateway. So
  ORCA-output work (parsers, `orca_plot` sequences, MOREAD inputs) can be verified
  **locally** with a tiny job, not shipped "blind." Running `orca` locally is fine —
  the gateway-only sanction is about not eating *gateway* compute. Local mode finds
  `orca_plot` beside `orca_path`; the *cluster* env (module wrapper, `.gbw` copyback)
  still needs a gateway check.
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
- **3D-viewer file rule** (same branch) — `calculations_tab.viewer_file_for_calc`
  picks the *richest* file a molecular viewer can use: a **FREQ** job opens its
  **`.out`** (Avogadro/molden animate the normal modes; a bare `.xyz` can't),
  everything else the optimised geometry `.xyz`. Wired into the Calc-tab
  right-click ("Open normal modes (3D viewer)" for a finished FREQ) and the
  Workflow node results button (labelled **Modes** for FREQ, **Struct** otherwise;
  now launches the real viewer via `open_xyz_3d`, not the OS default). Extend
  `_OUT_VIEWER_CALCTYPES` when density/MO viewing (needs print keywords or `.gbw`)
  is wired. (molden's ORCA-`.out` mode support is unverified on the gateway — if it
  won't animate, the fallback is generating a mode-displacement multi-frame xyz.)
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
  Later on the same import/recipes branch: consistent RDKit depiction font
  (`depict._apply_consistent_scale` pins `fixedBondLength` — a max, so small mols
  draw at a comfortable size and huge ones scale to fit — and the font follows the
  bonds, not the canvas); **Shift+Up/Down** range row-select across all row tabs
  (`shortcuts.install_tree_shift_select`, anchor-based so reversing shrinks the
  range; skips dividers); **Ctrl+Shift+O** = Import files (plain Ctrl+O is Open
  project); Recipes toolbar gains *Add folder*/*Manage folders* buttons and a red
  **DELETE** (matching the Calc tab's red **DECONSTRUCT**) that states plainly it
  removes the file from disk — folder *unlink* via Manage folders is the
  non-destructive option; and the Add-by-name dialog now shows a **fragment
  chooser** for multi-component hits (`resolve.fragments_of`) so a coordination
  complex isn't auto-reduced to its counter-ion (default still = largest fragment).
- **ZPVA workflow node** (branch `zpva-workflow-node`, stacked on the import
  branch) — the Fim_NMR ZPVA tooling pulled in-app and generalised. Core (pure,
  numpy, tested): `core/hess.py` (`.hess` parser + `normal_modes` with a
  `masses_amu` isotopologue override) and `core/zpva.py` (property-agnostic
  `zpva_correction`, `displaced_geometries`, `plan_zpva`/`assemble_zpva` with
  injected I/O, `parse_isotopologue_spec`, the 1-D Schrödinger `selftest`). UI: a
  **ZPVA builder node** (`workflow` kind `builder`, NOT statically expanded). It's
  two-step — wire `Frequencies -> ZPVA`, run the FREQ, then **Expand ZPVA** reads
  the `.hess` and drops the ±dq displaced single-points as locked `zpva`-category
  molecules/calcs (one shared eq + 2·modes per isotopologue); after they finish,
  **Assemble ZPVA** averages the chosen property (NMR shielding / energy / dipole),
  reports isotope shifts vs the base, writes JSON+CSV to `ZPVA/`, and shows a
  table + bar chart. Isotopologues via a text spec (`6:D,8:D ; 6:D`).
- **Workflow editor: Filter node + add-node chooser fix** (same branch) — the
  drop-a-pin "Add node" search now derives its list from the node registry (so new
  nodes like ZPVA/Filter appear) and, when you drag from a port, only offers
  type-compatible nodes (a geometry pin won't suggest Report; a results pin won't
  suggest Optimize). New **Filter node** (`workflow` kind `filter`,
  `filter_matches`): statically subsets which molecules continue downstream by
  filename substring or index range — distinct from Condition (which gates at
  runtime on a calculation's result). Geometry passes through (parent links
  survive), so e.g. optimise all, then NMR only a matching subset.
- **Node-graph refinements** (same branch) — **Optimize now exposes a `results`
  output** as well as `geometry`, so `Optimize -> Report` works (the OPT's
  optimised geometry / final energy / trajectory / gradient are reportable); and
  the add-node chooser is **node-aware for ZPVA** — it only suggests ZPVA
  downstream of a node that is (or traces back to) a Frequencies node
  (`Workflow.traces_to_type`), since ZPVA needs that job's `.hess`. The Expand-time
  freq check stays as the backstop. (Rationale: ZPVA = a post-BO vibrational
  averaging of a property over zero-point nuclear motion — harmonic curvature +
  anharmonic mean-shift — so it requires normal modes, i.e. a Freq job.)
- **Workflow UX: searchable recipes + trimmed palette** (same branch) — the
  per-node "Recipe" picker is now a **type-to-filter** combobox
  (`_recipe_search_combo`; the library is long), and the top palette buttons are
  curated to the common pipeline only (Molecules · Optimize · Frequencies ·
  Property · Condition · Report). Niche/utility nodes (**Filter, ZPVA**) live only
  in the F3 / drag-on-empty search popup (registry-driven, so still complete).
- **Node-editor annotations + auto-size** (same branch) — nodes now **auto-size
  their width to the (bold) title** (`_node_width`, used everywhere the right
  edge / ports / hit-test is computed) so long labels don't overflow. Two new
  `kind:"annotation"` node types (no ports, inert — ignored by validate/expand):
  **`comment`** (a resizable text note, key **`T`**, double-click to edit) and
  **`frame`** (key **`C`** frames the selected nodes — a titled box drawn behind
  them via `_nodes_in_frame` that drags its contents along; double-click the title
  to rename). Both resize by dragging the bottom-right corner (`_resize_handle_at`
  + a "resize" drag mode). Annotations are kept out of the F3 popup (spawned only
  by T/C). Hotkeys are **canvas-scoped** (only fire when the editor has focus —
  standard, and safe vs. typing in fields).
- **Editable SLURM template + DEBUG recipes** (same branch) — the SLURM submit
  script is now editable per-machine via **Settings ▸ SLURM submit script…**
  (`_SlurmTemplateDialog`), stored in config (`slurm_template` key, NOT in project
  files) so changing e.g. `--partition=long`→`short` applies to every built job;
  `slurm.load_template()` prefers the override, falls back to the packaged
  `data/slurm_template.sh`, and editing to match the default clears the override
  so package updates keep flowing. (Design note: per-machine template is the 90%
  case; per-*recipe* SLURM overrides behind an "Advanced" expander would be the
  next layer for per-job-type control — deliberately NOT a parallel "slurm recipe"
  tab + per-calc selector, to avoid complexity creep.) Plus three **DEBUG**
  recipes (HF/STO-3G, 1 core — `data/recipes/debug_*.json`) and
  `examples/gateway_tests/*.json` (pending-SMILES projects pre-wired for the
  two-network / branch+merge / ZPVA topologies) for ~free mechanics testing.
- **TD-DFT node set** (same branch) — excited-state / UV-Vis support, scoped to
  "nodes + recipes + spectra" (no benchmark recreation). `orca_parser.parse_absorption_spectrum`
  reads the ABSORPTION SPECTRUM block (robust across ORCA 5/6 layouts — anchors on
  the first float >1000 = cm⁻¹) → `{state,energy_eV,energy_cm,wavelength_nm,fosc}`;
  a `reporting._x_excited_states` extractor (+ lambda_max/max_fosc CSV cols).
  **No dedicated Excited-States node** — vertical UV-Vis *is* a property, so it's
  the **Property** node + a TD-DFT recipe (the plot button + extractor key off the
  recipe's `calctype`, not a node type; Property's label is now `(SP/NMR/UV-Vis/…)`).
  Excited-state *relaxation* (S1 opt) is an Optimize node with an S1-opt recipe.
  Recipes `uvvis_tddft_pbe0` (vertical), the existing CAM-B3LYP (CT), `es_opt_s1_pbe0`
  (emission/0-0), and a cheap `debug_tddft_hf_sto3g`; and a `UVVisSpectrumWindow`
  (`ui/spectra.py`, nm/eV axis toggle, FWHM, Gaussian-broadened fosc sticks, shared
  hover structure panel) opened via right-click ▸ Plot UV-Vis on a finished TDDFT
  calc or the node's UV-Vis button. (`parse_absorption_spectrum` is a *blind* port —
  verify against a real ORCA 6 TD-DFT `.out` on the gateway.) Gateway test:
  `examples/gateway_tests/uvvis_demo.json`.
- **Plotter overhaul** (`ui/spectra.py`) — IR now stacks multiple molecules like
  NMR (right-click several finished FREQ); both windows share ONE hover-driven
  structure side panel (`_StructurePanel`) instead of per-trace thumbnails; NMR
  x-range opens with a sensible minimum span (not zoomed to one peak); a
  **Maximize** button on both; on-hover sticks skipped in stacked IR.
- **Post-hoc properties from converged calcs** (branch `posthoc-properties`, NOT yet
  merged — gateway-test then merge) — "Q3": more out of a finished job. Four slices:
  (1) **Population charges** — `orca_parser.parse_population` (Mulliken + Löwdin atomic
  charges merged by atom index, + Mulliken spin for open-shell) → `reporting._x_population`
  (auto-appears as a Report-tab checkbox via the `EXTRACTORS` list) + CSV
  `mulliken_min`/`max`. (2) **Mayer bond orders** — `parse_mayer_bond_orders` (the
  "Mayer bond orders larger than…" block, several `B(i-El,j-El):order` per line) →
  `_x_bond_orders`. Both parse the `.out` (no `.gbw` needed) and are BLIND ports —
  verify vs a real ORCA 6 `.out`. (3) **MOREAD-derive** — a derived calc can restart
  from a parent's converged wavefunction: `PlannedCalc.orbital_source="parent:<id>"`
  makes the build inject `! … MOREAD` + `%moinp "<abs parent.gbw>"` via pure
  `inputs.add_moread`; the Derive flow asks "Restart from orbitals? (MOREAD)". Reuses
  the existing geometry-parent `afterok` edge; the derived recipe MUST use the same
  basis as the parent. (4) **Density/MO cubes** — `core/orca_plot.py` (pure: `plot_stdin`
  builds the `orca_plot` wizard keystrokes — verified ORCA 6.0.1 menu integers;
  `parse_output_cube` reads the result filename). Right-click a finished calc with a
  `.gbw` → "Generate density/MO cube…" → density / spin / an MO → `orca_plot` writes a
  Gaussian cube that opens in the external viewer (`open_xyz_3d`). orca_plot runs
  **directly, no sbatch** — it's a light post-processor (confirmed running on the gateway;
  the SCF-engine sanction doesn't apply); on the cluster it's launched in a login shell
  that loads the SAME `module load` lines the SLURM template uses. Tests:
  `tests/test_population.py`, `tests/test_moread.py`, `tests/test_orca_plot.py` (~192).
  **Verified against a real local ORCA 6.0.1** (`C:\ORCA_6.0.1`, water/STO-3G): the two
  parsers vs the real `.out`, `orca_plot` → a real `.eldens`/`.moNa` cube, and
  `add_moread` → ORCA `INITIAL GUESS: MOREAD` converging in 2 cycles. That testing
  caught a real bug — the orca_plot density/spin path needs a `y` ("Is this the one you
  want?") confirm the MO path lacks (now in `plot_stdin`; `_run_orca_plot` also has a
  `timeout` since orca_plot loops forever on EOF). (5) **Multiwfn/molden hand-off** —
  right-click ▸ "Export Molden file (for Multiwfn)…" runs `orca_2mkl <mol> -molden`
  (shared `_orca_aux_command` launcher, no sbatch) → `<mol>.molden.input`; verified
  locally. LEFT for the gateway (environment-only): the cluster `bash -lc`+`module load`
  launch finds orca_plot/orca_2mkl, and the SLURM `.gbw` copyback.
- **EPR (g-tensor + hyperfine)** (branch `posthoc-properties`, stacked after Q3) — the
  open-shell counterpart to NMR. `orca_parser.parse_epr` (electronic g-tensor + per-
  nucleus hyperfine A, MHz) → `reporting._x_epr` (Report checkbox + CSV g_iso /
  n_hyperfine_nuclei / max_abs_A_iso_MHz; parser also captures nuclear spin I).
  `core/epr.py` simulates BOTH an **isotropic** solution spectrum (`simulate`) and an
  **anisotropic powder** spectrum (`powder_spectrum`: orientation-averages the
  principal g + A tensors over a θ/φ grid → first-derivative lineshape; assumes
  coincident g/A frames + first-order resonance). Multiplet splitting is general-spin
  (`_spin_multiplet`: binomial for ½, 1:1:1 for ¹⁴N, …). `ui/spectra.EPRSpectrumWindow`
  has an **isotropic/powder mode toggle** + MW-freq/linewidth controls; wired to a
  Calc-tab right-click "Plot EPR spectrum", a workflow-node **EPR** button, and the
  Property node (EPR is a Property, no new node type). Recipes `epr_g_hfc_b3lyp` +
  `debug_epr_uhf_sto3g` (coords MUST precede `%eprnmr`; `Nuclei = all { aiso, adip }`).
  Gateway demo `examples/gateway_tests/epr_demo.json` (methyl radical doublet).
  **Verified vs real local ORCA 6.0.1** (methyl: g_iso 2.0026, 1:3:3:1 ¹H quartet +
  anisotropic ¹³C tensor [132,132,356] MHz). Tests `tests/test_epr.py` (~209 total).
- **Text-box UX + Interrupt button** (branch `posthoc-properties`) — (a) the node
  editor's recipe search combo no longer loses focus per keystroke (it stopped
  re-posting the dropdown, whose async focus grab beat `focus_set`; now it just
  narrows `cb["values"]`). (b) **Undo/redo app-wide**: `shortcuts.py` binds Ctrl+Z/Y
  via Tk *class* bindings — `tk.Text` uses native `edit_undo`; `ttk.Entry`/Spinbox
  (which have none) get a small per-widget undo stack that coalesces a typed word
  into one step. (c) **Word motion/selection**: Ctrl+←/→ and Ctrl+Shift+←/→ stop at
  word↔delimiter boundaries (word = `[A-Za-z0-9_]`; the rest are stops); pure
  `next/prev_word_boundary` helpers are unit-tested. `install_text_shortcuts` slimmed
  to just enabling undo (keys are class-wide) to avoid double-fire. (d) **Interrupt
  button** (`calculations_tab`, cluster only — local keeps its Stop): a red two-stage
  cancel that appears once calcs are submitted and floats centred between Build and
  the right cluster. Disarmed (muted) until the queue's been queried this session; a
  first press runs Refresh status and, if jobs are active, arms (red); an armed press
  confirms + `slurm_runtime.cancel_jobs` (one `scancel`). `_status_known` gates
  arm/disarm (False on submit/open, True after a status query). Chose Stop/Cancel over
  Pause/Resume: a normal user can't suspend a running SLURM job, only kill it, and
  cancel isn't reversible. Tests `tests/test_shortcuts.py`, `tests/test_cancel_jobs.py`
  (~219 total).
- **Workflow tab: Refresh + node alignment** (branch `posthoc-properties`) — (a) a
  **Refresh status** button re-queries SLURM (via the Calc tab) and rebuilds the
  node→calc map from each calc's `origin_node` (`_remap_node_calcs`), then redraws +
  rebuilds the config panel — so after jobs finish, node colours update and the
  per-node plot/viewer buttons appear (works after a reopen too, no re-expand needed).
  (b) **Blueprint-style tidy hotkeys** (canvas-scoped): **Q** straightens the selection
  (aligns vertical centres onto one line), **Shift+W/S/A/D** align top/bottom/left/right
  edges (`_align_selected`/`_straighten_selected`). Bound to uppercase keysyms (= Shift
  held) so Ctrl+A still selects all.
- **Workflow/Report polish + node locking + benchmark removal** (branch
  `posthoc-properties`) — a large UX batch: (a) **node-graph undo/redo** (Ctrl+Z/Y,
  canvas-scoped) via whole-graph snapshots at the single `_commit()` chokepoint (a drag
  commits once at release = one step; `refresh()` only wipes history on a real graph
  reload). (b) **Q now truly straightens** — moves each downstream node so its input pin
  aligns to the upstream output pin's height (`_port_xy`), left-to-right, so wires run
  straight. (c) **Node locking**: once a node has launched calcs (`_node_is_locked` via
  `origin_node`), its recipe combo disables and it can't be deleted (Delete keeps
  protected nodes + removes the rest); Report nodes are exempt (aggregate-only, stay
  editable/re-runnable). (d) **Report node** gets the Report tab's property checkboxes
  (`node.config['extractors']`, None=all) → `_report_specs` → `_generate_pipeline_reports`.
  (e) **Report tab**: the extractor list is now a fixed-height scrollable box so the
  Generate button can't be pushed off-screen. (f) toolbar consistency: removed **Clear**,
  greyed **Generate only**, renamed **Refresh** (both tabs); OPT nodes get a **Traj**
  button (`_trj.xyz`). (g) on-canvas help trimmed to a one-liner → **Help ▸ Node graphs**
  scrollable popup (bulleted guide + UE5-Blueprint/KNIME trivia). (h) **auto-refresh** the
  Workflow view on project open. (i) **removed the Benchmark tab** (redundant with
  Workflow + Calc-derive; was flagged experimental). ORCA-restart check (local): a
  cancelled OPT can resume from the last `_trj.xyz` geometry + MOREAD in ~half the cycles —
  a "Resume from last geometry" feature is viable (not built; Interrupt stays cancel-only).

## Open work / TODO
- Possible: a "Resume from last geometry" action (rebuild an OPT from `_trj.xyz` last
  frame + MOREAD) — verified locally it converges in ~half the cycles vs from scratch.
- Possible: an "add for all molecules × selected recipes" button on the Calc tab if the
  removed Benchmark tab's matrix convenience is ever missed.
- Optional: PyMOL support for a *local* (Windows) machine (gateway has molden only).
- The plotter overhaul (below) was a blind refactor (no GUI here) — worth a visual
  pass on the gateway: stacked IR, the shared hover structure panel, NMR margins,
  maximize button. Ping if anything renders oddly in ThinLinc.

## Origin / sibling project
This app grew out of a **ZPVA** study (¹⁹F isotope shifts of deuterated
4-fluoroimidazole) in `C:\Users\chris\Documents\Claude\Fim_NMR\` on the dev
machine. Stage 1 concluded: deuteration gives position-resolved, additive ¹⁹F
shifts (48–351 ppb, all above the 0.03 ppm floor). The reusable tooling from it
(`hess_tools.py`, `assemble_zpva.py`, `build_zpva_project.py`) has now been ported
into `core/hess.py` + `core/zpva.py` and exposed as the **ZPVA workflow node**
(see Recently built) — so the study's pipeline runs end-to-end inside the app.
