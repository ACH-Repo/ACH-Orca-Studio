"""Archive Export — bundle a whole project (project.json + its run tree +
generated figures) into one portable tar/zip, from the GUI or headlessly.

Two independent pieces, kept pure (no Tkinter) so they unit-test and run on the
gateway without a display:

  * `collect_results` walks a project's finished calculations and returns a plain
    list of result records (molecule, calctype, SMILES, the .out path) — the shared
    input for both the figure generator (core/figures) and any reporting.
  * `create_archive` packs a set of top-level members (project.json, calcs/,
    XYZ_INI/, TRANSFORM/, FIGS_EXPORT/, …) into a tarball or zip. `.tar.gz/.tar/
    .zip/.tar.bz2/.tar.xz` use the Python standard library (always available, no
    external tool). A format stdlib can't write (`.7z`, `.rar`) — or an explicit
    `external_tool` — shells out to 7-Zip / WinRAR (`<tool> a <archive> <members>`).

`export_archive` ties them together: optionally render every figure into
FIGS_EXPORT/, then pack. The GUI dialog and the `--archive_export` CLI both call it.
"""

import os
import subprocess
from typing import Callable, Dict, List, Optional

from orca_workbench.core import orca_parser as _P
from orca_workbench.core import slurm_runtime as _slurm_runtime


# Top-level project members packed by default, in this order. Only those that
# exist under the project root are included; a project.json basename is added
# separately (it may be named anything).
DEFAULT_MEMBER_DIRS = ["calcs", "XYZ_INI", "TRANSFORM", "ZPVA", "FIGS_EXPORT"]

# Archive formats the Python standard library can write directly (no external
# tool needed) -> the tarfile mode / "zip" sentinel.
_STDLIB_TAR_MODES = {
    "tar.gz": "w:gz", "tgz": "w:gz",
    "tar": "w:",
    "tar.bz2": "w:bz2", "tbz2": "w:bz2",
    "tar.xz": "w:xz", "txz": "w:xz",
}
STDLIB_FORMATS = list(_STDLIB_TAR_MODES.keys()) + ["zip"]

# Large, regenerable ORCA files excluded by default so a project archive stays
# small and fast to write (a finished project is dominated by binary wavefunction
# and scratch files that compress poorly and can be re-made by re-running). The
# .out / .xyz / .hess / .inp results are kept. Matched by fnmatch on the BASENAME.
# NOTE: dropping .gbw means the archive can't do a MOREAD restart or regenerate
# cubes without re-running the SCF — a deliberate size/completeness trade-off,
# switchable off (keep_heavy / the dialog checkbox / --keep-heavy).
DEFAULT_EXCLUDE = ["*.gbw", "*.tmp", "*.tmp.*", "*.cpcm", "*.densities",
                   "*.densitiesinfo", "*.ges", "*.bas.tmp", "*.nbo"]


def _abs(root, rel):
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


def _is_excluded(path, patterns):
    """True if a file's basename matches any fnmatch pattern in `patterns`."""
    if not patterns:
        return False
    import fnmatch
    base = os.path.basename(path)
    return any(fnmatch.fnmatch(base, pat) for pat in patterns)


# --------------------------------------------------------------- result collector

def collect_results(project, recipe_by_name, root=None):
    # type: (object, Dict[str, object], Optional[str]) -> List[dict]
    """One record per planned calc that has a run directory, describing what a
    figure/report needs about it. `recipe_by_name` maps recipe_name -> a recipe
    object exposing `.calctype` / `.method_label`. Reading the .out is left to the
    caller (via `out_path`), so this stays cheap.

    Each record: {calc_id, molecule, name, smiles, calctype, method, recipe,
    out_path (abs or None), finished (bool)}."""
    root = root or project.root()
    out = []
    for c in getattr(project, "planned_calcs", []):
        rec = recipe_by_name.get(c.recipe_name)
        mol = project.molecule_by_filename(c.molecule_filename)
        out_path = None
        if c.rundir and c.job_id:
            out_path = _slurm_runtime.find_output_file(
                _abs(root, c.rundir), c.molecule_filename, c.job_id)
        out.append({
            "calc_id": c.id,
            "molecule": c.molecule_filename,
            "name": (mol.name if mol else c.molecule_filename) or c.molecule_filename,
            "smiles": (mol.smiles if mol else None),
            "calctype": (rec.calctype if rec else "?"),
            "method": (rec.method_label if rec else ""),
            "recipe": c.recipe_name,
            "out_path": out_path,
            "finished": bool(out_path and os.path.isfile(out_path)),
        })
    return out


def read_out(record):
    # type: (dict) -> Optional[str]
    """Read a result record's .out text, or None. Cached on the record so repeated
    figure passes over the same project don't re-read the file."""
    if "_out_text" in record:
        return record["_out_text"]
    text = None
    p = record.get("out_path")
    if p and os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except (IOError, OSError):
            text = None
    record["_out_text"] = text
    return text


def terminated_ok(record):
    # type: (dict) -> bool
    text = read_out(record)
    return bool(text and _P._TERM_OK.search(text))


def final_energy(record):
    # type: (dict) -> Optional[float]
    """Final single-point energy (Eh) from a record's .out, or None."""
    text = read_out(record)
    if not text:
        return None
    hits = _P._FINAL_E.findall(text)
    return float(hits[-1]) if hits else None


# --------------------------------------------------------------- packaging

def normalize_format(fmt):
    # type: (str) -> str
    """Canonicalise an archive-format string ('.tar.gz', 'TGZ', 'zip' -> 'tar.gz'/
    'zip'); leave unknown ones (e.g. '7z') lowercased for the external path."""
    f = (fmt or "").strip().lower().lstrip(".")
    aliases = {"tgz": "tar.gz", "gz": "tar.gz", "tbz2": "tar.bz2", "bz2": "tar.bz2",
               "txz": "tar.xz", "xz": "tar.xz"}
    return aliases.get(f, f)


def format_needs_external(fmt):
    # type: (str) -> bool
    return normalize_format(fmt) not in STDLIB_FORMATS


def _iter_member_files(abs_path, exclude=None):
    """Yield (abs_file, rel_to_member) for a file or every file under a directory,
    skipping files whose basename matches an `exclude` fnmatch pattern."""
    if os.path.isfile(abs_path):
        if not _is_excluded(abs_path, exclude):
            yield abs_path, os.path.basename(abs_path)
        return
    base = os.path.dirname(abs_path.rstrip(os.sep))
    for dirpath, _dirs, files in os.walk(abs_path):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            if _is_excluded(fp, exclude):
                continue
            yield fp, os.path.relpath(fp, base)


def existing_members(root, members):
    # type: (str, List[str]) -> List[str]
    """Filter `members` (relative to root) to those that actually exist."""
    return [m for m in members if os.path.exists(_abs(root, m))]


def create_archive(out_path, root, members, fmt="tar.gz", arc_root=None,
                   external_tool=None, exclude=None, compresslevel=6, log=None):
    # type: (str, str, List[str], str, Optional[str], Optional[str], Optional[List[str]], int, Optional[Callable]) -> str
    """Pack `members` (paths relative to `root`) into `out_path`.

    `fmt` selects the container; a stdlib format (tar.gz/tar/zip/tar.bz2/tar.xz)
    is written with tarfile/zipfile, otherwise (or when `external_tool` is given)
    the archiver executable is invoked as `<tool> a <archive> <members>`. Every
    packed path is nested under `arc_root` (default: the archive's base name) so
    it extracts into one tidy folder.

    `exclude` is a list of fnmatch patterns (matched on each file's basename) to
    skip — the big regenerable ORCA files, which dominate a project's size and
    compress poorly (see DEFAULT_EXCLUDE). `compresslevel` (0-9, gz/bz2/zip only;
    lower = faster, bigger) tunes the speed/size trade-off. Both apply to the
    stdlib formats; the external-archiver path packs the members verbatim.
    Returns `out_path`; raises on failure."""
    log = log or (lambda m: None)
    members = existing_members(root, members)
    if not members:
        raise ValueError("nothing to archive — no project files found under {}".format(root))
    fmt = normalize_format(fmt)
    if arc_root is None:
        arc_root = os.path.basename(out_path)
        for suf in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip", ".7z", ".rar"):
            if arc_root.lower().endswith(suf):
                arc_root = arc_root[: -len(suf)]
                break

    use_external = external_tool or format_needs_external(fmt)
    if use_external:
        if not external_tool:
            raise ValueError(
                "format '{}' needs an external archiver (7-Zip/WinRAR) — set its path "
                "(Settings > External programs, or --archiver)".format(fmt))
        cmd = [external_tool, "a", os.path.abspath(out_path)] + list(members)
        log("running archiver: {} a {} {}".format(
            os.path.basename(external_tool), os.path.basename(out_path),
            " ".join(members)))
        proc = subprocess.run(cmd, cwd=root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            raise RuntimeError("archiver failed (exit {}):\n{}".format(
                proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")[-2000:]))
        return out_path

    if fmt == "zip":
        import zipfile
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=compresslevel) as zf:
            for m in members:
                for fp, rel in _iter_member_files(_abs(root, m), exclude=exclude):
                    zf.write(fp, arcname=os.path.join(arc_root, rel))
                log("added {}".format(m))
    else:
        import tarfile
        mode = _STDLIB_TAR_MODES[fmt]
        # compresslevel applies to gz/bz2; plain tar and xz don't take it.
        open_kwargs = {}
        if mode in ("w:gz", "w:bz2"):
            open_kwargs["compresslevel"] = compresslevel

        def _filter(tarinfo):
            # Drop excluded files; keep directories/links so the tree is intact.
            if tarinfo.isfile() and _is_excluded(tarinfo.name, exclude):
                return None
            return tarinfo

        with tarfile.open(out_path, mode, **open_kwargs) as tf:
            for m in members:
                tf.add(_abs(root, m), arcname=os.path.join(arc_root, m),
                       filter=_filter)
                log("added {}".format(m))
    return out_path


# --------------------------------------------------------------- orchestrator

def export_archive(project, recipe_by_name, out_path, fmt="tar.gz",
                   include_figures=True, img_format="svg", stacked=True,
                   offset_frac=0.5, members=None, external_tool=None,
                   exclude=None, compresslevel=6, log=None):
    # type: (...) -> dict
    """Build the FIGS_EXPORT figures (optional) then pack the project. `exclude`
    (fnmatch patterns) and `compresslevel` are forwarded to create_archive — pass
    exclude=DEFAULT_EXCLUDE to drop the big regenerable ORCA files. Returns a
    summary dict {archive, n_figures, members, figures, warnings}."""
    log = log or (lambda m: None)
    root = project.root()
    figures, warnings = [], []
    if include_figures:
        try:
            from orca_workbench.core import figures as figures_mod
            results = collect_results(project, recipe_by_name, root)
            fig_dir = os.path.join(root, "FIGS_EXPORT")
            figures = figures_mod.generate_all_figures(
                results, fig_dir, img_format=img_format, stacked=stacked,
                offset_frac=offset_frac, log=log)
        except Exception as e:       # a plotting failure must not lose the archive
            warnings.append("figure generation failed: {}: {}".format(type(e).__name__, e))
            log(warnings[-1])

    if members is None:
        members = []
        if project.path and os.path.isfile(project.path):
            members.append(os.path.basename(project.path))
        members += DEFAULT_MEMBER_DIRS
    members = existing_members(root, members)
    create_archive(out_path, root, members, fmt=fmt, external_tool=external_tool,
                   exclude=exclude, compresslevel=compresslevel, log=log)
    return {"archive": out_path, "n_figures": len(figures), "members": members,
            "figures": figures, "warnings": warnings}


def _default_out_path(project, fmt):
    base = (os.path.splitext(os.path.basename(project.path))[0]
            if project.path else "project")
    return os.path.join(project.root(), "{}_export.{}".format(base, fmt))


def archive_project_file(path, out=None, fmt="tar.gz", include_figures=True,
                         img_format="svg", stacked=True, offset_frac=0.5,
                         external_tool=None, keep_heavy=False, compresslevel=6,
                         log=None):
    # type: (...) -> str
    """Headless entry (the `--archive_export` CLI): load a project.json, build the
    figures + archive it. Recipes are loaded exactly like project_runner (so
    calctypes resolve identically). By default the big regenerable ORCA files
    (.gbw, scratch) are excluded for a smaller/faster archive; `keep_heavy=True`
    packs them too. Returns a one-line status ('error…' on failure) for the CLI
    exit code."""
    log = log or (lambda m: print(m))
    if not os.path.isfile(path):
        return "error: no such project file: {}".format(path)
    from orca_workbench.core import inputs as inputs_mod
    from orca_workbench.core.project import load_project
    from orca_workbench.core.project_runner import _resolve_recipe_dirs
    try:
        project = load_project(path)
    except Exception as e:
        return "error: could not open {}: {}".format(path, e)
    recipes = inputs_mod.load_recipes_from_dirs(_resolve_recipe_dirs(project))
    recipe_by_name = {r.name: r for r in recipes}
    fmt = normalize_format(fmt)
    if format_needs_external(fmt) and not external_tool:
        return ("error: format '{}' needs an external archiver — pass --archiver PATH "
                "(to 7z/rar)".format(fmt))
    out_path = out or _default_out_path(project, fmt)
    try:
        summary = export_archive(
            project, recipe_by_name, out_path, fmt=fmt,
            include_figures=include_figures, img_format=img_format, stacked=stacked,
            offset_frac=offset_frac, external_tool=external_tool,
            exclude=None if keep_heavy else DEFAULT_EXCLUDE,
            compresslevel=compresslevel, log=log)
    except Exception as e:
        return "error: {}: {}".format(type(e).__name__, e)
    for w in summary.get("warnings", []):
        log("warning: " + w)
    return "wrote {} ({} figure(s), {} member(s))".format(
        summary["archive"], summary["n_figures"], len(summary["members"]))
