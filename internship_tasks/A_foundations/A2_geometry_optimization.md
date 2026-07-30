# A2 — What a Molecule Really Looks Like: Geometry Optimization

|                      |                                                                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **Level**            | A — Foundations (undergraduate)                                                                                                        |
| **Est. effort**      | Half a day                                                                                                                            |
| **Prerequisites**    | A1 (the potential energy surface, method vs. basis). Lewis structures and VSEPR from general chemistry. No prior geometry-optimization experience assumed. |
| **Software**         | ORCA (through ORCA Workbench). Every level is given below as an explicit ORCA keyword line.                                            |
| **You will produce** | A table of optimized bond lengths and angles vs. experiment, across basis sets, for a curated set of molecules                        |

> This script says *what* to compute and *what* to report. Working out how to make
> the software carry it out is part of the exercise — the molecules and levels are
> chosen for you (Section 2).

> **A note on the equations.** Maths is written in LaTeX between `$…$` (inline) and
> `$$…$$` (displayed); it renders in VS Code's Markdown preview and on GitHub, and
> exports to PDF with Pandoc (see the end). Raw `$…$` just means your viewer isn't
> rendering maths — the text still reads fine.

---

## 1. Background and theory

In A1 we computed the energy of a molecule sitting at **one fixed geometry**. But a real
molecule is not frozen in a shape we hand it — its nuclei settle into the arrangement
the electrons "prefer", the one where the total energy is as low as it can be. Finding
that arrangement, letting the structure itself relax rather than dictating it, is a
**geometry optimization**. It is the single most common calculation in all of chemistry,
because you need a sensible structure before almost any other property means anything.

### 1.1 The potential energy surface

Recall the Born–Oppenheimer picture from A1: for any frozen set of nuclear positions the
electrons find their lowest energy, so the molecular energy becomes a function of the
**nuclear coordinates alone**. That function is the **potential energy surface (PES)**:

$$E = E(\mathbf{R}),$$

- $\mathbf{R}$: the collection of all nuclear positions (the molecule's geometry).
- $E(\mathbf{R})$: the electronic energy the software (A1's machinery) returns at that
  geometry.

A molecule of $N$ atoms has $3N$ Cartesian coordinates, but shifting or rotating the
whole molecule doesn't change its energy, so the number of coordinates that *do* matter
— the **internal degrees of freedom** — is

$$
3N - 6 \quad(\text{non-linear molecule}), \qquad 3N - 5 \quad(\text{linear molecule}).
$$

These are the bond lengths, bond angles and dihedral angles. The PES is therefore a
surface in that many dimensions — impossible to draw for anything bigger than a
diatomic, but you reason about it the same way you would a hilly landscape.

### 1.2 Stationary points: valleys and passes

The interesting places on any landscape are the **flat** ones. At a **stationary point**
the slope in every direction is zero — i.e. the **gradient** of the energy vanishes:

$$\mathbf{g}(\mathbf{R}) \equiv \frac{\partial E}{\partial \mathbf{R}} = \mathbf{0}.$$

- $\mathbf{g}$: the **gradient** — how fast the energy changes as each coordinate moves.
- The **force** on the nuclei is just $\mathbf{F} = -\mathbf{g}$, so "zero gradient"
  literally means "no net force is pushing any atom anywhere."

Whether a flat point is a valley bottom or a mountain pass is decided by the
**curvature**, held in the matrix of second derivatives — the **Hessian**:

$$H_{ij} = \frac{\partial^2 E}{\partial R_i\, \partial R_j}.$$

Diagonalise it and look at the signs of its eigenvalues:

- **Minimum** — the energy curves *upward* in every direction (all Hessian eigenvalues
  positive). These are the stable structures: equilibrium geometries and conformers.
  *This is what an optimization looks for.*
- **First-order saddle point** — the energy curves *down* in exactly one direction (one
  negative eigenvalue). These are **transition states** — the passes between valleys —
  and are the subject of a later script.

### 1.3 What a geometry optimization actually does

*In words:* start from a guessed structure and repeatedly **step downhill along the
forces** — nudging every atom in the direction that lowers the energy — until the forces
and the step size both fall below a **convergence threshold**. ORCA does exactly this and
declares success when the gradient and the displacement are numerically zero.

Two facts follow directly, and you must respect both:

- **The result is the nearest *local* minimum, and it depends on where you start.** A
  poor initial guess can slide into a different valley — a higher-energy conformer, or a
  different isomer entirely. Optimization finds *a* minimum, not necessarily the global
  one.
- **A converged optimization proves only that the gradient is zero — a *stationary*
  point — not that it is a *minimum*.** The only rigorous check is a **frequency
  calculation** (the Hessian again): a true minimum has **no imaginary frequencies**.
  You will use that check properly in A3; here we start from sensible structures and add
  a `Freq` verification at the best level.

### 1.4 What sets a molecule's shape — VSEPR vs. the energy

Your first, back-of-the-envelope guess for a shape is **VSEPR** (Valence-Shell
Electron-Pair Repulsion): the electron domains around a central atom spread out to
minimise their mutual repulsion (linear, trigonal planar, tetrahedral, …), with lone
pairs claiming a little more room than bonding pairs. VSEPR is remarkably good — and it
is most *instructive* exactly where it strains:

- **lone-pair squeeze** — water's angle is ~104.5° and ammonia's ~107°, both pushed
  below the ideal tetrahedral 109.5° by their lone pairs;
- **hypervalent centres** — SF₄'s see-saw, PF₅'s trigonal bipyramid, where the tidy
  octet picture runs out and the role of d-orbitals is debated;
- subtler second-order distortions.

An electronic-structure optimization needs **none** of these rationalisations. It simply
lowers the energy and reports whatever geometry results — which is precisely why laying
the computed shape next to the VSEPR expectation is so illuminating: the calculation is
"seeing" physics the cartoon leaves out.

### 1.5 Equilibrium vs. experimental geometry — a subtle but important point

The structure you optimize is the **equilibrium geometry** $r_e$: the literal bottom of
the PES well. Experiment does **not** measure $r_e$. Spectroscopy and diffraction report
**vibrationally averaged** structures ($r_0, r_g, r_s, …$) at a finite temperature, and
because bonds are **anharmonic** (the theme of A3) the average bond sits slightly
*longer* than the equilibrium bottom of the well. So:

> A computed bond length within ~0.01 Å of experiment is **excellent**, and part of the
> residual gap is *real physics* (vibrational averaging), not a failing of the method.

Two practical rules of thumb, both of which you will test:

- **Geometries converge faster than energies.** Even modest levels give good structures —
  you rarely need the huge bases A1 demanded for its energies.
- **Polarization functions matter most for angles** and for atoms beyond the first row,
  because bending requires the orbital flexibility that polarization functions supply.

### 1.6 Symbol glossary

| Symbol        | Name                     | What it is / does                                              |
| ------------- | ------------------------ | ------------------------------------------------------------- |
| $\mathbf{R}$  | nuclear coordinates      | The molecule's geometry (all atom positions)                  |
| $E(\mathbf R)$| potential energy surface | Electronic energy as a function of geometry                   |
| $\mathbf{g}$  | energy gradient          | $\partial E/\partial\mathbf R$; zero at a stationary point    |
| $\mathbf{F}$  | force on nuclei          | $-\mathbf g$; drives the optimization steps                   |
| $H_{ij}$      | Hessian                  | Second derivatives; its eigenvalue signs classify the point   |
| $3N-6$        | internal DOF             | Shape-defining coordinates (bonds, angles, dihedrals)         |
| $r_e$         | equilibrium geometry     | Bottom of the PES well — what you optimize                    |
| $r_0$         | vibrationally averaged   | What experiment measures; slightly longer than $r_e$          |

---

## 2. The assignment (curated)

You will optimize a small, deliberately mixed set of molecules and compare the computed
structures to experiment across basis sets.

### 2.1 The molecule set

Four molecules — three "easy" textbook shapes with precise experimental geometries, and
one "awkward" case that stresses the simple picture:

| Molecule | Why it is here | Key parameters to read off |
| --- | --- | --- |
| **H₂O** | bent, lone-pair angle | O–H length, H–O–H angle (~104.5°) |
| **NH₃** | pyramidal, lone-pair angle | N–H length, H–N–H angle (~107°) |
| **CO₂** | linear, multiple bonds | C=O length, O–C–O angle (180°) |
| **SO₂** | *awkward*: bent, second-row S, needs polarization | S–O length, O–S–O angle (~119°) |

### 2.2 Levels + ORCA keyword lines

Keep the **method fixed (B3LYP)** and vary the **basis** so you isolate the effect of the
basis on geometry. The middle pair is chosen to expose polarization functions: `6-31G`
has **none**, `6-31G**` **adds** them (d on heavy atoms, p on H); `def2-TZVP` is a large
reference.

| Purpose | ORCA line |
| --- | --- |
| small basis, **no** polarization | `! B3LYP 6-31G Opt` |
| same size, **with** polarization | `! B3LYP 6-31G** Opt` |
| large reference | `! B3LYP def2-TZVP Opt` |
| **confirm it is a true minimum** (best level) | `! B3LYP def2-TZVP Opt Freq` |

**ORCA keyword notes (verified in ORCA 6.0.1):**

- `Opt` requests a geometry optimization; append `Freq` to follow it with a frequency
  calculation on the optimized structure. A genuine minimum reports **no imaginary
  frequencies** (ORCA lists any as negative numbers, e.g. `-123.4 cm**-1`).
- `6-31G` and `6-31G**` are the classic Pople bases; the `**` is shorthand for
  `(d,p)` polarization. All four lines above run as bare keyword lines.
- *(Optional method contrast)* to also see the *method* effect on geometry, repeat one
  molecule with `! HF def2-TZVP Opt` and compare to B3LYP.

### 2.3 Reading the geometry out

After an optimization ORCA writes the final structure (and Workbench stores the optimized
`.xyz`). Read the bond lengths and angles from that optimized geometry — in Workbench,
open the finished job's optimized structure, or read the `CARTESIAN COORDINATES` /
`Redundant Internal Coordinates` block near the end of the `.out`. Collect **experimental
reference geometries** (NIST CCCBDB or a spectroscopy table) for the comparison.

---

## 3. Deliverables and evaluation questions

Produce a **table**, per molecule, of computed vs. experimental bond lengths and angles
at each basis set (`6-31G`, `6-31G**`, `def2-TZVP`).

Then answer:

1. **Accuracy.** How close are your optimized geometries to experiment? Are **bond
   lengths** or **bond angles** reproduced more accurately?
2. **Polarization functions.** Going from `6-31G` to `6-31G**`, which molecule and which
   parameter changed most, and where did it barely matter? Rationalise it: where is
   angular flexibility most needed (think SO₂ and the bond angles)?
3. **VSEPR vs. reality.** For **SO₂**, what shape and angle does simple VSEPR predict, and
   does the calculation agree? If they differ, what is the calculation "seeing" that the
   cartoon misses?
4. **The residual.** Even at `def2-TZVP` a small discrepancy with experiment remains. How
   much of it could be the *equilibrium vs. vibrationally averaged* difference (Section
   1.5), and in which direction should that error point (computed $r_e$ shorter or longer
   than the measured $r_0$)?
5. **Convergence contrast with A1.** Did the *geometry* need as large a basis to look
   converged as A1's *energies* did? What does that say about which properties are
   "cheap" and which are "expensive"?
6. **The minimum check.** For your `Opt Freq` run, were all frequencies real? What would
   an imaginary frequency have told you about the structure you found?

---

## 4. Going further (optional)

- **Charge/spin response.** Re-optimize one molecule in a different charge or spin state
  (e.g. ionise it) and see how the shape responds — the bond-angle change on ionization
  can be dramatic.
- **Basin hopping.** Deliberately start an optimization from a badly distorted guess and
  confirm it still reaches the same minimum; then find a (floppier) molecule where a
  *different* starting guess reaches a *different* minimum.

---

## Appendix — rendering this script to PDF

`pandoc A2_geometry_optimization.md -o A2.pdf --pdf-engine=xelatex` (needs Pandoc + a TeX
engine such as MiKTeX or TinyTeX). View live maths in VS Code's Markdown preview or on
GitHub.

## References

- R. J. Gillespie & I. Hargittai, *The VSEPR Model of Molecular Geometry*.
- F. Jensen, *Introduction to Computational Chemistry* — optimization, the PES, Hessians.
- NIST Computational Chemistry Comparison and Benchmark Database (CCCBDB) — experimental
  geometries and level-of-theory benchmarks.
