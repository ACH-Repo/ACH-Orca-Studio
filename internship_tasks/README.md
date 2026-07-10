# ORCA Workbench — Internship Task Scripts

A set of self-contained computational-chemistry assignments, written in the style
of a university lab-course script (*Praktikumsskript*): each begins with a
substantial **theory section** that teaches the concepts, then states an
**assignment** and a set of **evaluation questions** to answer.

## How to use these

Each script tells you **what to calculate and what to report — not which buttons
to press.** Working out how to make your tools (ORCA, driven through ORCA
Workbench) produce the required numbers and plots is part of the exercise. If a
task turns out to be awkward or impossible to carry out smoothly, that is useful
signal in its own right — note it.

A script is "passed" when you can:
1. carry out every calculation it asks for, and
2. answer the evaluation questions correctly, with the plots/tables to back them.

## Levels

- **A — Foundations** (undergraduate): the language of the potential energy
  surface, single points, geometry, bonds, and orbitals.
- **B — Intermediate** (upper undergrad / early grad): conformers,
  thermochemistry, and the common spectroscopies.
- **C — Graduate / research**: excited states, open-shell systems, intermolecular
  interactions, reaction barriers.
- **D — Research capstones**: isotope effects, solvation, redox — the hard edges.

## Index (A — Foundations)

| # | Title | Core idea |
|---|-------|-----------|
| A1 | [Method & Basis-Set Convergence](A_foundations/A1_method_basis_convergence.md) | How accuracy and cost trade off across levels of theory |
| A2 | [Geometry Optimization](A_foundations/A2_geometry_optimization.md) | Finding a molecule's real shape on the potential energy surface |
| A3 | [Potential Energy Curves](A_foundations/A3_bond_potential_curve.md) | The anatomy of a bond, and how a method breaks it |
| A4 | [Frontier Orbitals](A_foundations/A4_frontier_orbitals.md) | Gaps, colour, and reactivity from HOMO/LUMO |

> Levels B–D will be added once the A set is validated in practice.

## A note on numbers

Where a script quotes an experimental value, treat it as a target to compare
against, not a value to reproduce exactly. Computed *equilibrium* quantities and
*measured* quantities differ for real physical reasons (vibrational averaging,
temperature, solvent) — understanding **why** a computed number differs from
experiment is often the whole point.
