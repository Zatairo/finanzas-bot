"""normalize.py — Normalizacion determinista de texto, dinero y telefonos.

Colombian money: 5000, 5.000, $5.000, 5 mil, 5k, 1.2m, 1 millon, 500000.
"""
import re
import unicodedata


def strip_accents(s):
    try:
        return "".join(
            ch for ch in unicodedata.normalize("NFD", (s or ""))
            if unicodedata.category(ch) != "Mn"
        )
    except Exception:
        return s or ""


def normalize(text):
    """minusculas + sin acentos + espacios colapsados + puntuacion simple."""
    t = strip_accents(text or "").lower()
    t = re.sub(r"[\u00a0\u202f]", " ", t)          # espacios no separadores
    t = re.sub(r"\s+", " ", t)
    return t.strip()


_WORD_MAG = {
    "mil": 1_000, "k": 1_000, "k": 1_000,
    "millon": 1_000_000, "millones": 1_000_000, "m": 1_000_000,
}


def _clean_num(s):
    """Convierte '1.234,56' / '1,234.56' / '5.000' a float (miles como . o ,)."""
    s = s.replace("$", "").strip()
    if not s:
        return None
    comma = s.count(",")
    dot = s.count(".")
    if comma and dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "")
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif comma == 1 and (len(s.split(",")[1]) in (1, 2)):
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "").replace(".", "")
    try:
        return float(s)
    except Exception:
        return None


def parse_monto(text):
    """Extrae el monto de un texto tipo colombiano. Devuelve int o None.

    Reglas, en orden:
      - $1.234.567      -> 1234567
      - 5.000 / 5000    -> 5000
      - 5 mil / 5k      -> 5000
      - 1.2m / 1 millon -> 1200000
    """
    t = strip_accents(text or "").replace(",", ",")
    # primero: numeros con palabra multiplicadora inmediatamente despues
    pattern = r"(?:^|\s|\$)(\d{1,3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?)\s*(millones|millon|mil|m|k)(?=\s|$|[.,;])"
    found = []
    for m in re.finditer(pattern, t, re.IGNORECASE):
        val = _clean_num(m.group(1))
        mult = _WORD_MAG.get(m.group(2).lower())
        if val is not None:
            found.append(int(val * mult))
    if not found:
        # numeros planos (con o sin separadores)
        for m in re.finditer(r"(?:^|\s|\$|,)(\d{1,3}(?:[.,][\d{3}])+|\d+)(?=\s|$|[.,;])", t):
            val = _clean_num(m.group(1))
            if val is not None:
                found.append(int(val))
    if not found:
        return None
    return max(found)


def to_display(num):
    try:
        f = float(num)
    except Exception:
        return None
    if f == int(f):
        return "$%s" % format(int(f), ",d")
    return "$%s" % format(f, ",.2f")


def phone_key(phone):
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d