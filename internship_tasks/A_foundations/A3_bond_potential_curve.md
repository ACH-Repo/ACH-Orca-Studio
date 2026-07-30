# A3 — The Shape of a Bond: Potential Energy Curves and Spectroscopic Constants

|                      |                                                                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **Level**            | A — Foundations (undergraduate), with a graduate-level twist at the end                                                                   |
| **Est. effort**      | Half to a full day                                                                                                                        |
| **Prerequisites**    | A1 (method vs. basis) and A2 (the PES, minima). The harmonic oscillator and the idea of a bond as a spring. No prior scan experience assumed. |
| **Software**         | ORCA (through ORCA Workbench). Every level and every scan is given below as an explicit ORCA input.                                       |
| **You will produce** | A plotted potential energy curve, extracted spectroscopic constants, and a comparison of restricted vs. unrestricted dissociation        |

> This script says *what* to compute and *what* to report. Working out how to make
> the software carry it out is part of the exercise — the molecule and levels are chosen
> for you (Section 2).

> **A note on the equations.** Maths is written in LaTeX between `$…$` (inline) and
> `$$…$$` (displayed); it renders in VS Code's Markdown preview and on GitHub, and
> exports to PDF with Pandoc (see the end). Raw `$…$` just means your viewer isn't
> rendering maths.

---

## 1. Background and theory

A2 taught us to find the *bottom* of a PES valley — the equilibrium geometry. This
script zooms in on the **simplest possible valley** and maps it out in full: the energy
of a single bond as we stretch and compress it. That one curve secretly contains almost
everything the bond does — how long it is, how stiff it is, what frequency it vibrates
at, and how (and whether) a given method can describe it breaking.

### 1.1 A bond is a one-dimensional slice of the PES

For a **diatomic** molecule (or a single isolated bond in a larger one) the whole PES
collapses to a function of just **one** coordinate — the bond length $r$:

$$E = E(r).$$

- $r$: the internuclear distance (the one degree of freedom of a diatomic; recall
  $3N-5 = 1$ for $N=2$).
- $E(r)$: the potential energy curve. Stretch or squeeze the bond, record the energy,
  and you have mapped the curve that governs the bond's entire behaviour.

### 1.2 The Morse potential and the harmonic approximation

A realistic bond curve is captured beautifully by the **Morse potential**:

$$V(r) = D_e\left[\,1 - e^{-a\,(r - r_e)}\,\right]^2,$$

- $r_e$: the **equilibrium bond length** — where the curve bottoms out.
- $D_e$: the **well depth** — the energy needed to pull the atoms fully apart, measured
  from the bottom of the well (the *dissociation energy*).
- $a$: a **width parameter** — how steep and narrow the well is.

Near the very bottom, *any* smooth well looks like a **parabola**. This is the
**harmonic approximation**:

$$V(r) \approx \tfrac{1}{2}\,k\,(r - r_e)^2,
\qquad k = \left.\frac{d^2 V}{dr^2}\right|_{r=r_e},$$

- $k$: the **force constant** — literally the *curvature* at the bottom of the well
  (the Hessian of A2, for one coordinate). A stiffer bond = a narrower, steeper well =
  a larger $k$.

That curvature fixes the **vibrational frequency**. In wavenumbers ($\text{cm}^{-1}$,
the unit an IR spectrometer reports):

$$\tilde\nu_e = \frac{1}{2\pi c}\sqrt{\frac{k}{\mu}},
\qquad \mu = \frac{m_A\, m_B}{m_A + m_B},$$

- $\tilde\nu_e$: the harmonic vibrational wavenumber.
- $c$: the speed of light.
- $\mu$: the **reduced mass** of the two atoms — the effective mass that vibrates.

*In words:* there is a direct chain, **shape of the well bottom → force constant → IR
frequency**. Measure any one and you know the others — which is exactly why a computed
curve can be checked against a real vibrational spectrum.

### 1.3 Anharmonicity, and $D_e$ vs. $D_0$

The Morse well is **not** a symmetric parabola — it is softer on the stretching side and
flattens onto a dissociation plateau. This **anharmonicity** has two consequences you
must keep straight.

First, the real vibrational energy levels are **not** evenly spaced; they crowd together
as they climb toward dissociation:

$$G(v) = \tilde\nu_e\left(v + \tfrac12\right) - \tilde\nu_e x_e\left(v + \tfrac12\right)^2,$$

- $v$: the vibrational quantum number ($0, 1, 2, …$).
- $\tilde\nu_e x_e$: the **anharmonicity constant** — the correction that bends the ladder.

Second, even the *lowest* level ($v = 0$) sits a **zero-point energy** above the bottom
of the well:

$$\text{ZPE} = \tfrac{1}{2} h c\, \tilde\nu_e ,$$

so the dissociation energy you could actually *measure* starts from $v=0$, not from the
unreachable bottom:

$$D_0 = D_e - \text{ZPE}.$$

- $D_e$: well depth, from the very bottom (what your *curve* gives).
- $D_0$: the **observable** dissociation energy, from the $v=0$ level (what *experiment*
  gives). The gap between them is one ZPE.

### 1.4 How a bond breaks — and where a method breaks with it

Here is the deep lesson of this script. Take **H₂** dissociating into two H atoms, and
watch three different methods try to describe it.

**Restricted Hartree–Fock (RHF)** forces the two electrons into the *same* spatial
orbital with paired spins — all the way out to infinite separation. But two separated H
atoms are two **radicals**, one electron on each atom. RHF simply cannot represent that:
it keeps an equal mix of the correct covalent picture and a spurious **ionic** one
(H⁺ H⁻). As you stretch the bond, the RHF energy therefore climbs to a badly
**too-high**, wrong-shaped plateau. This is the textbook face of **static (non-dynamic)
correlation** — a situation no single closed-shell determinant can capture.

**Unrestricted Hartree–Fock (UHF)** lets the two spins occupy *different* spatial
orbitals. Past a certain bond length (the **Coulson–Fischer point**) it "breaks
symmetry," localising one electron on each atom, and dissociates to two neutral radicals
with a physically sensible curve shape. The price: the wavefunction is no longer a pure
spin state — it is **spin-contaminated**, which shows up as the expectation value
$\langle S^2\rangle$ drifting away from its ideal value (0 for a singlet) toward 1 as the
bond breaks.

**CCSD(T)** — or for a two-electron system, plain **CCSD**, which is *exact within the
basis* (it equals full configuration interaction) — recovers the missing correlation
explicitly and gives the **correct** curve to compare both HF variants against.

The moral: whether a method can even *describe* your chemistry depends on the electronic
situation. Bond breaking, diradicals, and many transition-metal and excited-state
problems demand more than one closed-shell configuration.

### 1.5 Symbol glossary

| Symbol            | Name                     | What it is / does                                          |
| ----------------- | ------------------------ | ---------------------------------------------------------- |
| $r,\ r_e$         | bond length; equilibrium | the coordinate; its value at the well minimum              |
| $V(r)$            | potential energy curve   | energy vs. bond length                                     |
| $D_e,\ D_0$       | dissociation energies    | from the well bottom; from $v=0$ ($D_0 = D_e - $ ZPE)      |
| $k$               | force constant           | curvature $V''(r_e)$; bond stiffness                       |
| $\mu$             | reduced mass             | $m_Am_B/(m_A+m_B)$; the effective vibrating mass           |
| $\tilde\nu_e$     | harmonic wavenumber      | vibration frequency in cm⁻¹                                |
| $\text{ZPE}$      | zero-point energy        | $\tfrac12 hc\tilde\nu_e$; energy of the $v=0$ level        |
| $\langle S^2\rangle$ | spin expectation      | 0 for a pure singlet; rises under UHF spin contamination   |

---

## 2. The assignment (curated)

You will dissect a single bond — **H₂**, the cleanest example in all of quantum chemistry
— by mapping its potential energy curve three different ways and pulling spectroscopic
constants out of the shape.

### 2.1 The system and the three curves

The molecule is **H₂**; keep the basis fixed at **`def2-TZVP`** throughout so the three
methods are compared on equal footing. You will compute the energy as a function of bond
length from compressed (~0.4 Å) to well past dissociation (~3.5 Å):

| Curve | What it shows | Method |
| --- | --- | --- |
| **RHF** | the *failure*: wrong, too-high dissociation | restricted HF |
| **UHF** (symmetry-broken) | the mean-field *fix*: right shape, spin contamination | unrestricted HF |
| **CCSD(T)** | the *reference*: near-exact curve (CCSD = full CI for 2 e⁻) | coupled cluster |

### 2.2 The scans + ORCA keyword lines

Each curve is a **relaxed bond scan** over the H–H distance (atoms 0 and 1), from 0.4 to
3.5 Å in ~40 steps. The scan block is the same; only the method on the `!` line changes.

**RHF** — dissociates incorrectly:
```
! RHF def2-TZVP
%geom Scan
  B 0 1 = 0.4, 3.5, 40
end end
* xyzfile 0 1 h2.xyz
```

**UHF (broken symmetry)** — you must *seed* the symmetry breaking, or UHF silently
collapses back onto the RHF curve. Mix the HOMO (MO 0) and LUMO (MO 1) in the initial
guess by a small angle so the SCF relaxes *down* into the broken-symmetry solution:
```
! UHF def2-TZVP
%scf
  Rotate {0,1,30,0,0} end    # seed spin-symmetry breaking (HOMO/LUMO mix, 30 deg)
end
%geom Scan
  B 0 1 = 0.4, 3.5, 40
end end
* xyzfile 0 1 h2.xyz
```

**CCSD(T)** — the near-exact reference:
```
! CCSD(T) def2-TZVP
%geom Scan
  B 0 1 = 0.4, 3.5, 40
end end
* xyzfile 0 1 h2.xyz
```

**ORCA keyword notes (verified in ORCA 6.0.1):**

- `B 0 1 = start, stop, N` scans the **b**ond between atoms 0 and 1 (0-based indices)
  over `N` points; ORCA prints the resulting energy surface and writes a
  `.relaxscanact.dat` that Workbench's scan-plot tool reads.
- The `Rotate {a,b,angle,op1,op2}` block breaks the guess symmetry. A **small** angle
  (~20–45°) relaxes into the broken-symmetry ground state; a 90° swap overshoots onto a
  higher excited configuration, so keep it small. Verify it worked by checking that
  $\langle S^2\rangle$ (printed each point) is ~0 near equilibrium and rises toward ~1 as
  the bond stretches — and that the UHF curve drops **below** RHF past the Coulson–Fischer
  point. (If a short-$r$ point looks anomalously high, it converged to the excited
  branch — reduce the angle.)
- `RHF`/`UHF` select the restricted/unrestricted references; `CCSD(T)` is the coupled
  cluster reference (no auxiliary basis needed).

### 2.3 Extracting the constants

1. **Equilibrium length** $r_e$: the minimum of your curve.
2. **Force constant / frequency**: fit the ~5 points nearest the minimum to a parabola
   $V \approx \tfrac12 k (r-r_e)^2$ → $k$ → $\tilde\nu_e$ via the formula in Section 1.2
   (reduced mass of H₂: $\mu = m_H/2$).
3. **Cross-check** that $\tilde\nu_e$ against a **direct** frequency calculation at the
   optimized geometry: `! CCSD(T) def2-TZVP Opt Freq` (or `! B3LYP def2-TZVP Opt Freq`
   for speed) — ORCA prints the harmonic frequency directly.

Reference values for H₂ to compare against: $r_e = 0.741$ Å, $\tilde\nu_e = 4401$ cm⁻¹,
$D_0 = 432$ kJ/mol.

---

## 3. Deliverables and evaluation questions

Produce:
- a **plot** of energy vs. bond length with the RHF, UHF and CCSD(T) curves overlaid;
- your extracted $r_e$ and $\tilde\nu_e$, next to the experimental values.

Then answer:

1. **Equilibrium length.** What $r_e$ do you extract, and how does it compare with
   experiment (and with a direct optimization)?
2. **Frequency from curvature.** What $\tilde\nu_e$ do you get from the well curvature?
   Does it agree with the directly calculated harmonic frequency? If they differ, why
   might a finite scan spacing bias the curvature estimate?
3. **Dissociation — the RHF failure.** Describe the large-$r$ behaviour of the **RHF**
   curve. Is the dissociation energy it implies physically reasonable? What is RHF
   failing to represent, and why can a single closed-shell determinant not fix it?
4. **Symmetry breaking.** At roughly what bond length does the **UHF** curve depart from
   the RHF one (the Coulson–Fischer point), and why *there* and not earlier? Track
   $\langle S^2\rangle$ along the curve — what is it doing, and what does that cost you?
   Which of the two HF curves would you trust for the dissociation energy, and how does
   it compare to CCSD(T)?
5. **Zero-point energy.** Using your $\tilde\nu_e$, estimate the ZPE of H₂. How large is
   the gap between $D_e$ (your well depth) and the observable $D_0$? Is it chemically
   significant?

---

## 4. Going further (optional)

- **Isotopologues.** Repeat the frequency extraction for H₂ → HD → D₂ (change the masses,
   not the electronic curve) and confirm $\tilde\nu_e \propto 1/\sqrt{\mu}$. This
   foreshadows the isotope-effect capstone.
- **Morse fit.** Fit your full CCSD(T) curve to the Morse form and extract $D_e$ and the
   anharmonicity $\tilde\nu_e x_e$; compare $D_e$ to the plateau height of your curve.
- **A harder bond.** Repeat for **N₂** (a triple bond): RHF fails far more dramatically,
   and even CCSD(T) struggles at full dissociation — a preview of multireference chemistry.

---

## Appendix — rendering this script to PDF

`pandoc A3_bond_potential_curve.md -o A3.pdf --pdf-engine=xelatex` (needs Pandoc + a TeX
engine such as MiKTeX or TinyTeX). View live maths in VS Code's Markdown preview or on
GitHub.

## References

- G. Herzberg, *Molecular Spectra and Molecular Structure I: Spectra of Diatomic Molecules*.
- A. Szabo & N. S. Ostlund, *Modern Quantum Chemistry* — RHF vs. UHF and the H₂
  dissociation problem (the canonical treatment).
- P. W. Atkins & R. Friedman, *Molecular Quantum Mechanics* — Morse potential,
  vibrational constants, anharmonicity.
