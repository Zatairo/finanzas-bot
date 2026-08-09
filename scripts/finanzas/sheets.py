"""sheets.py — Capa Google Sheets con append no-destructivo.

- append con spreadsheets.values.append (INSERT_ROWS) SIEMPRE al final lógico:
  no se calcula la fila leyendo la hoja, no se usa update sobre una fila
  calculada -> elimina la carrera/sobrescritura.
- la fila real se extrae de updates.updatedRange y se guarda en el ledger.
- ids unicos (timestamp+uuid corto) sin escanear -> no hay reutilizacion.
- idempotencia por reintento via op_key.
- borrar = marcar estado 'anulado' (nunca se vacian filas).
"""
import datetime
import re
import uuid

from . import config

# Rango de tabla para el append: inserta al final de las columnas A:P.
_APPEND_RANGE = "Hoja 1!A:P"


def _cred():
    import json
    import os
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    HERMES = os.environ.get("HERMES_HOME", "/home/soporte/.hermes")
    tok_path = os.path.join(HERMES, "google_token.json")
    if not os.path.exists(tok_path):
        raise SystemExit("ERROR: no existe google_token.json en %s" % tok_path)
    tok = json.load(open(tok_path, encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(tok)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def gen_id():
    """Timestamp(ms) + uuid corto. Unico sin escanear la hoja."""
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    return "%s-%s" % (ts, uuid.uuid4().hex[:6].upper())


def _extract_row(updated_range):
    """'Hoja 1!A16:P16' -> 16 (fila real devuelta por Google). None si no hay."""
    m = re.search(r"!A(\d+):", updated_range or "")
    return int(m.group(1)) if m else None


def append_row(srv, sid, op_key, row, retries=4, base=1.5):
    """Append idempotente a la última fila lógica de la tabla.

    Devuelve:
      (updated_range, row_id)  -> escritura confirmada (updatedRange existe)
      (None, id_existente)     -> op_key ya reclamado (reintento idempotente)
      ("", row_id)             -> sin updatedRange: no confirmada, sin claim
    """
    from .storage import get_ledger
    ledger = get_ledger()
    existing = ledger.claim(op_key)
    if existing:
        return None, existing   # ya registrado (reintento)

    row_id = row[0]
    last_err = None
    for attempt in range(retries):
        try:
            res = srv.spreadsheets().values().append(
                spreadsheetId=sid,
                range=_APPEND_RANGE,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            updated = (res.get("updates") or {}).get("updatedRange") or ""
            fila_real = _extract_row(updated)
            if not fila_real:
                # sin updatedRange no hay confirmación de escritura
                return "", row_id
            # guarda la fila REAL devuelta por Google (no una fila calculada)
            ledger.register(op_key, row_id, fila=fila_real)
            return updated, row_id
        except Exception as e:
            last_err = e
            import time
            time.sleep(base * (attempt + 1))
    raise last_err


def marcar_anulado(srv, sid, row_index):
    """Marca estado='anulado' en la fila. No vacia celdas."""
    rng = "Hoja 1!O%d:O%d" % (row_index, row_index)  # col 15 = estado
    srv.spreadsheets().values().update(
        spreadsheetId=sid, range=rng,
        valueInputOption="USER_ENTERED", body={"values": [["anulado"]]},
    ).execute()


def leer_filas(srv, sid, rango="Hoja 1!A2:T50000"):
    try:
        r = srv.spreadsheets().values().get(spreadsheetId=sid, range=rango).execute()
        return r.get("values", []) or []
    except Exception:
        return []


def fila_activa(row):
    """True si la fila no esta anulada y tiene datos utiles."""
    if len(row) < 15:
        return True
    estado = (row[14] or "").strip().lower()
    return estado != "anulado"


CABECERA = ["id", "fecha", "hora", "grupo", "usuario", "tipo", "monto", "moneda",
            "categoria", "subcategoria", "descripcion_orig", "descripcion_norm",
            "metodo", "evidencia", "estado", "prioridad"]


def build_row(data):
    """Construye la fila (16 col), igual que la hoja actual. data = dict."""
    d = data
    monto = d.get("monto_display") or ""
    return [d.get("id", ""), d.get("fecha", ""), d.get("hora", ""),
            d.get("grupo", ""), d.get("usuario", ""), d.get("tipo", "Gasto"),
            monto, d.get("moneda", "COP"), d.get("categoria", ""),
            d.get("subcategoria", ""), d.get("desc", ""), d.get("desc", ""),
            d.get("metodo", ""), d.get("evidencia", ""), "aprobado", "alta"]