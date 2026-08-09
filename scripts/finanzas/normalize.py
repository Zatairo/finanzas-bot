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
    """Convierte '1.234,56' / '1,234.56' / '5.000' / '1.2' a float.

    Regla determinista colombiana:
      - un unico separador con 1-2 digitos a la derecha  -> decimal (1.2, 5,5)
      - separadores que agrupan 3 digitos -> miles (5.000, 1.234.567)
    """
    s = s.replace("$", "").strip()
    if not s:
        return None
    if not re.search(r"[.,]", s):
        try:
            return float(s)
        except Exception:
            return None
    sep = None
    for ch in (".", ","):
        if s.count(ch) == 1:
            prev, post = s.split(ch)
            if post and not re.search(r"[.,]", post) and len(post) <= 2:
                sep = ch
                break
    if sep:
        # decimal (separador unico con 1-2 dec)
        s2 = s.replace(sep, ".")
        try:
            return float(s2)
        except Exception:
            return None
    # miles: quitar todos los separadores
    s = re.sub(r"[.,]", "", s)
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
        for m in re.finditer(r"(?:^|\s|\$|,)(\d{1,3}(?:[.,]\d{3})+|\d+)(?=\s|$|[.,;])", t):
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