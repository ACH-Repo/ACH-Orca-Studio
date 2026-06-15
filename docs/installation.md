# Installation

ORCA Studio is a Python package that installs a console command, `orca-studio`.
Python **≥ 3.9** with **Tkinter** (bundled with most Python builds) is required;
the heavy dependencies (`rdkit`, `openbabel-wheel`, `matplotlib`) are pulled in
automatically.

Pick whichever install fits.

## Quick — nothing to clone

Install straight from GitHub:

=== "Linux / macOS / cluster"

    ```bash
    pip install git+https://github.com/ACH-Repo/ACH-Orca-Studio.git
    ```

=== "Windows"

    ```bat
    py -m pip install git+https://github.com/ACH-Repo/ACH-Orca-Studio.git
    ```

Then launch with `orca-studio` (or `python -m orca_studio`). This is the fastest
path and needs no checked-out folder — but you can't edit the source in place.

## Editable — recommended on the cluster, or if you'll tweak the code

Clone once, then install in editable mode so a `git pull` updates the app in
place with **no reinstall** (don't move the folder afterwards — that breaks the
link):

=== "Cluster (e.g. Lido)"

    ```bash
    git clone https://github.com/ACH-Repo/ACH-Orca-Studio.git
    cd ACH-Orca-Studio
    module load python          # REQUIRED — see note below
    pip install --user -e .
    orca-studio                 # window appears on your PC over X-forwarding
    ```

=== "Windows"

    ```bat
    git clone https://github.com/ACH-Repo/ACH-Orca-Studio.git
    cd ACH-Orca-Studio
    py -m pip install --user -e .
    py -m orca_studio
    ```

!!! warning "`module load python` is mandatory on the cluster"
    The scientific stack (matplotlib → numpy) needs `libopenblas.so`, which that
    module autoloads. Without it the app fails at startup with
    `ImportError: libopenblas.so.0: cannot open shared object file`. Put
    `module load python` in your `~/.bashrc` so you never forget it.

!!! tip "Launching on Windows"
    The `orca-studio` command lands in a per-user `Scripts` folder that often
    isn't on `PATH`, so `py -m orca_studio` is the reliable way to launch from
    anywhere. (pip prints that Scripts path during install if you'd rather add it
    to `PATH`.)

## Verify the environment

```bash
orca-studio --diagnose      # or: python -m orca_studio --diagnose
```

Reports whether RDKit / OpenBabel can generate coordinates on this machine — run
it first if structure generation misbehaves. Open a saved project directly with
`orca-studio myproject.json`.

## Updating

- **Editable / cloned install:** `git pull` in the repo folder — done. The next
  launch picks up the changes; no reinstall needed.
- **Quick (git+pip) install:** re-run with `-U`:

    ```bash
    pip install -U git+https://github.com/ACH-Repo/ACH-Orca-Studio.git
    ```

- **On the cluster**, `module load python` is still required every session,
  whichever way you installed.
