# -*- coding: utf-8 -*-
"""Test du lecteur PDF SANS Kivy : python3 diag.py
Dit si PyMuPDF est installé et si chaque PDF du dossier 'pdf' se rend correctement."""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
print("Python", sys.version.split()[0])

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        print("ECHEC : PyMuPDF absent.\n  -> pip install --break-system-packages pymupdf")
        sys.exit(1)
print("PyMuPDF", getattr(fitz, "VersionBind", "?"))

for mod in ("kivy", "kivymd"):
    try:
        m = __import__(mod)
        print(mod, getattr(m, "__version__", "?"))
    except ImportError:
        print(f"ATTENTION : {mod} absent")

pdfs = []
for cur, dirs, files in os.walk(os.path.join(BASE, "pdf")):
    pdfs += [os.path.join(cur, f) for f in files if f.lower().endswith(".pdf")]
print(f"\n{len(pdfs)} PDF dans le dossier pdf\n")

bad = 0
out = os.path.join(tempfile.gettempdir(), "gbepc_diag.png")
for p in sorted(pdfs):
    name = os.path.basename(p)[:45]
    try:
        doc = fitz.open(p)
        if doc.needs_pass:
            print(f"PROTEGE  {name}")
            bad += 1
            continue
        page = doc[0]
        zoom = 600 / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(out)
        white = pix.samples.count(b"\xff") / max(len(pix.samples), 1)
        print(f"OK       {name} | {doc.page_count} p | {pix.width}x{pix.height} | contenu: {100 * (1 - white):.0f}%")
        doc.close()
    except Exception as e:
        bad += 1
        print(f"ECHEC    {name} -> {type(e).__name__}: {e}")
print("\nRESULTAT :", "tout est bon" if bad == 0 else f"{bad} PDF en échec")
