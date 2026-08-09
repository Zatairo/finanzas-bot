"""entities.py — Extraccion determinista de entidades de un mensaje."""
import datetime
import re

from .normalize import normalize, parse_monto, to_display
from .rules import get_rules
from . import storage

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}
# Abreviaturas comunes de recibos colombianos (07 AGO 2026, ENE, DIC...)
MESES_ABREV = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
               "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

_FECHA_RE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_FECHA_RE_DMY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
_FECHA_RE_DMY_GUION = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")
_FECHA_RE_TXT = re.compile(r"(\d{1,2})\s+(?:de\s+)?([a-z\u00e1\u00e9\u00ed\u00f3\u00fa]{3,})\s+(?:de\s+)?(\d{4})")
_HORA_RE_AMPM = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])\s*\.?\s*m\.?", re.IGNORECASE)
_HORA_RE_24 = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _fecha_valida(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d)).isoformat()
    except Exception:
        return None


def candidatos_fecha(text):
    """Todas las fechas calendario completas y válidas (orden de aparición).

    Solo acepta fechas completas (YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY o
    textuales como '9 agosto 2026' / '07 AGO 2026'). Un año, llave, NIT,
    referencia o UUID aislado NUNCA se convierte en fecha.
    """
    out = []
    t = normalize(text or "")
    for m in _FECHA_RE_ISO.finditer(t):
        r = _fecha_valida(m.group(1), m.group(2), m.group(3))
        if r:
            out.append(r)
    for pat in (_FECHA_RE_DMY, _FECHA_RE_DMY_GUION):
        for m in pat.finditer(t):
            y = m.group(3)
            y = ("20" + y) if len(str(y)) == 2 else y
            r = _fecha_valida(y, m.group(2), m.group(1))
            if r:
                out.append(r)
    for m in _FECHA_RE_TXT.finditer(t):
        mes = MESES.get(m.group(2)) or MESES_ABREV.get(m.group(2))
        if not mes:
            continue
        r = _fecha_valida(m.group(3), mes, m.group(1))
        if r:
            out.append(r)
    seen = set()
    res = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def extraer_fecha(text):
    """Primera fecha calendario completa y válida del texto, o None."""
    c = candidatos_fecha(text)
    return c[0] if c else None


def candidatos_hora(text):
    """Todas las horas HH:MM válidas (orden de aparición), formato 24h."""
    out = []
    t = normalize(text or "")
    for m in _HORA_RE_AMPM.finditer(t):
        h, mi = int(m.group(1)), int(m.group(2))
        merid = m.group(4).lower()
        if not (1 <= h <= 12) or not (0 <= mi <= 59):
            continue
        if merid == "p" and h < 12:
            h += 12
        elif merid == "a" and h == 12:
            h = 0
        out.append("%02d:%02d" % (h, mi))
    for m in _HORA_RE_24.finditer(t):
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            out.append("%02d:%02d" % (h, mi))
    seen = set()
    res = []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def extraer_hora(text):
    """Primera hora HH:MM válida del texto, o None. Un número aislado jamás."""
    c = candidatos_hora(text)
    return c[0] if c else None


# Líneas de recibo PROHIBIDAS como descripción: campos técnicos.
_PROHIBIDO_OCR = [
    "monto", "total", "subtotal", "impuesto", "valor", "pagado", "enviado",
    "recibido", "llave", "nit", "cuenta", "numero", "comprobante",
    "referencia", "entidad", "estado", "fecha", "hora", "uuid", "nombre",
    "mas informacion", "mas información", "informacion", "completada",
    "completado", "nucs", "via", "bre", "transferencia", "debitar", "cargo",
    "nombre del", "para", "de:",
]


def _formatear_desc(t):
    """Minúsculas descriptivas: oración con inicial mayúscula y marca final."""
    palabras = t.split()
    if not palabras:
        return None
    if len(palabras) == 1:
        return palabras[0][:1].upper() + palabras[0][1:]
    # la última palabra (marca/producto) se capitaliza, el resto en minúscula
    t = " ".join(palabras[:-1]) + " " + palabras[-1][:1].upper() + palabras[-1][1:]
    return t[:1].upper() + t[1:]


def _caption_limpio(caption):
    """Caption de WhatsApp listo para descripción. None si no aporta.

    Remueve SOLO conectores financieros no descriptivos (verbos de pago,
    preposiciones, montos, medios de pago y la etiqueta 'categoría X').
    Nunca usa el OCR.
    """
    t = normalize(caption or "")
    if not t:
        return None
    t = re.sub(r"\bcategor[iy]as?\b.*$", "", t)          # etiqueta 'categoría X'
    t = re.sub(r"\$?\s?\d[\d.,]*\s*(mil|k|m)?", " ", t)  # montos
    t = re.sub(r"\b(pagu?e|pago|pagar|gast[oe]|compr[oea]|recargu?e|abon?e|"
               r"recib[ií]|env[ií]e|debitar|pague)\w*\b", " ", t)
    t = re.sub(r"\b(por|en|de|un|una|el|la|los|las|con|a|para|y|al|que|"
               r"del|este|esta|ese|esa|es|unos|unas)\b", " ", t)
    t = re.sub(r"\b(mil|medio|media|mitad|compartid\w*|partimos|cada uno|a medias)\b", " ", t)
    t = re.sub(r"\b(peso?s?)\b", "", t)
    for w in ["nequi", "daviplata", "davivienda", "bancolombia", "efectivo",
              "tarjeta", "transferencia", "contado", "credito", "debito", "pse",
              "recibo", "foto", "adjunto", "evidencia"]:
        t = re.sub(r"\b%s\b" % re.escape(w), " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,;:!?")
    if not t or len(t) < 3:
        return None
    return _formatear_desc(t)


def _linea_ocr_segura(ocr_text):
    """Una única línea OCR corta y segura para describir, o None.

    Excluye líneas con dígitos (montos/fechas/llaves/NIT/UUID) y cualquier
    línea que contenga un campo técnico del recibo.
    """
    for ln in (ocr_text or "").splitlines():
        l = normalize(ln).strip().strip(" .,;:!?-")
        if not l or len(l) > 50 or len(l) < 3:
            continue
        if re.search(r"\d", l):
            continue
        if any(p in l for p in _PROHIBIDO_OCR):
            continue
        return l[:1].upper() + l[1:]
    return None


def resolver_descripcion(caption, ocr_text, comercio=None, producto=None):
    """Resuelve la descripción final de un gasto (nunca usa OCR completo).

    Prioridad obligatoria:
      a. caption útil de WhatsApp (limpio, sin conectores financieros);
      b. comercio/producto conocido o aprendido;
      c. una única línea OCR corta y segura;
      d. None -> el motor debe pedir la descripción.

    Devuelve (descripcion, origen) con origen ∈
    {caption, comercio, producto, ocr, pendiente}.
    """
    cap = _caption_limpio(caption)
    if cap:
        return cap, "caption"
    if comercio and str(comercio).strip():
        v = str(comercio).strip()
        return (v[:1].upper() + v[1:])[:120], "comercio"
    if producto and str(producto).strip():
        v = str(producto).strip()
        return (v[:1].upper() + v[1:])[:120], "producto"
    linea = _linea_ocr_segura(ocr_text)
    if linea:
        return linea, "ocr"
    return None, "pendiente"

_SHARED = [r"\bmitad\b", r"50\s?/\s?50", r"50\s?%\s?50", r"\ba medias\b",
           r"\bcompartid\w*\b", r"\bpartimos\b", r"\ba la mitad\b",
           r"\bcada uno\b", r"\bcada quien\b", r"50%", r"50 %", r"50 50"]


def _es_compartido(text):
    t = normalize(text or "")
    for pat in _SHARED:
        if re.search(pat, t):
            return True
    return False


def extraer_entidades(texto):
    """Devuelve (Entidades, fallback_metodo). No inventa datos: monto puede ser None.

    fecha/hora solo se rellenan si el texto/OCR las contiene como fecha y hora
    completas y válidas; si no, quedan en None (el Motor resuelve la prioridad).
    """
    from .models import Entidades
    t = texto or ""
    monto = parse_monto(t)
    reglas = get_rules()
    cat, sub, comercio = reglas.match_categoria(t) or (None, None, None)
    metodo = reglas.match_metodo(t)
    productos = reglas.match_productos(t)
    tipo = reglas.match_tipo(t)
    e = Entidades(
        monto=monto,
        monto_display=to_display(monto) if monto is not None else None,
        fecha=extraer_fecha(t),
        hora=extraer_hora(t),
        metodo=metodo,
        categoria=cat,
        subcategoria=sub,
        comercio=comercio,
        productos=productos,
        tipo=tipo,
        compartido=_es_compartido(t),
    )
    return e


def limpiar_descripcion(texto):
    t = normalize(texto or "")
    t = re.sub(r"\$?\s?\d[\d.,]*\s*(mil|k|m)?", " ", t)
    t = re.sub(r"\b(pagu?e|pago|pagar|gast[oe]|compr[oea]|recargu?e|abon?e|recib[ií])\w*\b",
               " ", t)
    t = re.sub(r"\b(por|en|de|un|una|el|la|los|las|con|a|para|y|al|que|las|los)\b", " ", t)
    t = re.sub(r"\b(mil|medio|media|mitad|compartid\w*|partimos|cada uno|a medias)\b", " ", t)
    t = re.sub(r"\b(peso?s?)\b", "", t)  # pesos = moneda
    for w in ["nequi", "daviplata", "davivienda", "bancolombia", "efectivo",
              "tarjeta", "transferencia", "contado", "credito", "debito", "pse"]:
        t = re.sub(r"\b%s\b" % re.escape(w), " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" .,;:!?")
    return (t[:1].upper() + t[1:]) if t else ""