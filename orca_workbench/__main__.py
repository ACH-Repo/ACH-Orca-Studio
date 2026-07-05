# Setup on Lido (one-time):
#   1. Copy the whole orca_workbench source tree into a stable location, e.g.
#         /work/<your_id>/hacks/orca_workbench
#      (the parent of this orca_workbench/ package — the dir holding pyproject.toml).
#   2. module load python                          # gives Python 3.9.x
#   3. pip install --user -e /work/<your_id>/hacks/orca_workbench
#      The -e (editable) flag means pip just registers the source — when you
#      edit a .py file in your work dir, the next launch picks it up. No
#      reinstall needed for changes.
#   4. Add ~/.local/bin to your PATH if it's not already (most setups already
#      do this — check `echo $PATH`).
#
# Now you can launch from anywhere with:
#         orca-workbench
#
# Alternative if you'd rather not pip-install: drop a line in your ~/.bashrc:
#         alias orca-workbench='module load python && python -m orca_workbench'
# and run `python -m orca_workbench` from a directory that contains the
# orca_workbench/ folder.
#
# MobaXterm automatically forwards X11, so the Tkinter window appears on
# your Windows desktop. Close the SSH session to close the app; any SLURM
# jobs already submitted keep running because SLURM owns them.

import os
import sys


def _print_usage():
    print("Usage: orca-workbench [PROJECT.json] [--diagnose | --check-backends | --help]")
    print("       Without arguments, launches the GUI with an empty project.")
    print()
    print("  PROJECT.json        Open this project file on startup.")
    print("  --diagnose, -d      Launch with live self-diagnostics ON. Times each tab")
    print("                      build / project load / tab switch as you use the app,")
    print("                      and writes a perf .log to your home dir on quit — use")
    print("                      this to diagnose slow X-forwarded gateway sessions.")
    print("                      Also prints the coordinate-backend probe at startup.")
    print("                      (Equivalent: set ORCA_WORKBENCH_DIAG=1.)")
    print("  --check-backends    Probe RDKit/OpenBabel and exit, no GUI. Use this if")
    print("                      Generate XYZ fails, to see whether the backends work.")
    print("  --run FILE.inp      Headless, no GUI: on a SLURM login node, submit FILE.inp")
    print("                      as a proper batch job (node-local scratch + copy-back);")
    print("                      on a plain machine, run it locally (orca FILE > FILE.out).")
    print("  --execute_project PROJECT.json")
    print("                      Headless, no GUI: EXPAND the project's Workflow graph")
    print("                      (materialising any Transform/Combine geometries), then build")
    print("                      every resulting calc and submit them as a SLURM dependency")
    print("                      chain (login node) or run them locally in order (plain")
    print("                      machine). Design the project in the GUI, copy project.json +")
    print("                      XYZ_INI/ to the cluster, then run this - no Generate step")
    print("                      needed. Add --local or --slurm to force the mode, or")
    print("                      --no-expand to run only already-generated calcs.")
    print("  --simple, --gateway_mode")
    print("                      Lightweight mode: load only the core pipeline tabs")
    print("                      (Molecules/Recipes/Calculations/Report), build them")
    print("                      on first click, skip Workflow, and disable")
    print("                      tooltips. For slow/high-latency X-forwarded sessions")
    print("                      (e.g. over a VPN). (Equivalent: ORCA_WORKBENCH_SIMPLE=1.)")
    print("  --help, -h          Show this message.")


def _cli():
    if "--help" in sys.argv or "-h" in sys.argv:
        _print_usage()
        sys.exit(0)
    # Headless backend probe — kept separate so it works even without a display.
    if "--check-backends" in sys.argv:
        from orca_workbench.core.coords import diagnose_backends
        print(diagnose_backends())
        sys.exit(0)

    # Headless single-job run/submit — no display needed (like --check-backends).
    if "--run" in sys.argv:
        idx = sys.argv.index("--run")
        if idx + 1 >= len(sys.argv) or sys.argv[idx + 1].startswith("-"):
            print("error: --run needs a path to a .inp file")
            sys.exit(2)
        from orca_workbench.core.headless import run_infile
        msg = run_infile(sys.argv[idx + 1])
        print(msg)
        # Non-zero on any failure: bad input, failed submit, or a non-zero ORCA
        # exit (so `--run` is scriptable like a bare `orca my.inp`).
        ok = not (msg.startswith("error") or "failed" in msg or "(exit " in msg)
        sys.exit(0 if ok else 1)

    # Headless whole-project build + run/submit — no display needed.
    if "--execute_project" in sys.argv:
        idx = sys.argv.index("--execute_project")
        if idx + 1 >= len(sys.argv) or sys.argv[idx + 1].startswith("-"):
            print("error: --execute_project needs a path to a project .json file")
            sys.exit(2)
        force = "local" if "--local" in sys.argv else ("slurm" if "--slurm" in sys.argv else None)
        from orca_workbench.core.project_runner import execute_project_file
        msg = execute_project_file(sys.argv[idx + 1], force=force,
                                   expand="--no-expand" not in sys.argv)
        print(msg)
        sys.exit(1 if msg.startswith("error") else 0)

    # Live diagnostics mode: instrument the GUI and write a perf log on quit.
    diag_on = ("--diagnose" in sys.argv or "-d" in sys.argv
               or os.environ.get("ORCA_WORKBENCH_DIAG", "") not in ("", "0"))
    if diag_on:
        from orca_workbench.core import diagnostics
        diagnostics.enable()
        try:  # show the coordinate-backend probe up front (old --diagnose info)
            from orca_workbench.core.coords import diagnose_backends
            print(diagnose_backends())
        except Exception:
            pass
        print("\n[diagnostics] live mode ON — a perf .log is written to your home "
              "dir on quit.\n")
        sys.stdout.flush()

    # Lightweight / gateway mode: load only the core feature tier.
    if ("--simple" in sys.argv or "--gateway_mode" in sys.argv
            or os.environ.get("ORCA_WORKBENCH_SIMPLE", "") not in ("", "0")):
        from orca_workbench.core import features
        features.set_max_tier(features.CORE)

    # First non-flag argument is treated as a project file to open.
    project_path = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            project_path = arg
            break
    from orca_workbench.ui.app import main
    main(project_path)


if __name__ == "__main__":
    _cli()
