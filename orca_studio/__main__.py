# Setup on Lido (one-time):
#   1. Copy the whole orca_studio source tree into a stable location, e.g.
#         /work/<your_id>/hacks/orca_studio
#      (the parent of this orca_studio/ package — the dir holding pyproject.toml).
#   2. module load python                          # gives Python 3.9.x
#   3. pip install --user -e /work/<your_id>/hacks/orca_studio
#      The -e (editable) flag means pip just registers the source — when you
#      edit a .py file in your work dir, the next launch picks it up. No
#      reinstall needed for changes.
#   4. Add ~/.local/bin to your PATH if it's not already (most setups already
#      do this — check `echo $PATH`).
#
# Now you can launch from anywhere with:
#         orca-studio
#
# Alternative if you'd rather not pip-install: drop a line in your ~/.bashrc:
#         alias orca-studio='module load python && python -m orca_studio'
# and run `python -m orca_studio` from a directory that contains the
# orca_studio/ folder.
#
# MobaXterm automatically forwards X11, so the Tkinter window appears on
# your Windows desktop. Close the SSH session to close the app; any SLURM
# jobs already submitted keep running because SLURM owns them.

import sys


def _cli():
    # Subcommands handled before importing the UI, so they work even if
    # tkinter / X-forwarding isn't available.
    if "--diagnose" in sys.argv or "-d" in sys.argv:
        from orca_studio.core.coords import diagnose_backends
        print(diagnose_backends())
        sys.exit(0)
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: orca-studio [PROJECT.json] [--diagnose | --help]")
        print("       Without arguments, launches the GUI with an empty project.")
        print()
        print("  PROJECT.json     Open this project file on startup.")
        print("  --diagnose, -d   Probe RDKit and OpenBabel and print whether each can")
        print("                   generate coordinates for methane on this host. Use this")
        print("                   if Generate XYZ fails for everything in the app — it")
        print("                   tells you whether the backends are installed and working.")
        sys.exit(0)
    # First non-flag argument is treated as a project file to open.
    project_path = None
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            project_path = arg
            break
    from orca_studio.ui.app import main
    main(project_path)


if __name__ == "__main__":
    _cli()
