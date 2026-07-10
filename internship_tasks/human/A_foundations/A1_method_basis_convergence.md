# A1 — The Cost of Accuracy: Method and Basis-Set Convergence

|                      |                                                                                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Level**            | A — Foundations (undergraduate)                                                                                                                              |
| **Est. effort**      | Half a day of compute + analysis                                                                                                                             |
| **Prerequisites**    | First-year quantum mechanics: you have met the Schrödinger equation and the idea of a wavefunction and an orbital. No prior computational chemistry assumed. |
| **Software**         | ORCA (driven through ORCA Workbench). Every level of theory you need is given below as an explicit ORCA keyword line.                                        |
| **You will produce** | A table of a target property vs. level of theory, a convergence plot, and a value-vs-cost plot                                                               |

> This script says *what* to compute and *what* to report. Working out how to make
> the software carry it out is part of the exercise — but unlike a blank-page task,
> the **molecules and the levels of theory are chosen for you** (Section 2), so your
> data will show the intended effects cleanly.

> **A note on the equations.** Maths below is written in LaTeX between `$…$` (inline)
> and `$$…$$` (displayed). It renders live in VS Code's built-in Markdown preview and
> on GitHub, and exports to a clean PDF with Pandoc (see the end of this file). If you
> only see the raw `$…$`, your viewer isn't rendering maths — the content is still
> readable.

---

## 1. Background and theory

A molecule is a collection of positively charged **nuclei** and negatively charged **electrons** that attract and repel each other through the Coulomb force. For a given distribution of nuclei, that we get to specify, we want a software like ORCA to calculate the distribution the electrons adapt around those nuclei. Once a converged electron distribution is found, the software has a normalized **wavefunction** whose square describes the **electron density distribution** in three dimensions. This means that the **electronic structure** of the entire system is known and can be used to calculate physical properties, that require knowledge of that electronic structure.

### 1.1 Basics: A molecule is nuclei and electrons

Quantum mechanics says a system is completely described by its **wavefunction**
$\Psi$, and that $\Psi$ is found by solving the (time-<u>in</u>dependent) **Schrödinger
equation**:

$\hat{H}\,\Psi = E\,\Psi .$ 

- $\hat{H}$: **Hamiltonian operator**. Calculates total energy (kinetic + potential).
- $\Psi$:  **Wavefunction**. Unit: "Probability Amplitude". Not an observable. Contains all information of the quantum system being probed.
- $E$: **Total electronic energy**: Sum of kinetic and potential energy for a given state of the quantum system.

This is an **Eigenvalue equation**: *"The energy operator $\hat H$ (Hamilton-Operator) acts on the wavefunction $\Psi$. The operator $\hat H$ contains all the mathematical operations necessary that must be applied to the wavefunction $\Psi$, to get the energy eigenvalue $E_i$ for a given state of the molecule $\Psi_i$."* 

All of quantum mechanics is founded on this calculation scheme. If $\Psi$ contains all information about the molecule, then all we need is the right operator to apply to $\Psi$ in order to get any information we'd ever want about our system. Any observable quantity (energy, momentum, location, dipolar moment etc.) a "system" (usually a single molecule in vacuum at 0 K) can exhibit, also has a corresponding operator with which it can be calculated. Typically, an operator in quantum mechanics is written by using the symbol for the desired, observable property (like momentum $p$) and adding a "hat" on top. For example: The momentum operator is written as $\hat p$.

Everything a program like ORCA does is an attempt to find an <u>approximate</u> wave function $\Psi$ for a system a user (you) has specified. After that it can also apply various operators to $\Psi$ so that they can find out properties about it. What these are will become clearer over the course of this tutorial, however suffice to say that many spectra, such as NMR (Nuclear Magnetic Resonance) and FTIR (Fourier Transform Infrared Spectroscopy) can be readily calculated using ORCA based on electron density distributions and energy gradients. 

### 1.2 The molecular Hamiltonian, written out

For a molecule with electrons labelled $i,j$ and nuclei labelled $A,B$, the
Hamiltonian (in **atomic units**, where $\hbar=m_e=e=4\pi\varepsilon_0=1$, so
energies come out in **hartree**, $1\,E_\text{h}\approx 2625.5$ kJ/mol) is:

$$
\hat{H}
= \underbrace{-\sum_i \tfrac{1}{2}\nabla_i^2}_{\text{(1) electron kinetic}}
\;\underbrace{-\sum_A \tfrac{1}{2M_A}\nabla_A^2}_{\text{(2) nuclear kinetic}}
\;\underbrace{-\sum_{i,A}\frac{Z_A}{r_{iA}}}_{\text{(3) electron–nucleus attraction}}
\;\underbrace{+\sum_{i<j}\frac{1}{r_{ij}}}_{\text{(4) electron–electron repulsion}}
\;\underbrace{+\sum_{A<B}\frac{Z_A Z_B}{R_{AB}}}_{\text{(5) nucleus–nucleus repulsion}}
$$

Term by term:

1. **Electron kinetic energy.** $\nabla_i^2$ (the Laplacian) measures how sharply
   $\Psi$ curves in the coordinates of electron $i$; a more sharply varying
   wavefunction means faster-moving electrons and more kinetic energy.
2. **Nuclear kinetic energy.** The same for the nuclei; $M_A$ is the mass of nucleus
   $A$. Because $M_A$ is thousands of times $m_e$, this term is small — the seed of
   the next approximation.
3. **Electron–nucleus attraction.** Lowers the energy; $Z_A$ is the nuclear charge
   and $r_{iA}$ the electron–nucleus distance. Electrons are bound because of this.
4. **Electron–electron repulsion.** Raises the energy; $r_{ij}$ is the distance
   between two electrons. **This is the troublesome term** (Section 1.4).
5. **Nucleus–nucleus repulsion.** With the nuclei fixed (next section) this is just a
   constant added at the end; $R_{AB}$ is the internuclear distance.

### 1.3 The Born–Oppenheimer approximation: clamp the nuclei

Nuclei are at least ~1800 times heavier than electrons, so on the timescale of
electron motion the nuclei are essentially stationary. We therefore **fix the nuclei
in place**, drop their kinetic energy — term (2) — and treat their repulsion — term
(5) — as a constant. What is left is the **electronic Hamiltonian**:

$$
\hat{H}_\text{el}
= -\sum_i \tfrac{1}{2}\nabla_i^2
\;-\;\sum_{i,A}\frac{Z_A}{r_{iA}}
\;+\;\sum_{i<j}\frac{1}{r_{ij}} ,
\qquad
\hat{H}_\text{el}\,\Psi_\text{el} = E_\text{el}\,\Psi_\text{el} .
$$

Solving this for one fixed set of nuclear positions $\mathbf{R}$ gives one energy.
Repeat at many geometries and you map out $E(\mathbf{R})$, the **potential energy
surface (PES)** — the landscape on which all of chemistry (bonds, transition states,
vibrations) happens. Later A-scripts explore the PES; here we sit at a fixed geometry
and study how accurately we can get $E$ itself.

### 1.4 Why it is hard: electron correlation

The electron–electron term (4), $\sum_{i<j} 1/r_{ij}$, couples the coordinates of
*every* electron to *every* other: where one electron goes depends instantaneously on
where all the others are. This coupling means $\hat H_\text{el}$ **cannot be
separated** into independent one-electron problems, and the electronic Schrödinger
equation **has no exact solution for more than one electron**. Every practical method
is a strategy for coping with this one term.

This is the origin of two *independent* approximations that you must always keep
apart:

1. **The method** — *how* the physics of the $1/r_{ij}$ term (electron correlation)
   is approximated.
2. **The basis set** — *what mathematical functions* the unknown wavefunction is
   built out of.

A result is trustworthy only when it is converged along **both** axes. A classic
beginner error is to push one axis hard (an expensive method) while starving the
other (a tiny basis) and call the result "high level." It is not.

### 1.5 Approximation 1 — the method

**Orbitals and the Slater determinant.** The first move is to build the many-electron
$\Psi$ out of **one-electron functions** — **orbitals** $\psi_i$ (more precisely
spin-orbitals, an orbital times a spin). To respect the Pauli principle (the
wavefunction must change sign when two electrons swap), the orbitals are combined into
a **Slater determinant** rather than a simple product. This is the mathematical form
of the familiar "electrons in orbitals" picture.

**Hartree–Fock (HF).** HF finds the best *single* Slater determinant by letting each
electron move in the **average** (mean) field of all the others. It uses the
**variational principle**: for any trial wavefunction,

$$
E[\Psi_\text{trial}]
= \frac{\langle \Psi_\text{trial}|\hat{H}_\text{el}|\Psi_\text{trial}\rangle}
        {\langle \Psi_\text{trial}|\Psi_\text{trial}\rangle}
\;\ge\; E_0 ,
$$

i.e. any approximate energy is an **upper bound** to the true ground-state energy
$E_0$ — so *lower is better*. ($\langle\,\cdot\,|\,\hat H\,|\,\cdot\,\rangle$ is
shorthand for "integrate $\Psi^\ast\,\hat H\,\Psi$ over all electron coordinates"; the
denominator just normalises.)

HF captures electron **exchange** (a same-spin avoidance) exactly, but by using an
*average* field it misses the **instantaneous** avoidance of electrons — **electron
correlation**. We define the missing piece as the **correlation energy**:

$$E_\text{corr} = E_\text{exact} - E_\text{HF}\quad(\text{same basis}).$$

It is only ~1 % of the total energy — but that 1 % is tens to hundreds of kJ/mol,
which *is* the energy scale of chemistry. Recovering it is the whole game. Two
families do so:

**(a) Density Functional Theory (DFT).** Instead of the full wavefunction, DFT works
with the **electron density** $\rho(\mathbf r)$ and writes the energy as

$$E[\rho] = T_s[\rho] + V_\text{ne}[\rho] + J[\rho] + E_\text{xc}[\rho],$$

where $T_s$ is a kinetic term, $V_\text{ne}$ the electron–nucleus attraction, $J$ the
classical electron–electron repulsion, and $E_\text{xc}$ the **exchange–correlation
functional** — the one unknown piece where all the hard physics (including
correlation) is hidden. The exact $E_\text{xc}$ is unknown; the different DFT
"functionals" are different *approximations* to it, often pictured as rungs of a
ladder ("Jacob's Ladder") of increasing sophistication and usually cost:

> **LDA** (local density) → **GGA** (adds the density *gradient*: PBE, BLYP) →
> **meta-GGA** (adds kinetic-energy density: TPSS, SCAN) → **hybrid** (mixes in a
> fraction of exact HF exchange: B3LYP, PBE0) → **double-hybrid** (adds an MP2-like
> correlation term: B2PLYP).

Crucially, **DFT is not systematically improvable**: a higher rung is a *better
model*, not a guaranteed step toward the exact answer. Higher rung is *usually* — but
**not always** — more accurate. You will see this directly (Question 5).

**(b) Wavefunction correlation methods (WFT).** These add correlation *explicitly* on
top of HF, and — unlike DFT — form a **systematic hierarchy** that provably approaches
the exact answer:

$$\text{HF} \;\to\; \text{MP2} \;\to\; \text{CCSD} \;\to\; \text{CCSD(T)},$$

with **CCSD(T)** the "gold standard" for well-behaved (single-reference) molecules.
The price is steep cost scaling (Section 1.7).

### 1.6 Approximation 2 — the basis set

The orbitals $\psi_i$ themselves are unknown functions. We make them tractable by
expanding each as a **linear combination of a fixed, finite set of known functions**
$\chi_\mu$ centred on the atoms (the **LCAO** approximation):

$$\psi_i(\mathbf r) = \sum_{\mu=1}^{K} c_{\mu i}\,\chi_\mu(\mathbf r).$$

- $\chi_\mu$ — the **basis functions** (in practice **Gaussian**-shaped functions,
  chosen because the required integrals are fast to evaluate).
- $c_{\mu i}$ — the **expansion coefficients**; solving HF/DFT *is* finding the best
  $c_{\mu i}$.
- $K$ — the **size of the basis set** (number of functions). Larger $K$ = more
  flexible orbitals = closer to the truth, but more expensive.

The quality ladder for basis sets:

- **Minimal** (e.g. `STO-3G`): one function per occupied atomic orbital. Crude; use it
  only to *see* how bad "too small" is.
- **Split-valence / multiple-$\zeta$**: several functions per valence orbital —
  **double-$\zeta$** (`def2-SVP`), **triple-$\zeta$** (`def2-TZVP`),
  **quadruple-$\zeta$** (`def2-QZVP`). More $\zeta$ ("zeta") = more radial
  flexibility.
- **Polarization functions**: higher-angular-momentum functions (d on carbon, p on
  hydrogen) that let bonds bend and polarise. The `P` in `def2-SVP`/`def2-TZVP`
  denotes them.
- **Diffuse functions**: extra long-range tails, essential for **anions**, excited
  states, and weak interactions. Denoted by a leading `aug-` or, minimally, `ma-`
  (as in `ma-def2-TZVP`).

As $K$ grows, the energy of a *given method* converges toward its **complete-basis-set
(CBS) limit** — the best that method can do:

$$E(\text{basis}) \xrightarrow{\;K\to\infty\;} E_\text{CBS}.$$

Bigger is better but never finished; in practice you stop when **the property you care
about** stops changing.

### 1.7 Cost — why you cannot just "use the best"

Formal cost rises steeply with system size $N$ (roughly, the number of basis
functions):

| Method  | Formal cost scaling  |
| ------- | -------------------- |
| HF, DFT | $\sim N^{3}$–$N^{4}$ |
| MP2     | $\sim N^{5}$         |
| CCSD(T) | $\sim N^{7}$         |

$N^7$ means **doubling** the system size raises the cost by a factor of
$2^7 = 128$. Choosing a level of theory is therefore an **engineering decision**: the
*cheapest* level that is *converged for your property* — not the most expensive one
you can afford to run once.

### 1.8 The saving grace — error cancellation

If total energies converge so slowly, why is quantitative chemistry possible at all?
Because we almost never want a total energy — we want a **difference**: a reaction
energy, a rotation barrier, an isomer preference. Write the target as

$$\Delta E = E_\text{product} - E_\text{reactant}.$$

The large method- and basis-set errors are *similar* in the two similar species and
**cancel** in the difference. Relative energies therefore converge **much faster** —
often by an order of magnitude — than the absolute energies they are built from.
Recognising and exploiting this is the single most important practical skill in this
exercise, and the reason the assignment centres on a **relative** energy.

### 1.9 Symbol glossary

| Symbol                      | Name                            | What it is / does                                                 |
| --------------------------- | ------------------------------- | ----------------------------------------------------------------- |
| $\hat H,\ \hat H_\text{el}$ | (electronic) Hamiltonian        | Operator giving the total (electronic) energy                     |
| $\Psi,\ \Psi_\text{el}$     | wavefunction                    | Full quantum state; $                                             |
| $E,\ E_0$                   | energy; ground-state energy     | Eigenvalue of $\hat H$; $E_0$ is the exact lowest one             |
| $\psi_i$                    | (molecular) orbital             | One-electron function the many-electron $\Psi$ is built from      |
| $\rho(\mathbf r)$           | electron density                | Electrons per unit volume; the variable in DFT                    |
| $\nabla_i^2$                | Laplacian                       | Curvature of $\Psi$ → kinetic energy of electron $i$              |
| $Z_A,\ M_A$                 | nuclear charge, mass            | Properties of nucleus $A$                                         |
| $r_{iA},\ r_{ij},\ R_{AB}$  | distances                       | electron–nucleus, electron–electron, nucleus–nucleus              |
| $\chi_\mu$                  | basis function                  | Fixed known (Gaussian) function; building block of orbitals       |
| $c_{\mu i}$                 | MO coefficient                  | Weight of $\chi_\mu$ in orbital $\psi_i$; the unknowns solved for |
| $K$                         | basis-set size                  | Number of basis functions; ↑ = more accurate, more costly         |
| $E_\text{xc}[\rho]$         | exchange–correlation functional | The unknown DFT term each functional approximates                 |
| $E_\text{corr}$             | correlation energy              | $E_\text{exact}-E_\text{HF}$; the piece HF misses                 |
| $E_\text{CBS}$              | complete-basis-set limit        | A method's best possible energy ($K\to\infty$)                    |
| $\Delta E$                  | relative energy                 | A difference of total energies; converges fast                    |
| $E_\text{h}$                | hartree                         | Atomic unit of energy; $1\,E_\text{h}=2625.5$ kJ/mol              |

---

## 2. The assignment (curated)

You will study how a **total energy** and a **relative energy** converge along the two
axes, using a **fixed molecule and a fixed grid of levels** so the effects come out
cleanly.

### 2.1 The system — the internal-rotation barrier of ethane

Your target relative energy is the **barrier to internal rotation of ethane**
(C₂H₆): the energy difference between the **eclipsed** (higher) and **staggered**
(lower) conformers as one CH₃ group rotates against the other about the C–C bond:

$$\Delta E_\text{rot} = E(\text{eclipsed}) - E(\text{staggered}).$$

Why this system is chosen for you:

- It is tiny (8 atoms) — even quadruple-$\zeta$ and CCSD(T) run in seconds.
- The two conformers are **almost identical** geometrically, so error cancellation is
  dramatic: the *total* energy of ethane converges slowly with basis, but the
  *barrier* is nearly converged already at double-$\zeta$. This is the headline lesson
  made visible.
- Both conformers are closed-shell, single-reference, and well behaved, so **CCSD(T)**
  is a trustworthy reference, and every DFT rung and HF/MP2 has a clean number to be
  compared against.
- The experimental barrier is well known, $\approx 12$ kJ/mol ($\approx 2.9$
  kcal/mol), staggered lower — a target to compare against (not to reproduce exactly).

### 2.2 Geometry protocol — isolate the electronic-structure effect

So that you are studying **method and basis**, not geometry, use **two fixed
geometries** for the whole grid:

1. **Staggered:** fully optimise ethane once at a single reference level,
   **`! B3LYP def2-TZVP Opt`** (D₃H, staggered minimum).
2. **Eclipsed:** starting from the staggered structure, set the H–C–C–H torsion to
   $0^\circ$ and optimise with **that dihedral frozen** — a constrained optimisation.
   In ORCA Workbench this is the Calc-tab right-click **"Geometry constraints /
   scan…"** (freeze the H–C–C–H dihedral at 0°); the underlying ORCA block is:
   
   ```
   ! B3LYP def2-TZVP Opt
   %geom Constraints
     { D <H> <C> <C> <H> 0.0 C }   # atom indices of one H–C–C–H torsion
   end end
   * xyzfile 0 1 ethane_staggered.xyz
   ```
   
   *(Tip: read off the four atom indices from the Molecules-tab atom list. The `C` at
   the end of the constraint line means "constrain"; indices are 0-based in Workbench's
   dialog.)*

Then run **single-point** energies of every grid cell below on these two frozen
geometries. (You may also obtain the barrier directly with a **relaxed dihedral
scan** from 60°→0° — Workbench's scan tool plots it — but the frozen-geometry single
points are what make the convergence study clean.)

### 2.3 The curated grid + ORCA keyword lines

Run this **method × basis** grid as single points on **both** geometries (32 single
points). Each cell's ORCA input is just the keyword line shown, plus your geometry —
these are the exact lines to paste into a Workbench **recipe** (`calctype = SP`):

| Method ↓ / Basis → | `STO-3G` (minimal) | `def2-SVP` (DZ)    | `def2-TZVP` (TZ)    | `def2-QZVP` (QZ)    |
| ------------------ | ------------------ | ------------------ | ------------------- | ------------------- |
| **HF**             | `! HF STO-3G`      | `! HF def2-SVP`    | `! HF def2-TZVP`    | `! HF def2-QZVP`    |
| **PBE** (GGA)      | `! PBE STO-3G`     | `! PBE def2-SVP`   | `! PBE def2-TZVP`   | `! PBE def2-QZVP`   |
| **B3LYP** (hybrid) | `! B3LYP STO-3G`   | `! B3LYP def2-SVP` | `! B3LYP def2-TZVP` | `! B3LYP def2-QZVP` |
| **MP2** (WFT)      | `! MP2 STO-3G`     | `! MP2 def2-SVP`   | `! MP2 def2-TZVP`   | `! MP2 def2-QZVP`   |

Plus **one reference** at your best affordable level:

- `! CCSD(T) def2-TZVP` — the gold-standard barrier to benchmark everything against.

**ORCA keyword notes (verified in ORCA 6.0.1):**

- The **first token** after `!` is the method, the **second** the basis. Order does not
  matter to ORCA, but keep it consistent.
- `HF`, `PBE`, `B3LYP`, `MP2`, `CCSD(T)` and the four `def2`/`STO-3G` bases all work as
  **bare** keyword lines — no auxiliary basis needed.
- **Cores/memory** are set with `%pal nprocs <N> end` and `%maxcore <MB> end` (or via
  Workbench's global hardware defaults). For timing comparisons keep them **fixed**
  across the grid.
- To **record wall-clock time**: ORCA prints `TOTAL RUN TIME` at the bottom of each
  `.out`. Read it from there (or Workbench's live timing).

### 2.4 Sub-study — the DFT ladder at fixed basis

To probe whether "higher rung = better," run this **ladder at one good basis**
(`def2-TZVP`), single points on both geometries, and compare each barrier to the
CCSD(T) reference:

| Rung          | Functional | ORCA line                        |
| ------------- | ---------- | -------------------------------- |
| LDA           | PWLDA      | `! PWLDA def2-TZVP`              |
| GGA           | PBE        | `! PBE def2-TZVP`                |
| meta-GGA      | TPSS       | `! TPSS def2-TZVP`               |
| hybrid        | B3LYP      | `! B3LYP def2-TZVP`              |
| hybrid        | PBE0       | `! PBE0 def2-TZVP`               |
| double-hybrid | B2PLYP     | `! B2PLYP def2-TZVP def2-TZVP/C` |

> **Keyword gotcha (verified):** the **double-hybrid B2PLYP** (and any `RI-MP2`) needs
> a **correlation-fitting auxiliary basis**, added as the token **`def2-TZVP/C`** — omit
> it and ORCA stops with *"RI-MP2 needs an AuxC basis but none was defined!"*. The
> lower rungs and plain `MP2`/`CCSD(T)` do **not** need it. This is exactly the kind of
> ORCA-specific detail that a "just pick a functional" instruction hides.

### 2.5 Optional sub-study — when diffuse functions matter

To make Question 3 concrete, compute a relative energy that **involves an anion**, the
**deprotonation energy** of methanol:

$$\text{CH}_3\text{OH} \;\longrightarrow\; \text{CH}_3\text{O}^- + \text{H}^+,
\qquad \Delta E = E(\text{CH}_3\text{O}^-) + E(\text{H}^+) - E(\text{CH}_3\text{OH}).$$

(The bare proton H⁺ has no electrons, so $E(\text{H}^+)=0$; set the methoxide anion's
**charge = −1** in Workbench.) Run it **without** and **with** diffuse functions:

- `! B3LYP def2-TZVP` (no diffuse) vs. `! B3LYP ma-def2-TZVP` (minimally augmented).

The neutral rotation barrier barely moves when you add diffuse functions; this anion
energy moves a **lot**. That contrast is the point.

---

## 3. Deliverables and evaluation questions

Produce:

- a **table** of the ethane barrier $\Delta E_\text{rot}$ for every method × basis cell
  (Section 2.3), plus the CCSD(T) reference;
- a **convergence plot** of $\Delta E_\text{rot}$ vs. basis-set size (STO-3G → SVP →
  TZVP → QZVP), one line per method;
- a **value-vs-cost plot**: the barrier (or its error vs. the CCSD(T) reference) on the
  $y$-axis against wall-clock time on the $x$-axis (log scale helps).

Then answer:

1. **Basis convergence & cancellation.** Hold the method fixed. How does the *total*
   energy of staggered ethane behave as the basis grows — does it keep dropping, and
   does it level off? Now the *barrier*: how many basis steps until it stops changing
   to within ~1 kJ/mol? Quantify how much faster the **relative** energy converges than
   the **total**, and explain it via error cancellation (Section 1.8).
2. **Method convergence.** At your largest basis (`def2-QZVP`), how far apart are the
   HF, PBE, B3LYP and MP2 barriers? Is that spread larger or smaller than the
   basis-set spread you found in Q1? Which HF/DFT/MP2 number is closest to CCSD(T)?
3. **Which axis dominates** for the ethane barrier — method or basis? Now use Section
   2.5: for the **anion** deprotonation energy, how much does adding diffuse functions
   (`def2-TZVP` → `ma-def2-TZVP`) change the answer, and how does that compare to its
   effect on the neutral barrier? What does this say about "the right basis depends on
   the property"?
4. **The knee.** On your value-vs-cost plot, identify the **cheapest** level whose
   barrier is within chemical accuracy (~4 kJ/mol ≈ 1 kcal/mol) of the CCSD(T)
   reference. Justify it as the level you would actually use for a *bigger* version of
   this system.
5. **DFT is not a ladder to the exact answer.** Using the Section 2.4 rung study, plot
   each functional's barrier against the CCSD(T) reference. Is "higher rung" always
   closer? Contrast this with the HF → MP2 → CCSD(T) trend. What does the difference
   tell you about DFT vs. the wavefunction hierarchy (Section 1.5)?

---

## 4. Going further (optional)

- **CBS extrapolation.** Estimate the complete-basis-set limit *by hand* from your
  triple- and quadruple-$\zeta$ numbers. The **correlation** energy converges as
  $X^{-3}$ in the cardinal number $X$ (TZ ⇒ $X=3$, QZ ⇒ $X=4$), so the two-point
  Helgaker extrapolation is
  $$E_\text{corr}^\text{CBS} = \frac{X^3\,E_\text{corr}(X) - Y^3\,E_\text{corr}(Y)}{X^3 - Y^3}
  \qquad (X=4,\ Y=3).$$
  The HF part converges faster (roughly exponentially), so its `def2-QZVP` value is
  already close to CBS. Compare the extrapolate to your explicit `def2-QZVP` barrier.
  *(ORCA also has a built-in basis-extrapolation feature — look up the current keyword
  in the ORCA manual for your version rather than assuming it; the manual approach
  above always works.)*
- **A second relative energy.** Repeat the convergence study for the **ethanol vs.
  dimethyl ether** isomer energy (both C₂H₆O; experimental difference ≈ 50 kJ/mol) and
  compare which axis dominates there versus for the ethane barrier.

---

## Appendix — rendering this script to PDF

The maths uses standard LaTeX `$…$`/`$$…$$`, so:

- **View** with live maths: VS Code's built-in Markdown preview (KaTeX, no extension
  needed) or GitHub.
- **Export to PDF:** install **Pandoc** + a TeX engine (**MiKTeX** or the lighter
  **TinyTeX**) and run
  
  ```
  pandoc A1_method_basis_convergence.md -o A1.pdf --pdf-engine=xelatex
  ```
  
  which typesets the equations and tables into a clean report.

## References

- F. Jensen, *Introduction to Computational Chemistry* — Hamiltonian, basis sets,
  convergence, CBS extrapolation.
- A. Szabo & N. S. Ostlund, *Modern Quantum Chemistry* — the Schrödinger equation,
  Hartree–Fock, and the Slater determinant, from the ground up.
- C. J. Cramer, *Essentials of Computational Chemistry* — methods and functionals.
- Weigend & Ahlrichs, *Phys. Chem. Chem. Phys.* **7**, 3297 (2005) — the def2 basis sets.
- Perdew et al. — the "Jacob's Ladder" picture of density functionals.
- NIST Computational Chemistry Comparison and Benchmark Database (CCCBDB) — reference
  geometries and energies.
