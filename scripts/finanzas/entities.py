"""entities.py — Extraccion determinista de entidades de un mensaje."""
import datetime
import re

from .normalize import normalize, parse_monto, to_display
from .rules import get_rules
from . import storage

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}

_SHARED = [r"\bmitad\b", r"50\s?/\s?50", r"50\s?%\s?50", r"\ba medias\b",
           r"\bcompartid\w*\b", r"\bpartimos\b", r"\ba la mitad\b",
           r"\bcada uno\b", r"\bcada quien\b", r"50%", r"50 %", r"50 50"]


def extraer_fecha(text):
    t = text or ""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = ("20" + y) if len(y) == 2 else y
        return "%s-%s-%s" % (y, str(mo).zfill(2), str(d).zfill(2))
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return "%s-%s-%s" % (m.group(1), m.group(2).zfill(2), m.group(3).zfill(2))
    m = re.search(r"(\d{1,2})\s+de\s+([a-z\u00e1\u00e9\u00ed\u00f3\u00fa]+)\s+de\s+(\d{4})", normalize(t))
    if m and m.group(2) in MESES:
        return "%s-%02d-%02d" % (m.group(3), MESES[m.group(2)], int(m.group(1)))
    return None


def _es_compartido(text):
    t = normalize(text or "")
    for pat in _SHARED:
        if re.search(pat, t):
            return True
    return False


def extraer_entidades(texto):
    """Devuelve (Entidades, fallback_metodo). No inventa datos: monto puede ser None."""
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
        hora=datetime.datetime.now().strftime("%H:%M"),
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