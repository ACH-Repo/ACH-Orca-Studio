"""Static figure rendering for Archive Export (and reused by the interactive SCF
bar window).

Pure-ish: it builds matplotlib `Figure` objects with an explicitly-attached Agg
canvas and never calls `matplotlib.use()`, so it renders to files WITHOUT
switching the process-global backend — safe to call from the running Tk GUI (which
uses TkAgg) and from a headless CLI with no display.

What it draws (into FIGS_EXPORT/):
  * a grouped **SCF final-energy bar** chart — bars grouped by molecule, one
    colour-coded bar per calculation type in each group (`draw_scf_bars`, shared
    with the interactive `SCFEnergyBarWindow`);
  * simulated **IR / UV-Vis / NMR / EPR** spectra, one trace per molecule, as an
    overlay and — when >1 molecule has data — a vertically-offset **stacked**
    variant (offset = `offset_frac` x the tallest trace, default half).

Export figures carry a full **legend** attributing each line to its molecule (the
interactive hover structure panel has no place in a static image, so SMILES
depictions are deliberately omitted — the legend is the attribution)."""

import os
from typing import Callable, Dict, List, Optional

from orca_workbench.core import archive as _archive
from orca_workbench.core import orca_parser as _P
from orca_workbench.core import spectra as _S


# A qualitative colour cycle shared with the UI (ui/spectra._COLORS mirrors it),
# so a molecule/type keeps the same colour across the app and the exports.
QUAL_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
               "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]

HARTREE_TO_KCAL = 627.5094740631


def matplotlib_available():
    # type: () -> bool
    try:
        import matplotlib.figure  # noqa: F401
        from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: F401
        return True
    except Exception:
        return False


def _new_figure(figsize=(9.0, 5.2), dpi=120):
    """A Figure with an Agg canvas attached (so savefig works) WITHOUT touching the
    global backend."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=figsize, dpi=dpi)
    FigureCanvasAgg(fig)
    try:
        fig.set_layout_engine("tight")
    except Exception:
        pass
    return fig


def _save(fig, path, img_format):
    fig.savefig(path, format=img_format, bbox_inches="tight")
    return path


def color_of(index):
    return QUAL_COLORS[index % len(QUAL_COLORS)]


# ------------------------------------------------------------------ SCF bar chart

def scf_bar_groups(records):
    # type: (List[dict]) -> tuple
    """Assemble grouped-bar data from result records with a final energy.

    Returns (groups, calctypes):
      groups    = [{molecule, name, smiles, bars:[{calctype, method, energy}]}] in
                  project (first-seen) order, one per molecule that has any energy;
      calctypes = the ordered set of calculation types across all groups (the bar
                  categories, each a legend colour).
    A calc contributes only if its .out yielded a FINAL SINGLE POINT ENERGY."""
    order, by_mol = [], {}
    calctypes = []
    for r in records:
        # A precomputed 'energy' (the interactive window already read the .out) is
        # used as-is; otherwise parse it from the record's out_path.
        e = r.get("energy")
        if e is None:
            e = _archive.final_energy(r)
        if e is None:
            continue
        mol = r["molecule"]
        if mol not in by_mol:
            by_mol[mol] = {"molecule": mol, "name": r.get("name") or mol,
                           "smiles": r.get("smiles"), "bars": []}
            order.append(mol)
        ct = r.get("calctype") or "?"
        by_mol[mol]["bars"].append({"calctype": ct, "method": r.get("method", ""),
                                    "energy": e})
        if ct not in calctypes:
            calctypes.append(ct)
    return [by_mol[m] for m in order], calctypes


def _bar_values(groups, calctypes, mode):
    """Per (group, calctype) plotted value + the y-axis label, for the chosen
    mode: 'absolute' (Eh), 'rel_molecule' (kcal/mol vs the group's lowest),
    'rel_global' (kcal/mol vs the overall lowest)."""
    all_e = [b["energy"] for g in groups for b in g["bars"]]
    gmin = min(all_e) if all_e else 0.0
    if mode == "absolute":
        return (lambda g, b: b["energy"]), "final SCF energy (Eh)"
    if mode == "rel_global":
        return (lambda g, b: (b["energy"] - gmin) * HARTREE_TO_KCAL), \
               "relative energy (kcal/mol, vs global min)"
    # rel_molecule (default meaningful view): each group referenced to its own min
    mins = {g["molecule"]: min((b["energy"] for b in g["bars"]), default=0.0)
            for g in groups}
    return (lambda g, b: (b["energy"] - mins[g["molecule"]]) * HARTREE_TO_KCAL), \
           "relative energy (kcal/mol, per molecule)"


def draw_scf_bars(ax, groups, calctypes, mode="absolute", annotate=False):
    """Draw grouped bars onto `ax`: one group per molecule, one colour-coded bar
    per calculation type. Returns a list of bar-record dicts
    {rect, molecule, name, smiles, calctype, energy, value, x} so an interactive
    caller can map a hovered bar back to its molecule. Shared by the exporter and
    the SCFEnergyBarWindow so both look identical."""
    value_of, ylabel = _bar_values(groups, calctypes, mode)
    n_types = max(1, len(calctypes))
    type_color = {ct: color_of(i) for i, ct in enumerate(calctypes)}
    total_w = 0.8
    bw = total_w / n_types
    bar_recs = []
    seen_types = set()
    for gi, g in enumerate(groups):
        by_type = {b["calctype"]: b for b in g["bars"]}
        for ti, ct in enumerate(calctypes):
            b = by_type.get(ct)
            if b is None:
                continue
            x = gi + (ti - (n_types - 1) / 2.0) * bw
            val = value_of(g, b)
            label = ct if ct not in seen_types else None
            seen_types.add(ct)
            rect = ax.bar(x, val, width=bw * 0.92, color=type_color[ct],
                          edgecolor="#333333", linewidth=0.4, label=label)[0]
            bar_recs.append({"rect": rect, "molecule": g["molecule"], "name": g["name"],
                             "smiles": g.get("smiles"), "calctype": ct,
                             "energy": b["energy"], "value": val, "x": x})
            if annotate:
                ax.annotate("{:.4g}".format(val), xy=(x, val),
                            ha="center", va="bottom", fontsize=7, rotation=90,
                            xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g["molecule"] for g in groups], rotation=30, ha="right",
                       fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("molecule")
    ax.set_title("Final SCF energies by molecule and calculation type")
    if calctypes:
        ax.legend(title="calc type", fontsize=8, loc="best")
    ax.axhline(0.0, color="#888888", linewidth=0.6)
    ax.margins(x=0.02)
    return bar_recs


def render_scf_bar(records, mode="absolute"):
    """A Figure of the grouped SCF-energy bars, or None if no energies were found."""
    groups, calctypes = scf_bar_groups(records)
    if not groups:
        return None
    fig = _new_figure(figsize=(max(7.0, 1.4 * len(groups) + 3), 5.2))
    ax = fig.add_subplot(111)
    draw_scf_bars(ax, groups, calctypes, mode=mode, annotate=True)
    return fig


# --------------------------------------------------------- spectrum line figures

def _line_figure(traces, xlabel, ylabel, title, invert_x=False, stacked=False,
                 offset_frac=0.5):
    """One overlay/stacked line figure from `traces` = [{label, color, xs, ys}].
    Stacked lifts trace i by i*offset_frac*max_amplitude. Always draws a legend
    (the molecule attribution for a static image)."""
    fig = _new_figure()
    ax = fig.add_subplot(111)
    ymax = max((max(t["ys"]) for t in traces if t["ys"]), default=1.0) or 1.0
    for i, t in enumerate(traces):
        base = i * offset_frac * ymax if stacked else 0.0
        ax.plot(t["xs"], [y + base for y in t["ys"]], color=t["color"], lw=0.9,
                label=t["label"])
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title + (" (stacked)" if stacked else ""))
    ax.legend(fontsize=8, loc="best", framealpha=0.9)
    if invert_x:
        ax.set_xlim(max((max(t["xs"]) for t in traces if t["xs"]), default=1.0),
                    min((min(t["xs"]) for t in traces if t["xs"]), default=0.0))
    return fig


def _ir_traces(records):
    traces = []
    for r in records:
        if (r.get("calctype") or "").upper() != "FREQ":
            continue
        text = _archive.read_out(r)
        if not text:
            continue
        ir = _P.parse_ir(text)
        pairs = [(row["freq_cm"], row["intensity_km_mol"]) for row in ir
                 if abs(row["freq_cm"]) > 1.0]
        if not pairs:
            continue
        lo, hi = _S.auto_range([p[0] for p in pairs], min_pad=80.0)
        xs, ys = _S.broaden([p[0] for p in pairs], [p[1] for p in pairs], lo, hi,
                            n=1200, fwhm=20.0)
        traces.append({"label": "{} / {}".format(r["molecule"], r["recipe"]),
                       "color": color_of(len(traces)), "xs": xs, "ys": ys})
    return traces


def _uvvis_traces(records):
    traces = []
    for r in records:
        if (r.get("calctype") or "").upper() != "TDDFT":
            continue
        text = _archive.read_out(r)
        if not text:
            continue
        states = _P.parse_absorption_spectrum(text)
        pairs = [(s["wavelength_nm"], s["fosc"]) for s in states
                 if s.get("wavelength_nm") and s.get("fosc") is not None]
        if not pairs:
            continue
        lo, hi = _S.auto_range([p[0] for p in pairs], min_pad=40.0)
        xs, ys = _S.broaden([p[0] for p in pairs], [p[1] for p in pairs], lo, hi,
                            n=1200, fwhm=20.0, shape="gaussian")
        traces.append({"label": "{} / {}".format(r["molecule"], r["recipe"]),
                       "color": color_of(len(traces)), "xs": xs, "ys": ys})
    return traces


def _nmr_traces_by_element(records):
    """{element: [traces]} of broadened shielding sticks, one trace per NMR calc."""
    by_el = {}
    for r in records:
        if (r.get("calctype") or "").upper() != "NMR":
            continue
        text = _archive.read_out(r)
        if not text:
            continue
        sh = _P.parse_nmr_shieldings(text)
        elems = {}
        for row in sh:
            elems.setdefault(row["element"], []).append(row["isotropic_ppm"])
        for el, sigmas in elems.items():
            lo, hi = _S.auto_range(sigmas, min_pad=5.0)
            # Adaptive grid keeps sharp NMR lines crisp across a wide shielding range.
            grid = _S.adaptive_grid(sigmas, lo, hi, 2.0)
            xs, ys = _S.broaden_at(sigmas, None, grid, fwhm=2.0)
            by_el.setdefault(el, []).append(
                {"label": "{} / {}".format(r["molecule"], r["recipe"]),
                 "color": color_of(len(by_el.get(el, []))), "xs": xs, "ys": ys})
    return by_el


def _epr_traces(records):
    from orca_workbench.core import epr as _EPR
    traces = []
    for r in records:
        if (r.get("calctype") or "").upper() != "EPR":
            continue
        text = _archive.read_out(r)
        if not text:
            continue
        epr = _P.parse_epr(text)
        g_iso = ((epr or {}).get("g_tensor") or {}).get("g_iso")
        if not g_iso:
            continue
        try:
            sim = _EPR.simulate(g_iso, epr.get("hyperfine") or [], freq_GHz=9.5)
            xs, ys = sim["field_mT"], sim["derivative"]
        except Exception:
            continue
        traces.append({"label": "{} / {}".format(r["molecule"], r["recipe"]),
                       "color": color_of(len(traces)), "xs": xs, "ys": ys})
    return traces


# ------------------------------------------------------------------- entry point

def generate_all_figures(records, out_dir, img_format="svg", stacked=True,
                         offset_frac=0.5, log=None):
    # type: (List[dict], str, str, bool, float, Optional[Callable]) -> List[str]
    """Render every applicable figure into `out_dir` and return the written paths.
    Each figure family is independently guarded, so one failure (e.g. a malformed
    .out) doesn't abort the rest. `records` come from archive.collect_results."""
    log = log or (lambda m: None)
    if not matplotlib_available():
        log("matplotlib not available — skipping figure generation.")
        return []
    os.makedirs(out_dir, exist_ok=True)
    ext = (img_format or "svg").lower().lstrip(".")
    written = []

    def emit(fig, name):
        if fig is None:
            return
        path = os.path.join(out_dir, "{}.{}".format(name, ext))
        try:
            _save(fig, path, ext)
            written.append(path)
            log("wrote {}".format(os.path.basename(path)))
        except Exception as e:
            log("could not write {}: {}".format(name, e))

    # SCF energy bars — absolute always; a per-molecule relative view too when at
    # least one molecule has more than one calc type (so the comparison is real).
    try:
        groups, _cts = scf_bar_groups(records)
        if groups:
            emit(render_scf_bar(records, mode="absolute"), "SCF_final_energies")
            if any(len(g["bars"]) > 1 for g in groups):
                emit(render_scf_bar(records, mode="rel_molecule"),
                     "SCF_final_energies_relative")
    except Exception as e:
        log("SCF bar figure failed: {}".format(e))

    def emit_pair(traces, name, xlabel, ylabel, title, invert_x=False):
        if not traces:
            return
        emit(_line_figure(traces, xlabel, ylabel, title, invert_x=invert_x,
                          stacked=False), name)
        if stacked and len(traces) > 1:
            emit(_line_figure(traces, xlabel, ylabel, title, invert_x=invert_x,
                              stacked=True, offset_frac=offset_frac), name + "_stacked")

    try:
        emit_pair(_ir_traces(records), "IR_spectrum", r"wavenumber (cm$^{-1}$)",
                  "IR absorbance (a.u.)", "Simulated IR spectrum", invert_x=True)
    except Exception as e:
        log("IR figure failed: {}".format(e))
    try:
        emit_pair(_uvvis_traces(records), "UVVis_spectrum", "wavelength (nm)",
                  "absorbance (a.u.)", "Simulated UV-Vis spectrum")
    except Exception as e:
        log("UV-Vis figure failed: {}".format(e))
    try:
        for el, traces in _nmr_traces_by_element(records).items():
            emit_pair(traces, "NMR_{}_shieldings".format(el),
                      "isotropic shielding (ppm)", "intensity (a.u.)",
                      "Simulated {} NMR (shielding)".format(el), invert_x=True)
    except Exception as e:
        log("NMR figure failed: {}".format(e))
    try:
        emit_pair(_epr_traces(records), "EPR_spectrum", "field (mT)",
                  "dI/dB (a.u.)", "Simulated EPR spectrum (isotropic)")
    except Exception as e:
        log("EPR figure failed: {}".format(e))

    return written
