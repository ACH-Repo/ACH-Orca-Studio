# Installation

ORCA Workbench is a Python package that installs a console command, `orca-workbench`.
Python **≥ 3.9** with **Tkinter** (bundled with most Python builds) is required;
the heavy dependencies (`rdkit`, `openbabel-wheel`, `matplotlib`) are pulled in
automatically.

Pick whichever install fits.

## Quick — nothing to clone

Install straight from GitHub:

=== "Linux / macOS / cluster"

    ```bash
    pip install git+https://github.com/ACH-Repo/ACH-Orca-Workbench.git
    ```

=== "Windows"

    ```bat
    py -m pip install git+https://github.com/ACH-Repo/ACH-Orca-Workbench.git
    ```

Then launch with `orca-workbench` (or `python -m orca_workbench`). This is the fastest
path and needs no checked-out folder — but you can't edit the source in place.

## Editable — recommended on the cluster, or if you'll tweak the code

Clone once, then install in editable mode so a `git pull` updates the app in
place with **no reinstall** (don't move the folder afterwards — that breaks the
link):

=== "Cluster (e.g. Lido)"

    ```bash
    git clone https://github.com/ACH-Repo/ACH-Orca-Workbench.git
    cd ACH-Orca-Workbench
    module load python          # REQUIRED — see note below
    pip install --user -e .
    orca-workbench                 # window appears on your PC over X-forwarding
    ```

=== "Windows"

    ```bat
    git clone https://github.com/ACH-Repo/ACH-Orca-Workbench.git
    cd ACH-Orca-Workbench
    py -m pip install --user -e .
    py -m orca_workbench
    ```

!!! warning "`module load python` is mandatory on the cluster"
    The scientific stack (matplotlib → numpy) needs `libopenblas.so`, which that
    module autoloads. Without it the app fails at startup with
    `ImportError: libopenblas.so.0: cannot open shared object file`. Put
    `module load python` in your `~/.bashrc` so you never forget it.

!!! tip "Launching on Windows"
    The `orca-workbench` command lands in a per-user `Scripts` folder that often
    isn't on `PATH`, so `py -m orca_workbench` is the reliable way to launch from
    anywhere. (pip prints that Scripts path during install if you'd rather add it
    to `PATH`.)

## Verify the environment

```bash
orca-workbench --check-backends    # or: python -m orca_workbench --check-backends
```

Probes whether RDKit / OpenBabel can generate coordinates on this machine (no GUI)
— run it if structure generation misbehaves. Open a saved project directly with
`orca-workbench myproject.json`.

!!! tip "Slow over X-forwarding? Run the self-diagnostics"
    `orca-workbench --diagnose` launches with live timing instrumentation and
    writes a performance `.log` to your home dir when you quit — handy for pinning
    down why a gateway session feels sluggish.

## Updating

- **Editable / cloned install:** `git pull` in the repo folder — done. The next
  launch picks up the changes; no reinstall needed.
- **Quick (git+pip) install:** re-run with `-U`:

    ```bash
    pip install -U git+https://github.com/ACH-Repo/ACH-Orca-Workbench.git
    ```

- **On the cluster**, `module load python` is still required every session,
  whichever way you installed.
