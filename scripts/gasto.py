#!/usr/bin/env python3
"""gasto.py — CLI compatible del motor deterministico de finanzas.

Uso (compatibilidad con adapter_whatsapp.py):
  gasto.py --grupo personal|hogar|andrea --texto "pague 5000 mercado por nequi"
  gasto.py --grupo hogar --imagen /ruta/recibo.jpg --evidencia img_123.jpg
  gasto.py --grupo hogar --texto "..." --sender 573002084572
  gasto.py --grupo personal --texto "..." --dry-run
  gasto.py --grupo personal --texto "..." --ts 1786373940   # epoch del mensaje

El adapter Hermes invoca este binario via subprocess y devuelve stdout al chat.
Se preservan --imagen, --evidencia, --sender y --dry-run.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from finanzas.intents import Motor  # noqa: E402
from finanzas import sheets as _sheets_mod  # noqa: E402


def _build_srv():
    return _sheets_mod._cred()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", required=True, choices=["personal", "hogar", "andrea"])
    ap.add_argument("--texto", default=None)
    ap.add_argument("--imagen", default=None)
    ap.add_argument("--evidencia", default=None)
    ap.add_argument("--sender", default=None)
    ap.add_argument("--ts", default=None, help="timestamp Unix del mensaje (epoch)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.texto and not a.imagen:
        print("Uso: gasto.py --grupo KEY [--texto TEXTO] [--imagen RUTA]")
        sys.exit(2)

    srv = None
    if not a.dry_run:
        try:
            srv = _build_srv()
        except SystemExit as e:
            # en dry-run no hace falta conexion
            if not a.dry_run:
                print(str(e))
                sys.exit(1)
        except Exception as e:
            if not a.dry_run:
                print("⚠️ Banco sin conexión a Google: %s" % e)
                sys.exit(1)

    # OCR local (se mantiene de la version previa)
    texto = a.texto or ""
    ocr_txt = None
    ocr_inestable = False
    if a.imagen:
        from finanzas.entities import extraer_entidades
        ocr_res = _ocr_local(a.imagen)
        ocr_txt = ocr_res["text"]
        ocr_inestable = ocr_res["inestable"]
        if a.texto:
            texto = a.texto + "\n" + ocr_txt
        else:
            texto = ocr_txt

    motor = Motor(srv=srv)
    msgs = motor.procesar(
        grupo=a.grupo,
        sender=a.sender,
        texto=texto or "",
        imagen=a.imagen,
        evidencia=a.evidencia or (os.path.basename(a.imagen) if a.imagen else ""),
        dry_run=a.dry_run,
        ts_mensaje=a.ts,
        caption=a.texto or None,
        ocr_text=ocr_txt,
        ocr_inestable=ocr_inestable,
    )
    for m in msgs:
        if m:
            print(m)


def _preprocesar_ocr(path):
    """Escala de grises + upscale 2x + autocontraste (PIL). Devuelve ruta tmp."""
    import tempfile
    from PIL import Image, ImageOps
    im = Image.open(path).convert("L")
    w, h = im.size
    im = im.resize((int(w * 2), int(h * 2)), Image.LANCZOS)
    im = ImageOps.autocontrast(im)
    out = os.path.join(tempfile.gettempdir(), "finanzas_ocr_pre.png")
    im.save(out)
    return out


def _ocr_local(path):
    """Doble pasada de tesseract (--psm 6 y --psm 3) sobre la imagen
    preprocesada. Devuelve {"text": str, "inestable": bool}.

    Si los montos candidatos difieren entre pasadas, el candidato se marca
    inestable (no se autoconfía). Si una pasada falla del todo, se conserva
    la otra sin marcar inestable.
    """
    import re
    import subprocess
    from finanzas.normalize import analizar_monto
    try:
        pre = _preprocesar_ocr(path)
    except Exception:
        pre = path  # sin PIL, tesseract sobre el original
    salidas = {}
    for psm in ("6", "3"):
        try:
            r = subprocess.run(["tesseract", pre, "stdout", "-l", "spa", "--psm", psm],
                               capture_output=True, text=True, timeout=120)
            salidas[psm] = r.stdout or ""
        except Exception:
            salidas[psm] = ""
    txt6, txt3 = salidas.get("6", ""), salidas.get("3", "")
    # valores candidatos de monto por cada pasada (independiente del formato)
    vals6 = {c["valor"] for c in analizar_monto(txt6).get("candidatos", [])}
    vals3 = {c["valor"] for c in analizar_monto(txt3).get("candidatos", [])}
    inestable = bool(txt6 and txt3 and vals6 != vals3)
    # el texto más rico (con montos) se usa para descripción/entidades
    texto = txt6 if txt6.strip() else txt3
    return {"text": texto, "inestable": inestable}


if __name__ == "__main__":
    main()