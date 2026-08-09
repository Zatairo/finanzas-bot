"""config.py — Configuracion central: grupos, hojas, usuarios, permisos y rutas.

Toda la config de identidades y permisos vive aqui para que sea facil de auditar.
Los datos financieros quedan separados por grupo; los aprendizajes globales se
comparten entre los 3 chats (requisito del producto).
"""
import os
import re

# --- Base de datos del proyecto -------------------------------------------
# Permite override con env FINANZAS_DATA_DIR; por defecto, datos junto al paquete.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../scripts
DATA_DIR = os.environ.get("FINANZAS_DATA_DIR") or os.path.join(_BASE, "data")


def _data(subpath):
    p = os.path.join(DATA_DIR, subpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


AR_APRENDIZAJES = _data("aprendizajes_globales.json")
AR_PRESUPUESTOS = _data("presupuestos.json")
AR_INVENTARIO = _data("inventario.json")
AR_HISTORIAL = _data("historial.jsonl")
# Reglas: archivo estatico del paquete (no depende de DATA_DIR en pruebas).
AR_REGLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_global.yaml")
AR_ESTADOS_DIR = _data("estados")
AR_LEDGER = _data("ledger.json")
AR_LEGACY_APRENDIZAJES = os.path.join(_BASE, "aprendizajes.json")

# Estado conversacional: escribe en ruta persistente del proyecto (nunca /tmp).
ESTADOS_TTL_SEG = int(os.environ.get("FINANZAS_TTL", "600"))  # 10 min

# --- Hojas por grupo ------------------------------------------------------
PERSONAL_SHEET = "14OPB7X4V4QL6RE20zqMoWztNoGEFGHDLUwk3u2zEQho"
HOGAR_SHEET = "1WJMPeSNTlPzKF5TU2EljiwXU4d_O54CQpA1aJvatduM"
ANDREA_SHEET = "1GQt6_AKWOp_GNKg2PAo0P-XObVPcekV2HyyZBuSa_iY"

# grupo -> (spreadsheet, grupo_id, usuario_default)
GROUPS = {
    "personal": (PERSONAL_SHEET, "G1", "U1"),
    "hogar": (HOGAR_SHEET, "G2", "U2"),
    "andrea": (ANDREA_SHEET, "G1", "U2"),
}

# Quien puede REGISTRAR/CONSULTAR en cada grupo (los datos financieros son por grupo)
PERMISOS = {
    "personal": {"U1"},
    "hogar": {"U1", "U2"},
    "andrea": {"U2"},
}

# Admin (configurable): solo admin ejecuta revisar / palabra = categoria /
# modificar o eliminar aprendizajes globales.
ADMIN_USERS = {"U1"}

# Etiquetas de usuario usadas en la columna "usuario" de las hojas.
USER_LABELS = {
    "U1": "U1",
    "U2": "U2",
    "U3": "U3",   # compra compartida (mitad/mitad) - solo aplica en Hogar
}
# Para gastos compartidos se usa U3 (mitad), no un pseudo-reparto en columnas.

# --- Identidad de remitentes (tablas SEPARADAS: teléfono y LID) -----------
# Un teléfono y un LID son identificadores distintos; un LID NO se resuelve
# con sus últimos 10 dígitos (podría colisionar con un teléfono de otro).
PHONE_TO_USER = {
    "3002084572": "U1",   # Esnaider Idrobo
    "3147359270": "U2",   # Andrea
}

# LID de WhatsApp -> usuario. La coincidencia SIEMPRE es exacta contra esta
# tabla y jamás se reduce a los últimos 10 dígitos.
LID_TO_USER = {
    "53201961234666": "U1",   # LID del teléfono 573002084572 (Esnaider)
    "5063900668131": "U2",    # LID del teléfono 573147359270 (Andrea)
}


def _last10_digits(raw):
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def user_from_sender(raw_sender):
    """Resuelve un remitente raw a U1/U2, o None si es desconocido.

    Formatos aceptados:
      - número pelado:        3002084572
      - con prefijo:          +573002084572 / 573002084572
      - wa.me:                wa.me/573002084572
      - JID telefónico:       573002084572@s.whatsapp.net
      - JID con dispositivo:  573002084572:10@s.whatsapp.net
      - LID:                  53201961234666@lid (coincidencia EXACTA)

    Un LID desconocido retorna None; nunca se trata como teléfono ni se
    resuelve con sus últimos 10 dígitos.
    """
    s = (raw_sender or "").strip()
    if not s:
        return None
    s = re.sub(r"^wa\.me/", "", s)
    s = s.replace("+", "")
    if "@lid" in s:
        lid = s.split("@")[0].split(":")[0]
        return LID_TO_USER.get(lid)
    local = s.split("@")[0] if "@" in s else s
    local = local.split(":")[0]
    return PHONE_TO_USER.get(_last10_digits(local))


def can_write(grupo, usuario):
    return usuario in PERMISOS.get(grupo, set())


def is_admin(usuario):
    return usuario in ADMIN_USERS


def authenticate(sender, grupo):
    """Devuelve (permiso_ok, usuario)."""
    usuario = user_from_sender(sender)
    if not usuario:
        # sin identidad confiable -> denegar escritura/consulta
        return False, "?"
    return can_write(grupo, usuario), usuario