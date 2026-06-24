"""Try to import every file in a directory through ORCA Workbench's universal
coordinate reader, logging what succeeds and what fails (and why).

A triage aid for the Molecules-tab importer: point it at a folder of structure
files and see, per file, whether read_structures() can turn it into atoms — and
for failures, the exact exception. Read-only; writes only the log file.

Usage:
    python tools/diagnose_import.py <dir> [logfile]
Defaults: logfile = import_diagnostic.log in the current directory.
"""

import os
import sys

from orca_workbench.core import coords


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    logpath = sys.argv[2] if len(sys.argv) > 2 else "import_diagnostic.log"
    if not os.path.isdir(src):
        print("Not a directory: {}".format(src))
        return 2

    files = sorted(os.path.join(src, f) for f in os.listdir(src)
                   if os.path.isfile(os.path.join(src, f)))
    rows = []
    ok = fail = 0
    for path in files:
        ext = (os.path.splitext(path)[1].lstrip(".") or "(none)")
        supported = "Y" if coords.is_supported_import_file(path) else "-"
        size = os.path.getsize(path)
        try:
            structs = coords.read_structures(path)
            n = len(structs)
            natoms = len(structs[0][0]) if structs else 0
            ok += 1
            rows.append("OK    {:<26} ext={:<9} dlg={} size={:<6} structs={} atoms={}".format(
                os.path.basename(path), ext, supported, size, n, natoms))
        except Exception as e:
            fail += 1
            msg = "{}: {}".format(type(e).__name__, e).replace("\n", " | ")
            rows.append("FAIL  {:<26} ext={:<9} dlg={} size={:<6} :: {}".format(
                os.path.basename(path), ext, supported, size, msg))

    header = "Import diagnostic: {}\n{} files — {} ok, {} fail   (dlg=Y: shown in file dialog)".format(
        src, len(files), ok, fail)
    out = header + "\n" + "=" * 72 + "\n" + "\n".join(rows) + "\n"
    with open(logpath, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    print("Wrote {}".format(os.path.abspath(logpath)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
