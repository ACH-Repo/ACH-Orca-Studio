"""Canvas images (core.canvas_images) — the pure half of "paste a picture onto
the Workflow canvas": header parsing, project-relative storage, node sizing.

Run:  python -m pytest tests/test_canvas_images.py -q
"""

import os
import struct
import zlib

import pytest

from orca_workbench.core import canvas_images as ci


def _png_bytes(w, h, colour=b"\xff\x00\x00"):
    """A minimal but REAL png (so PIL/Tk can also open it if present)."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)          # 8-bit RGB
    raw = b"".join(b"\x00" + colour * w for _ in range(h))       # filter byte per row
    return (ci._PNG_SIG + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_png_size_from_header():
    assert ci.image_size(_png_bytes(37, 11)) == (37, 11)


def test_gif_size_from_header():
    data = b"GIF89a" + struct.pack("<HH", 64, 48) + b"\x00" * 10
    assert ci.image_size(data) == (64, 48)


def test_unknown_bytes_have_no_size():
    assert ci.image_size(b"not an image at all") == (0, 0)


def test_store_image_is_project_relative_and_deduplicated(tmp_path):
    data = _png_bytes(8, 8)
    rel1 = ci.store_image(str(tmp_path), data)
    rel2 = ci.store_image(str(tmp_path), data)          # same bytes -> same file
    assert rel1 == rel2
    assert rel1.startswith("WORKFLOW_IMG/") and rel1.endswith(".png")
    assert "\\" not in rel1                             # portable in project.json
    assert os.path.isfile(os.path.join(str(tmp_path), rel1))
    # different image -> different file, both kept
    other = ci.store_image(str(tmp_path), _png_bytes(9, 9))
    assert other != rel1
    assert len(os.listdir(os.path.join(str(tmp_path), "WORKFLOW_IMG"))) == 2


def test_read_image_file_rejects_non_images(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    assert ci.read_image_file(str(p)) == b""
    img = tmp_path / "pic.png"
    img.write_bytes(_png_bytes(4, 4))
    assert ci.read_image_file(str(img))[:8] == ci._PNG_SIG
    assert ci.read_image_file(str(tmp_path / "missing.png")) == b""


def test_to_png_passes_png_through():
    data = _png_bytes(5, 5)
    assert ci.to_png(data) is data


def test_fit_box_preserves_aspect_and_clamps():
    w, h = ci.fit_box((1600, 800), max_w=320.0, max_h=260.0)
    assert (round(w), round(h)) == (320, 160)           # width-limited, 2:1 kept
    w, h = ci.fit_box((100, 2000), max_w=320.0, max_h=260.0)
    assert round(h) == 260 and w >= 80                  # height-limited
    w, h = ci.fit_box((40, 30), max_w=320.0, max_h=260.0)
    assert (round(w), round(h)) == (80, 30)             # small image: min width floor
    assert ci.fit_box((0, 0)) == (320.0, 260.0)         # unknown -> plain box


def test_paths_from_text_handles_quotes_and_file_uris():
    got = ci._paths_from_text('"C:/tmp/a.png"\nfile:///C:/tmp/b.png\n/home/me/c.png\n')
    assert got == ["C:/tmp/a.png", "C:/tmp/b.png", "/home/me/c.png"]


def test_clipboard_falls_back_to_a_path_in_the_text_clipboard(tmp_path, monkeypatch):
    img = tmp_path / "from_text.png"
    img.write_bytes(_png_bytes(6, 6))
    # no Pillow grab, no xclip/wl-paste: the text clipboard is all that's left
    monkeypatch.setattr(ci, "_pillow", lambda: False)
    monkeypatch.setattr(ci, "_run_capture", lambda cmd, timeout=4: b"")
    data, why = ci.clipboard_image_bytes(clipboard_text=str(img))
    assert data[:8] == ci._PNG_SIG and why == "from_text.png"


def test_clipboard_reports_why_when_empty(monkeypatch):
    monkeypatch.setattr(ci, "_pillow", lambda: False)
    monkeypatch.setattr(ci, "_run_capture", lambda cmd, timeout=4: b"")
    data, why = ci.clipboard_image_bytes(clipboard_text="")
    assert data == b"" and "no image on the clipboard" in why


def test_clipboard_uses_xclip_png_target(monkeypatch):
    png = _png_bytes(3, 3)
    monkeypatch.setattr(ci, "_pillow", lambda: False)
    monkeypatch.setattr(ci, "_run_capture",
                        lambda cmd, timeout=4: png if cmd[0] == "xclip" else b"")
    data, why = ci.clipboard_image_bytes()
    assert data == png and why == "xclip"


def test_to_png_without_pillow_refuses_foreign_formats(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_pil(name, *a, **k):
        if name.startswith("PIL"):
            raise ImportError("no pillow")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pil)
    with pytest.raises(ValueError) as e:
        ci.to_png(b"\xff\xd8\xff\xe0 fake jpeg")
    assert "Pillow" in str(e.value)
