"""Workflow model — a node graph describing a computational pipeline.

A workflow is a directed acyclic graph. Nodes are pipeline steps; typed ports
carry tokens between them:

  - geometry : a molecular structure flowing into a calculation
  - results  : computed properties (energies, frequencies, shieldings, …)

The graph is authored visually in the Workflow tab and persisted in project.json.
For Milestone 1 it is expanded *statically* (no conditionals) into the existing
PlannedCalc model: every calculation node, for every molecule emitted by the
Molecules source, becomes a PlannedCalc, and geometry edges become parent_id
links — so the whole thing runs through the normal build/submit/monitor path.

This module is pure (no Tkinter) so it is unit-testable and serialisable.
"""

import uuid
from typing import Dict, List, Optional, Tuple


# Node-type registry. Each: label, input ports, output ports (name, token type),
# kind (source/calc/sink), and which config keys it carries.
NODE_TYPES = {
    "molecules": {
        "label": "Molecules",
        "inputs": [],
        "outputs": [("geometry", "geometry")],
        "kind": "source",
        "config": {"mode": "all", "filenames": []},
    },
    "optimize": {
        "label": "Optimize",
        "inputs": [("geometry", "geometry")],
        # geometry: the optimised structure flows on to the next calc. results:
        # an OPT also yields reportable properties (optimised geometry, final
        # energy, trajectory, gradient), so it can feed a Report node directly.
        "outputs": [("geometry", "geometry"), ("results", "results")],
        "kind": "calc",
        # geom_spec: optional geometry constraints / a relaxed scan (core.geomspec),
        # applied to every molecule this node optimizes. None = plain optimization.
        "config": {"recipe": "", "geom_spec": None},
    },
    "frequencies": {
        "label": "Frequencies",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry"), ("results", "results")],
        "kind": "calc",
        "config": {"recipe": ""},
    },
    # Any single-geometry property calculation: SP, NMR, dipole, AND TD-DFT
    # vertical UV-Vis (excited states ARE a property — geometry in, results out).
    # Which property is run is the recipe's job (calctype drives the report
    # extractors + plot buttons). Excited-state geometry RELAXATION (S1 opt,
    # fluorescence/0-0) is NOT a property — use an Optimize node with a TD-DFT opt
    # recipe, which outputs the relaxed geometry.
    "property": {
        "label": "Property",
        "inputs": [("geometry", "geometry")],
        "outputs": [("results", "results")],
        "kind": "calc",
        "config": {"recipe": ""},
    },
    "condition": {
        "label": "Condition",
        "inputs": [("in", "geometry")],
        "outputs": [("pass", "geometry")],
        "kind": "gate",
        "config": {"predicate": "no_imaginary_freqs"},
    },
    # A Filter restricts WHICH molecules continue downstream by identity (filename
    # substring or index range) — a *static* subset chosen by the user. Distinct
    # from Condition, which gates on a calculation's *result* at runtime. Geometry
    # passes through unchanged; molecules that don't match simply get no
    # downstream calcs.
    "filter": {
        "label": "Filter",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry")],
        "kind": "filter",
        "config": {"mode": "include", "kind": "substring", "pattern": ""},
    },
    # Geometry builders (run at EXPAND time, before any calculation exists):
    # Transform applies an ordered list of rigid moves / alignments / dihedral
    # edits (core.transform ops) to every molecule flowing through it, emitting
    # NEW derived molecules. Combine appends the geometries arriving on its
    # (fan-in) input into one merged structure — place/rotate fragments with
    # Transform nodes first, then Combine. Both must sit BEFORE the calc nodes
    # (they read each molecule's current .xyz when the graph is expanded).
    "transform": {
        "label": "Transform",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry")],
        "kind": "transform",
        "config": {"ops": []},
    },
    "combine": {
        "label": "Combine",
        "inputs": [("geometry", "geometry")],
        "outputs": [("geometry", "geometry")],
        "kind": "combine",
        # 'geometry' inputs normally take ONE wire; Combine's is variadic —
        # every inbound wire contributes its molecules (in connection order).
        "fan_in": ("geometry",),
        "config": {"name": "", "mode": "merge", "charge": None, "mult": None},
    },
    "report": {
        "label": "Report",
        "inputs": [("results", "results")],
        "outputs": [],
        "kind": "sink",
        # format: "both" (json+csv) | "json" | "csv". csv_columns is None (= all
        # default columns) or an ordered list of {"key","header"} the user
        # curated. csv_missing is the cell value for an absent property.
        "config": {"name": "report", "extractors": None, "format": "both",
                   "csv_columns": None, "csv_missing": ""},
    },
    # A "builder" is a meta-node that does NOT expand through expand_to_calcs;
    # it runs its own action (here, the two-step ZPVA builder) from the config
    # panel. ZPVA reads the .hess of a finished upstream Frequencies job, then
    # generates the mode-displaced single-points and, once they finish, averages
    # the chosen property (with optional isotopologue shifts).
    "zpva": {
        "label": "ZPVA",
        "inputs": [("geometry", "geometry")],
        "outputs": [("results", "results")],
        "kind": "builder",
        "config": {"recipe": "", "property": "nmr_shielding", "target": "",
                   "dq": 1.0, "isotopologues": "", "manifests": []},
    },
    # Annotations: free-floating boxes that are NOT part of the computation (no
    # ports; ignored by expand_to_calcs/validate). 'comment' is a text note;
    # 'frame' is a titled group box drawn behind the nodes and drags the nodes it
    # contains. Both carry their own size (w/h in config) and are resizable.
    "comment": {
        "label": "Comment",
        "inputs": [],
        "outputs": [],
        "kind": "annotation",
        "config": {"text": "Comment", "w": 200.0, "h": 90.0},
    },
    "frame": {
        "label": "Frame",
        "inputs": [],
        "outputs": [],
        "kind": "annotation",
        "config": {"title": "Group", "w": 260.0, "h": 180.0},
    },
}

CALC_NODE_TYPES = {t for t, d in NODE_TYPES.items() if d["kind"] == "calc"}
BUILDER_NODE_TYPES = {t for t, d in NODE_TYPES.items() if d["kind"] == "builder"}
ANNOTATION_NODE_TYPES = {t for t, d in NODE_TYPES.items() if d["kind"] == "annotation"}
# Nodes that CREATE geometry at expand time (before any calc runs).
GEOMETRY_NODE_TYPES = {t for t, d in NODE_TYPES.items()
                       if d["kind"] in ("transform", "combine")}


# Condition predicates: evaluated at runtime on the .out of the calculation
# feeding the condition. label is for the picker; the eval is in eval_predicate.
PREDICATES = {
    "no_imaginary_freqs": "No imaginary frequencies (a true minimum)",
    "has_imaginary_freqs": "Has an imaginary frequency (e.g. a transition state)",
    "terminated_ok": "Terminated normally",
}


def eval_predicate(name, out_text):
    # type: (str, str) -> bool
    """Evaluate a condition predicate against a calculation's .out text."""
    from orca_workbench.core import orca_parser as P
    if not out_text:
        return False
    if name == "terminated_ok":
        return bool(P._TERM_OK.search(out_text))
    if name in ("no_imaginary_freqs", "has_imaginary_freqs"):
        vibs = P.real_frequencies(P.parse_frequencies(out_text))
        n_imag = sum(1 for f in vibs if f < 0)
        if name == "no_imaginary_freqs":
            return len(vibs) > 0 and n_imag == 0
        return n_imag > 0
    return True


def filter_matches(config, mol_filename, index, n_total):
    # type: (dict, str, int, int) -> bool
    """Whether a molecule passes a Filter node.

    config keys: mode ('include' keeps matches, 'exclude' drops them); kind
    ('substring' = comma-separated substrings matched against the filename;
    'index' = an index-range spec like '0-3,5' over the molecule's position in
    its source set); pattern (the text). An empty pattern matches everything.
    """
    pattern = (config.get("pattern") or "").strip()
    mode = config.get("mode", "include")
    kind = config.get("kind", "substring")
    if not pattern:
        matched = True
    elif kind == "index":
        from orca_workbench.core.coords import parse_structure_selection
        matched = index in parse_structure_selection(pattern, n_total)
    else:
        toks = [t.strip().lower() for t in pattern.split(",") if t.strip()]
        matched = any(t in mol_filename.lower() for t in toks) if toks else True
    return matched if mode == "include" else not matched


def gate_outcome(predicate, source_done, source_out_text):
    # type: (str, bool, Optional[str]) -> str
    """Resolve a conditional gate to 'pending' | 'open' | 'closed'.

    A gated calc runs only when its source calc has finished AND the predicate
    holds. While the source is still running we don't know yet (pending).
    """
    if not source_done:
        return "pending"
    return "open" if eval_predicate(predicate, source_out_text or "") else "closed"


def _geometry_input_port(node):
    for name, t in node.inputs():
        if t == "geometry":
            return name
    return None


def _new_id():
    return uuid.uuid4().hex[:10]


class WorkflowNode(object):
    def __init__(self, type, x=40.0, y=40.0, config=None, id=None):
        # type: (str, float, float, Optional[dict], Optional[str]) -> None
        self.id = id or _new_id()
        self.type = type
        self.x = float(x)
        self.y = float(y)
        # start from the type's default config, overlaid with any given values
        base = dict(NODE_TYPES.get(type, {}).get("config", {}))
        if config:
            base.update(config)
        self.config = base

    @property
    def label(self):
        return NODE_TYPES.get(self.type, {}).get("label", self.type)

    @property
    def kind(self):
        return NODE_TYPES.get(self.type, {}).get("kind", "calc")

    def inputs(self):
        return NODE_TYPES.get(self.type, {}).get("inputs", [])

    def outputs(self):
        return NODE_TYPES.get(self.type, {}).get("outputs", [])

    def port_type(self, port_name, is_input):
        ports = self.inputs() if is_input else self.outputs()
        for name, ptype in ports:
            if name == port_name:
                return ptype
        return None

    def to_dict(self):
        return {"id": self.id, "type": self.type, "x": self.x, "y": self.y,
                "config": self.config}

    @classmethod
    def from_dict(cls, d):
        return cls(type=d["type"], x=d.get("x", 40), y=d.get("y", 40),
                   config=d.get("config"), id=d.get("id"))


class WorkflowEdge(object):
    def __init__(self, src_node, src_port, dst_node, dst_port, id=None):
        self.id = id or _new_id()
        self.src_node = src_node
        self.src_port = src_port
        self.dst_node = dst_node
        self.dst_port = dst_port

    def to_dict(self):
        return {"id": self.id, "src_node": self.src_node, "src_port": self.src_port,
                "dst_node": self.dst_node, "dst_port": self.dst_port}

    @classmethod
    def from_dict(cls, d):
        return cls(d["src_node"], d["src_port"], d["dst_node"], d["dst_port"], id=d.get("id"))


class Workflow(object):
    def __init__(self, nodes=None, edges=None, category="wf"):
        self.nodes = nodes or []          # type: List[WorkflowNode]
        self.edges = edges or []          # type: List[WorkflowEdge]
        self.category = category

    # ---- serialisation ----

    def to_dict(self):
        return {"category": self.category,
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges]}

    @classmethod
    def from_dict(cls, d):
        if not d:
            return cls()
        return cls(nodes=[WorkflowNode.from_dict(n) for n in d.get("nodes", [])],
                   edges=[WorkflowEdge.from_dict(e) for e in d.get("edges", [])],
                   category=d.get("category", "wf"))

    # ---- lookups ----

    def node(self, node_id):
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def edge(self, edge_id):
        for e in self.edges:
            if e.id == edge_id:
                return e
        return None

    def network_sources(self, node_ids):
        """The Molecules-source node ids in the connected components (treating
        edges as undirected) that contain any of the given node ids. Used to run
        just the pipeline(s) the selected nodes belong to."""
        if not node_ids:
            return set()
        adj = {}
        for e in self.edges:
            adj.setdefault(e.src_node, set()).add(e.dst_node)
            adj.setdefault(e.dst_node, set()).add(e.src_node)
        seen = set()
        stack = list(node_ids)
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            stack.extend(adj.get(nid, ()))
        return {n.id for n in self.nodes if n.type == "molecules" and n.id in seen}

    def traces_to_type(self, node_id, node_type):
        # type: (str, str) -> bool
        """True if `node_id` is, or has a geometry-input ancestor that is, of
        `node_type`. Used to decide whether a node with an upstream prerequisite
        (e.g. ZPVA, which needs a Frequencies job's .hess) belongs downstream of a
        given port — a check stronger than raw port-type compatibility."""
        cur = self.node(node_id)
        seen = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            if cur.type == node_type:
                return True
            port = None
            for name, t in cur.inputs():
                if t == "geometry":
                    port = name
                    break
            ein = self.edges_into(cur.id, port) if port else []
            if not ein:
                return False
            cur = self.node(ein[0].src_node)
        return False

    def edges_into(self, node_id, port=None):
        return [e for e in self.edges if e.dst_node == node_id
                and (port is None or e.dst_port == port)]

    def edges_out(self, node_id, port=None):
        return [e for e in self.edges if e.src_node == node_id
                and (port is None or e.src_port == port)]

    # ---- mutation ----

    def add_node(self, type, x=40.0, y=40.0, config=None):
        n = WorkflowNode(type, x, y, config)
        self.nodes.append(n)
        return n

    def remove_node(self, node_id):
        self.nodes = [n for n in self.nodes if n.id != node_id]
        self.edges = [e for e in self.edges if e.src_node != node_id and e.dst_node != node_id]

    def remove_edge(self, edge_id):
        self.edges = [e for e in self.edges if e.id != edge_id]

    def can_connect(self, src_node, src_port, dst_node, dst_port):
        # type: (str, str, str, str) -> Tuple[bool, str]
        if src_node == dst_node:
            return False, "can't connect a node to itself"
        sn, dn = self.node(src_node), self.node(dst_node)
        if sn is None or dn is None:
            return False, "node missing"
        st = sn.port_type(src_port, is_input=False)
        dt = dn.port_type(dst_port, is_input=True)
        if st is None or dt is None:
            return False, "port missing"
        if st != dt:
            return False, "type mismatch ({} -> {})".format(st, dt)
        # 'results' inputs fan in (a Report merges many results into one file);
        # 'geometry' inputs take a single structure — except ports a node type
        # declares variadic via "fan_in" (Combine merges many geometries).
        fan_in = dst_port in NODE_TYPES.get(dn.type, {}).get("fan_in", ())
        if dt != "results" and not fan_in and self.edges_into(dst_node, dst_port):
            return False, "input already connected"
        # don't allow the exact same edge twice
        for e in self.edges_into(dst_node, dst_port):
            if e.src_node == src_node and e.src_port == src_port:
                return False, "already connected"
        if self._would_cycle(src_node, dst_node):
            return False, "would create a cycle"
        return True, ""

    def add_edge(self, src_node, src_port, dst_node, dst_port):
        ok, why = self.can_connect(src_node, src_port, dst_node, dst_port)
        if not ok:
            return None, why
        e = WorkflowEdge(src_node, src_port, dst_node, dst_port)
        self.edges.append(e)
        return e, ""

    def _would_cycle(self, src_node, dst_node):
        # adding src->dst makes a cycle iff src is already reachable from dst
        seen = set()
        stack = [dst_node]
        while stack:
            cur = stack.pop()
            if cur == src_node:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            for e in self.edges_out(cur):
                stack.append(e.dst_node)
        return False

    def topo_order(self):
        # type: () -> Optional[List[str]]
        """Kahn's algorithm. Returns node ids in dependency order, or None if cyclic."""
        indeg = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            if e.dst_node in indeg:
                indeg[e.dst_node] += 1
        queue = [nid for nid, d in indeg.items() if d == 0]
        order = []
        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for e in self.edges_out(nid):
                indeg[e.dst_node] -= 1
                if indeg[e.dst_node] == 0:
                    queue.append(e.dst_node)
        return order if len(order) == len(self.nodes) else None

    # ---- validation ----

    def has_calc_downstream(self, node_id):
        # type: (str) -> bool
        """True if any descendant of `node_id` is a calculation node. Used to
        insist a Combine explicitly set charge/multiplicity once its merged
        molecule actually feeds a calculation (the auto sum/ferromagnetic guess
        is rarely what you want for a real job)."""
        seen = set()
        stack = [e.dst_node for e in self.edges_out(node_id)]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = self.node(nid)
            if n is None:
                continue
            if n.type in CALC_NODE_TYPES:
                return True
            stack.extend(e.dst_node for e in self.edges_out(nid))
        return False

    def combine_needs_charge_mult(self):
        # type: () -> list
        """Combine nodes that feed a calculation but haven't had BOTH charge and
        multiplicity set explicitly. Returns [(node_id, [missing keys]), ...]."""
        out = []
        for n in self.nodes:
            if n.type != "combine" or not self.has_calc_downstream(n.id):
                continue
            missing = [k for k in ("charge", "mult") if n.config.get(k) is None]
            if missing:
                out.append((n.id, missing))
        return out

    def has_calc_upstream(self, node_id):
        # type: (str) -> bool
        """True if any ancestor (over every inbound edge) is a calculation /
        condition / builder node. Used to keep Transform/Combine in the
        geometry-prep zone: they read each molecule's CURRENT .xyz at expand
        time, so a not-yet-run upstream calc could not feed them."""
        seen = set()
        stack = [e.src_node for e in self.edges_into(node_id)]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = self.node(nid)
            if n is None:
                continue
            if n.type in CALC_NODE_TYPES or n.kind in ("gate", "builder"):
                return True
            stack.extend(e.src_node for e in self.edges_into(nid))
        return False

    def validate(self):
        # type: () -> List[str]
        issues = []
        sources = [n for n in self.nodes if n.type == "molecules"]
        if len(sources) == 0:
            issues.append("No Molecules source node.")
        elif len(sources) > 1:
            # Informational, not a blocker: each Molecules node is expanded as its
            # own independent network over its own molecule set (verified in
            # expand_to_calcs, which iterates every source). Calcs that would
            # collide on the same molecule+category+recipe target are de-duplicated
            # to a single run dir, so overlapping sources reuse rather than clash.
            issues.append("Multiple Molecules nodes — each expands its own molecule "
                          "set as an independent network (overlapping targets are merged).")
        for n in self.nodes:
            if n.type in CALC_NODE_TYPES:
                if not n.config.get("recipe"):
                    issues.append("{} node has no recipe selected.".format(n.label))
                if not self.edges_into(n.id, "geometry"):
                    issues.append("{} node has no geometry input connected.".format(n.label))
            elif n.type == "condition":
                fin = self.edges_into(n.id, "in")
                if not fin:
                    issues.append("Condition node has no input connected.")
                else:
                    feeder = self.node(fin[0].src_node)
                    if feeder is None or feeder.type not in CALC_NODE_TYPES:
                        issues.append("Condition must be fed by a calculation node (so there's "
                                      "a result to test).")
            elif n.type == "filter":
                if not self.edges_into(n.id, "geometry"):
                    issues.append("Filter node has no input connected.")
            elif n.type == "transform":
                if not self.edges_into(n.id, "geometry"):
                    issues.append("Transform node has no input connected.")
                try:
                    from orca_workbench.core import transform as transform_mod
                    for msg in transform_mod.validate_ops(n.config.get("ops") or []):
                        issues.append("Transform: " + msg)
                except ImportError:
                    pass
                if self.has_calc_upstream(n.id):
                    issues.append("Transform reads geometries when the graph is expanded "
                                  "— place it BEFORE the calculation nodes (between "
                                  "Molecules and the first Optimize/Property step).")
            elif n.type == "combine":
                if not self.edges_into(n.id, "geometry"):
                    issues.append("Combine node has no inputs connected.")
                if self.has_calc_upstream(n.id):
                    issues.append("Combine reads geometries when the graph is expanded "
                                  "— place it before the calculation nodes.")
                if self.has_calc_downstream(n.id):
                    miss = [k for k in ("charge", "mult") if n.config.get(k) is None]
                    if miss:
                        issues.append("Combine feeds a calculation: set its {} explicitly "
                                      "(the auto guess is rarely right for a merged "
                                      "molecule).".format(" and ".join(
                                          {"charge": "charge", "mult": "multiplicity"}[k]
                                          for k in miss)))
        if self.topo_order() is None:
            issues.append("The graph contains a cycle.")
        return issues


def compute_streams(workflow, source_mols, transform_apply=None):
    # type: (Workflow, Dict[str, List[str]], Optional[callable]) -> Tuple[Dict[str, List[str]], List[str]]
    """Resolve the molecule stream (ordered filenames) each node EMITS.

    `source_mols` maps each Molecules node id to its (selection-applied)
    molecule list. Walking the graph in topological order:

      * molecules  -> its entry in source_mols
      * filter     -> the subset of its input stream that matches
      * transform  -> `transform_apply(node, [in_stream])` — new derived names
      * combine    -> `transform_apply(node, [stream per inbound wire])`
      * calc/condition -> pass the input stream through unchanged (an OPT
        changes coordinates but not molecule identity — parent links carry that)

    `transform_apply(node, streams)` is the injected geometry backend (the UI
    materialises derived molecules; tests fake it). It gets a LIST of streams —
    one for a Transform, one per inbound wire (in connection order) for a
    Combine — and returns the node's output filename list. With no backend a
    Transform passes molecules through untouched and a Combine emits nothing
    (with a warning): pure callers see the old behaviour.
    """
    warnings = []
    streams = {}  # type: Dict[str, List[str]]
    for nid in (workflow.topo_order() or []):
        node = workflow.node(nid)
        t = node.type
        if t == "molecules":
            streams[nid] = list(source_mols.get(nid, []))
            continue
        port = _geometry_input_port(node)
        ein = workflow.edges_into(nid, port) if port else []
        instream = list(streams.get(ein[0].src_node, [])) if ein else []
        if t == "filter":
            n_tot = len(instream)
            streams[nid] = [m for i, m in enumerate(instream)
                            if filter_matches(node.config, m, i, n_tot)]
        elif t == "transform":
            ops = node.config.get("ops") or []
            if not instream or not ops or transform_apply is None:
                streams[nid] = instream
            else:
                try:
                    streams[nid] = list(transform_apply(node, [instream]))
                except Exception as e:
                    warnings.append("Transform failed: {}".format(e))
                    streams[nid] = []
        elif t == "combine":
            gathered = []
            for e in ein:
                s = list(streams.get(e.src_node, []))
                if s:
                    gathered.append(s)
                else:
                    src = workflow.node(e.src_node)
                    warnings.append(
                        "Combine: the wire from '{}' carries no molecules — that "
                        "input was skipped (check the upstream node's selection / "
                        "ops).".format(src.label if src else e.src_node))
            if not gathered:
                streams[nid] = []
            elif transform_apply is None:
                warnings.append("Combine needs the app's geometry backend — skipped.")
                streams[nid] = []
            else:
                try:
                    streams[nid] = list(transform_apply(node, gathered))
                except Exception as e:
                    warnings.append("Combine failed: {}".format(e))
                    streams[nid] = []
        else:
            # calc / condition / anything else with a geometry input: identity.
            streams[nid] = instream
    return streams, warnings


def expand_to_calcs(workflow, molecule_filenames, planned_calc_factory, source_ids=None,
                    transform_apply=None):
    # type: (Workflow, List[str], callable, Optional[set], Optional[callable]) -> Tuple[list, List[str], Dict[str, list]]
    """Expand a workflow into PlannedCalcs, attaching conditional gates.

    If `source_ids` is given, only the networks rooted at those Molecules nodes
    are expanded (used to run one pipeline of several on the canvas).

    Each calc node runs once per molecule in the stream ARRIVING at it (see
    compute_streams — filters subset it, Transform/Combine nodes replace it
    with derived molecules via the injected `transform_apply` backend). A calc
    whose geometry comes from the source / a Transform / a Combine uses
    geometry_source 'initial' (their .xyz already IS the input geometry); one
    fed by an upstream Optimize uses 'parent:<that node's calc for this
    molecule>'.

    `planned_calc_factory(molecule, recipe_name, category, geometry_source,
    parent_id, gate, origin_node)` builds OR reuses a PlannedCalc (passed in to
    keep this module free of UI imports). `gate` is None, or {"source": calc_id,
    "predicate": name} when the geometry path crosses a Condition node — the calc
    then runs only if that predicate holds on the source calc's output.
    `origin_node` is the graph node id, so the factory can reuse an existing calc
    for the same (node, molecule) instead of creating a duplicate.

    Returns (calcs, warnings, node_map) where node_map maps each calc node id to
    the list of calc ids it produced (one per molecule), for live UI coloring.
    """
    warnings = []
    order = workflow.topo_order()
    if order is None:
        return [], ["Graph has a cycle — cannot expand."], {}

    sources = [n for n in workflow.nodes if n.type == "molecules"]
    if not sources:
        return [], ["No Molecules source node."], {}

    for n in workflow.nodes:
        if n.type == "report":
            warnings.append("Report results are written when the pipeline finishes (Run "
                            "pipeline). 'Generate only' just creates the calculations.")
            break

    # Per-source molecule lists (selection mode applied). A source outside
    # `source_ids` contributes an empty stream, so its whole network is skipped.
    source_mols = {}
    for src in sources:
        if source_ids is not None and src.id not in source_ids:
            source_mols[src.id] = []
            continue
        mols = list(molecule_filenames)
        cfg = src.config
        if cfg.get("mode") == "selection" and cfg.get("filenames"):
            sel = set(cfg["filenames"])
            mols = [m for m in mols if m in sel]
        if not mols:
            warnings.append("A Molecules node selects no molecules — its network was skipped.")
        source_mols[src.id] = mols

    streams, s_warnings = compute_streams(workflow, source_mols, transform_apply)
    warnings.extend(s_warnings)

    def root_source(node):
        """Walk the geometry path all the way back (through calc / condition /
        transform nodes too) to a Molecules source feeding this node, or None.
        (Through a Combine it follows the FIRST inbound wire — only reachability
        matters here; molecule lists come from the streams.)"""
        cur = node
        guard = 0
        while guard < 200:
            guard += 1
            port = _geometry_input_port(cur)
            ein = workflow.edges_into(cur.id, port) if port else []
            if not ein:
                return None
            src = workflow.node(ein[0].src_node)
            if src is None:
                return None
            if src.type == "molecules":
                return src.id
            cur = src

    calc_for = {}  # (node_id, molecule) -> calc id

    def resolve(node, mol):
        """Walk back along the geometry path, passing through condition /
        frequencies / property nodes (none of which produce a *new* optimized
        geometry) to the nearest optimize node (parent), a Transform/Combine
        (whose materialised .xyz IS the initial geometry), or the molecules
        source (initial). If the path crosses a condition node, attach a gate
        keyed on the calc feeding that condition."""
        gate = None
        cur = node
        guard = 0
        while guard < 200:
            guard += 1
            port = _geometry_input_port(cur)
            ein = workflow.edges_into(cur.id, port) if port else []
            if not ein:
                return "initial", None, gate
            src = workflow.node(ein[0].src_node)
            if src is None or src.type == "molecules":
                return "initial", None, gate
            if src.type in GEOMETRY_NODE_TYPES:
                return "initial", None, gate
            if src.type == "optimize":
                pid = calc_for.get((src.id, mol))
                if pid:
                    return "parent:" + pid, pid, gate
                return "initial", None, gate
            if src.type == "condition":
                if gate is None:
                    cin = workflow.edges_into(src.id, "in")
                    if cin:
                        fid = calc_for.get((cin[0].src_node, mol))
                        if fid:
                            gate = {"source": fid,
                                    "predicate": src.config.get("predicate", "terminated_ok")}
                cur = src
                continue
            if src.type in ("frequencies", "property", "filter"):
                cur = src
                continue
            return "initial", None, gate

    calcs = []
    node_map = {}  # node_id -> [calc id, ...] across molecules (for live coloring)
    for nid in order:
        node = workflow.node(nid)
        if node.type not in CALC_NODE_TYPES:
            continue
        ein = workflow.edges_into(node.id, "geometry")
        if not ein:
            continue  # validated elsewhere
        if root_source(node) is None:
            warnings.append("{} isn't connected to a Molecules source — skipped."
                            .format(node.label))
            continue
        feeder_id = ein[0].src_node
        for mol in streams.get(feeder_id, []):
            geometry_source, parent_id, gate = resolve(node, mol)
            calc = planned_calc_factory(mol, node.config.get("recipe", ""),
                                        workflow.category, geometry_source, parent_id,
                                        gate, node.id)
            calc_for[(node.id, mol)] = calc.id
            node_map.setdefault(node.id, []).append(calc.id)
            calcs.append(calc)
    if not calcs and not warnings:
        warnings.append("No calculation nodes are connected to a Molecules source.")
    return calcs, warnings, node_map
