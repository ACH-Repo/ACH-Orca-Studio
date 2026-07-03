"""Tests for the pure path-cleaning helper in ui.extprog.

strip_path_quotes has no Tk/config dependency, so it always runs.

Run:  python -m pytest tests/test_extprog.py -q
"""

from orca_workbench.ui.extprog import strip_path_quotes


def test_strips_windows_copy_as_path_quotes():
    # Windows "Copy as path" wraps in double quotes.
    assert strip_path_quotes('"C:\\Program Files\\Avogadro2\\bin\\avogadro2.exe"') \
        == "C:\\Program Files\\Avogadro2\\bin\\avogadro2.exe"


def test_strips_single_quotes_and_whitespace():
    assert strip_path_quotes("  '/usr/bin/jmol'  ") == "/usr/bin/jmol"


def test_leaves_unquoted_paths_untouched():
    p = "C:\\Program Files\\Avogadro2\\bin\\avogadro2.exe"
    assert strip_path_quotes(p) == p
    assert strip_path_quotes("avogadro") == "avogadro"


def test_only_a_matched_pair_is_removed():
    # An unbalanced quote is left alone (don't corrupt the value).
    assert strip_path_quotes('"C:\\x') == '"C:\\x'
    assert strip_path_quotes('C:\\x"') == 'C:\\x"'
    # mismatched pair (double vs single) is left alone
    assert strip_path_quotes("\"C:\\x'") == "\"C:\\x'"


def test_empty_and_none():
    assert strip_path_quotes("") == ""
    assert strip_path_quotes(None) == ""
    assert strip_path_quotes('""') == ""
