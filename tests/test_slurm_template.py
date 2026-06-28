"""The editable SLURM template: a per-machine config override wins over the
packaged default, an explicit path always wins, and required placeholders are
detectable for a save-time warning.

Run:  python -m pytest tests/test_slurm_template.py -q
"""

from orca_workbench.core import slurm, config


def test_explicit_path_wins(tmp_path):
    p = tmp_path / "t.sh"
    p.write_text("EXPLICIT !!##RUNDIR##!!\n", encoding="utf-8")
    assert "EXPLICIT" in slurm.load_template(str(p))


def test_config_override_preferred(monkeypatch):
    monkeypatch.setattr(config, "get",
                        lambda k, d=None: "MY TEMPLATE !!##CORES##!!\n" if k == slurm.CONFIG_KEY else d)
    assert "MY TEMPLATE" in slurm.load_template()


def test_default_used_when_no_override(monkeypatch):
    monkeypatch.setattr(config, "get", lambda k, d=None: "")
    t = slurm.load_template()
    assert "#SBATCH --partition" in t          # the packaged default


def test_default_template_text_has_partition():
    assert "--partition=" in slurm.default_template_text()


def test_missing_required_placeholders():
    assert slurm.missing_required_placeholders("nothing here") == list(slurm.REQUIRED_PLACEHOLDERS)
    full = "x !!##CORES##!! y !!##RUNDIR##!! z !!##INPFILE##!!"
    assert slurm.missing_required_placeholders(full) == []
