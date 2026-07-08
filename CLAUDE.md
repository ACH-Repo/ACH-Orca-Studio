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
  anisotropic ¹³C tensor [132,132,356] MHz). Tests `tests/test_epr.py`.
  The EPR window is now MULTI-molecule (stacked colour-matched traces + on-hover
  g-value, like IR), with a MW-band dropdown (L/S/C/X/K/Q/W — no "Z"), an
  absorption/1st/2nd-derivative selector, and shared `_AxisLimitControls` (x0/x1/y0/y1
  on every plotter). **ENDOR** (`ENDORSpectrumWindow` + `core/epr.endor_lines/
  endor_spectrum`) reuses the SAME hyperfine data — lines at |ν_n ± A/2|, no new calc —
  via a Calc-tab right-click / node ENDOR button; gated on resolvable A (STO-3G≈0 →
  prompts for the B3LYP recipe). (~227 tests.)
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
- **Workflow config-panel scroll + global hardware defaults + polish** (branch
  `posthoc-properties`) — (a) the **node settings panel is scrollable** (canvas +
  scrollbar; wheel bound on every child so it scrolls anywhere, not just on the bar),
  fixing Property/Optimize panels cut off at the bottom; the Report node's extractor
  checkboxes now pack straight into it (no nested scroll). (b) a **locked** node shows
  its recipe as a read-only "`<recipe>` (locked - node has run)" label, not a greyed
  combobox. (c) Workflow **Refresh (F5)** — F5 is now context-aware in `app._on_f5`
  (Workflow tab → `wt.on_refresh_status` in place; else Calc tab), and the workflow
  refresh queries SLURM **directly** (updates `ct._squeue_states` without calling the
  Calc tab's refresh) so it no longer switches tabs. (d) **Traj** tooltip notes it's a
  multi-frame `.xyz` (molden/Avogadro/PyMOL/VMD). (e) help-popup bullets rewritten terse.
  (f) **Global hardware defaults** — Settings ▸ *Default cores per job* / *Default
  memory per core (MB)* (config `default_cores`/`default_maxcore_mb`, 0=off) override
  every built job's `%pal nprocs` / `%maxcore` in `_build_one` before the SLURM core
  count is derived, so you set PC specs once instead of per recipe. `inputs.set_maxcore`
  added; tests in `tests/test_hardware_defaults.py`.

- **Zoom fonts, X11 wheel scroll, Lock column** (branch `posthoc-properties`) —
  (a) node text scales with zoom (`_fs` floor 1pt not 5; node widths are sized for the
  unscaled font, so a 5pt floor overflowed the box on zoom-out). (b) `shortcuts.
  bind_mousewheel(widget, canvas)` — binds the whole subtree for BOTH `<MouseWheel>`
  (Win/Mac) and `<Button-4/5>` (X11/ThinLinc), fixing "wheel only scrolls over the
  scrollbar" in the node settings panel, Report tab list, and Node-graphs popup (Tk
  wheel events don't bubble, and X11 doesn't use `<MouseWheel>`). (c) node settings
  panel sash set to ~72% on `<Map>` so it's a narrow side panel. (d) **Molecules Lock
  column** — `Molecule.locked` (+migration); a "Lock" column ([x]/[ ]) after Q/M,
  **Ctrl+L** toggles the selection, locked rows grey (tag "locked") and are removal-
  protected; click/drag the Lock cells to flip (drag-paint). (e) the Report node's
  property checkboxes get the same press-and-drag flip. Decision (with the maintainer):
  keep hardware settings **explicit in the recipe + a global manual override** (built
  earlier), NOT `!!##DEFAULT_RAM##!!`-style placeholders — explicit recipes stay self-
  documenting/portable; the override is the one-place knob.

- **Molecules-tab UX batch** (branch `posthoc-properties`) — four hand-entry fixes.
  (a) **Add is now row-first**: `on_add` creates one real, selected, editable row per
  press and focuses the Name field (select-all'd, so the first keystroke replaces the
  default filename-name) — no more "first press just deselects, second press adds". A
  freshly-added row keeps the draft-mode SMILES→charge/mult auto-fill via a
  `_autofill_row` tracker (cleared when you navigate away; follows a filename rename),
  so committing early doesn't lose the auto-fill. The blank-form draft still exists for
  the empty state (type-then-Add commits it). (b) **Shift+arrow selection follows the
  cursor**: some Tk builds only re-scroll a ttk/classic Entry after a plain cursor move,
  not after `Extend`, so an extending highlight ran off-screen. `shortcuts._entry_see_insert`
  (a Python EntrySeeInsert) is called after `Ctrl(+Shift)+arrow` word moves and, via a
  **synchronous** `_entry_see_after` bound to `<Shift-Left/Right/Home/End>`, after native
  selection too. **ThinLinc note**: the first cut scrolled one char per loop iteration
  (O(distance)) — fine locally, but hundreds of `xview`+redraws per keypress lagged out
  over the remote framebuffer so the highlight never appeared to follow; now it's ONE
  `xview` jump (pin cursor to the near edge). (c) **Paste an XYZ file string to add a
  molecule**: `on_paste_smiles`
  first tries `coords.parse_xyz_frames_text` (refactored out of `_read_xyz_frames`, needs
  a leading atom-count line so a SMILES list is correctly rejected) — if it's XYZ it adds
  imported, `coords_locked` molecules straight from the clipboard (`_add_xyz_structures`),
  else the SMILES dialog opens as before. This avoids the old failure where XYZ atom lines
  were mistaken for one-atom SMILES. (d) **Reorder rows**: press-drag a row, OR **Alt+Up/
  Down** on the selection (`_move_focused`). Both gated to `_sort_col is None`; drag
  suppresses the Treeview's native band-select and coexists with the lock-cell click. On
  drop `_renumber_molecules` reassigns filenames 000.. top-to-bottom (the fixed %03d
  convention — clobbers custom/imported names by design; user re-edits after), two-phase
  renaming the `XYZ_INI/*.xyz` on disk and repointing planned-calc FKs. **Guarded**: if
  any calc is already exported/submitted (`exported`/`job_id`/`rundir`), renumber is
  skipped (those are keyed to the on-disk filename) and only the row ORDER changes.
  **ThinLinc note**: the first cut re-`move`d + `see`d the row on every `<B1-Motion>` (a
  redraw storm the remote framebuffer choked on, and remote X coalesces/drops motion), so
  drag "didn't work" over ThinLinc; now the reorder happens ONCE on release from the drop
  Y (needs only press+release, no motion stream), and Alt+Up/Down is the keyboard fallback
  that's reliable regardless. Tests: `parse_xyz_frames_text` in `tests/test_coords.py`
  (233 total).

- **Plotter foundation: `BaseSpectrumWindow`** (branch `posthoc-properties`, `ui/spectra.py`)
  — the five spectrum windows (IR/UV-Vis/NMR/EPR/ENDOR) were five ~250-line `tk.Toplevel`
  clones with no shared base, so every chrome fix was 5-place whack-a-mole and they'd
  drifted. Replaced with ONE `BaseSpectrumWindow` that owns all chrome; each subclass is now
  ~data-prep + `add_controls(bar)` + `plot(ax)` (+ optional `add_summary`/`after_plot`/
  `_on_motion`). What the base centralises / what it fixed:
  (a) **Non-modal** window (dropped `make_modal` for the plots) → real WM decorations incl.
  the **standard maximize button** (the custom "Maximize" did nothing on ThinLinc's WM —
  `state("zoomed")`/`-zoomed` unsupported); the app also stays usable and multiple plots can
  be open. (b) **Embedded matplotlib `NavigationToolbar2Tk`** (Home/Pan/Zoom/Save) below the
  plot + a **slim top control row** (plot-specific widgets only) → fixes the horizontal
  cut-off / too-many-buttons (replaced the custom axis-limit boxes + Save + Maximize).
  (c) **Stack y-offset slider** (shown when >1 trace; `baseline(i, ref)` = `i·frac·ref`) for
  a waterfall of any stacked plot, not just EPR. (d) **Colour-matched hover structure border**
  (`_StructurePanel` wraps the image in a `tk.Frame` whose `highlightbackground` = the trace
  colour). (e) **EPR/ENDOR line markers now draw per-trace even when stacked** (were gated
  `not _stacked`), each scaled to its own trace peak, at its stacking baseline; EPR/ENDOR
  hover adds `m["_base"]` so the y-match still works when offset. (f) **IR stick heights**:
  the broadening kernels are peak-normalised (peak value 1.0), so an isolated line's curve
  peaks at exactly its intensity — sticks now draw at height = raw intensity, not
  `summed_ymax·frac` (which overshot wherever two lines overlapped). Dropped the per-window
  `make_modal`/custom Save/Maximize; `_save_figure`/`_ask_image_format` kept (unused, MPL
  toolbar does Save now). Public constructors unchanged (callers in `calculations_tab.py`).
  BLIND refactor (no GUI here) — headless-smoke-tested all 5 (construct/redraw/offset/mode
  toggles/markers/hover incl. single-mol), but wants a visual pass on ThinLinc.
- **Plotter navigation is all-keyboard now** (same branch) — the embedded matplotlib
  NavigationToolbar was **removed**: over ThinLinc the Linux desktop panel draws on top of it
  and it's unreachable (and the window wouldn't rescale to expose it). Replaced with hotkeys
  in `BaseSpectrumWindow` so ALL five plots inherit them: **Z** cycles zoom horizontal→vertical→box,
  **P** cycles pan horizontal→vertical→free (custom handlers on mpl button/motion/release; zoom
  uses a **blitted** rubber band = one snapshot + re-blit per move, no full redraw, ThinLinc-safe;
  pan preserves reversed axes via `_set_axis`), **Esc** exits a mode (a live `_mode_label` shows
  the active one), **F** two-stage reset, **M** focuses the x-limit box, **R**/**F5** redraw,
  **Ctrl+S** save (`_save_figure`), **Ctrl+W** close. Plain keys bind on the canvas widget (fire
  only while the pointer's over the plot, so they don't disturb typing in the top fields);
  Ctrl/F5 bind window-wide. Also: **Enter activates the focused Button/Checkbutton**
  (`_activate_focused`; Tk only does Space by default), and the right-hand controls are created
  in tab order **offset → Redraw → Close** but packed to read L→R (Tk tab order follows creation
  order, which is why Close used to tab before Redraw). `_disable_mpl_keymap()` drops mpl's
  default f/p/o/g keys. Single connected `_on_motion` dispatcher routes to an active drag else
  the subclass `_hover` (was `_on_motion`). BLIND — headless-smoke-tested (zoom cycle, box-zoom
  drag sets both limits, pan shift, reversed-axis preserved, F/Esc/R, all 5 windows).
- **Plotter refinements** (same branch, on top of the foundation) — from a ThinLinc review:
  (a) ~~matplotlib toolbar embedding~~ (superseded — toolbar removed, see above). (b) **EPR/ENDOR
  common x-range** — each trace was simulated over its
  own narrow field/RF window around its own B0, so at high bands (W) different g's gave
  non-overlapping windows and fragmented lines; the UI now computes a shared min/max (+~10%
  tolerance) and extends every trace flat at its baseline across it (no core change). (c) EPR
  **g_iso folded into the colour-coded legend** (`label="000  g=2.0024"`, `loc="best"`) —
  removed the permanent top-left g-list box that would collide with offset traces. (d) a
  compact **x/y limit-box row** + **Mestrenova keys over the plot**: `F` two-stage reset (X to
  the data view, then Y — clears the boxes; captured as `_home_xlim/_ylim` pre-override), `M`
  focuses the x0 box, `Z`/`P` toggle mpl zoom/pan (hold x or y while dragging = one axis;
  full h/v/box *cycling* not yet wired). Base `_disable_mpl_keymap()` drops mpl's default
  f/p/o/g keys so ours aren't shadowed. TODO/asked-for: **blitting** the hover (currently a
  full `draw_idle()` per mouse-move — a ThinLinc cost) and the full Z/P axis cycle.
- **Plotter phase-A quick fixes** (same branch, committed first) — calc-tab right-click menu
  now **dismisses on click-away** (deferred `menu.grab_release()` to `<Unmap>` instead of
  firing it right after `tk_popup`, which on X11 left the menu posted-but-ungrabbed so it
  only closed on an item click); and the structure hover border (carried into the base).

- **Rebindable hotkeys** (branch `rebindable-hotkeys`, off `main` after 1.3.0) — a
  Settings ▸ **Keyboard shortcuts…** dialog to view/rebind/reset the app's hotkeys, the
  "why don't more programs do this" feature. Foundation is a pure registry `core/keymap.py`:
  action_id → {category, label, default Tk sequence}, with per-user overrides persisted in
  config under `"keymap"` (setting a value == the default clears the override, so package
  defaults keep flowing). Helpers: `sequence(id)` (override-or-default), `set_override/reset/
  reset_all`, `humanize` (`<Control-Shift-n>`→`Ctrl+Shift+N`), `event_to_sequence(state,
  keysym)` (capture a keypress → Tk sequence; None for bare modifiers), `sequence_variants`
  (binds BOTH cases of a single-letter key — incl. WITH modifiers: `event_to_sequence`
  lower-cases the captured letter → `<Control-Shift-m>`, but a real Ctrl+Shift+M keypress
  reports keysym **`M`**, so Tk only matches the upper-case pattern; the first cut skipped the
  case-expansion for modified sequences, so any Shift+letter rebind silently did nothing —
  now fixed + tested), `conflicts` (same-
  category, normalised compare — cross-category app-vs-plot overlap is allowed since they're
  different windows). UI: `ui/keybindings.KeybindingsDialog` (scrollable, grouped, click-to-
  capture, Reset/Reset-all). Wiring: app-wide shortcuts (New/Open/Save/Add-by-name/Import/
  Refresh) route through `App._install_global_shortcuts`/`apply_global_keymap` — the dialog's
  `on_change` re-binds them **live** (unbind old seq on the `all` tag, bind new). Plot-window
  keys (`BaseSpectrumWindow._bind_action` reads the keymap in `_bind_plot_keys`) apply to
  **newly opened** plots. Catalogue registered so far: the 6 app globals + 7 plot keys (reset/
  limits/zoom/pan/redraw/save/close); per-tab tree keys (Ctrl+L, Delete, …) can be added to
  the registry later. Tests `tests/test_keymap.py` (registry/override/humanize/event/variants/
  conflicts, fake-config injected so no real `~/.orca_workbench.json` write). 241 tests.
- **Editor round-trips** (branch `editor-roundtrips`, off `main`/1.3.0) — external-editor
  editing launched from the **Molecules tab only** (the prepping phase; everywhere else stays
  read-only so a finished calc can't be mutated). Two independent round-trips:
  (a) **SMILES via a 2D editor** — the Structure panel's **"Edit in ChemDraw..."** button
  writes the current SMILES to a temp `.mol` (`roundtrip.write_smiles_molfile`, RDKit
  `Compute2DCoords`; empty SMILES → blank canvas to draw from scratch), launches the editor,
  and a non-modal `EditRoundtripDialog` waits; **Import** reads back whatever the editor saved
  (`roundtrip.newest_structure_file` picks the newest structure file in the temp dir, so it
  copes with ChemDraw choosing `.cdxml` over the `.mol`), converts to canonical SMILES
  (`read_structure_smiles`: RDKit for `.mol/.sdf`, OpenBabel/pybel for `.cdx/.cdxml/.mrv`),
  and on a confirmed diff sets `smiles_var` (→ `_on_field_change`: updates the mol, redraws
  the depiction, invalidates a generated geometry, re-fills charge/mult). **SMILES only** —
  never the geometry. (b) **Geometry via Avogadro** — double-clicking a molecule row now opens
  its `.xyz` in a LOCAL Avogadro to edit (`_edit_geometry`); Avogadro saves in place, and
  **Reload** (`_reload_geometry`, dialog stays open for iterate-reload) re-reads it, refreshes
  the preview, and **locks the coords** (`coords_locked=True` + a provenance note — a
  hand-edited geometry mustn't be clobbered by SMILES regen). Falls back to the read-only
  viewer (`open_xyz_3d` → OpenXyzDialog/molden) when no local Avogadro (the gateway). Editor
  paths: `config` keys `structure_editor_path` (auto-detects ChemDraw via `_CHEMDRAW_CANDIDATES`,
  else asks) and the existing `avogadro_path`; both settable via **Settings**. New pure module
  `core/roundtrip.py`; tests `tests/test_roundtrip.py` (242 total). **Verified locally** (real
  RDKit round-trips incl. carboxylate; ChemDraw + Avogadro both launch; UI import/reload/resolve
  smoke). The human draw-and-save step wasn't automatable — needs a real ChemDraw save to
  confirm the `.cdxml`/`.mol` read-back end to end.
- **Editor round-trips — UX pass** (same branch, from a local ChemDraw/Avogadro test) —
  (a) **row double-click = VIEW-only** again (`_view_geometry`); the geometry **edit** round-trip
  moved to a **right-click menu** (`_on_row_right_click`: View geometry / Edit geometry / Edit 2D
  structure) so browsing rows never pops the never-self-closing reload dialog. (b) **double-click
  the depiction image** opens the 2D editor (intuitive; the button stays, renamed **"Edit 2D
  structure..."**, product-neutral). (c) **abstract program paths**: new `extprog.PROGRAM_SLOTS`
  (`viewer_3d_path`, `editor_3d_path`, `editor_2d_path`, `text_editor_path`) with a **fallback
  chain** (`program_path()`: view/edit-3D default to the SAME program and to legacy
  `avogadro_path`/`structure_editor_path`) and ONE **Settings ▸ External programs** dialog
  (`ExternalProgramsDialog`) replacing the per-program menu lines — ORCA exe kept separate. These
  path targets are the main future-extension point (JMol/PyMOL/Marvin = just set the path). (d)
  geometry edit now **flags SMILES as possibly stale** (can't re-derive SMILES from coords) — a
  once-per-molecule note + a locked-coords preview caveat. (e) removed the redundant **top-level
  Recipes menu** (Reload/Add folder/Manage folders are all buttons on the Recipes tab). Still
  needs a real ChemDraw draw-and-save pass; ChemDraw's own "save as Original/CDXML" prompts are
  unavoidable (non-native `.mol`).
- **Trajectory viewer path** (same branch) — a **5th program slot** `traj_viewer_path`
  (fallback → `viewer_3d_path`) for opening an optimisation **`_trj.xyz`** as a movie
  (Calc-tab "Open trajectory", node **Traj** button); `open_xyz_3d` gained a `slot=` arg.
  PyMOL animates trajectories best — **verified locally**: a real ORCA HF/STO-3G water OPT
  writes `_trj.xyz` and `PyMOLWin.exe <trj>` loads/steps the frames. NOTE for PyMOL 3 (conda
  installer): point at **`PyMOLWin.exe`**, NOT the generated `PyMOL.bat` — the .bat is a conda
  activation wrapper that ends `... PyMOLWin.exe` with **no `%*`**, so it drops the file
  argument (opens PyMOL empty).

- **Geometry constraints + relaxed surface scans** (branch `relaxed-scans-constraints`,
  off `main`) — roadmap #2, COMPLETE (Calc tab + Workflow node + scan plot). ONE unified
  "geometry spec" for OPT jobs,
  rendered into ORCA's `%geom` block: **constraints** (freeze bond/angle/dihedral `{B a b [val] C}`
  / `{A …}` / `{D …}`, or a Cartesian position `{C a C}`, optionally pinned at a value) and/or
  **one relaxed scan** (`Scan\n <B/A/D> a b = r1, r2, N\n end` → energy profile). Pure
  `core/geomspec.py` (spec dict → `build_geom_inner`; `validate`/`describe`/`coord_describe`;
  atoms 0-based) + `inputs.add_geom_block` (injects after the `!` line, or splices sub-blocks into
  an existing `%geom`). Stored on **`PlannedCalc.geom_spec`** (+migration), injected in `_build_one`
  after MOREAD. UI: Calc-tab **right-click ▸ "Geometry constraints / scan…"** → `GeomSpecDialog`
  (`ui/geomspec_dialog.py`, reusable — shows the molecule's atom list for index reference; warns if
  the recipe isn't OPT). **Chained constrained opts** come for free (Derive/Optimize→Optimize passes
  geometry, each calc has its own spec) — incl. the carboxylate "distance floor" via freeze-at-floor
  → release → re-opt (ORCA constraints are hard freezes, no native inequality; the chain approximates
  it). **Verified against real local ORCA 6.0.1**: a constrained OPT held an O–H bond at exactly
  1.1000 Å; a relaxed scan produced the expected coordinate→energy surface + `.relaxscanact.dat`.
  Phase 2: the **Optimize node** carries the same spec (config-panel "Constraints / scan…" button →
  same dialog, shows the FIRST molecule's atoms for reference; the expand factory reads
  `node.config['geom_spec']` — no signature change — and sets it on each calc, unfinished calcs
  adopt graph edits). **Scan-energy plot**: `orca_parser.parse_relaxed_scan` (the "Calculated
  Surface" table in the .out) / `parse_relaxed_scan_dat` (the `.relaxscanact.dat`), shown by
  `plot_window.ScanPlotWindow` (ΔE kcal/mol vs the scanned coordinate, min marked; absolute-Eh
  toggle) via Calc-tab right-click **"Plot scan energy profile"** on a finished OPT whose .out has a
  scan surface. Tests `tests/test_geomspec.py` (263 total, incl. scan parsers).
- **Skins / Styles** (branch `relaxed-scans-constraints`, roadmap #3 "styling
  intermission") — re-skin the whole app, live. NOT a notebook tab: a right-aligned
  **Styles...** button on the top toolbar (next to "Show tooltips") opens the gallery
  as a non-modal dialog (`App.on_open_styles`) — appearance isn't part of the
  fundamental pipeline, and ttk can't right-align a single notebook tab. Pure
  registry `core/theme.py`: ordered skin dicts (`id`, `label`, `tagline`, `ttk_base`,
  flat palette) + `active_skin_id`/`set_active_skin_id` persisted in config under
  `"skin"` (per-user, never in project files). Four skins: **Default** (native, a
  deliberate no-op so a fresh launch is pixel-identical to before), **Dark** (neutral
  VS-Code-ish), **Frutiger Aero** (bright glassy sky-blue + fresh green — the "make it
  brighter" brief), **Boombox** (Winamp brushed-metal + green LCD accent). The applier
  `ui/theming.py` solves Tkinter's two-worlds problem: **ttk** widgets restyle globally
  by switching the base theme to `clam` and configuring styles (no tree walk needed);
  **classic tk** widgets (Text/Listbox/Canvas/Menu) are recoloured by (a) seeding the
  option DB for future widgets/dialogs and (b) a recursive tree walk. The walk is
  **conservative** — a classic widget's colour is only overwritten if it's currently one
  we "manage" (a probed native default, or a colour some skin paints), so intentionally
  coloured widgets survive every skin: the workflow Run/Generate/Submit buttons, the red
  DECONSTRUCT/DELETE, LCD-style fields. **Contrast rule** (from user testing): the
  fg/selection/caret repaint is GATED on the widget's bg being managed too — a widget
  keeping a custom pale bg (amber Run, blue Refresh set bg= but not fg=) must also keep
  its own fg, else dark skins painted light text onto light custom buttons. `theme.TAG_NAMES` covers every Treeview lifecycle
  row tag (Molecules/Calc/Recipes) so status rows stay legible on dark skins (re-tinted
  per-Treeview in the walk). Tooltip colours via `tooltip.set_colors`. Wiring: `App.
  apply_skin` (Styles tab + startup) repaints + persists; `_apply_startup_skin` runs only
  when `not is_simple()` and skips the default (keeping the native path untouched); lazy
  tabs re-run the applier on first build so late widgets get themed. The Styles tab shows
  a card per skin with a live mini-preview canvas (mock window: titlebar/surface/selected-
  row/button). **NOT built in `--simple`/gateway mode** (styling is off the low-latency
  path, per the roadmap). Node-graph canvas + matplotlib plots keep their purpose-built
  colours (only their backdrop themes). **Verified locally** (real ORCA-workbench GUI on
  Windows): all four skins apply with no exception, semantic button colours preserved,
  default stays native. Tests `tests/test_theme.py` (registry/keys/tags/roundtrip, fake
  config; 274 total). Reference: `Ngram Game`'s CSS-variable skins (aurora=Sonique,
  boombox=Winamp) — same idea, re-expressed for ttk. Still wants a ThinLinc visual pass.
- **Transform + Combine workflow nodes** (branch `relaxed-scans-constraints`) — geometry
  BUILDING in the node graph: append mol A to mol B after rigid moves. Pure
  `core/transform.py` (numpy): translate/rotate (Rodrigues, centre = centroid/COM/atom/
  point), `align_axis` (2-atom axis → x/y/z, atom i anchored), `align_plane` (3-atom
  face normal → axis), `align_principal` (inertia tensor, long axis → order[0]),
  `set_dihedral` (D a b c d → angle; covalent-radii bond graph partitions the d-side;
  raises on ring bonds) — plus `combine` (concat fragments), and the **ops-list
  interpreter** (`apply_ops`/`validate_ops`/`describe_op`, JSON-able dicts, 0-based
  atoms like geomspec). Between-mol alignment is COMPOSITIONAL: align each mol's chosen
  axis/plane to the same lab axis, then Combine — keeps every node 1-in-1-out.
  Graph model: `compute_streams` (core/workflow.py) resolves the molecule stream each
  node emits (filter subsets, transform/combine REPLACE it via an injected
  `transform_apply` backend — pure/testable); `expand_to_calcs` reworked onto streams
  (per-(node,mol) calc map; old semantics kept, 30 pre-existing tests untouched).
  **Combine's geometry input is variadic** (`fan_in` in NODE_TYPES; can_connect allows
  multi-wire), merge or **pairwise/zip** mode (broadcast len-1 streams — solvate n
  solutes with the same water), charge=sum / mult=Σ(2S)+1 defaults with overrides.
  UI (`workflow_tab`): palette + F3 entries, sand-coloured kinds, ops-list editor panel
  (`ui/transform_dialog.TransformOpDialog`, shows the incoming molecule's atoms
  0-based), and **Preview output (3D)** on both panels = the "run until here" debug
  view (computes the chain IN MEMORY, writes temp xyz, opens the external viewer —
  project untouched; no separate Debug node needed since geometry nodes are static).
  On real expand the backend materialises derived molecules only AFTER the user
  confirms (`_flush_geom_materialisations` → `TRANSFORM/<name>.xyz` + locked Molecule
  rows, method="transform", stable names `<mol>_tf<node4>`/`<name>_cb<node4>` reused on
  re-expand; skip+warn if calcs already built/submitted on the old coords).
  **v1 placement rule** (validated): Transform/Combine read each molecule's CURRENT
  .xyz at expand time → must sit BEFORE calc nodes (validate() blocks a calc upstream).
  Transforming an OPTIMISED geometry = phase 2 (needs finished-calc geometry reads).
  Tests: `tests/test_transform.py` (16, incl. dihedral round-trip + ring guard),
  `tests/test_workflow_transform.py` (11, fake backend). Headless-smoke: full app
  build→wire→streams→materialise→expand on real xyz files.
- **Live progress plot overhaul** (branch `relaxed-scans-constraints`) — four user
  pain points fixed. (1) **SCF history survives opt cycles**: new
  `orca_parser.parse_scf_blocks` returns EVERY SCF block (D-I-I-S → S-O-S-C-F handover
  = one block; stop-marker then new header = new block); `parse_orca_output` grows
  `scf_blocks` (old `scf_iterations` = last block, back-compat). **Verified vs real
  local ORCA 6.0.1** (water HF/STO-3G TightOpt: 7 blocks [11,9,7,5,4,3,2] = 6 cycles +
  the stationary-point re-eval, block tails match the FINAL SINGLE POINT energies).
  `LivePlotWindow` now draws up to 3 panels — opt energy / **cumulative SCF history
  with dashed cycle boundaries** / gradients — panel set data-driven (running SP = one
  full-height SCF panel). (2) **Navigation hotkeys** (same rebindable keymap actions as
  the spectrum windows, read at open): Z/P cycle zoom/pan (multi-PANEL: drag acts on
  the axes under the cursor; blitted rubber band), Esc, F two-stage reset (all-X then
  all-Y), R/F5 refresh, Ctrl+W close — and a **manual zoom now survives auto-refresh**
  (off-home limits are re-applied after each poll redraw). (3) **Save image** button +
  Ctrl+S (reuses `spectra._save_figure`). (4) **Iteration timing**: a second status
  line shows ~s/SCF iteration + ~s/opt step + "last new data Ns ago", measured from
  poll arrivals while the window is open (ORCA doesn't timestamp iterations, so
  pre-open history can't be timed — labelled honestly). (5) **View current geometry
  mid-OPT**: window button + Calc-tab right-click "Open current geometry (last opt
  step)" on an unfinished OPT — extracts the LAST `_trj.xyz` frame to a temp xyz →
  external viewer. Works for local runs; on the cluster the trj sits on node-local
  /scratch until copyback (the button explains). Callers pass `app`+`trj_path`
  (`_expected_trj`). Tests `tests/test_scf_blocks.py`; window smoke-tested headless on
  the real water .out (panel sets, zoom-persistence, F, trj extraction). 305 tests.

- **Node-editor UX batch 2 + transform ops round 2** (branch `relaxed-scans-constraints`,
  from Christian's hands-on test) —
  (a) **Wire UX like real node editors**: dropping a wire on an occupied single input
  REPLACES the old connection (restored if the new one would cycle); dragging a
  CONNECTED input pin picks the wire up (re-plug to rewire, drop on empty space to
  DELETE — never opens the add-node popup); dropping an isolated 1-in/1-out node onto
  a wire SPLICES it in (`_maybe_splice_at_drop` + `_edge_near` polyline hit-test).
  (b) **Keys**: J / L / K cut the selection's input / output / all wires (vim-ish);
  **connect moved J → V**; Alt+A clears the selection; **Ctrl+C/Ctrl+V copy/paste
  nodes** (internal wires kept, new ids, offset accumulates per paste; plain C/T/V
  still frame/comment/connect — Tk prefers the more specific Control- bindings);
  **Q now also distributes horizontally** (even gaps, `_distribute`), and right after
  Q / Shift+WASD the ARROW keys tune the gaps (Left/Right horizontal, Up/Down
  vertical, `_align_ctx`; any click ends it — deliberately NOT hold-key-based, since
  X11 auto-repeat over ThinLinc makes hold detection unreliable). Arrows otherwise
  still pan. (c) **F3 search aliases** — "align/rotate/mirror/..." finds Transform,
  "merge/dimer/..." Combine. (d) **Transform ops round 2** (core/transform.py):
  `mirror` (xy/yz/xz, optional center, improper-op caveat), **rotate about an
  atom-pair axis** (`rotate_about_atoms`, position-invariant, `axis_atoms` in the op;
  the dialog's axis field takes x/y/z, a vector, or 'i j'), **center anchored on an
  atom or an i-j midpoint** (`anchor_point`, op `atoms` key) — enables the
  align-bond→set-dihedral→center-on-bond-midpoint→shift→mirror dimer recipe (tested
  end-to-end in test_transform.py). (e) **Combine no longer fails silently**: the
  transform backend skips (and NAMES) molecules whose ops don't fit instead of
  emptying the stream, compute_streams warns per empty Combine wire, and Preview
  surfaces every warning in a dialog. (f) **Write output to file...** button beside
  the (now bold) Preview on Transform/Combine panels → `coords.write_structure_file`
  (.xyz native; other formats via pybel, RDKit fallback for mol/sdf/pdb; extension
  picks the format; verified locally via OpenBabel). (g) **Ctrl+Enter on the calc
  TABLE = the launch action** (Run locally on a PC / Submit on the cluster), a
  rebindable keymap action (`calc.launch`, new "Calculations tab" category) — bound
  on the tree only so a stray Ctrl+Enter in a text field can't launch; both paths
  confirm first. NO Duplicate node: geometry outputs already fan out, so
  "duplicate" = wire the same output into two Transforms → Combine (help updated).
  Deferred: KNIME-style meta-nodes, saveable macros (roadmap). 315 tests.
- **Node theming + editor UX batch 3** (branch `relaxed-scans-constraints`, Christian's
  2026-07-05 review pt2) —
  (a) **Node-graph is now skin-themed**: `theme.node_palette(skin_id)` returns a full
  node colour dict (body/fg/outline/sel/ports/wires/kind title-fills/annotations/canvas),
  built from `_NODE_DEFAULTS` + per-skin `_NODE_OVERRIDES` (dark/aero/boombox get real
  dark/tinted node colours). `workflow_tab._np` reads it; `App.apply_skin` calls
  `WorkflowTab.apply_theme()` to re-read + redraw live. The old module-level `_KIND_COLOR/
  _BODY/_SEL` are gone.
  (b) **JSON custom skins**: drop a `*.json` in `~/.orca_workbench_skins/` — it need only
  carry `id` + `base` ("default"/"dark"/…) + the colours to change (hover/tab/selection are
  DERIVED from the palette by `_style_ttk`, so a colours-only file gives full theming).
  `theme.load_user_skins/reload_user_skins/write_skin_template`; Styles dialog gains **New
  custom skin… / Open skins folder / Reload skins**. A user skin whose id matches a built-in
  replaces it.
  (c) **Styles window fixes**: it's now `transient(root)` so it can't hide behind the
  maximized main window (the "can't alt-tab back" bug), and the wheel binding is applied
  AFTER the cards exist so scrolling works over them.
  (d) **Ctrl+PageDown/PageUp** cycle the major tabs (app-wide, `App._cycle_tab`).
  (e) **Transform op-list reorder**: EXTENDED multi-select + move-as-a-block, drag-to-
  reorder, and Up/Down **keep the selection** (the list refreshes in place, no full panel
  rebuild).
  (f) **Combine→calc demands charge/mult**: `Workflow.has_calc_downstream` +
  `combine_needs_charge_mult`; validate() blocks, and `_expand` runs a **guided fix** —
  popup, select the node, red-highlight the empty box(es), focus the FIRST only (then hands
  control back), green-flash each on a valid integer (`_guide_combine_fix`; the charge/mult
  fields are classic `tk.Entry` for bg control).
  (g) **CSV report customisation**: Report node `format` (both/json/csv) + a **column
  editor** (pick columns, rename headers, order = left-to-right; rows are always
  calculations) + missing-value policy (empty cell vs `NaN`). `reporting.available_csv_columns/
  default_csv_columns` + `write_csv(report, path, columns=, missing=)`. Pipeline report
  writer honours the spec.
  (h) **Splice affordances**: dragging an isolated node over a wire **highlights** it
  (`_splice_candidate`, live in `_on_motion`), and on drop the splice **makes horizontal
  room** by shifting the downstream subtree right (`_shift_subtree_right`).
  Tests: `tests/test_theme.py` (+node palette/JSON skins), `tests/test_reporting.py`
  (+CSV columns), `tests/test_workflow_transform.py` (+charge/mult), `tests/test_transform.py`
  (mirror/atom-axis/anchor from pt1). 322 tests. Headless-smoke: skins re-theme the graph,
  op reorder keeps selection, guided charge/mult red→green, splice+make-room, tab cycling,
  JSON skin inheritance.
- **Headless project execution + editor polish batch 4** (branch `relaxed-scans-constraints`,
  Christian's 2026-07-05 pt3) —
  (a) **`orca-workbench --execute_project PROJECT.json`** — new pure `core/project_runner.py`
  builds every planned calc's ORCA input (reusing the exact GUI builders: render_inp / geomspec
  / MOREAD / hardware defaults / provenance) into its run dir, then on a **login node** submits
  them as a SLURM dependency chain (parent geometry via `* xyzfile` + `afterok`, like the GUI's
  submit-unattended) or on a **plain machine** runs them locally in dependency order (embedding
  each finished parent's optimised `<mol>.xyz`). `order_dependency_first` (ported topo sort),
  `execute_project_file` saves run dirs/job ids back so the GUI can monitor/harvest. `--local`/
  `--slurm` force the mode. Workflow **must be Generated in the GUI first** (this runs the
  resulting planned calcs; it doesn't re-expand graphs or materialise Transform/Combine).
  **Verified with real local ORCA 6.0.1**: OPT→NMR chain, child built from the parent's
  optimised geometry (O z 0.30→0.232). Tests `tests/test_project_runner.py` (fake-ORCA local
  run + ordering/target-dir/build). CLI in `__main__`.
  (b) **CSV column dialog**: `fit_to_content` + minsize (was a fixed 560×460 that came out
  cramped on a high-DPI local display) + **Add all / Remove all** buttons; dropped **Reset**.
  (c) **UI scale override** — Settings ▸ *UI scale*, config `ui_scale` / env
  `ORCA_WORKBENCH_UI_SCALE` (0.5–4.0), MULTIPLIES the auto tk-scaling AND the named-font sizes
  (`_scale_named_fonts`) — for ThinLinc/remote desktops that report a low DPI so everything's
  tiny. Restart to apply.
  (d) **Transform op reorder**: a real **insertion LINE** (a placed 2px Frame) between rows
  shows where the block lands, replacing the confusing per-row highlight; the listbox's
  band-select is suppressed during a reorder drag; a held multi-selection is preserved.
  (e) **Geomspec dialog**: no auto-blank bond-constraint row (an empty spec shows a "No
  constraints" hint, not an inert default) + a **View geometry (3D)** button (the reference
  molecule — the first when a node optimises several — via `view_xyz` callback) for reading off
  atom indices. 327 tests.
- **Headless workflow expansion + Report-tab CSV styling** (branch `relaxed-scans-constraints`)
  — (a) **`--execute_project` now EXPANDS the Workflow graph itself** (roadmap: the old
  `--expand_and_execute` idea, folded into the ONE command — expansion is the default, no
  separate verb). New pure `core/workflow_expand.py` is the single source of truth the GUI and
  the CLI now share: `GeometryBackend` (Transform/Combine → in-memory derived geometries),
  `flush_materialisations` (writes `TRANSFORM/<name>.xyz` + locked Molecule rows),
  `find_existing_calc` (calc reuse), and `expand_project_workflow(project, calc_done, log)`
  which validates → expands → materialises → appends new PlannedCalcs (idempotent: an
  already-generated project reuses, finished calcs kept verbatim). **`workflow_tab.py`'s
  `_make_geom_backend`/`_read_geom`/`_flush_geom_materialisations`/`_find_existing_calc` were
  gutted to thin delegations** to it (signatures unchanged, so every call site is untouched) —
  so the app's Generate and the headless run expand a graph byte-identically. `ProjectRunner.
  execute(expand=True)` runs `expand()` first (blockers → logged, run continues on any existing
  calcs; blockers + nothing runnable → "workflow has errors" error). CLI: `--execute_project`
  expand-by-default, `--no-expand` escape hatch to run only already-generated calcs. **Verified
  end-to-end via the real CLI** (`--slurm` on a no-sbatch box): a Molecules→Transform→Optimize
  graph expanded, materialised the +translated `TRANSFORM/*.xyz`, built the `.inp` (geometry
  shifted +3 Å in x) + `.slurm`, failed only at submit. Tests `tests/test_workflow_expand.py`
  (7, real geometries/transform: linear pipeline + parent links, idempotency, Transform/Combine
  materialisation, missing-recipe & combine-charge/mult blockers) + a project_runner integration
  test (expand→build→local-run with fake ORCA). (b) **Report TAB gets the Report NODE's CSV
  styling**: format selector (JSON+CSV / JSON only / CSV only), **Customise CSV columns…**
  (pick/rename/order), and a missing-value picker (empty vs `NaN`). The column editor was
  extracted from `workflow_tab` into reusable `ui/csv_columns.edit_csv_columns_dialog` (the node
  now delegates too — DRY), and `report_tab.on_generate` honours format + `csv_columns` +
  `csv_missing`. Headless-smoke-tested both. 335 tests.
- **Transform ops round 3** (branch `relaxed-scans-constraints`, v1.4.1) — two Transform-op
  additions from Christian's butane-alignment use case. (a) **`set_plane_angle`** — the RIGID
  planar analogue of `set_dihedral`: rotate the (i,j,k) plane to a chosen angle (0–90°) from a
  coordinate plane (xy/yz/xz) — 0 = lies flat in it, 90 = perpendicular. Rotates about the two
  planes' line of intersection (minimal tilt; conformation unchanged); handles the
  already-parallel degeneracy (intersection line undefined → tilt about any in-plane axis). Plus
  `plane_angle()` (the measure). This is what pins a molecule's remaining spin after `align_axis`
  fixes one bond to an axis (e.g. butane: `set_dihedral` 0-1-2-3=0, `align_axis` 1-2→x,
  `set_plane_angle` to fix the tilt, `center` on the 1-2 midpoint). (b) **fractional bond
  anchor** — `center` on a two-atom anchor now takes a `frac` ∈ [0,1] (0 = atom i, 1 = atom j,
  **0.5 = midpoint = default**), so the origin can sit anywhere along a bond; the op dict omits
  `frac` at the default midpoint (back-compat). Both wired into `ui/transform_dialog.py` (new op
  type + reference-plane combo + angle field; a fraction field on the center op). Tests in
  `tests/test_transform.py` (plane-angle hits target for every ref plane/target + rigidity +
  parallel start; fractional anchor 0/0.25/0.5/1 + range validation). 340 tests.
- **align_moiety op + ring-orientation cycling** (uncommitted v1.4.2 batch) — a Transform op
  that rigidly superposes a molecule's `mobile` atoms onto another project molecule's `ref`
  atoms via **Kabsch** best fit (`core/transform.kabsch/_rmsd/align_moiety`; proper-rotation only,
  chirality preserved). A symmetric moiety (phenyl fits ~12 ways) is resolved EITHER by extra
  matched **anchor** atoms (score every `_ring_orderings` mapping) OR by an explicit **`ordering`**
  index — the "cycle through the N candidate ring alignments in the preview" control: `moiety_orderings(mobile)`
  gives the finite set, the Transform node's **"Cycle moiety orientation >"** button steps the
  op's `ordering` (wrapping 0..N-1) and re-opens the 3D preview so you eyeball each and keep the
  right one. Dialog (`ui/transform_dialog`) has a template picker + mobile/ref atom-list fields +
  a live "of N orientations" count. Tests in `tests/test_transform.py`.
- **Archive Export + SCF-energy bar plot** (uncommitted v1.4.2 batch) — (a) **File > Archive
  Export...** (below Save as) bundles the whole project into one tar/zip: `core/archive.py` (pure)
  `collect_results` (finished-calc records: mol/smiles/calctype/out_path/energy) + `create_archive`
  (stdlib `tarfile`/`zipfile` for tar.gz[default]/tar/zip/tar.bz2/tar.xz — **no external tool
  needed**; `.7z`/`.rar` shell out to a configured 7-Zip/WinRAR via `<tool> a <archive> <files>`)
  + `export_archive` orchestrator (optionally render figures, then pack project.json + calcs/ +
  XYZ_INI/ + TRANSFORM/ + ZPVA/ + FIGS_EXPORT/, nested under one arc-root folder). `ui/archive_dialog.py`
  options form (include figures? img format=svg default; archive format=tar.gz default; stack
  offset=0.5) runs on the main thread with a streaming log. Headless: **`--archive_export PROJECT.json`**
  (`archive.archive_project_file`) with `--out/--archive-format/--fig-format/--no-figs/--stack-offset/--archiver`.
  New `archiver_path` slot in `ui/extprog.PROGRAM_SLOTS` (optional; only for 7z/rar). (b) **`core/figures.py`**
  (pure; attaches an **Agg** canvas per-Figure and NEVER calls `matplotlib.use()`, so it renders
  files without clobbering the GUI's TkAgg backend — verified they coexist) renders into
  FIGS_EXPORT/: a grouped **SCF final-energy bar** chart (bars grouped by molecule, one colour-coded
  bar per calc type; `scf_bar_groups` + `draw_scf_bars`, absolute Eh / Δ-per-molecule / Δ-vs-global
  modes) plus simulated **IR/UV-Vis/NMR/EPR** spectra as overlay AND (when >1 molecule) a
  vertically-**stacked** variant (offset = 0.5×max), each with a full molecule **legend** (static
  images omit the interactive SMILES hover panel — the legend is the attribution). (c) Interactive
  **`SCFEnergyBarWindow`** (`ui/spectra.py`, a `BaseSpectrumWindow` subclass; new `SHOW_OFFSET`/
  `AUTO_LEGEND` class hooks let a bar chart drop the stack slider + line-legend) — shares
  `draw_scf_bars` with the exporter (identical output), hovering a bar shows that molecule's 2D
  structure in the side panel; wired via Calc-tab right-click **"Plot final SCF energies (bar
  chart)"** (`_plot_scf_energies`, any finished calcs). Tests `tests/test_archive.py`,
  `tests/test_figures.py` (375 total). LEFT for the gateway: matplotlib must be importable there for
  `--archive_export` figures (Agg, no display needed); a ThinLinc visual pass on the bar chart /
  stacked exports. **Design note**: Python's stdlib already writes tar.gz/zip everywhere, so the
  external-archiver path is only for 7z/rar (corrected the "Windows has no zipper" assumption).

## Open work / TODO
- `--execute_project` now expands the Workflow graph AND materialises Transform/Combine
  geometries headlessly (see `core/workflow_expand.py`) — the old `--expand_and_execute` gap is
  closed. Remaining headless gaps: (1) no live monitoring (reopen in the GUI to watch/harvest);
  (2) **Report NODE outputs aren't written headlessly** — after a `--local` pipeline finishes,
  the Report node's merged JSON/CSV isn't generated (the GUI's `_generate_pipeline_reports` walks
  ancestor chains on the main thread). A headless port of that (local mode only; SLURM runs async)
  would make an unattended local run fully self-contained. The Report TAB CSV styling shipped.
- KNIME-style meta-nodes (collapse a selection into one node, reversible) and, beyond
  that, saveable/exportable node macros (UE5-style function nodes) — both parked from
  Christian's 2026-07-05 batch; meta-nodes are the simpler first step.
- Transform/Combine phase 2: allow them DOWNSTREAM of a finished Optimize (read the
  optimised .xyz at expand time, like ZPVA reads a finished FREQ's .hess — the
  two-step Expand pattern); today validate() blocks calc-upstream placement.
- Live plot: the M / limit-box row from the spectrum windows isn't ported (no boxes
  in the live window); could add if wanted.
- Skins: the **node-graph is now fully themed** (see the batch-3 entry); the matplotlib
  spectrum/live plots still only theme their *backdrop* (line palette from the skin accent
  is the natural next step). Wants a ThinLinc visual pass (clam theme + dark palettes render
  differently on X than local Windows). New built-in skins = one entry in `core/theme._SKINS`;
  users can now also drop a JSON skin in `~/.orca_workbench_skins/`.
- Keymap catalogue is partial — only app-globals + plot keys are registered/rebindable so far;
  the Molecules/Calc/Workflow tab tree shortcuts still bind directly. Extend `keymap` +
  route those through `bind_action` to make them rebindable too.
- The SLURM `--mem` line isn't driven by the global "memory per core" default (it lives
  in the submit-script template); if per-job `--mem` should scale with cores×maxcore,
  add a MEM placeholder to the template + render_slurm.
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
