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
    if a.imagen:
        from finanzas.entities import extraer_entidades
        ocr_txt = _ocr_local(a.imagen)
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
    )
    for m in msgs:
        if m:
            print(m)


def _ocr_local(path):
    import re
    import subprocess
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", "spa", "--psm", "6"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout or ""
    except Exception:
        return ""


if __name__ == "__main__":
    main()