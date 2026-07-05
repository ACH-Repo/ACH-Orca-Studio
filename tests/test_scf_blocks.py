"""parse_scf_blocks: the FULL SCF history (every cycle), not just the last
block — what the live progress plot shows for an OPT job.

Run:  python -m pytest tests/test_scf_blocks.py -q
"""

from orca_workbench.core import orca_parser as P


_TWO_CYCLES = """
                       *****************************
                       * Geometry Optimization Run *
                       *****************************

GEOMETRY OPTIMIZATION CYCLE   1

                                      D-I-I-S
  Iteration    Energy (Eh)           Delta-E    RMSDP     MaxDP
     1     -74.9612license34 header noise
     1     -74.96123456      0.00e+00  1.2e-02  4.5e-02
     2     -74.96234567     -1.11e-03  3.4e-03  1.2e-02
     3     -74.96245678     -1.11e-04  5.6e-04  2.3e-03
               ***Energy Check signals convergence***
FINAL SINGLE POINT ENERGY       -74.962456780000

GEOMETRY OPTIMIZATION CYCLE   2

                                      D-I-I-S
  Iteration    Energy (Eh)           Delta-E    RMSDP     MaxDP
     1     -74.96250000      0.00e+00  1.0e-03  3.0e-03
     2     -74.96256789     -6.79e-05  2.0e-04  8.0e-04
               *** SCF CONVERGED AFTER   2 CYCLES ***
FINAL SINGLE POINT ENERGY       -74.962567890000
"""

_HANDOVER = """
                                      D-I-I-S
  Iteration    Energy (Eh)
     1     -100.10000000      0.0
     2     -100.20000000     -0.1
                                     S-O-S-C-F
  Iteration    Energy (Eh)
     3     -100.25000000     -0.05
     4     -100.26000000     -0.01
               ***Energy Check signals convergence***
"""


def test_two_cycles_two_blocks():
    blocks = P.parse_scf_blocks(_TWO_CYCLES)
    assert len(blocks) == 2
    assert blocks[0] == [-74.96123456, -74.96234567, -74.96245678]
    assert blocks[1] == [-74.96250000, -74.96256789]


def test_diis_soscf_handover_is_one_block():
    blocks = P.parse_scf_blocks(_HANDOVER)
    assert len(blocks) == 1
    assert len(blocks[0]) == 4
    assert blocks[0][-1] == -100.26


def test_parse_orca_output_carries_both_keys():
    parsed = P.parse_orca_output(_TWO_CYCLES)
    assert parsed["scf_blocks"] == P.parse_scf_blocks(_TWO_CYCLES)
    # back-compat: scf_iterations is still the LAST block
    assert parsed["scf_iterations"] == parsed["scf_blocks"][-1]
    assert parsed["n_opt_cycles"] == 2
    assert len(parsed["final_energies"]) == 2


def test_no_scf_no_blocks():
    assert P.parse_scf_blocks("nothing to see here\n1 2.0\n") == []
