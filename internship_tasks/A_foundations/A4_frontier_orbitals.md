# A4 — Frontier Orbitals: Gaps, Colour, and Reactivity

|                      |                                                                                                                             |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Level**            | A — Foundations (undergraduate)                                                                                              |
| **Est. effort**      | Half a day                                                                                                                   |
| **Prerequisites**    | A1 (method vs. basis). Molecular orbitals (LCAO), the particle-in-a-box, and conjugation from general/physical chemistry.   |
| **Software**         | ORCA (through ORCA Workbench). Levels are given below as explicit ORCA keyword lines; orbital pictures come from Workbench's cube tool. |
| **You will produce** | HOMO–LUMO gaps across a conjugated series, a scaling plot, and HOMO/LUMO visualizations                                      |

> This script says *what* to compute and *what* to report. Working out how to make
> the software carry it out is part of the exercise — the series and levels are chosen
> for you (Section 2).

> **A note on the equations.** Maths is written in LaTeX between `$…$` (inline) and
> `$$…$$` (displayed); it renders in VS Code's Markdown preview and on GitHub, and
> exports to PDF with Pandoc (see the end). Raw `$…$` just means your viewer isn't
> rendering maths.

---

## 1. Background and theory

In A1 we built the many-electron wavefunction out of **orbitals**. Fill those orbitals
from the bottom up, like a ladder, and two of them turn out to matter far more than all
the others for how a molecule *behaves*. This script is about those two — the **frontier
orbitals** — and how much real chemistry (colour, reactivity, stability) you can read off
from the tiny energy gap between them.

### 1.1 The frontier orbitals

Electrons fill a ladder of molecular orbitals from the bottom. The two rungs at the
frontier between filled and empty are the ones that do chemistry:

- **HOMO** — the **H**ighest **O**ccupied **M**olecular **O**rbital: the most loosely held
  electrons, the ones a molecule most readily **donates**.
- **LUMO** — the **L**owest **U**noccupied **M**olecular **O**rbital: the cheapest place to
  put an extra electron, the one a molecule most readily **accepts**.

By **Koopmans' theorem**, for Hartree–Fock the frontier orbital energies approximate two
measurable quantities:

$$-\varepsilon_\text{HOMO} \approx \text{IE} \quad(\text{ionization energy}),
\qquad -\varepsilon_\text{LUMO} \approx \text{EA} \quad(\text{electron affinity}).$$

- $\varepsilon_\text{HOMO}, \varepsilon_\text{LUMO}$: the HOMO and LUMO orbital energies
  (negative for bound orbitals).

*In words:* removing the easiest electron costs about $-\varepsilon_\text{HOMO}$, and
adding one releases about $-\varepsilon_\text{LUMO}$. It is a useful mnemonic with real
caveats: it ignores how the other orbitals **relax** when you add or remove an electron,
and in **DFT** the orbital energies mean something different again (Section 1.5).

### 1.2 Why the HOMO–LUMO gap matters

Define the gap as

$$\Delta E_\text{H-L} = \varepsilon_\text{LUMO} - \varepsilon_\text{HOMO}.$$

It is a first, cheap proxy for several *very* different physical quantities:

- **Colour / lowest excitation.** Promoting an electron from HOMO to LUMO is, roughly,
  the lowest electronic excitation, so the gap sets a first estimate of the absorption
  energy — and hence the wavelength, via the photon relation
  $$\Delta E = \frac{hc}{\lambda} \;\Longrightarrow\; \lambda = \frac{hc}{\Delta E}.$$
  A small gap → absorbs visible light → the molecule is **coloured**.
- **Chemical hardness / kinetic stability.** A large gap means the electrons resist being
  rearranged: the molecule is **hard**, unreactive, kinetically stable. Chemical hardness
  is $\eta \approx \tfrac12\,\Delta E_\text{H-L}$.
- **Conductivity**, in extended/materials contexts — the molecular analogue of a band gap.

### 1.3 The particle-in-a-box picture of conjugation

For a **conjugated π system** — a chain of alternating double bonds — the π electrons are
**delocalized** over the whole framework, behaving much like a **particle in a
one-dimensional box** of length $L$. The box energy levels are

$$E_n = \frac{n^2 h^2}{8 m L^2},$$

- $n$: the level index ($1, 2, 3, …$).
- $L$: the length of the box — here, the length of the conjugated chain.
- $m$: the electron mass.

The lowest excitation (from the highest filled level to the first empty one) then scales
as

$$\Delta E \;\propto\; \frac{1}{L^2},$$

so the gap **shrinks** as the conjugation lengthens — each added double bond lengthens the
box and narrows the gap. This "free-electron model" explains a wealth of real chemistry:
why longer polyenes and cyanine dyes absorb at ever longer wavelengths (β-carotene, with
11 conjugated double bonds, is orange; short polyenes are colourless), and it is the
design principle behind tuning a dye's colour by changing its length.

### 1.4 Frontier orbitals and reactivity

Beyond their *energies*, the **shapes** of the frontier orbitals predict *where* a
molecule reacts (Fukui / frontier molecular orbital theory):

- the **HOMO** shows where a molecule is most **nucleophilic** (donates from);
- the **LUMO** shows where it is most **electrophilic** (accepts into);
- the number and placement of **nodes** (sign changes in the orbital) grows as you climb
  the ladder, and the *symmetry* of the frontier orbitals governs whether pericyclic
  reactions are allowed (the Woodward–Hoffmann rules).

### 1.5 The honest caveat

The HOMO–LUMO gap is a **qualitative** tool, not a spectroscopic observable — and part of
this exercise is to feel exactly how far the simple picture can be pushed:

- The true lowest absorption energy is **not** the bare orbital gap. The real excitation
  involves electron–electron interaction and the relaxation of the orbitals in the
  excited state, which needs proper excited-state theory (**TD-DFT**, a later script). The
  orbital gap systematically differs from the real optical gap.
- **DFT orbital energies (and hence the gap) depend strongly on the functional** — GGAs
  give substantially smaller gaps than hybrids. So gaps are meaningful for *trends within
  one consistent method*, not as absolute numbers.

### 1.6 Symbol glossary

| Symbol                       | Name              | What it is / does                                          |
| ---------------------------- | ----------------- | ---------------------------------------------------------- |
| $\varepsilon_\text{HOMO}$    | HOMO energy       | energy of the highest occupied orbital                     |
| $\varepsilon_\text{LUMO}$    | LUMO energy       | energy of the lowest unoccupied orbital                    |
| $\Delta E_\text{H-L}$        | HOMO–LUMO gap     | $\varepsilon_\text{LUMO}-\varepsilon_\text{HOMO}$          |
| $\lambda$                    | wavelength        | of the estimated lowest absorption ($hc/\Delta E$)         |
| $L$                          | box length        | length of the conjugated chain                             |
| $\eta$                       | chemical hardness | $\approx \tfrac12\Delta E_\text{H-L}$; resistance to react |
| IE, EA                       | ionization / affinity | Koopmans links these to $-\varepsilon_\text{HOMO/LUMO}$ |

---

## 2. The assignment (curated)

You will follow the frontier gap along a **series of increasing conjugation length** and
test the particle-in-a-box prediction against real electronic-structure numbers.

### 2.1 The series — the linear polyenes

The cleanest possible test system: an all-*trans* linear polyene chain, grown one double
bond at a time.

| Molecule | Double bonds | SMILES |
| --- | --- | --- |
| ethene | 1 | `C=C` |
| 1,3-butadiene | 2 | `C=CC=C` |
| 1,3,5-hexatriene | 3 | `C=CC=CC=C` |
| 1,3,5,7-octatetraene | 4 | `C=CC=CC=CC=C` |
| (optional) 1,3,5,7,9-decapentaene | 5 | `C=CC=CC=CC=CC=C` |

### 2.2 Levels + ORCA keyword lines

Keep the **method and basis fixed across the whole series** so the *trend* is meaningful.

| Step | ORCA line |
| --- | --- |
| 1. optimize each geometry | `! B3LYP def2-SVP Opt` |
| 2. orbital energies (single point) | `! B3LYP def2-SVP` |
| 3. functional-sensitivity check (one member) | `! PBE def2-SVP` vs. `! B3LYP def2-SVP` |

**Reading and visualizing the orbitals in ORCA/Workbench:**

- The single point prints an **`ORBITAL ENERGIES`** block in the `.out`. The **HOMO** is
  the last orbital with occupation `2.0000`; the **LUMO** is the first with `0.0000`.
  ORCA lists each energy in both hartree and **eV** — use the eV column directly.
- The gap in eV converts to a predicted wavelength via $\lambda\,[\text{nm}] = 1239.84 /
  \Delta E\,[\text{eV}]$.
- To **visualize** the HOMO and LUMO: the SCF writes a `.gbw` file; in Workbench,
  right-click the finished job → **"Generate density/MO cube…"** → pick the MO index
  (the HOMO/LUMO numbers from the orbital list). This runs `orca_plot` and writes a
  Gaussian cube that opens in the external 3D viewer, where you can see the shape and
  count the nodes.

### 2.3 What to collect

For each member: the HOMO energy, the LUMO energy, the gap (eV), the predicted wavelength
(nm), and cube images of the HOMO and LUMO for at least the shortest and longest members.

---

## 3. Deliverables and evaluation questions

Produce:
- a **table** of HOMO, LUMO, and gap for each member of the series;
- a **plot** of the gap vs. conjugation length (number of double bonds) and, to test the
  model, vs. $1/L^2$ (or $1/N^2$);
- **images** of the HOMO and LUMO for at least the shortest and longest members.

Then answer:

1. **Scaling.** How does the gap change as the chain lengthens? Is the trend consistent
   with the particle-in-a-box prediction ($\Delta E \propto 1/L^2$)? Plot the gap against
   $1/L^2$ and judge — is it roughly linear?
2. **Colour.** Convert each gap to a predicted lowest-absorption wavelength. At what chain
   length does the series first reach the **visible** range (~400 nm)? Does that match
   your expectation for when polyenes start to look coloured?
3. **Orbital shape.** Compare the HOMO and LUMO images along the series. How does the
   number of **nodes** change from HOMO to LUMO, and as the chain grows? Where is the LUMO
   bonding vs. antibonding?
4. **The caveat, made concrete.** Your gap-based wavelengths are only estimates of the
   true absorption. Predict *which direction* the error goes (does the bare orbital gap
   over- or under-estimate the real optical gap?), and state what you would need to
   compute (Section 1.5) to get the real value.
5. **Functional sensitivity.** Recompute the gap for one member with the GGA (`PBE`) and
   the hybrid (`B3LYP`). How different are they? What does that say about ever quoting an
   *absolute* HOMO–LUMO gap?

---

## 4. Going further (optional)

- **Push–pull dye.** Add a strong donor and acceptor to opposite ends of one polyene and
  watch how the gap *and* the spatial distribution of the frontier orbitals change — the
  doorway to charge-transfer excited states (a later script).
- **Against the truth.** Compare your best gap-based wavelength for one member against a
  proper excited-state (TD-DFT) calculation of the same molecule, and quantify the error
  you predicted in Question 4.

---

## Appendix — rendering this script to PDF

`pandoc A4_frontier_orbitals.md -o A4.pdf --pdf-engine=xelatex` (needs Pandoc + a TeX
engine such as MiKTeX or TinyTeX). View live maths in VS Code's Markdown preview or on
GitHub.

## References

- I. Fleming, *Molecular Orbitals and Organic Chemical Reactions* — frontier orbital theory.
- H. Kuhn — the free-electron (particle-in-a-box) model of dye absorption.
- R. G. Parr & W. Yang, *Density-Functional Theory of Atoms and Molecules* — hardness and
  frontier concepts.
- R. B. Woodward & R. Hoffmann, *The Conservation of Orbital Symmetry*.
