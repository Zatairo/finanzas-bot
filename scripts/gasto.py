#!/usr/bin/env python3
"""gasto.py — Registro deterministico de gastos en Google Sheets (sin LLM).

Uso:
  gasto.py --grupo personal|hogar|andrea --texto "pague 5000 mercado por nequi"
  gasto.py --grupo hogar --imagen /ruta/recibo.jpg --evidencia img_123.jpg
  gasto.py --grupo hogar --texto "..." --sender 573002084572
  gasto.py --grupo personal --texto "..." --dry-run
"""
import argparse, json, os, re, subprocess, sys, datetime

HERMES = os.environ.get("HERMES_HOME", "/home/soporte/.hermes")
TOKEN = os.path.join(HERMES, "google_token.json")

PERSONAL_SHEET = "14OPB7X4V4QL6RE20zqMoWztNoGEFGHDLUwk3u2zEQho"
HOGAR_SHEET = "1WJMPeSNTlPzKF5TU2EljiwXU4d_O54CQpA1aJvatduM"
ANDREA_SHEET = "1GQt6_AKWOp_GNKg2PAo0P-XObVPcekV2HyyZBuSa_iY"
REF_SHEET = HOGAR_SHEET

# Grupo (clave whatsapp) -> (spreadsheet, grupo_id por defecto, usuario_id por defecto)
GROUP_TARGETS = {
    "personal": (PERSONAL_SHEET, "G1", "U1"),
    "hogar": (HOGAR_SHEET, "G2", "U2"),
    "andrea": (ANDREA_SHEET, "G1", "U2"),
}

PHONE_USER = {
    "3002084572": "U1",
    "573002084572": "U1",
    "3147359270": "U2",
    "573147359270": "U2",
}

# Categoria/subcategoria por palabras clave (orden = prioridad alta primero)
CATEGORY_RULES = [
    ("Gasolina / combustible", ["gasolina", "terpel", "primax", "movil (?!and)", "puma energy", "tanqueo", "combustible"], "Transporte", "Gasolina / combustible"),
    ("Mercado / plaza", ["mercado", "mercado libre", "exito", "carulla", "d1", "justo y bueno", "olimpica", "supermercado", "tienda", "fruver", "frutas", "verduras", "jumbo", "makro", "plaza"], "Alimentacion", "Mercado / plaza"),
    ("Restaurante / comida fuera", ["restaurante", "almuerzo", "cena", "desayuno", "hamburguesa", "pizza", "pollo", "corral", "crepes", "frisby", "kokoriko", "sanduche", "sushi", "asado", "parrilla", "comida", "comida afuera", "alimentacion", "comer", "almorzar", "cenar"], "Alimentacion", "Restaurante / comida fuera"),
    ("Domicilios", ["domicilio", "rappi", "uber eats", "didi food", "ifood", "delivery"], "Alimentacion", "Domicilios"),
    ("Bebidas / snacks", ["gaseosa", "jugo", "cafe", "tinto", "snack", "papas", "galletas", "helado", "dulce", "bebida", "ponque"], "Alimentacion", "Bebidas / snacks"),
    ("Arriendo", ["arriendo", "renta"], "Vivienda", "Arriendo"),
    ("Servicios publicos (agua/luz/gas)", ["servicios publicos", "agua", "luz", "gas", "energia", "enel", "factura de luz"], "Vivienda", "Servicios publicos (agua/luz/gas)"),
    ("Internet / telefono", ["internet", "claro", "movistar", "tigo", "wifi", "fibra", "telefono", "recarga"], "Vivienda", "Internet / telefono"),
    ("Aseo y hogar", ["aseo", "jabon", "shampoo", "papel higienico", "detergente", "fabuloso", "limpido"], "Vivienda", "Aseo y hogar"),
    ("Transporte publico / bus", ["bus", "transmilenio", "taxi", "uber", "pasaje", "metro", "sitp", "transporte"], "Transporte", "Transporte publico / bus"),
    ("Parqueadero / peaje", ["parqueadero", "parqueo", "peaje", "parking"], "Transporte", "Parqueadero / peaje"),
    ("Mantenimiento vehiculo", ["taller", "mecanico", "llanta", "filtro", "frenos", "bateria", "cambio de aceite", "aceite de motor", "aceite motor", "llanta"], "Transporte", "Mantenimiento vehiculo"),
    ("Componentes / electronica", ["electronica", "tecnologia", "celular", "memoria", "cargador", "audifonos"], "Tecnologia", "Componentes / electronica"),
    ("Suscripciones digitales", ["spotify", "netflix", "disney", "hbo", "youtube", "prime", "suscripcion", "icloud", "google one"], "Tecnologia", "Suscripciones digitales"),
    ("Equipos / dispositivos", ["computador", "laptop", "tablet", "impresora"], "Tecnologia", "Equipos / dispositivos"),
    ("Medicamentos", ["drogueria", "farmacia", "medicamento", "acetaminofen", "ibuprofeno", "pastillas", "formula"], "Salud", "Medicamentos"),
    ("Consulta medica / EPS", ["medico", "doctor", "eps", "consulta", "odontologo", "laboratorio", "salud", "salud", "arl", "pension", "pensión", "planilla", "eps", "seguridad social"], "Salud", "Consulta medica / EPS"),
    ("Gimnasio / bienestar", ["gimnasio", "gym", "bodytech", "entrenamiento"], "Salud", "Gimnasio / bienestar"),
    ("Cursos / certificaciones", ["curso", "certificacion", "plataforma", "taller", "diplomado"], "Educacion", "Cursos / certificaciones"),
    ("Libros / materiales", ["libro", "papeleria", "cuaderno", "libreria"], "Educacion", "Libros / materiales"),
    ("Tesis / universidad", ["universidad", "tesis", "matricula", "semestre"], "Educacion", "Tesis / universidad"),
    ("Entretenimiento / streaming", ["cine", "streaming", "entrada", "concierto", "teatro", "parque de diversiones"], "Ocio", "Entretenimiento / streaming"),
    ("Salidas / reuniones", ["bar", "fiesta", "salida", "reunion", "discoteca", "cerveza"], "Ocio", "Salidas / reuniones"),
    ("Viajes", ["vuelo", "tiquete", "hotel", "avion", "aerolinea", "viaje", "airbnb"], "Ocio", "Viajes"),
    ("Ropa y accesorios", ["ropa", "zara", "h&m", "falabella", "tenis", "zapatos", "camisa", "pantalon", "vestido", "bolso"], "Ropa", "Ropa y accesorios"),
    ("Alimento / veterinario", ["veterinario", "veterinaria", "mascota", "perro", "gato", "concentrado", "mascotas"], "Mascotas", "Alimento / veterinario"),
    ("Salario / nomina", ["salario", "nomina", "sueldo", "pago de nomina"], "Ingreso", "Salario / nomina"),
    ("Freelance / proyecto", ["freelance", "proyecto", "contrato", "honorarios"], "Ingreso", "Freelance / proyecto"),
    ("Otros ingresos", ["ingreso", "abono", "recibido", "ganancia", "venta", "ahorro", "ahorros", "ahorré", "ahorre", "consignacion", "consignación", "upla", "uala"], "Ingreso", "Otros ingresos"),
]

# Metodo de pago: variantes -> valor validado
PAY_METHOD_RULES = [
    ("transferencia Nequi", ["nequi", "transferencia nu", "transferencia nequi"]),
    ("tarjeta debito", ["tarjeta debito", "debito", "débito"]),
    ("tarjeta credito", ["tarjeta credito", "credito", "crédito", "rappi card", "rappicard"]),
    ("PSE", ["pse"]),
    ("transferencia", ["transferencia", "banco", "daviplata", "bancolombia", "efecty", "pago movil"]),
    ("efectivo", ["efectivo", "cash", "contado"]),
]
PAY_DEFAULT = "transferencia"

IS_GASTO_KEYWORDS = ["gasto", "gaste", "gasté", "pague", "pagué", "pago", "pagar", "compre",
                     "compré", "comprar", "factura", "recibo", "abono", "nequi", "transferencia",
                     "recarga", "mercado", "recargue", "recargué", "pague en", "gaste en", "llevé", "lleve"]
INCOME_HINTS = ["salario", "nomina", "abono recibido", "ingreso", "sueldo", "pago recibido", "ahorro", "ahorros", "ahorré", "ahorre", "consignacion", "consignación", "abono"]


def get_srv():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    if not os.path.exists(TOKEN):
        raise SystemExit("ERROR: no existe google_token.json en %s" % TOKEN)
    tok = json.load(open(TOKEN))
    creds = Credentials.from_authorized_user_info(tok)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def ocr(path):
    def _run(img, psm):
        cmd = ["tesseract", img, "stdout", "-l", "spa", "--psm", psm]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return r.stdout or ""
        except Exception:
            return ""

    def _dedupe(lines):
        out = []
        seen = set()
        for ln in lines:
            t = " ".join(ln.split())
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return "\n".join(out)

    # 1) pimero el original limpio (mejor para recibos buenos)
    out = _run(path, "6")
    has_digits = any(ch.isdigit() for ch in out)

    # 2) si no salieron numeros, prepara y reintenta (fotos sucias/rotadas)
    if not has_digits:
        pre = None
        try:
            import tempfile
            from PIL import Image, ImageOps
            im = Image.open(path)
            im = ImageOps.grayscale(im)
            im = ImageOps.autocontrast(im)
            w, h = im.size
            scale = 2.0 if min(w, h) < 1500 else 1.5
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            pre = os.path.join(tempfile.gettempdir(), "ocr_%d.png" % os.getpid())
            im.save(pre)
        except Exception:
            pre = None
        target = pre or path
        extra = _run(target, "6") + "\n" + _run(target, "3")
        if pre:
            try:
                os.remove(pre)
            except Exception:
                pass
        out = _dedupe((out + "\n" + extra).splitlines())
    else:
        out = _dedupe(out.splitlines())

    # 3) respaldo: RapidOCR (PP-OCRv4) si tesseract no extrajO numeros (fotos muy malas)
    if not any(ch.isdigit() for ch in out):
        try:
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
            res, _ = engine(path)
            rapid = "\n".join(l[1] for l in (res or [])) or ""
            if rapid:
                out = _dedupe(rapid.splitlines())
        except Exception:
            pass
    return out


def clean_amount(s):
    s = s.replace("$", "").strip()
    s = s.replace(".", "").replace(",", "")
    m = re.match(r"^\d+(\.\d+)?$", s)
    return m.group(0) if m else s


def extract_amount(text):
    KEY = r"(total|total a pagar|a pagar|monto|valor|cuota|cancelar|cancelado|canon|arriendo|mensualidad|abono|debe|neto|efectivo|cuanto)"
    LIMIT = 200000000

    def _num(tok):
        s = (tok or "").strip().replace("$", "").replace(" ", "")
        if not s:
            return None
        if re.search(r",\d{1,2}$", s):
            s = re.sub(r"\.", "", s).replace(",", ".")
        else:
            s = s.replace(",", "").replace(".", "")
        try:
            return int(round(float(s)))
        except Exception:
            return None

    def _best(items):
        flagged = [c for c in items if c[0]]
        pool = flagged or items
        return max(pool, key=lambda c: c[1])[1]

    dollar, plain = [], []
    for m in re.finditer(r"(\$\s?[\d][\d.,]*\d|\d[\d.,]*\d)", text):
        tok = m.group(1).strip()
        iv = _num(tok)
        if not iv or iv < 100 or iv > LIMIT:
            continue
        seg = text[max(0, m.start() - 80): m.start() + 80]
        hur = bool(re.search(KEY, seg))
        (dollar if tok.startswith("$") else plain).append((hur, iv))

    if dollar:
        return str(_best(dollar))
    if plain:
        return str(_best(plain))
    return None


def to_monto_display(num):
    try:
        f = float(num)
    except Exception:
        return None
    if f == int(f):
        return "$%s" % format(int(f), ",d")
    return "$%s" % format(f, ",.2f")


def parse_fecha(text):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return "%s-%s-%s" % (y, str(mo).zfill(2), str(d).zfill(2))
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return "%s-%s-%s" % (m.group(1), m.group(2).zfill(2), m.group(3).zfill(2))
    return None


def _strip_accents(s):
    try:
        import unicodedata as _ud
        return "".join(ch for ch in _ud.normalize("NFD", (s or "").lower()) if _ud.category(ch) != "Mn")
    except Exception:
        return (s or "").lower()

def classify_category(text):
    t = " " + _strip_accents(text) + " "
    best = None
    for name, kws, cat, sub in CATEGORY_RULES:
        for kw in kws:
            kw2 = _strip_accents(kw.strip())
            if not kw2:
                continue
            if re.search(r"\b" + re.escape(kw2) + r"\b", t):
                if best is None or len(kw2) > best[0]:
                    best = (len(kw2), cat, sub)
                break
    if best is None:
        return None, None
    return best[1], best[2]


def classify_pay(text):
    t = text.lower()
    for val, kws in PAY_METHOD_RULES:
        for kw in kws:
            if kw in t:
                return val
    return PAY_DEFAULT


def is_shared(text):
    t = (text or "").lower()
    if re.search(r"\b(mitad|50.?50|a medias|compartido|partimos|a la mitad)\b", t):
        return True
    if any(w in t for w in ("cada uno", "50%", "50 %")):
        return True
    return False


OPCIONES_CAT = [
    (1, "Alimentacion", "Mercado / plaza"),
    (2, "Alimentacion", "Restaurante / comida fuera"),
    (3, "Alimentacion", "Domicilios"),
    (4, "Alimentacion", "Bebidas / snacks"),
    (5, "Vivienda", "Arriendo"),
    (6, "Vivienda", "Servicios publicos (agua/luz/gas)"),
    (7, "Vivienda", "Internet / telefono"),
    (8, "Transporte", "Transporte publico / bus"),
    (9, "Salud", "Medicamentos"),
    (10, "Tecnologia", "Suscripciones digitales"),
    (11, "Ocio", "Entretenimiento / streaming"),
    (12, "Ingreso", "Otros ingresos"),
]
_STOP = set("la el los las de del en por para un una con y o a al lo le me se su sus mi tu hoy ayer que es fue eran pero ya no mas bien como cuando donde quien algo cada entre hasta sobre bajo".split())


def _aprendizaje_path():
    return os.path.join(HERMES, "scripts", "aprendizajes.json")


def _load_aprendizajes():
    import json as _json
    try:
        return _json.load(open(_aprendizaje_path(), encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_aprendizajes(d):
    import json as _json
    try:
        _json.dump(d, open(_aprendizaje_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _aprender(palabra, cat, sub):
    palabra = _strip_accents((palabra or "").strip().lower())
    if not palabra:
        return
    d = _load_aprendizajes()
    d[palabra] = {"cat": cat, "sub": sub}
    _save_aprendizajes(d)


def _aprender_producto(palabra, producto):
    palabra = _strip_accents((palabra or "").strip().lower())
    producto = _strip_accents((producto or "").strip().lower())
    if not palabra or not producto:
        return
    d = _load_aprendizajes()
    d[palabra] = {"producto": producto}
    _save_aprendizajes(d)


def _lookup_aprendizaje(text):
    t = _strip_accents(text or "").lower()
    d = _load_aprendizajes()
    if not d:
        return None
    toks = set(re.findall(r"[a-z]{3,}", t))
    mejor = None
    for kw, info in d.items():
        if kw in toks:
            if mejor is None or len(kw) > len(mejor[0]):
                mejor = (kw, info)
    return (mejor[1].get("cat"), mejor[1].get("sub")) if mejor else None


def _unknown_word(text):
    t = _strip_accents(text or "").lower()
    toks = re.findall(r"[a-z]{3,}", t)
    known = set()
    for _name, _kws, _c, _sub in CATEGORY_RULES:
        for _k in _kws:
            known.add(_strip_accents(_k))
    for _val, _alts in PAY_METHOD_RULES:
        for _k in _alts:
            known.add(_strip_accents(_k))
    cands = []
    for tok in toks:
        if tok in _STOP or tok in known or tok in MESES:
            continue
        if re.fullmatch(r"\d+", tok):
            continue
        cands.append(tok)
    cands.sort(key=len, reverse=True)
    return cands[0] if cands else None


def _opcion(num):
    for n, cat, sub in OPCIONES_CAT:
        if n == num:
            return (cat, sub)
    return None


def _opcion_from_text(text):
    t = (text or "").strip()
    m = re.match(r"^(?:opcion|opción|numero|número|respuesta|la|el|pongo|pon|es)\s*([0-9]{1,2})\b[,.;:\-]?\s*(.*)$", t, re.IGNORECASE)
    if not m:
        m = re.match(r"^([0-9]{1,2})\b[,.;:]?\s*(.*)$", t)
    if not m:
        return None
    n = int(m.group(1))
    rest = (m.group(2) or "").strip()
    if n == 0:
        if rest:
            _c2, _s2 = classify_category(rest)
            if _c2:
                return (_c2, _s2)
        return None
    op = _opcion(n)
    if op:
        return op
    if rest:
        _c2, _s2 = classify_category(rest)
        if _c2:
            return (_c2, _s2)
    return None


def _opcion_prod_pendiente(PPEND, text):
    t = _strip_accents((text or "").strip().lower())
    if not t or t in ("no", "nop", "n", "ninguno", "ninguna", "no se", "no sé", "no es producto"):
        return False, None
    t = re.sub(r"^(es|fue|era|es de|fue de|se llama|llaman|nombre|el|la|un|una|compro|compré|es un|es una)\s+", "", t)
    t = t.strip(" .,;:!?")
    if len(t) < 2:
        return False, None
    return True, t[:40]


def _questionnaire(palabra, monto_disp=None):
    lines = ['🔎 No conozco la palabra "%s".' % (palabra or "ese comercio")]
    if monto_disp:
        lines.append("Para registrar %s: ¿a qué categoría pertenece?" % monto_disp)
    else:
        lines.append("¿A qué categoría pertenece?")
    for n, cat, sub in OPCIONES_CAT:
        lines.append("%d. %s" % (n, sub or cat))
    lines.append("0. Otra (escríbela, ej: 'fue de veterinaria')")
    lines.append("Responde con el número (o 'no sé' para que la revise el admin).")
    return "\n".join(lines)


def _handle_entrenamiento(a, monto_num, monto_disp, PEND, text, evid, datos):
    """Flujo cuando classify_category no encontro categoria."""
    import json as _json
    palabra = None
    if PEND:
        palabra = PEND.get("palabra") or _unknown_word(PEND.get("descripcion", ""))
    if not palabra:
        palabra = _unknown_word(text)
    if not palabra:
        palabra = "ese comercio"
    if not PEND:
        PEND = {
            "monto": str(monto_num), "monto_disp": monto_disp,
            "fecha": datos.get("fecha") or parse_fecha(text) or datetime.date.today().isoformat(),
            "hora": datos.get("hora") or datetime.datetime.now().strftime("%H:%M"),
            "metodo": datos.get("metodo") or classify_pay(text),
            "evidencia": evid,
            "descripcion": (a.texto or "").strip(),
            "palabra": palabra,
            "words": _unknown_words(((a.texto or "") + " " + str(datos.get("descripcion") or "")) if a.imagen else (a.texto or "")),
            "no_aprender": False,
        }
        if not PEND.get("words"):
            PEND["words"] = [palabra] if palabra != "ese comercio" else []
        if palabra in PEND["words"]:
            PEND["words"].remove(palabra)
        _save_pending(a.grupo, PEND)
    else:
        if not PEND.get("palabra"):
            PEND["palabra"] = palabra
        if not PEND.get("descripcion"):
            PEND["descripcion"] = (a.texto or "").strip()
        if not PEND.get("words"):
            PEND["words"] = _unknown_words(PEND.get("descripcion") or "") or []
        _save_pending(a.grupo, PEND)
    print(_questionnaire(palabra, monto_disp))
    sys.exit(0)



def read_refs(srv):
    out = {"grupos": {}, "usuarios": {}, "categorias": []}
    for tab, key in (("grupos", "grupos"), ("usuarios", "usuarios"), ("categorias", "categorias")):
        try:
            r = srv.spreadsheets().values().get(spreadsheetId=REF_SHEET, range="%s!A1:D300" % tab).execute()
            vals = r.get("values", [])[1:]
            if tab == "grupos":
                for v in vals:
                    if v and v[0]:
                        out["grupos"][v[0]] = v[1] if len(v) > 1 else ""
            elif tab == "usuarios":
                for v in vals:
                    if v and v[0]:
                        out["usuarios"][v[0]] = {"grupos": v[3] if len(v) > 3 else "", "nombre": v[1] if len(v) > 1 else ""}
            else:
                for v in vals:
                    if v and v[0]:
                        out["categorias"].append({"cat": v[1] if len(v) > 1 else "", "sub": v[2] if len(v) > 2 else "", "grupos": v[3] if len(v) > 3 else ""})
        except Exception:
            continue
    return out


def gen_id(srv, sid, today):
    try:
        r = srv.spreadsheets().values().get(spreadsheetId=sid, range="Hoja 1!A2:A60000").execute()
        vals = r.get("values", [])
    except Exception:
        vals = []
    prefix = today + "-"
    maxn = 0
    for v in vals:
        if v and v[0].startswith(prefix):
            try:
                n = int(v[0].rsplit("-", 1)[1])
                maxn = max(maxn, n)
            except Exception:
                pass
    return "%s-%04d" % (today, maxn + 1)


def clean_desc(t):
    t = re.sub(r"\$?\s?\d[\d.,]*", " ", t or "")
    t = re.sub(r"\bpagu[ée]?\b|\bpago\b|\bpagar\b|\bgast[ée]\b|\bcompr[éea]\b", " ", t, flags=re.I)
    t = re.sub(r"\bpor\b|\ben\b|\bde\b|\bun\b|\buna\b|\bel\b|\bla\b|\blos\b|\blas\b|\bcon\b", " ", t, flags=re.I)
    for w in ["nequi", "daviplata", "davivienda", "bancolombia", "efectivo", "tarjeta", "transferencia", "contado", "credito", "debito"]:
        t = re.sub(r"\b%s\b" % re.escape(w), " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:1].upper() + t[1:]) if t else ""
_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_OCR_FIELD = (
    "id", "nombre del", "entidad", "llave", "numero", "estado", "monto", "monto total",
    "total", "impuesto", "fecha", "hora", "referencia", "comprobante", "cuenta", "banco",
    "desde", "para", "mas informacion", "a las", "envio realizado", "movimiento",
)

def parse_ocr(text):
    d = {}
    d["monto"] = extract_amount(text)
    d["metodo"] = classify_pay(text)

    _AM = {"ene":1,"feb":2,"mar":3,"abr":4,"may":5,"jun":6,"jul":7,"ago":8,"sep":9,"sept":9,"oct":10,"nov":11,"dic":12}
    fm = re.search(r"\b(\d{1,2})\s+de\s+([a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+)(?:\s+de)?\s+(\d{4})\b", text, re.I)
    if fm and fm.group(2).lower() in _MONTHS:
        d["fecha"] = "%s-%02d-%02d" % (fm.group(3), _MONTHS[fm.group(2).lower()], int(fm.group(1)))
    elif fm and fm.group(2).lower()[:3] in _AM:
        d["fecha"] = "%s-%02d-%02d" % (fm.group(3), _AM[fm.group(2).lower()[:3]], int(fm.group(1)))
    else:
        fs = re.search(r"\b(\d{1,2})\s+([a-zA-Záéíóú\u00f1]{3})\s+(\d{4})\b", text)
        if fs and fs.group(2).lower()[:3] in _AM:
            d["fecha"] = "%s-%02d-%02d" % (fs.group(3), _AM[fs.group(2).lower()[:3]], int(fs.group(1)))
    if not d.get("fecha"):
        fd = re.search(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b", text)
        if fd and 1 <= int(fd.group(2)) <= 12:
            dd, mm, yy = int(fd.group(1)), int(fd.group(2)), int(fd.group(3))
            if yy < 100:
                yy += 2000
            d["fecha"] = "%04d-%02d-%02d" % (yy, mm, dd)

    hm = re.search(r"\b(\d{1,2}):(\d{2})\b\s*(am|pm|a\.?m\.?|p\.?m\.?)?", text, re.I)
    if hm:
        hh, mn = int(hm.group(1)), int(hm.group(2))
        ap = (hm.group(3) or "").lower()
        if "p" in ap and hh < 12:
            hh += 12
        elif "a" in ap and hh == 12:
            hh = 0
        d["hora"] = "%02d:%02d" % (hh, mn)

    rr = re.search(r"(?:referencia|numero\s+(?:de\s+)?comprobante|comprobante)[^\s]{0,12}:?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", text, re.I)
    if rr:
        d["referencia"] = rr.group(1)

    best, best_len = None, 10 ** 9
    for ln in text.splitlines():
        t = " ".join(ln.split())
        if not t:
            continue
        low = t.lower().rstrip(":")
        if any(low == f or low.startswith(f + " ") or low.startswith(f + ":") for f in _OCR_FIELD):
            continue
        if re.match(r"^[\d.,\-$]+$", t):
            continue
        if re.search(r"-", t) and re.search(r"\d", t):
            continue
        words = [w for w in re.sub(r"[^A-Za-z]+", " ", t).split() if len(w) > 1]
        if len(words) < 2:
            continue
        cand = " ".join(words)
        if len(words) < best_len:
            best, best_len = cand, len(words)
    if best:
        d["descripcion"] = best[:120]
    return d





def _pending_path(grupo):
    import os as _os
    return _os.path.join("/tmp", "gasto_pendiente_%s.json" % (grupo or "x"))

def _save_pending(grupo, d):
    import json as _json
    try:
        with open(_pending_path(grupo), "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except Exception:
        pass

def _load_pending(grupo):
    import json as _json
    try:
        with open(_pending_path(grupo), "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None

def _clear_pending(grupo):
    import os as _os
    try:
        _os.remove(_pending_path(grupo))
    except Exception:
        pass


MESES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,
         "agosto":8,"septiembre":9,"setiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
QUERY_WORDS = ["resumen", "cuanto", "total", "gastos", "gastado", "gastamos",
               "gaste", "gasto", "cuanto gastamos", "cuanto gaste"]




def _parse_monto_disp(val):
    """Parse $1,500,000.00 / 81700 / 1.500.000,00 -> float."""
    if val is None:
        return None
    v = re.sub(r"[^0-9.,]", "", str(val).strip())
    if not v:
        return None
    if "," in v and "." in v:
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    elif "," in v:
        if v.count(",") == 1 and len(v.split(",")[1]) <= 2:
            v = v.replace(",", ".")
        else:
            v = v.replace(",", "")
    try:
        return float(v)
    except Exception:
        return None

def _prev_month(y, m):
    m -= 1
    if m < 1:
        m = 12; y -= 1
    return y, m


def _ctx_path(grupo):
    return os.path.join("/tmp", "gasto_ctx_%s.json" % (grupo or "x"))


def _load_ctx(grupo):
    try:
        import json as _json
        return _json.load(open(_ctx_path(grupo), encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_ctx(grupo, d):
    try:
        import json as _json
        _json.dump(d, open(_ctx_path(grupo), "w", encoding="utf-8"))
    except Exception:
        pass


def _month_from_text(text):
    t = _strip_accents(text or "")
    for m in MESES:
        if re.search(r"\b" + re.escape(m) + r"\b", t):
            return m
    return None


def _looks_like_query(text):
    if re.search(r"\$\s?\d", text or ""):
        return False
    if re.search(r"(^|\s)\d[\d.,]{4,}(\s|$)", text or ""):
        return False
    if extract_amount(text):
        return False
    t = _strip_accents(text or "")
    for v in ("compre en", "compre por", "pague por", "pago por", "pague en",
              "recargue en", "abone a", "gaste en", "gasto en", "comprar"):
        if re.search(r"\b" + re.escape(v) + r"\b", t):
            return False
    if any(w in t for w in QUERY_WORDS):
        return True
    es_pregunta = bool(re.search(r"cuanto|cuánto|cual|cuáles|como fue|que gast|cuanto gast", t))
    for v in ("pague", "pagué", "compre", "compré", "recargue", "recargué", "abone",
              "aboné", "pago", "pagó", "comprar", "gaste en", "gasto en", "pague en",
              "compre en", "llevé", "lleve"):
        if re.search(r"\b" + re.escape(v) + r"\b", t):
            if es_pregunta and ("gast" in t or "gasto" in t or "gastos" in t):
                return True
            return False
    if _month_from_text(t):
        return True
    if re.search(r"\b(mes pasado|mes anterior|ultimo mes|este mes|del mes|el mes|los de|las de)\b", t):
        return True
    return False


def run_consulta(srv, sid, grupo, text, ctx):
    t = _strip_accents(text or "")
    hoy = datetime.date.today()
    year, month = hoy.year, hoy.month
    subject = None
    if any(w in t for w in ("ingreso", "ingresos", "recibido", "recibimos", "entradas")):
        subject = "ingresos"
    elif any(w in t for w in ("gasto", "gastos", "gastado", "resumen", "total")):
        subject = "gastos"
    mname = _month_from_text(t)
    is_follow = bool(re.search(r"\b(y|y los|y las|y el|los de|las de|tambien|que tal|como fue|y del)\b", t))
    if mname:
        month = MESES[mname]
    elif re.search(r"\b(mes pasado|mes anterior|ultimo mes|el mes pasado)\b", t):
        year, month = _prev_month(year, month)
    elif is_follow and ctx and ctx.get("month"):
        month = int(ctx["month"])
        year = int(ctx.get("year", year))
        if subject is None:
            subject = ctx.get("subject")
    if subject is None:
        subject = "gastos"
    ym = re.search(r"(?:de|del)\s+(20\d{2})", t)
    if ym:
        year = int(ym.group(1))
    prefix = "%04d-%02d" % (year, month)
    gastos = ingresos = 0.0
    n = 0
    by_cat = {}
    try:
        r = srv.spreadsheets().values().get(
            spreadsheetId=sid, range="Hoja 1!A2:P50000").execute()
        rows = r.get("values", [])
    except Exception:
        rows = []
    for row in rows:
        if len(row) < 9:
            continue
        fech = (row[1] or "")[:7]
        if fech != prefix:
            continue
        monto = _parse_monto_disp(row[6] if len(row) > 6 else None)
        if not monto:
            continue
        tipo = row[5] if len(row) > 5 else "Gasto"
        cat = row[8] if len(row) > 8 else "?"
        n += 1
        if str(tipo).lower() == "ingreso":
            ingresos += monto
        else:
            gastos += monto
            by_cat[cat] = by_cat.get(cat, 0.0) + monto
    _save_ctx(grupo, {"month": month, "year": year, "subject": subject})
    mes_nombre = [k for k, v in MESES.items() if v == month][0]
    if subject == "ingresos":
        if n == 0 or ingresos == 0:
            return "📊 No hay ingresos registrados en %s %d." % (mes_nombre, year)
        lines = ["📊 Ingresos de %s %d (n=%d)" % (mes_nombre, year, n)]
        lines.append("• Ingresos: %s" % to_monto_display(ingresos))
    else:
        if n == 0:
            return "📊 No hay movimientos registrados en %s %d." % (mes_nombre, year)
        lines = ["📊 Resumen de %s %d (n=%d)" % (mes_nombre, year, n)]
        lines.append("• Gastos: %s" % to_monto_display(gastos))
        if ingresos:
            lines.append("• Ingresos: %s" % to_monto_display(ingresos))
        lines.append("• Saldo del mes: %s" % to_monto_display(ingresos - gastos))
    if by_cat:
        lines.append("Por categoría:")
        for c in sorted(by_cat, key=lambda k: -by_cat[k]):
            lines.append("  • %s: %s" % (c, to_monto_display(by_cat[c])))
    lines.append("Tip: dime 'gastos de septiembre', 'resumen del mes pasado' o 'cuánto gasté'.")
    return "\n".join(lines)


PRODUCT_RULES = [
    ("arroz", ["arroz"]),
    ("aceite", ["aceite"]),
    ("leche", ["leche"]),
    ("huevos", ["huevo", "huevos"]),
    ("pan", ["pan"]),
    ("papa", ["papa", "papas"]),
    ("tomate", ["tomate", "tomates"]),
    ("cebolla", ["cebolla"]),
    ("pollo", ["pollo"]),
    ("carne", ["carne"]),
    ("pescado", ["pescado"]),
    ("cafe", ["cafe", "café", "tinto"]),
    ("azucar", ["azucar", "azúcar"]),
    ("sal", ["sal"]),
    ("detergente", ["detergente"]),
    ("jabon", ["jabon", "jabón"]),
    ("shampoo", ["shampoo", "champu", "champú"]),
    ("papel higienico", ["papel higienico", "papel higiénico", "rollos de papel"]),
    ("toallas", ["toalla", "toallas", "papel toalla"]),
    ("servilletas", ["servilleta", "servilletas"]),
    ("panales", ["pañal", "pañales", "panal", "panales"]),
    ("concentrado mascotas", ["concentrado"]),
    ("fruver", ["fruver", "fruta", "frutas", "verduras"]),
    ("galletas", ["galleta", "galletas"]),
    ("snacks", ["snack", "snacks", "papitas"]),
    ("gaseosa", ["gaseosa", "gaseosas", "coca", "postobon"]),
    ("jugo", ["jugo", "jugos"]),
    ("cerveza", ["cerveza", "cervezas", "pola", "polas"]),
    ("gasolina", ["gasolina", "combustible"]),
    ("medicamento", ["acetaminofen", "ibuprofeno", "pastillas", "vitaminas"]),
    ("celular", ["celular", "movil", "móvil"]),
    ("cargador", ["cargador"]),
    ("audifonos", ["audifonos", "audífonos"]),
    ("libro", ["libro", "libros"]),
]


def _inventario_path():
    return os.path.join(HERMES, "scripts", "inventario.json")


def _load_inventario():
    import json as _json
    try:
        return _json.load(open(_inventario_path(), encoding="utf-8")) or []
    except Exception:
        return []


def _save_inventario(d):
    import json as _json
    try:
        _json.dump(d, open(_inventario_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def extract_products(text):
    t = _strip_accents(text or "").lower()
    out = []
    for prod, kws in PRODUCT_RULES:
        for kw in kws:
            if re.search(r"\b" + re.escape(_strip_accents(kw)) + r"\b", t):
                out.append(prod)
                break
    for _kw, _info in _load_aprendizajes().items():
        if _info.get("producto") and re.search(r"\b" + re.escape(_kw) + r"\b", t):
            out.append(_info["producto"])
    return list(dict.fromkeys(out))


def _record_inventario(grupo, products, fecha, monto, cat):
    if not products:
        return
    d = _load_inventario()
    try:
        monto_f = float(monto)
    except Exception:
        monto_f = 0.0
    for prod in products:
        d.append({"grupo": grupo, "fecha": fecha, "producto": prod,
                  "monto": monto_f, "cat": cat})
    if len(d) > 20000:
        d = d[-20000:]
    _save_inventario(d)


def _log_evento(grupo, tipo, data=None):
    import json as _json, datetime as _dt
    try:
        line = _json.dumps({"ts": _dt.datetime.now().isoformat(timespec="seconds"),
                            "grupo": grupo, "tipo": tipo, "data": data or {}},
                           ensure_ascii=False)
        with open(os.path.join(HERMES, "scripts", "historial.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _prodpend_path(grupo):
    return os.path.join("/tmp", "gasto_prodpend_%s.json" % (grupo or "x"))


def _save_prodpend(grupo, d):
    import json as _json
    try:
        with open(_prodpend_path(grupo), "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except Exception:
        pass


def _load_prodpend(grupo):
    import json as _json
    try:
        with open(_prodpend_path(grupo), "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def _clear_prodpend(grupo):
    import os as _os
    try:
        _os.remove(_prodpend_path(grupo))
    except Exception:
        pass


def _mega_path():
    return os.path.join(HERMES, "scripts", "mega_config.json")


_MEGA_GRUPOS = {"personal": "Personal", "hogar": "Hogar", "andrea": "Andrea"}


def _mega_folder(m, parent, nombre):
    """Busca una carpeta hija de `parent` con el nombre dado; si no existe la crea. Devuelve su handle."""
    try:
        _files = m.get_files()
    except Exception:
        _files = {}
    for _h, _info in _files.items():
        _a = _info.get("a") or {}
        if isinstance(_a, dict) and _a.get("n") == nombre and _info.get("p") == parent:
            return _h
    try:
        _node = m.create_folder(nombre, parent)
        return _node.get(nombre)
    except Exception:
        return None


def _subir_mega(evidencia, grupo, nombre):
    """Sube una imagen de recibo a Mega en Recibos/<Grupo> y devuelve la URL pública (o None)."""
    try:
        import mega
    except Exception:
        return None
    try:
        import json as _json
        cfg = _json.load(open(_mega_path(), encoding="utf-8"))
        email = cfg.get("email") or ""
        pw = cfg.get("password") or ""
        if not email or not pw:
            return None
        m = mega.Mega()
        m.login(email, pw)
        base = (cfg.get("folder") or "").strip() or "Recibos"
        base_h = None
        try:
            _files = m.get_files()
            for _h, _info in _files.items():
                _a = _info.get("a") or {}
                if _info.get("h") == base or (isinstance(_a, dict) and _a.get("n") == base):
                    base_h = _h
                    break
        except Exception:
            _files = {}
        if not base_h:
            try:
                _node = m.create_folder(base, None)
                base_h = _node.get(base)
            except Exception:
                base_h = None
        sub = _MEGA_GRUPOS.get(grupo or "", grupo or "General")
        sub_h = None
        if base_h:
            sub_h = _mega_folder(m, base_h, sub)
        res = m.upload(evidencia, dest=sub_h or base_h, dest_filename=nombre)
        url = m.get_upload_link(res)
        _log_evento(grupo, "mega_subida", {"archivo": nombre, "url": url, "carpeta": "%s/%s" % (base, sub)})
        return url
    except Exception as _e:
        _log_evento(grupo, "mega_error", {"archivo": nombre, "error": str(_e)})
        return None


def run_frecuencia(srv, sid, grupo, text):
    t = _strip_accents(text or "").lower()
    prod_pedido = None
    for prod, kws in PRODUCT_RULES:
        for kw in kws:
            if re.search(r"\b" + re.escape(_strip_accents(kw)) + r"\b", t):
                prod_pedido = prod
                break
        if prod_pedido:
            break
    reg = _load_inventario()
    if prod_pedido:
        rows = [r for r in reg if r.get("grupo") == grupo and r.get("producto") == prod_pedido]
        if not rows:
            return "🔎 No tengo registro de compras de %s en este grupo." % prod_pedido
        fechas = sorted({str(r.get("fecha", ""))[:10] for r in rows})
        total = sum(float(r.get("monto") or 0) for r in rows)
        n = len(fechas)
        gaps = []
        for i in range(1, len(fechas)):
            try:
                from datetime import datetime as _dt
                g = (_dt.strptime(fechas[i], "%Y-%m-%d") - _dt.strptime(fechas[i - 1], "%Y-%m-%d")).days
                gaps.append(g)
            except Exception:
                continue
        prom = (int(sum(gaps) / len(gaps)) if gaps else "n/a")
        lineas = ["🛒 %s: %d compras, gasto total %s" % (prod_pedido, n, to_monto_display(total))]
        lineas.append("• Última compra: %s" % fechas[-1])
        if gaps:
            lineas.append("• Frecuencia aprox: cada %d días" % prom)
        return "\n".join(lineas)
    # resumen general de los productos mas frecuentes del grupo
    from collections import Counter
    c = Counter(r.get("producto") for r in reg if r.get("grupo") == grupo)
    if not c:
        return "🔎 No tengo inventario registrado. Registra gastos con productos para construir el inventario."
    top = c.most_common(10)
    lineas = ["🛒 Productos más comprados en este grupo:"]
    for prod, cnt in top:
        lineas.append("  • %s: %d compras" % (prod, cnt))
    return "\n".join(lineas)


# ============ 2) Presupuestos ============
def _presupuesto_path():
    return os.path.join(HERMES, "scripts", "presupuestos.json")


def _load_presupuestos():
    import json as _json
    try:
        return _json.load(open(_presupuesto_path(), encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_presupuestos(d):
    import json as _json
    try:
        _json.dump(d, open(_presupuesto_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


BUDGET_CAT_ALIASES = {
    "alimentacion": "Alimentacion", "comida": "Alimentacion", "mercado": "Alimentacion",
    "vivienda": "Vivienda", "hogar": "Vivienda", "casa": "Vivienda",
    "transporte": "Transporte", "bus": "Transporte",
    "salud": "Salud", "medicamentos": "Salud", "farmacia": "Salud",
    "tecnologia": "Tecnologia", "tecno": "Tecnologia",
    "educacion": "Educacion", "estudio": "Educacion", "universidad": "Educacion",
    "ocio": "Ocio", "entretenimiento": "Ocio",
    "ropa": "Ropa", "vestimenta": "Ropa",
    "mascotas": "Mascotas", "mascota": "Mascotas",
    "ingreso": "Ingreso", "ingresos": "Ingreso", "ahorro": "Ahorro",
}


def _budget_monto(text):
    t = (text or "").lower()
    m = re.search(r"(\d[\d.,]*)\s*(k|mil|m)?", t)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(".", "").replace(",", "."))
    except Exception:
        return None
    suf = (m.group(2) or "")
    if suf == "k":
        val *= 1000
    elif suf == "m":
        val *= 1000000
    elif "mil" in suf:
        val *= 1000
    return val


def _define_presupuesto(grupo, text):
    t = _strip_accents(text or "").lower()
    cat = None
    for alias, canon in BUDGET_CAT_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", t):
            cat = canon
            break
    if cat is None:
        cc, _s = classify_category(text)
        cat = cc or "Alimentacion"
    monto = _budget_monto(text)
    if not monto:
        return "⚠️ Dime el monto del presupuesto, ej: 'presupuesto de alimentación 600 mil'."
    d = _load_presupuestos()
    d.setdefault(grupo, {})[cat] = monto
    _save_presupuestos(d)
    return "✅ Presupuesto de %s = %s/mes en %s." % (cat, to_monto_display(monto), grupo)


def _gasto_mensual_categoria(srv, sid, grupo, cat):
    r = srv.spreadsheets().values().get(
        spreadsheetId=sid, range="Hoja 1!A2:P50000").execute()
    rows = r.get("values", [])
    hoy = datetime.date.today()
    prefix = hoy.strftime("%Y-%m")
    total = 0.0
    n = 0
    for row in rows:
        if len(row) < 9:
            continue
        fech = (row[1] or "")[:7]
        if fech != prefix:
            continue
        if str(row[8] or "") != cat:
            continue
        m = _parse_monto_disp(row[6] if len(row) > 6 else None)
        if not m:
            continue
        if str(row[5] or "Gasto").lower() == "ingreso":
            continue
        total += m
        n += 1
    return total, n


def run_presupuestos(srv, sid, grupo):
    d = _load_presupuestos().get(grupo, {})
    if not d:
        return "⚠️ No hay presupuestos definidos en %s. Ej: 'presupuesto de alimentación 600 mil'." % grupo
    lineas = ["📋 Presupuestos %s (este mes):" % grupo]
    for cat, tope in sorted(d.items(), key=lambda x: -x[1]):
        gastado, n = _gasto_mensual_categoria(srv, sid, grupo, cat)
        pct = (gastado / tope * 100.0) if tope else 0.0
        bar = "▓" * min(int(pct / 10), 10) + "░" * max(0, 10 - min(int(pct / 10), 10))
        lineas.append("  • %s: %s de %s (%d%%) %s" % (cat, to_monto_display(gastado), to_monto_display(tope), int(pct), bar))
    return "\n".join(lineas)


def _check_presupuesto(srv, sid, grupo, cat, monto_extra):
    """Alerta si el gasto nuevo supera 80% o 100% del presupuesto de la categoria."""
    d = _load_presupuestos().get(grupo, {})
    if cat not in d:
        return None
    tope = d[cat]
    gastado, n = _gasto_mensual_categoria(srv, sid, grupo, cat)
    gastado += float(monto_extra)
    pct = gastado / tope * 100.0 if tope else 0.0
    if pct >= 100:
        return "🚨 ALERTA: %s superó su presupuesto mensual (%s de %s, %d%%)." % (cat, to_monto_display(gastado), to_monto_display(tope), int(pct))
    if pct >= 80:
        return "⚠️ OJO: %s va en %d%% del presupuesto (%s de %s)." % (cat, int(pct), to_monto_display(gastado), to_monto_display(tope))
    return None


# ============ 3) Deteccion de intenciones presupuesto/frecuencia ============
def _borrar_path(grupo):
    return os.path.join("/tmp", "gasto_borrar_%s.json" % (grupo or "x"))


def _save_borrar(grupo, fila, datos):
    import json as _json
    try:
        with open(_borrar_path(grupo), "w", encoding="utf-8") as f:
            _json.dump({"fila": fila, "datos": datos}, f)
    except Exception:
        pass


def _load_borrar(grupo):
    import json as _json
    try:
        with open(_borrar_path(grupo), "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def _clear_borrar(grupo):
    import os as _os
    try:
        _os.remove(_borrar_path(grupo))
    except Exception:
        pass


def _confirmar_borrar(grupo, text):
    """Si hay un borrado pendiente y el usuario confirma, ejecuta el borrado."""
    pend = _load_borrar(grupo)
    if not pend:
        return None
    t = _strip_accents((text or "").strip().lower())
    if t not in ("si", "sí", "sip", "dale", "confirma", "confirmar", "borra si", "si borra", "ok", "okey", "listo", "adelante"):
        return None
    sid = GROUP_TARGETS[grupo][0]
    srv = get_srv()
    fila = pend.get("fila")
    info = pend.get("datos", {})
    rng = "Hoja 1!A%d:P%d" % (fila, fila)
    try:
        srv.spreadsheets().values().update(
            spreadsheetId=sid, range=rng,
            valueInputOption="USER_ENTERED",
            body={"values": [[""] * 16]},
        ).execute()
    except Exception as _e:
        return "⚠️ No pude borrar la entrada: %s" % _e
    _clear_borrar(grupo)
    _log_evento(grupo, "borrar", {"fila": fila, "datos": info, "confirmado": True})
    msg = "🗑️ Borré la última entrada (fila %d):\n" % fila
    if info.get("fecha"):
        msg += "• Fecha: %s\n" % info["fecha"]
    if info.get("tipo"):
        msg += "• Tipo: %s\n" % info["tipo"]
    if info.get("monto"):
        msg += "• Monto: %s\n" % info["monto"]
    if info.get("categoria"):
        msg += "• Categoría: %s\n" % info["categoria"]
    if info.get("descripcion"):
        msg += "• Detalle: %s\n" % info["descripcion"]
    if info.get("id") and info["id"] != "?":
        msg += "• id: %s" % info["id"]
    return msg.strip()


def run_borrar(srv, sid, grupo):
    """Pide confirmacion para borrar la ultima entrada registrada en la hoja del grupo."""
    try:
        r = srv.spreadsheets().values().get(spreadsheetId=sid, range="Hoja 1!A2:P50000").execute()
    except Exception as _e:
        return "⚠️ No pude leer la hoja: %s" % _e
    vals = r.get("values", [])
    last = None
    for i, v in enumerate(vals, start=2):
        if v and any(str(c).strip() for c in v[:16]):
            last = i
    if last is None:
        return "✅ No hay entradas que borrar."
    row = (vals[last - 2] + [""] * 16)[:16]
    info = {
        "id": row[0] if len(row) > 0 else "?",
        "fecha": row[1] if len(row) > 1 else "?",
        "tipo": row[5] if len(row) > 5 else "?",
        "monto": row[6] if len(row) > 6 else "?",
        "categoria": row[8] if len(row) > 8 else "?",
        "descripcion": row[10] if len(row) > 10 else "",
    }
    _save_borrar(grupo, last, info)
    msg = "⚠️ ¿Seguro que quieres borrar la última entrada?\n"
    if info["fecha"]:
        msg += "• Fecha: %s\n" % info["fecha"]
    if info["tipo"]:
        msg += "• Tipo: %s\n" % info["tipo"]
    if info["monto"]:
        msg += "• Monto: %s\n" % info["monto"]
    if info["categoria"]:
        msg += "• Categoría: %s\n" % info["categoria"]
    if info["descripcion"]:
        msg += "• Detalle: %s\n" % info["descripcion"]
    if info["id"] and info["id"] != "?":
        msg += "• id: %s\n" % info["id"]
    msg += "\nResponde 'si' para borrarla, o 'no' para cancelar."
    return msg


MENU_TEXT = """📱 *Bot de Finanzas — Guía rápida*

*1. Registrar un gasto/ingreso*
  • Escríbelo con cifra y detalle:
    "pague 5000 mercado por nequi"
    "grande recibo 2.026 como regalo"
  • O envía la *foto del recibo*.

*2. Si falta información*
  El bot te pide SOLO lo que falta
  (monto → descripción → categoría).
  Lo que ya sabes no se vuelve a preguntar.

*3. Categorías disponibles*
  1. Mercado/plaza    7. Internet/teléfono
  2. Restaurante      8. Transporte
  3. Domicilios       9. Medicamentos
  4. Bebidas/snacks  10. Suscripciones
  5. Arriendo        11. Entretenimiento
  6. Servicios       12. Otros ingresos
  0. Otra (descríbela)

*4. Palabras nuevas*
  Si el bot no conoce una palabra, te pregunta
  a qué categoría pertenece → respóndele un
  número y la aprende sola. Si no sabes,
  responde "no sé" y la revisa el administrador.

*5. Consultas*
  • "resumen de agosto"
  • "cuánto gasté este mes"
  • "presupuesto de alimentacion 600 mil"
  • "estado de presupuestos"
  • "cada cuánto compro arroz"

*6. Borrar*
  "borra la última entrada" → confirma con "si".

*7. Recibos*
  La foto del gasto se sube a tu bóveda Mega
  organizada por grupo (Recibos/Hogar, etc).

Escribe *ayuda* cuando quieras ver esto otra vez.
"""


def _cola_path():
    return os.path.join(HERMES, "scripts", "cola_aprendizaje.json")


def _load_cola():
    import json as _json
    try:
        return _json.load(open(_cola_path(), encoding="utf-8")) or []
    except Exception:
        return []


def _save_cola(d):
    import json as _json
    try:
        _json.dump(d, open(_cola_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def run_ayuda():
    return MENU_TEXT


def _unknown_words(text):
    """Devuelve TODAS las palabras desconocidas (en singular, ordenadas por longitud)."""
    t = _strip_accents(text or "").lower()
    toks = re.findall(r"[a-z]{3,}", t)
    known = set()
    for _name, _kws, _c, _sub in CATEGORY_RULES:
        for _k in _kws:
            known.add(_strip_accents(_k))
    for _val, _alts in PAY_METHOD_RULES:
        for _k in _alts:
            known.add(_strip_accents(_k))
    for ppk in list(PRODUCT_RULES) + [(w, [w]) for w in _load_aprendizajes()]:
        for _k in ppk[1] if len(ppk) > 1 else []:
            known.add(_strip_accents(str(_k)))
    cands = []
    seen = set()
    for tok in toks:
        if tok in _STOP or tok in known or tok in MESES or tok in {"hoy", "ayer", "anoche", "compra", "compre", "una", "esa", "fue", "por", "para", "con", "en", "de", "el", "la", "los"}:
            continue
        if re.fullmatch(r"\d+", tok):
            continue
        kw = _strip_accents(tok)
        if kw in seen:
            continue
        seen.add(kw)
        cands.append(kw)
    cands.sort(key=len, reverse=True)
    return cands


def run_revisar(grupo, text):
    """Muestra la cola de aprendizaje pendiente, y aprende "palabra = categoria"."""
    cola = _load_cola()
    t = _strip_accents(text or "").lower()
    m = re.match(r"^([a-zñáéíóú]{3,})\s*=\s*(.+)$", t)
    if m:
        palabra = _strip_accents(m.group(1).strip().lower())
        valor = m.group(2).strip()
        cat, sub = classify_category(valor)
        if not cat:
            op = _opcion_from_text(valor)
            if op:
                cat, sub = op
        if not cat:
            vlow = _strip_accents(valor.lower())
            for _n, _cat, _sub in OPCIONES_CAT:
                if vlow in (_strip_accents(_sub.lower()), _strip_accents(_cat.lower()), str(_n)):
                    cat, sub = _cat, _sub
                    break
        if not cat:
            return "⚠️ No reconozco la categoría '%s'. Ej: 'planilla = salud' o 'exito = mercado'." % valor
        _aprender(palabra, cat, sub)
        cola = [c for c in cola if c.get("palabra") != palabra]
        _save_cola(cola)
        _log_evento(grupo, "admin_aprende", {"palabra": palabra, "categoria": cat, "sub": sub})
        return "✅ Administrador: aprendí que '%s' = %s (%s). Se re-mapearán gastos futuros." % (palabra, cat, sub)
    if t and re.match(r"^\d+$", t.strip()):
        return "⚠️ Para enseñar escribe: <palabra> = <categoría>, ej 'exito = mercado'."
    if not cola:
        return "✅ No hay palabras pendientes de revisión. ¡Todo aprendido!"
    lines = ["🗂 *Cola de aprendizaje* (%d pendiente%s)" % (len(cola), "" if len(cola) == 1 else "s")]
    for i, c in enumerate(cola, start=1):
        g = c.get("grupos") or []
        _pal = c.get("palabra") or "?"
        _gr = ", ".join(g) if g else "?"
        lines.append('%d. "%s" (visto en: %s)' % (i, _pal, _gr))
    lines.append('\nPara enseñarle: *<palabra> = <categoría>*, ej: "planilla = salud".')
    return "\n".join(lines)


def _cola_add(palabra, grupo):
    palabra = _strip_accents((palabra or "").strip().lower())
    if not palabra or palabra in ("ese comercio",):
        return
    cola = _load_cola()
    for c in cola:
        if c.get("palabra") == palabra:
            gs = c.setdefault("grupos", [])
            if grupo and grupo not in gs:
                gs.append(grupo)
            c["visto"] = c.get("visto", 0) + 1
            _save_cola(cola)
            return
    cola.append({"palabra": palabra, "grupos": [grupo] if grupo else [], "visto": 1, "ts": datetime.datetime.now().isoformat(timespec="seconds")})
    _save_cola(cola)
    _log_evento(grupo or "", "cola_aprendizaje", {"palabra": palabra})


def _aprender_path():
    return os.path.join(HERMES, "scripts", "aprendizajes.json")


def _unknown_word(text):
    t = _strip_accents(text or "").lower()
    toks = re.findall(r"[a-z]{3,}", t)
    known = set()
    for _name, _kws, _c, _sub in CATEGORY_RULES:
        for _k in _kws:
            known.add(_strip_accents(_k))
    for _val, _alts in PAY_METHOD_RULES:
        for _k in _alts:
            known.add(_strip_accents(_k))
    cands = []
    for tok in toks:
        if tok in _STOP or tok in known or tok in MESES:
            continue
        if re.fullmatch(r"\d+", tok):
            continue
        cands.append(tok)
    cands.sort(key=len, reverse=True)
    return cands[0] if cands else None


def _intent(text):
    t = _strip_accents(text or "").lower()
    if re.match(r"^[a-zñáéíóú]{3,}\s*=\s*.+", t):
        return "revisar"
    if re.search(r"\b(ayuda|menu|menú|instrucciones|como usar|como se usa|como funciona|que sabes hacer|manual)\b", t):
        return "ayuda"
    if re.search(r"\b(revisar|revision|revisión|cola de aprendizaje|pendientes de aprender|que me falta aprender|aprende|enseñar|aprendizaje)\b", t):
        return "revisar"
    if re.search(r"\b(borra|borrar|borre|borralo|borrálo|cancela|cancelar|cancele|anula|anular|no agregues|no agregue|no añadas|no anadas|no registres|quita|quitar|quitemos|elimina|eliminar|elimine|deshacer|ultima entrada|última entrada|ultimo gasto|último gasto|ultimo registro|último registro|deshaz)\b", t):
        return "borrar"
    if re.search(r"\b(presupuesto|presupuestos)\b", t):
        return "presupuesto"
    if re.search(r"\b(frecuencia|cada cuanto|cada cuánto|que tan seguido|qué tan seguido|que compro|cada cuando|cada cuándo|inventario)\b", t):
        return "frecuencia"
    return None


def main():

    ap = argparse.ArgumentParser()
    ap.add_argument("--grupo", required=True, choices=list(GROUP_TARGETS))
    ap.add_argument("--texto", default=None)
    ap.add_argument("--imagen", default=None)
    ap.add_argument("--evidencia", default=None)
    ap.add_argument("--sender", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.texto and not a.imagen:
        print("Uso: gasto.py --grupo KEY [--texto TEXTO] [--imagen RUTA]")
        sys.exit(2)

    sid, def_grupo, def_usuario = GROUP_TARGETS[a.grupo]

    if a.imagen is None and a.texto:
        _int = _intent(a.texto)
        if _int == "ayuda":
            print(run_ayuda())
            _log_evento(a.grupo, "ayuda", {"texto": a.texto[:120]})
            sys.exit(0)
        if _int == "revisar":
            _r = run_revisar(a.grupo, a.texto)
            print(_r)
            _log_evento(a.grupo, "revisar", {"texto": a.texto[:120], "res": _r[:200]})
            sys.exit(0)
        if _int == "presupuesto":
            _r = _define_presupuesto(a.grupo, a.texto) if (re.search(r"\b(define|definir|pon|pongo|ponle|poner|fija|fijar|establece|establecer)\b", _strip_accents(a.texto).lower()) or _budget_monto(a.texto)) else run_presupuestos(get_srv(), sid, a.grupo)
            print(_r)
            _log_evento(a.grupo, "presupuesto", {"texto": a.texto[:120], "res": _r[:200]})
            sys.exit(0)
        if _int == "frecuencia":
            _r = run_frecuencia(get_srv(), sid, a.grupo, a.texto)
            print(_r)
            _log_evento(a.grupo, "frecuencia", {"texto": a.texto[:120], "res": _r[:200]})
            sys.exit(0)
        if _int == "borrar":
            _r = run_borrar(get_srv(), sid, a.grupo)
            print(_r)
            _log_evento(a.grupo, "borrar", {"texto": a.texto[:120], "res": _r[:200]})
            sys.exit(0)
    if a.imagen is None and a.texto and _looks_like_query(a.texto):
        _r = run_consulta(get_srv(), sid, a.grupo, a.texto, _load_ctx(a.grupo))
        print(_r)
        _log_evento(a.grupo, "consulta", {"texto": a.texto[:120], "res": _r[:200]})
        sys.exit(0)

    if a.imagen:
        evid = a.evidencia or os.path.basename(a.imagen)
        raw = ocr(a.imagen)
        if a.texto:
            raw = a.texto + "\n" + raw
    else:
        evid = a.evidencia or ""
        raw = a.texto

    if not raw or not raw.strip():
        print("⚠️ No pude leer el mensaje/recibo. Intenta con otra foto o escríbelo, ej: 'pague 5000 mercado por nequi'.")
        sys.exit(0)

    text = raw.strip()
    if a.imagen is None:
        _ctx_t = _strip_accents(text.lower())
        if re.search(r"\b(anterior|anteriormente)\b", _ctx_t) and re.search(r"\b(fue|era|es)?\s*v?\s*de\b", _ctx_t) and not re.search(r"\b(pague|pagué|compre|compré|recargue|recargué|abone|aboné|gaste|gasté|comprar|pagar|registra|registrar)\b", _ctx_t):
            print("📌 Parece que te refieres a una entrada anterior (contexto), no a un gasto nuevo.\nDime el gasto real con su cifra (ej: 'pague 5000 en mercado') o 'borra' para eliminar la última entrada.")
            _log_evento(a.grupo, "contexto_ignorado", {"texto": text[:120]})
            sys.exit(0)
    datos = parse_ocr(text) if a.imagen else {}
    if a.dry_run and datos:
        print("[lectura] " + json.dumps({k: v for k, v in datos.items() if v}, ensure_ascii=False))

    if a.imagen is None and a.texto and _load_borrar(a.grupo):
        _borrar_pend = _load_borrar(a.grupo)
        _bt = _strip_accents(a.texto.strip().lower())
        if _bt in ("no", "nop", "n", "cancelar", "cancela", "no borres", "no la borres"):
            _clear_borrar(a.grupo)
            print("✅ No borré nada. La entrada se mantiene.")
            sys.exit(0)
        _confirm = _confirmar_borrar(a.grupo, a.texto)
        if _confirm:
            print(_confirm)
            _log_evento(a.grupo, "borrar", {"texto": a.texto[:120], "res": _confirm[:200]})
            sys.exit(0)

    _pend_cat_check = _load_pending(a.grupo) if (a.imagen is None and a.texto) else None
    PPEND = _load_prodpend(a.grupo)
    if PPEND and a.imagen is None and a.texto and not (_pend_cat_check and _pend_cat_check.get("palabra")) and _opcion_prod_pendiente(PPEND, a.texto):
        _ok, _prod = _opcion_prod_pendiente(PPEND, a.texto)
        _aprender_producto(PPEND.get("palabra", ""), _prod)
        _record_inventario(a.grupo, [_prod], PPEND.get("fecha") or datetime.date.today().isoformat(), PPEND.get("monto") or 0, PPEND.get("cat") or "Sin categoria")
        _clear_prodpend(a.grupo)
        _log_evento(a.grupo, "producto_aprendido", {"palabra": PPEND.get("palabra"), "producto": _prod})
        print("✅ Aprendí que %s es el producto %s. Quedó en el inventario." % (PPEND.get("palabra"), _prod))
        sys.exit(0)

    monto_num = datos.get("monto") or extract_amount(text)
    PEND = None
    monto_disp = None
    if monto_num is None:
        PEND = _load_pending(a.grupo)
        if PEND and PEND.get("monto"):
            monto_num = str(PEND["monto"])
            monto_disp = PEND.get("monto_disp")
        else:
            print("⚠️ No encontré el monto. Escríbelo con cifra (ej: '750 mil en arriendo') o reenvía la foto del recibo.")
            sys.exit(0)

    if monto_disp is None:
        monto_disp = to_monto_display(monto_num)
    if monto_disp is None:
        print("⚠️ No pude interpretar el monto '%s'." % monto_num)
        sys.exit(0)

    monto_vino_de_pend = bool(PEND and PEND.get("monto"))
    cat, sub = classify_category(text)
    aprend = _lookup_aprendizaje(text)
    if cat is None and aprend:
        cat, sub = aprend
    if a.imagen and not PEND:
        PEND = {
            "monto": str(monto_num), "monto_disp": monto_disp,
            "fecha": datos.get("fecha") or parse_fecha(text) or datetime.date.today().isoformat(),
            "hora": datos.get("hora") or datetime.datetime.now().strftime("%H:%M"),
            "metodo": datos.get("metodo") or classify_pay(text),
            "evidencia": evid,
            "descripcion": (a.texto or "").strip(),
        }
        _save_pending(a.grupo, PEND)
    if cat is None and PEND and PEND.get("palabra"):
        _resp_t = _strip_accents(text or "").strip().lower()
        _no_se = _resp_t in ("no se", "no se!", "no se.", "no sé", "no", "nop", "n", "no se que es", "no se qué es", "no la conozco", "no conozco", "ni idea", "no se cual", "no se cuál", "no cacho", "no se que categoria", "no se qué categoría")
        if _no_se:
            _cola_add(PEND.get("palabra"), a.grupo)
            _log_evento(a.grupo, "cola_aprendizaje", {"palabra": PEND.get("palabra")})
            PEND["palabra"] = "ese comercio"
            PEND["no_aprender"] = True
            _save_pending(a.grupo, PEND)
            print("✅ Entendido: guardé la palabra para revisión del administrador.")
            print(_questionnaire("ese comercio", monto_disp))
            sys.exit(0)
        op = _opcion_from_text(text)
        if op:
            cat, sub = op
            if PEND.get("no_aprender") or not PEND.get("palabra") or PEND["palabra"] == "ese comercio":
                PEND.pop("palabra", None)
            else:
                _aprender(PEND["palabra"], cat, sub)
                _log_evento(a.grupo, "aprendido", {"palabra": PEND["palabra"], "categoria": cat, "sub": sub})
                PEND.pop("palabra", None)
            _save_pending(a.grupo, PEND)
            _words = PEND.get("words") or []
            if _words:
                _sig = _words.pop(0)
                PEND["words"] = _words
                PEND["palabra"] = _sig
                PEND["no_aprender"] = False
                _save_pending(a.grupo, PEND)
                print(_questionnaire(_sig, monto_disp))
                sys.exit(0)
        else:
            cat2, sub2 = classify_category(text)
            if cat2:
                cat, sub = cat2, sub2
                if PEND.get("no_aprender") or not PEND.get("palabra") or PEND["palabra"] == "ese comercio":
                    PEND.pop("palabra", None)
                else:
                    _aprender(PEND["palabra"], cat, sub)
                    _log_evento(a.grupo, "aprendido", {"palabra": PEND["palabra"], "categoria": cat, "sub": sub})
                    PEND.pop("palabra", None)
                _save_pending(a.grupo, PEND)
                _words = PEND.get("words") or []
                if _words:
                    _sig = _words.pop(0)
                    PEND["words"] = _words
                    PEND["palabra"] = _sig
                    PEND["no_aprender"] = False
                    _save_pending(a.grupo, PEND)
                    print(_questionnaire(_sig, monto_disp))
                    sys.exit(0)
            else:
                _handle_entrenamiento(a, monto_num, monto_disp, PEND, text, evid, datos)
    elif cat is None:
        _handle_entrenamiento(a, monto_num, monto_disp, PEND, text, evid, datos)

    hoy = datetime.date.today()
    metodo = datos.get("metodo") or classify_pay(text)
    fecha = datos.get("fecha") or parse_fecha(text) or hoy.isoformat()
    hora = datos.get("hora") or datetime.datetime.now().strftime("%H:%M")
    if PEND:
        fecha = PEND.get("fecha") or fecha
        hora = PEND.get("hora") or hora
        metodo = PEND.get("metodo") or metodo
        if PEND.get("evidencia"):
            evid = PEND["evidencia"]

    grupo = def_grupo
    def _phone_key(ph):
        d = re.sub(r"\D", "", ph or "")
        return d[-10:] if len(d) >= 10 else d

    PHONE_NATIONAL = {}
    for _k, _u in PHONE_USER.items():
        PHONE_NATIONAL.setdefault(_phone_key(_k), _u)

    LID_TO_NATIONAL = {}
    try:
        import json as _json
        _session = os.path.join(
            os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"),
            "whatsapp", "session")
        for _f in os.listdir(_session):
            _mm = None
            import re as _re
            _mm = _re.match(r"lid-mapping-(\d+)\.json$", _f)
            if not _mm:
                continue
            _path = os.path.join(_session, _f)
            try:
                _lid = _json.load(open(_path, encoding="utf-8"))
                _lid = str(_lid)
            except Exception:
                continue
            if not _lid:
                continue
            _native = _phone_key(_mm.group(1))
            LID_TO_NATIONAL[_lid] = _native
            LID_TO_NATIONAL[_phone_key(_lid)] = _native
    except Exception:
        pass

    usuario = def_usuario
    quien = None
    if a.sender:
        _full = re.sub(r"\D", "", a.sender or "")
        quien = _phone_key(a.sender)
        _nav = LID_TO_NATIONAL.get(_full) or LID_TO_NATIONAL.get(quien)
        if _nav:
            quien = _nav
        usuario = PHONE_NATIONAL.get(quien, usuario)
    if is_shared(text):
        usuario = "U3"
    tipo = "Ingreso" if any(w in text.lower() for w in INCOME_HINTS) else "Gasto"

    user_text = (a.texto or "").strip()
    if monto_vino_de_pend and PEND and PEND.get("descripcion"):
        user_text = str(PEND["descripcion"])
    descripcion = ""
    if user_text:
        descripcion = clean_desc(user_text)
        if len(descripcion) < 4:
            descripcion = user_text
    else:
        descripcion = datos.get("descripcion") or ""
        _toks = [ln for ln in text.splitlines() if ln.strip()]
        for _ln in _toks:
            _cl = re.sub(r"[^A-Za-zA-Za-zÁáéÑñÜü ]", "", _ln)
            _cl = " ".join(_cl.split())
            if len(_cl) >= 4:
                descripcion = _cl
                break
    descripcion = (descripcion or "").strip()[:120]
    if not descripcion:
        print("⚠️ Necesito la descripción del gasto. ¿Qué compraste? Ej: 'mercado en éxito'. Puedes adjuntar la foto o escribirlo.")
        sys.exit(0)

    srv = get_srv()
    refs = read_refs(srv)

    # validar referencia categoria para el grupo
    ok_cat = any(c["cat"] == cat and c["sub"] == sub and (grupo in (c["grupos"] or "").split(",")) for c in refs["categorias"])
    if not ok_cat:
        ok_cat = any(c["cat"] == cat and c["sub"] == sub for c in refs["categorias"])

    desc_orig = descripcion
    desc_norm = descripcion[:120]

    if a.dry_run:
        print("DRY-RUN:", json.dumps({
            "hoja": sid, "id": "(pendiente)", "fecha": fecha, "hora": hora,
            "grupo": grupo, "usuario": usuario, "remitente_phone": quien, "tipo": tipo, "monto": monto_disp, "descripcion": descripcion,
            "moneda": "COP", "categoria": cat, "subcategoria": sub,
            "metodo_pago": metodo, "evidencia": evid, "estado_validacion": "aprobado",
            "datos_imagen_extra": {k: v for k, v in (datos or {}).items() if v and k not in ("monto", "metodo", "descripcion", "fecha", "hora")},
            "confianza": "alta", "validacion_categoria_ok": ok_cat,
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    row_id = gen_id(srv, sid, hoy.strftime("%Y%m%d"))
    row = [row_id, fecha, hora, grupo, usuario, tipo, monto_disp, "COP", cat, sub,
           desc_orig, desc_norm, metodo, evid, "aprobado", "alta"]

    def _next_data_row():
        r = srv.spreadsheets().values().get(
            spreadsheetId=sid, range="Hoja 1!A2:A50000").execute()
        vals = r.get("values", [])
        last = 1
        for i, v in enumerate(vals, start=2):
            if v and str(v[0]).strip():
                last = i
        return last + 1

    target_row = _next_data_row()
    rng = "Hoja 1!A%d:P%d" % (target_row, target_row)

    import time as _time
    last_err = None
    for _attempt in range(4):
        try:
            srv.spreadsheets().values().update(
                spreadsheetId=sid, range=rng,
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            last_err = None
            break
        except Exception as _e:
            last_err = _e
            _time.sleep(1.5 * (_attempt + 1))
    if last_err is not None:
        raise last_err
    _clear_pending(a.grupo)

    _prod = extract_products((a.texto or "") + " " + descripcion)
    if _prod and tipo != "Ingreso":
        _record_inventario(a.grupo, _prod, fecha, monto_num, cat)

    mega_url = None
    if evid and os.path.exists(os.path.join(HERMES, "cache", "images", evid)):
        mega_url = _subir_mega(os.path.join(HERMES, "cache", "images", evid), a.grupo, "%s__%s" % (row_id, evid))

    nota_uso = (" · compartido 50/50 (U3)" if usuario == "U3" else " · usuario " + usuario)
    palabra_ap = _unknown_word((a.texto or "") + " " + descripcion)
    if palabra_ap and not (PEND and PEND.get("palabra")):
        if _lookup_aprendizaje(palabra_ap):
            print("✅ Ya aprendí que %s = %s." % (palabra_ap, sub))
    print("Gasto registrado: %s %s en %s (%s · %s)" % (monto_disp, "ingreso" if tipo == "Ingreso" else "gasto", cat, sub, metodo))
    print("id: %s · hoja: %s · metodo: %s%s" % (row_id, "personal" if sid == PERSONAL_SHEET else ("andrea" if sid == ANDREA_SHEET else "1:1"), metodo, nota_uso))
    if mega_url:
        print("📎 Recibo subido a la bóveda: %s" % mega_url)
    elif evid:
        print("📎 Recibo guardado localmente: %s" % evid)
    if tipo != "Ingreso":
        _al = _check_presupuesto(srv, sid, a.grupo, cat, monto_num)
        if _al:
            print(_al)
    _log_evento(a.grupo, "registro", {"id": row_id, "monto": monto_disp, "categoria": cat, "subcategoria": sub,
                                      "descripcion": descripcion, "metodo": metodo, "evidencia": evid,
                                      "mega_url": mega_url or "", "productos": _prod})
    if not _prod and tipo != "Ingreso":
        palabra_ap2 = _unknown_word((a.texto or "") + " " + descripcion)
        if palabra_ap2 and not _lookup_aprendizaje(palabra_ap2) and not (PEND and PEND.get("palabra")):
            _save_prodpend(a.grupo, {"palabra": palabra_ap2, "fecha": fecha, "monto": str(monto_num), "cat": cat})
            print("🔎 No reconozco \"%s\" como producto. Responde el nombre o responde no." % palabra_ap2)
    if quien:
        print("remitente: %s -> usuario %s" % (quien, usuario))


if __name__ == "__main__":
    main()