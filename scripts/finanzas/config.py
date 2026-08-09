"""config.py — Configuracion central: grupos, hojas, usuarios, permisos y rutas.

Toda la config de identidades y permisos vive aqui para que sea facil de auditar.
Los datos financieros quedan separados por grupo; los aprendizajes globales se
comparten entre los 3 chats (requisito del producto).
"""
import os

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

# --- Identidad de remitentes (query params -> usuario) ---------------------
_PHONE_KEY = {
    "3002084572": "U1",
    "573002084572": "U1",
    "3147359270": "U2",
    "573147359270": "U2",
}

# Soporte para pruebas / remitentes sin mapeo: se resuelve con el numero.
TEST_USERS = {
    "1111111111": "U1",
    "2222222222": "U2",
}

USER_PHONES = dict(_PHONE_KEY)
USER_PHONES.update(TEST_USERS)


def phone_key(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def user_from_phone(phone, default=None):
    if not phone:
        return default
    k = phone_key(phone)
    # probar el numero completo de 10 digitos y luego el prefijo 57
    u = USER_PHONES.get(k) or USER_PHONES.get(phone)
    if u:
        return u
    # ultimos 10 de cualquier formato
    for src, user in USER_PHONES.items():
        if phone_key(src) == k:
            return user
    return default


def can_write(grupo, usuario):
    return usuario in PERMISOS.get(grupo, set())


def is_admin(usuario):
    return usuario in ADMIN_USERS


def authenticate(sender, grupo):
    """Devuelve (permiso_ok, usuario)."""
    sid, _g, _d = GROUPS[grupo]
    usuario = user_from_phone(sender)
    if not usuario:
        # sin identidad confiable -> denegar escritura
        return False, "?"
    return can_write(grupo, usuario), usuario