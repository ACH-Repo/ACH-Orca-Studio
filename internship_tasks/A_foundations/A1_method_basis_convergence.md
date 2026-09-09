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

A molecule is a collection of positively charged **nuclei** and negatively charged **electrons** that attract and repel each other through the Coulomb force. For a given distribution of nuclei, that we get to specify, we want a software like ORCA to calculate the distribution the electrons adapt around those nuclei. Once a converged electron distribution is found, the software has a normalized **wavefunction** $\Psi(\mathbf {r})$ whose square describes the **electron density distribution** $\rho = \lvert\Psi(\mathbf{r})\rvert^2$ in three dimensions, where $\mathbf{r}$ is the positional vector in space. This means that the **electronic structure** of the entire system is known and can be used to calculate physical properties, that require knowledge of that electronic structure.

### 1.1 Basics: A molecule is nuclei and electrons

Quantum mechanics says a system is completely described by its **wavefunction**
$\Psi$, and that $\Psi$ is found by solving the (time-<u>in</u>dependent) **Schrödinger
equation**:

$\hat{H}\,\Psi = E\,\Psi .$ 

- $\hat{H}$: **Hamiltonian operator**. Calculates total energy (kinetic + potential).
- $\Psi$:  **Wavefunction**. Unit: "Probability Amplitude". Not an observable quantity. Contains all information of the quantum system being probed.
- $E$: **Total electronic energy**: Sum of kinetic and potential energy for a given state of the quantum system.

This is an **Eigenvalue equation**: *"The energy operator $\hat H$ (Hamilton-Operator) acts on the wavefunction $\Psi$. The operator $\hat H$ contains all the mathematical operations necessary that must be applied to the wavefunction $\Psi$, to get the energy eigenvalue $E$ for a given state of the molecule $\Psi$."* 

All of quantum mechanics is founded on this calculation scheme. If $\Psi$ contains all information about the molecule, then all we need is the right operator to apply to $\Psi$ in order to get any information we'd ever want about our system. Any observable quantity (energy, momentum, location, dipolar moment etc.) a "system" (usually a single molecule in vacuum at 0 K) can exhibit, also has a corresponding operator with which it can be calculated. Typically, an operator in quantum mechanics is written by using the symbol for the desired, observable property (like momentum $p$) and adding a "hat" on top. For example: The momentum operator is written as $\hat p$.

Everything a program like ORCA does is an attempt to find an <u>approximate</u> wave function $\Psi$ for a system a user (you) has specified. After that it can also apply various operators to $\Psi$ so that they can find out properties about it. What these are will become clearer over the course of this tutorial, however suffice to say that many spectra, such as NMR (Nuclear Magnetic Resonance) and FTIR (Fourier Transform Infrared Spectroscopy) can be readily calculated using ORCA based on electron density distributions and energy gradients. 

### 1.2 The molecular Hamiltonian, written out

Section 1.1 states that the operator $\hat H$ "contains all the mathematical operations necessary" to get the total energy $E$ of a state $\Psi$. Since we are looking at a system in which nuclei and electrons are interacting with each other via Coulomb forces, $\hat H$ needs to contain terms for every kinetic and potential energy contribution those particles experience. A system possesses **kinetic energy** $T$ due to its moving particles plus the **potential energy** $V$ between those charge carriers in the form of Coulomb attraction and repulsion. As long as we do not have to consider external magnetic fields being applied, or the influence of time dilation at relativistic speeds, **potential and kinetic energy can be fully separated into additive terms.** Ergo, the total energy operator $\hat H$ can be written as:

$\hat H = \hat T + \hat V$

The question now becomes how exactly the individual parts of this operator can be written for a collection of electrons and nuclei interacting with one another. For a molecule with electrons labelled $i,j$ and nuclei labelled $A,B$, we can model the system as sums of individual contributions per partice (kinetic energies) and pairwise interactions per particle pairs (potential energies). To keep things legible, we write everything in **atomic units** — a unit system chosen so that the fundamental constants $\hbar=m_e=e=4\pi\varepsilon_0=1$ vanish from the formulae and stop cluttering them. In these units energy comes out in **hartree** ($E_\text{h}$), where $1\,E_\text{h}\approx 2625.5$ kJ/mol. The operator beomes a summation of **five terms** which themselves are sums over the aforementioned individual and pairwise contributions to the total energy:

$$
\hat{H}
= \underbrace{-\sum_i \tfrac{1}{2}\nabla_i^2}_{\text{(1) electron kinetic}}
\;\underbrace{-\sum_A \tfrac{1}{2M_A}\nabla_A^2}_{\text{(2) nuclear kinetic}}
\;\underbrace{-\sum_{i,A}\frac{Z_A}{r_{iA}}}_{\text{(3) electron–nucleus attraction}}
\;\underbrace{+\sum_{i<j}\frac{1}{r_{ij}}}_{\text{(4) electron–electron repulsion}}
\;\underbrace{+\sum_{A<B}\frac{Z_A Z_B}{R_{AB}}}_{\text{(5) nucleus–nucleus repulsion}}
$$

Read left to right; each term is one physical effect:

1. **Electron kinetic energy.** $\nabla_i^2$ (the **Laplacian**) measures how sharply
   $\Psi$ curves in the coordinates of electron $i$. A more sharply varying
   wavefunction means faster-moving electrons and so more kinetic energy.
2. **Nuclear kinetic energy.** The same, for the nuclei; $M_A$ is the mass of nucleus
   $A$. Because $M_A$ is thousands of times the electron mass, this term is tiny — the
   seed of the approximation in the next section.
3. **Electron–nucleus attraction.** *Lowers* the energy (note the minus sign); $Z_A$ is
   the nuclear charge and $r_{iA}$ the electron–nucleus distance. This is the term that
   binds a molecule together in the first place.
4. **Electron–electron repulsion.** *Raises* the energy; $r_{ij}$ is the distance
   between two electrons. **This is the troublesome term** — hold that thought until
   section 1.4.
5. **Nucleus–nucleus repulsion.** With the nuclei held fixed (next section) this is
   just a constant we add on at the end; $R_{AB}$ is the internuclear distance.

*In words:* the total energy is "(the electrons moving) + (the nuclei moving) − (the
electrons drawn toward the nuclei) + (the electrons pushing each other away) + (the
nuclei pushing each other away)." Nothing but motion and Coulomb's law — all of the
real difficulty hides in *how many particles feel each other at the same time*.

### 1.3 The Born–Oppenheimer approximation: clamp the nuclei

The nuclei are at least ~1800 times heavier than the electrons, so on the timescale on
which electrons move the nuclei barely budge — the electrons rearrange around them
essentially instantly. This is the physical content of the **Born–Oppenheimer (BO)
approximation**: we **fix the nuclei in place**, drop their kinetic energy (term 2),
and treat their mutual repulsion (term 5) as a constant. What survives is the
**electronic Hamiltonian**, which acts only on the electrons:

$$
\hat{H}_\text{el}
= -\sum_i \tfrac{1}{2}\nabla_i^2
\;-\;\sum_{i,A}\frac{Z_A}{r_{iA}}
\;+\;\sum_{i<j}\frac{1}{r_{ij}} ,
\qquad
\hat{H}_\text{el}\,\Psi_\text{el} = E_\text{el}\,\Psi_\text{el} .
$$

*In words:* freeze a nuclear skeleton, and ask only "what do the electrons do, and what
is their energy?" Solving this for **one** fixed set of nuclear positions $\mathbf{R}$
gives **one** number, $E_\text{el}(\mathbf R)$. Now imagine doing that at every
conceivable geometry: you trace out $E(\mathbf{R})$, the **potential energy surface
(PES)** — the landscape on which all of chemistry lives, with valleys for stable
structures and passes for reactions. Later A-scripts wander over that landscape; here
we stand at a single point on it and ask how accurately we can pin down $E$ itself.

### 1.4 Why it is hard: electron correlation

Look again at term (4), the electron–electron repulsion $\sum_{i<j} 1/r_{ij}$. It ties
the coordinates of *every* electron to *every* other: where one electron is likely to
be depends, at that instant, on where all the others are. That coupling is what makes
the problem genuinely hard — $\hat H_\text{el}$ **cannot be separated** into independent
one-electron pieces, and as a result the electronic Schrödinger equation **has no exact
solution for more than one electron**. Every practical method in all of quantum
chemistry is, at bottom, a different strategy for coping with this one term.

Coping with it forces **two separate approximations**, and the single most important
habit you can build is to *keep them apart*:

1. **The method** — *how* the physics of the $1/r_{ij}$ term (the **electron
   correlation**) is approximated.
2. **The basis set** — *what* set of mathematical functions the unknown wavefunction is
   built out of.

A result is only trustworthy once it is converged along **both** axes. The classic
beginner's mistake is to push one axis hard (an expensive method) while starving the
other (a tiny basis) and then call the number "high level." It is not — it is
lopsided, and this script is designed to let you *watch* that lopsidedness happen.

### 1.5 Approximation 1 — the method

**Orbitals and the Slater determinant.** The first practical move is to build the
complicated many-electron $\Psi$ out of simple **one-electron functions** — the
**orbitals** $\psi_i$ you already know from general chemistry (more precisely
*spin-orbitals*: an orbital multiplied by an up- or down-spin). Electrons are
indistinguishable and obey the **Pauli principle** — the total wavefunction must flip
sign when any two of them are swapped — and the neat mathematical object that builds
that sign-flip in automatically is a **Slater determinant**. So "electrons occupying
orbitals" is not just a picture; it is the literal first approximation to $\Psi$.

**Hartree–Fock (HF).** Hartree–Fock finds the best *single* Slater determinant by
letting each electron move in the **average** (mean) field created by all the others.
It leans on the **variational principle**: for *any* trial wavefunction,

$$
E[\Psi_\text{trial}]
= \frac{\langle \Psi_\text{trial}|\hat{H}_\text{el}|\Psi_\text{trial}\rangle}
        {\langle \Psi_\text{trial}|\Psi_\text{trial}\rangle}
\;\ge\; E_0 ,
$$

- $E[\Psi_\text{trial}]$: the energy of your guessed wavefunction.
- $\langle \Psi|\hat H|\Psi\rangle$: shorthand for "integrate $\Psi^\ast\,\hat H\,\Psi$
  over the coordinates of all electrons"; the denominator just normalises.
- $E_0$: the true (unknown) ground-state energy.

*In words:* any approximate energy you compute is an **upper bound** on the true
energy, so — for a given method — **lower is better**. That single fact is what lets a
program improve a wavefunction: keep adjusting it until the energy stops dropping.

HF gets electron **exchange** (an avoidance between same-spin electrons) exactly right,
but because each electron only sees the *average* of the others it misses their
**instantaneous** avoidance — the **electron correlation**. We name the missing piece
the **correlation energy**:

$E_\text{corr} = E_\text{exact} - E_\text{HF}\quad(\text{same basis}).$

It is only ~1 % of the total energy — but that 1 % is tens to hundreds of kJ/mol, which
is *precisely* the energy scale of bonds, barriers and reactions. Recovering it is the
whole game, and two families of methods do so in very different ways.

**(a) Density Functional Theory (DFT).** Rather than track the full $N$-electron
wavefunction, DFT works with the **electron density** $\rho(\mathbf r)$ — just "how many
electrons per unit volume, everywhere" — and writes the energy as

$E[\rho] = T_s[\rho] + V_\text{ne}[\rho] + J[\rho] + E_\text{xc}[\rho],$

- $T_s[\rho]$: kinetic energy of the electrons.
- $V_\text{ne}[\rho]$: electron–nucleus attraction.
- $J[\rho]$: the classical electron–electron repulsion.
- $E_\text{xc}[\rho]$: the **exchange–correlation functional** — the single term into
  which *all* the hard physics (exchange + correlation) is swept.

The catch is that the *exact* $E_\text{xc}$ is unknown; every named DFT "functional" is
a different **approximation** to it. These are often drawn as rungs of a ladder
("**Jacob's Ladder**") of rising sophistication and, usually, cost:

> **LDA** (local density) → **GGA** (adds the density *gradient*: PBE, BLYP) →
> **meta-GGA** (adds the kinetic-energy density: TPSS, SCAN) → **hybrid** (mixes in a
> fraction of exact HF exchange: B3LYP, PBE0) → **double-hybrid** (adds an MP2-like
> correlation term: B2PLYP).

Here is the crucial subtlety: **DFT is not systematically improvable**. A higher rung
is a *better model*, not a guaranteed step nearer the exact answer — higher is
*usually*, but **not always**, more accurate. You will see this with your own data
(Question 5).

**(b) Wavefunction correlation methods (WFT).** These instead add the missing
correlation *explicitly* on top of HF, and — unlike DFT — form a **systematic
hierarchy** that provably marches toward the exact answer:

$\text{HF} \;\to\; \text{MP2} \;\to\; \text{CCSD} \;\to\; \text{CCSD(T)} .$

- **MP2**: second-order Møller–Plesset perturbation theory — the cheapest correlation
  correction.
- **CCSD**: coupled-cluster with single and double excitations.
- **CCSD(T)**: CCSD plus a perturbative estimate of triple excitations — the
  "**gold standard**" for well-behaved (single-reference) molecules.

Each rung is a defined step toward the truth; the price is a cost that climbs steeply
(section 1.7).

### 1.6 Approximation 2 — the basis set

The orbitals $\psi_i$ are themselves unknown functions, so we make them tractable by
expressing each as a **weighted sum of a fixed, finite set of known functions**
$\chi_\mu$ centred on the atoms. This is the **LCAO** (Linear Combination of Atomic
Orbitals) approximation, and it turns "find an unknown function" into the far easier
"find the best set of numbers":

$\psi_i(\mathbf r) = \sum_{\mu=1}^{K} c_{\mu i}\,\chi_\mu(\mathbf r).$

- $\chi_\mu$: the **basis functions** — in practice **Gaussian**-shaped, chosen because
  the integrals they demand are fast to evaluate.
- $c_{\mu i}$: the **expansion coefficients**. Solving HF or DFT literally *is* finding
  the best $c_{\mu i}$.
- $K$: the **size of the basis set** (how many functions). Larger $K$ = more flexible
  orbitals = closer to the truth, but more expensive.

The functions come in families of increasing richness:

- **Minimal** (e.g. `STO-3G`): one function per occupied atomic orbital. Crude — you use
  it here only to *see* how bad "too small" looks.
- **Split-valence / multiple-$\zeta$**: several functions per valence orbital, giving
  the orbitals **radial** flexibility — **double-$\zeta$** (`def2-SVP`),
  **triple-$\zeta$** (`def2-TZVP`), **quadruple-$\zeta$** (`def2-QZVP`). More $\zeta$
  ("zeta") = more flexibility.
- **Polarization functions**: higher-angular-momentum functions (d on carbon, p on
  hydrogen) that let bonds **bend and polarise**. The `P` in `def2-SVP`/`def2-TZVP`
  marks them.
- **Diffuse functions**: extra long-range tails, essential for **anions**, excited
  states, and weak interactions. Marked by a leading `aug-` or, minimally, `ma-` (as in
  `ma-def2-TZVP`).

As $K$ grows, the energy of a *given method* settles toward its **complete-basis-set
(CBS) limit** — the very best that method can do:

$E(\text{basis}) \xrightarrow{\;K\to\infty\;} E_\text{CBS}.$

*In words:* bigger is better, but the improvement never truly stops — so in practice you
stop when **the property you actually care about** stops changing.

### 1.7 Cost — why you cannot just "use the best"

The reason we agonise over the choice at all is that cost rises **steeply** with system
size $N$ (loosely, the number of basis functions):

| Method  | Formal cost scaling  |
| ------- | -------------------- |
| HF, DFT | $\sim N^{3}$–$N^{4}$ |
| MP2     | $\sim N^{5}$         |
| CCSD(T) | $\sim N^{7}$         |

That $N^7$ is brutal: **doubling** the size of the system multiplies the CCSD(T) cost by
$2^7 = 128$. So picking a level of theory is really an **engineering decision** — you
want the *cheapest* level that is *converged for your property*, not the most expensive
one you can afford to run exactly once.

### 1.8 The saving grace — error cancellation

If total energies converge so slowly, how is quantitative chemistry possible at all?
Because we almost never actually want a total energy — we want a **difference**: a
reaction energy, a rotation barrier, a preference between two conformers. Write the
target as

$\Delta E = E_\text{product} - E_\text{reactant}.$

The large method- and basis-set errors are *similar* in the two similar species, so they
**cancel** in the subtraction. Relative energies therefore converge **far faster** —
often by an order of magnitude — than the absolute energies they are built from.
Recognising and exploiting this is the single most valuable practical skill in the whole
exercise, and it is exactly why the assignment is built around a **relative** energy.

### 1.9 Symbol glossary

| Symbol                      | Name                            | What it is / does                                                  |
| --------------------------- | ------------------------------- | ------------------------------------------------------------------ |
| $\hat H,\ \hat H_\text{el}$ | (electronic) Hamiltonian        | Operator giving the total (electronic) energy                      |
| $\Psi,\ \Psi_\text{el}$     | wavefunction                    | Full quantum state; its square is the electron probability density |
| $E,\ E_0$                   | energy; ground-state energy     | Eigenvalue of $\hat H$; $E_0$ is the exact lowest one              |
| $\psi_i$                    | (molecular) orbital             | One-electron function the many-electron $\Psi$ is built from       |
| $\rho(\mathbf r)$           | electron density                | Electrons per unit volume; the variable in DFT                     |
| $\nabla_i^2$                | Laplacian                       | Curvature of $\Psi$ → kinetic energy of electron $i$               |
| $Z_A,\ M_A$                 | nuclear charge, mass            | Properties of nucleus $A$                                          |
| $r_{iA},\ r_{ij},\ R_{AB}$  | distances                       | electron–nucleus, electron–electron, nucleus–nucleus               |
| $\chi_\mu$                  | basis function                  | Fixed known (Gaussian) function; building block of orbitals        |
| $c_{\mu i}$                 | MO coefficient                  | Weight of $\chi_\mu$ in orbital $\psi_i$; the unknowns solved for  |
| $K$                         | basis-set size                  | Number of basis functions; more = more accurate, more costly       |
| $E_\text{xc}[\rho]$         | exchange–correlation functional | The unknown DFT term each functional approximates                  |
| $E_\text{corr}$             | correlation energy              | $E_\text{exact}-E_\text{HF}$; the piece HF misses                  |
| $E_\text{CBS}$              | complete-basis-set limit        | A method's best possible energy ($K\to\infty$)                     |
| $\Delta E$                  | relative energy                 | A difference of total energies; converges fast                     |
| $E_\text{h}$                | hartree                         | Atomic unit of energy; $1\,E_\text{h}=2625.5$ kJ/mol               |

---

## 2. The assignment (curated)

Now we put the theory to work. You will watch how a **total energy** and a **relative
energy** converge along the two axes — method and basis — using a **fixed molecule and a fixed grid of levels**, so that the effects from section 1 come out cleanly instead of being buried in noise.

### 2.1 The system — the internal-rotation barrier of ethane

Your target relative energy is the **barrier to internal rotation of ethane**
(C₂H₆): the energy difference between the **eclipsed** (higher) and **staggered**
(lower) conformers as one CH₃ group twists against the other about the C–C bond:

$\Delta E_\text{rot} = E(\text{eclipsed}) - E(\text{staggered}).$

This system was chosen for you for four specific reasons, each tied to a lesson from
section 1:

- It is **tiny** (8 atoms) — even quadruple-$\zeta$ and CCSD(T) finish in seconds, so
  you can afford the whole grid.
- The two conformers are **almost identical** geometrically, so **error cancellation**
  (section 1.8) is dramatic: the *total* energy of ethane crawls toward convergence as
  the basis grows, but the *barrier* is nearly converged already at double-$\zeta$.
  That contrast is the headline of the whole script, made visible in one plot.
- Both conformers are closed-shell, single-reference and well behaved, so **CCSD(T)** is
  a trustworthy reference against which every DFT rung and HF/MP2 can be scored.
- The experimental barrier is well known — $\approx 12$ kJ/mol ($\approx 2.9$ kcal/mol),
  staggered lower — a target to compare against (not to reproduce exactly).

### 2.2 Geometry protocol — isolate the electronic-structure effect

So that you are studying **method and basis** and *not* geometry, use **two fixed
geometries** for the whole grid:

1. **Staggered:** fully optimise ethane once at a single reference level,
   **`! B3LYP def2-TZVP Opt`** (this gives the D₃d staggered minimum).

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
geometries. (You may also obtain the barrier directly with a **relaxed dihedral scan**
from 60°→0° — Workbench's scan tool plots it — but the frozen-geometry single points are what make the convergence study clean.)

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
- **Cores/memory** are set with `%pal nprocs <N> end` and `%maxcore <MB> end` (or via Workbench's global hardware defaults). For timing comparisons keep them **fixed** across the grid.
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

$\text{CH}_3\text{OH} \;\longrightarrow\; \text{CH}_3\text{O}^- + \text{H}^+,
\qquad \Delta E = E(\text{CH}_3\text{O}^-) + E(\text{H}^+) - E(\text{CH}_3\text{OH}).$

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
