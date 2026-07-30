"""Clipboard / file images for the node-graph canvas — the pure half.

Pasting a picture onto the Workflow canvas (a whiteboard sketch of the pipeline,
a spectrum from a paper, an annotated screenshot of a geometry) needs three
things that have nothing to do with Tkinter:

  * getting image BYTES out of the system clipboard. Tk itself can't do it —
    `clipboard_get` only handles text targets — so this is Pillow's
    `ImageGrab.grabclipboard()` where available, then `xclip` / `wl-paste` on
    X11 / Wayland (the gateway case), and finally a *path* sitting in the text
    clipboard (copying a file in a file manager);
  * reading the pixel size out of those bytes WITHOUT Pillow, so a node can be
    created at the right aspect ratio even on a bare install (PNG/GIF/JPEG
    headers are parsed here);
  * storing them INSIDE the project (``WORKFLOW_IMG/<sha1>.png``) rather than
    referencing wherever they came from, so a project stays self-contained and
    portable — the reason "paste an image" was never just a canvas feature.

Tk 8.6 displays PNG and GIF natively, so bytes in any other format are converted
to PNG when Pillow is present and refused (with a clear reason) when it isn't.
"""

import hashlib
import io
import os
import struct
import subprocess


IMAGE_DIR = "WORKFLOW_IMG"
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Formats Tk's PhotoImage can display unaided; anything else needs Pillow.
NATIVE_EXTS = (".png", ".gif")
READABLE_EXTS = NATIVE_EXTS + (".jpg", ".jpeg", ".bmp", ".ppm", ".pgm", ".tif", ".tiff")


def image_size(data):
    # type: (bytes) -> tuple
    """(width, height) parsed from PNG / GIF / JPEG bytes, or (0, 0) if unknown.
    Pure header parsing — no Pillow needed, so a freshly pasted image can be
    given a sensibly proportioned node on any install."""
    try:
        if data[:8] == _PNG_SIG and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if data[:2] == b"\xff\xd8":                       # JPEG: walk the segments
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h)
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg
    except Exception:
        pass
    return 0, 0


def _pillow():
    try:
        from PIL import Image      # noqa: F401
        return True
    except Exception:
        return False


def to_png(data):
    # type: (bytes) -> bytes
    """`data` re-encoded as PNG (via Pillow). Returns it unchanged when it's
    already PNG; raises ValueError when conversion isn't possible here."""
    if data[:8] == _PNG_SIG:
        return data
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        raise ValueError("this image isn't a PNG/GIF and Pillow isn't installed to "
                         "convert it (pip install --user pillow)")
    except Exception as e:
        raise ValueError("could not read the image data: {}".format(e))


def read_image_file(path):
    # type: (str) -> bytes
    """Bytes of an image file, or b'' if it isn't one we can use."""
    if not path or not os.path.isfile(path):
        return b""
    if os.path.splitext(path)[1].lower() not in READABLE_EXTS:
        return b""
    try:
        with open(path, "rb") as f:
            return f.read()
    except IOError:
        return b""


def _run_capture(cmd, timeout=4):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (OSError, ValueError):
        return b""
    try:
        out, _err = p.communicate(timeout=timeout)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
        return b""
    return out or b""


def _paths_from_text(text):
    """Candidate file paths out of a text clipboard (a file manager copy puts a
    path, or a file:// URI, possibly several lines)."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip().strip('"').strip("'")
        if s.startswith("file://"):
            s = s[7:]
            if len(s) > 2 and s[0] == "/" and s[2] == ":":
                s = s[1:]              # file:///C:/... on Windows
        if s:
            out.append(s)
    return out


def clipboard_image_bytes(clipboard_text=None):
    # type: (str) -> tuple
    """(png_bytes, source_description) for whatever image is on the clipboard, or
    (b'', reason) if there isn't one.

    Tried in order: Pillow's ImageGrab (Windows / macOS / Linux with the helper
    tools), `xclip` then `wl-paste` reading the ``image/png`` target directly
    (X11 / Wayland — what a ThinLinc session offers), and finally a file path
    from the TEXT clipboard, which `clipboard_text` supplies (the caller reads it
    from Tk; this module stays UI-free).
    """
    reasons = []
    if _pillow():
        try:
            from PIL import ImageGrab
            obj = ImageGrab.grabclipboard()
        except Exception as e:
            obj = None
            reasons.append("clipboard grab unavailable ({})".format(e))
        if isinstance(obj, list):        # a list of file names
            for p in obj:
                data = read_image_file(p)
                if data:
                    return to_png(data), os.path.basename(p)
            reasons.append("the clipboard holds files, none of them an image")
        elif obj is not None:
            try:
                buf = io.BytesIO()
                obj.save(buf, format="PNG")
                return buf.getvalue(), "clipboard image"
            except Exception as e:
                reasons.append("could not encode the clipboard image ({})".format(e))
    for cmd in (["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                ["wl-paste", "--type", "image/png"]):
        data = _run_capture(cmd)
        if data[:8] == _PNG_SIG:
            return data, cmd[0]
    for p in _paths_from_text(clipboard_text):
        data = read_image_file(p)
        if data:
            try:
                return to_png(data), os.path.basename(p)
            except ValueError as e:
                reasons.append(str(e))
    if not reasons:
        reasons.append("no image on the clipboard (copy one, or use 'Add image from "
                       "file...'). On Linux this needs Pillow, xclip or wl-clipboard.")
    return b"", "; ".join(reasons)


def store_image(project_root, data, subdir=IMAGE_DIR):
    # type: (str, bytes, str) -> str
    """Write `data` into the project as `WORKFLOW_IMG/img_<sha1>.png` and return
    the PROJECT-RELATIVE path (forward slashes, so project.json stays portable).

    Content-addressed: pasting the same picture twice reuses one file instead of
    growing the project, and an image referenced by a node can never be silently
    replaced by a different one.
    """
    png = to_png(data)
    digest = hashlib.sha1(png).hexdigest()[:16]
    rel = "{}/img_{}.png".format(subdir, digest)
    target = os.path.join(project_root, subdir, "img_{}.png".format(digest))
    if not os.path.isfile(target):
        d = os.path.dirname(target)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(target, "wb") as f:
            f.write(png)
    return rel


def fit_box(native, max_w=320.0, max_h=260.0, min_w=80.0):
    # type: (tuple, float, float, float) -> tuple
    """A node box (w, h) for an image of `native` (w, h) pixels: the native size,
    scaled down to fit max_w × max_h, aspect preserved. Unknown native size falls
    back to a plain rectangle."""
    nw, nh = native
    if nw <= 0 or nh <= 0:
        return (float(max_w), float(max_h))
    scale = min(1.0, float(max_w) / nw, float(max_h) / nh)
    return (max(float(min_w), nw * scale), max(20.0, nh * scale))
