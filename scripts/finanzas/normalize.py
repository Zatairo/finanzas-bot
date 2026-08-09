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

# Etiquetas monetarias: el número asociado es candidato a monto.
_LABEL_RE = re.compile(
    r"\b(total|subtotal|monto|valor|enviado|pagado|recibido|debito|credito|"
    r"pague|pago|pagar|gaste|gastamos|gasto|compre|compramos|compro|"
    r"recargue|abone|recibi|salario|ingreso)\b", re.IGNORECASE)

# Contexto NO monetario: números de estas líneas nunca son monto.
_EXCL_RE = re.compile(
    r"\b(llave|telefono|celular|cuenta|nit|referencia|comprobante|uuid|"
    r"operacion|documento)\b", re.IGNORECASE)

# Símbolo/divisa explícita: convierte un candidato en evidencia fuerte.
_FUERTE_RE = re.compile(r"\$|\bcop\b", re.IGNORECASE)

_MULT_NUM_RE = re.compile(
    r"(?:^|\s|\$)(\d{1,3}(?:[.,]\d{3})*|\d+(?:[.,]\d+)?)\s*"
    r"(millones|millon|mil|m|k)(?=\s|$|[.,;])", re.IGNORECASE)

_PLAIN_NUM_RE = re.compile(
    r"(?:^|\s|\$|,)(\d{1,3}(?:[.,]\d{3})+|\d+)(?=\s|$|[.,;])")

# Un número de 10+ dígitos sin $/COP nunca es monto automático (p.ej. una llave
# de Nequi como 3127702186). Montos con $ sí pueden ser altos y válidos.
_UMAYOR_SIN_MONEDA = 1_000_000_000


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


def _candidatos_linea(linea):
    """Numeros de una linea con su valor y span (inicio/fin) en la linea."""
    res = []
    for m in _MULT_NUM_RE.finditer(linea):
        v = _clean_num(m.group(1))
        if v is None:
            continue
        mult = _WORD_MAG.get(m.group(2).lower())
        res.append((int(v * mult), True, m.start(), m.end()))
    if res:
        return res
    for m in _PLAIN_NUM_RE.finditer(linea):
        v = _clean_num(m.group(1))
        if v is not None:
            res.append((int(v), False, m.start(), m.end()))
    return res


def analizar_monto(texto):
    """Analiza montos con contexto por número. Devuelve dict:
        monto: int|None
        confianza: 'alta'|'media'|'baja'|'ambiguo'|'ninguno'
        candidatos: [{valor, linea}] con valores positivos y sin contexto
                    excluido (llave/teléfono/cuenta/NIT/referencia/comprobante/
                    UUID/número de operación/documento).
        motivo: string corto.

    Prioridad por evidencia monetaria (ventana de contexto de cada número):
      alta  -> símbolo $ o 'cop' cerca del número;
      media -> etiqueta monetaria (total/monto/pagué/recibí/...) o multiplicador
               (mil/k/m) que declara el monto;
      baja  -> número plano <= 9 dígitos sin contexto.

    Si hay más de un valor distinto en el nivel de mayor evidencia -> 'ambiguo'
    (monto None); nunca se elige arbitrariamente (ya no se usa max(found)).
    Un número de 10+ dígitos sin $/COP nunca es monto automático.
    """
    t = strip_accents(texto or "")
    cands = []  # {valor, linea, fuerza}
    for ln in t.split("\n"):
        line = " " + (ln or "").strip() + " "
        for v, es_mult, s, e in _candidatos_linea(line):
            if v <= 0:
                continue
            ctx = line[max(0, s - 30):e + 15]
            fuerte = bool(_FUERTE_RE.search(ctx))
            excluido = bool(_EXCL_RE.search(ctx))
            etiqueta = bool(_LABEL_RE.search(ctx))
            if not fuerte:
                if v >= _UMAYOR_SIN_MONEDA:
                    continue  # 10+ dígitos sin $/COP nunca es monto
                if excluido:
                    continue  # llave/NIT/referencia/comprobante/documento...
            fuerza = 4 if fuerte else (3 if (etiqueta or es_mult) else 2)
            cands.append({"valor": v, "linea": ln.strip()[:80], "fuerza": fuerza})
    if not cands:
        return {"monto": None, "confianza": "ninguno",
                "candidatos": [], "motivo": "sin_candidatos"}
    for fuerza in (4, 3, 2):
        sel = [c for c in cands if c["fuerza"] == fuerza]
        if not sel:
            continue
        unicos = sorted({c["valor"] for c in sel})
        publicos = [{"valor": c["valor"], "linea": c["linea"]} for c in cands]
        if len(unicos) == 1:
            return {"monto": unicos[0],
                    "confianza": {4: "alta", 3: "media", 2: "baja"}[fuerza],
                    "candidatos": publicos,
                    "motivo": "unico_" + {4: "fuerte", 3: "etiqueta", 2: "plano"}[fuerza]}
        return {"monto": None, "confianza": "ambiguo",
                "candidatos": publicos, "motivo": "multiples_candidatos"}
    return {"monto": None, "confianza": "ninguno",
            "candidatos": [{"valor": c["valor"], "linea": c["linea"]} for c in cands],
            "motivo": "sin_evidencia"}


def parse_monto(text):
    """Extrae el monto de un texto tipo colombiano. Devuelve int o None.

    Reglas, en orden:
      - $1.234.567      -> 1234567
      - 5.000 / 5000    -> 5000
      - 5 mil / 5k      -> 5000
      - 1.2m / 1 millon -> 1200000
      - 'Llave 3127702186' / NIT / referencia / comprobante -> None
      - varios montos plausibles distintos -> None (ambiguo)
    """
    return analizar_monto(text)["monto"]


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