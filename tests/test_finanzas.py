"""Pruebas del motor deterministico de finanzas.

FakeSrv replica la superficie minima del cliente Google Sheets usada por
finanzas.sheets, para poder probar append/consulta/anulacion sin red.
El registro usa checklist: tras pedir datos, se confirma con 'si'.
"""
import datetime
import os
import re
import sys
import tempfile
import threading
from zoneinfo import ZoneInfo

import pytest

# Asegurar import del paquete (scripts/)
os.environ["FINANZAS_DATA_DIR"] = tempfile.mkdtemp(prefix="fztest_")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from finanzas.intents import Motor  # noqa: E402
from finanzas import config, storage, sheets  # noqa: E402
from finanzas.entities import extraer_fecha, extraer_hora, resolver_descripcion  # noqa: E402
from finanzas.normalize import parse_monto, analizar_monto  # noqa: E402
import finanzas.rules as _rules_mod  # noqa: E402


# --------------------------------------------------------------------------
# FakeSrv
# --------------------------------------------------------------------------
class FakeValues:
    def __init__(self, srv):
        self.srv = srv

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        return _FakeCall(self.srv.append, range, insertDataOption, body["values"][0])

    def get(self, spreadsheetId, range):
        return _FakeCall(self.srv.get, range)

    def update(self, spreadsheetId, range, valueInputOption, body):
        return _FakeCall(self.srv.update, range, body["values"][0])


class _FakeCall:
    def __init__(self, fn, *a):
        self._fn, self._a = fn, a

    def execute(self):
        return self._fn(*self._a)


class FakeSpreadsheet:
    def __init__(self, srv):
        self._srv = srv

    def values(self):
        return FakeValues(self._srv)


class FakeSrv:
    def __init__(self):
        self.rows = [["id", "fecha", "hora", "grupo", "usuario", "tipo", "monto",
                      "moneda", "categoria", "subcategoria", "desc_orig",
                      "desc_norm", "metodo", "evidencia", "estado", "prioridad"]]
        self.lock = threading.Lock()
        self.anuladas = 0
        self.append_calls = []       # evidencia de uso de append + INSERT_ROWS
        self.get_calls = 0           # lecturas masivas (no deben usarse para la fila)
        self.sin_updated_range = False
        self.fallar_lecturas = False  # simula error transitorio de red al leer

    def spreadsheets(self):
        return FakeSpreadsheet(self)

    def append(self, range, insertDataOption, row):
        with self.lock:
            self.append_calls.append({"range": range, "insertDataOption": insertDataOption})
            if self.sin_updated_range:
                return {}
            self.rows.append(list(row))
            return {"updates": {"updatedRange": "Hoja 1!A%d:P%d" % (len(self.rows), len(self.rows))}}

    def get(self, rng):
        with self.lock:
            self.get_calls += 1
            if self.fallar_lecturas:
                raise ConnectionError("red caida (simulado)")
            m = re.match(r"Hoja 1!([A-Z]+)(\d+):([A-Z]+)(\d+)", rng or "")
            if m and int(m.group(2)) == int(m.group(4)):
                # lectura de UNA fila real (fila N de la hoja == rows[N-1])
                li = int(m.group(2)) - 1
                if 1 <= li < len(self.rows):
                    return {"values": [list(self.rows[li])]}
                return {"values": []}
            return {"values": [r[:] for r in self.rows[1:]]}

    def update(self, rng, row):
        with self.lock:
            import re
            m = re.match(r"Hoja 1!([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
            if not m:
                return {"updatedCells": 0}
            row_idx = int(m.group(2))
            li = row_idx - 1
            col0 = self._col_idx(m.group(1))
            if 1 <= li < len(self.rows):
                cur = list(self.rows[li])
                for off, val in enumerate(row):
                    if col0 + off < len(cur):
                        cur[col0 + off] = val
                self.rows[li] = cur
                self.anuladas += 1
            return {"updatedCells": 1}

    def _col_idx(self, letters):
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
        return n - 1

    def buscar_ultima(self):
        for i in range(len(self.rows) - 1, 0, -1):
            if sheets.fila_activa(self.rows[i]):
                return i
        return None


@pytest.fixture
def motor(tmp_path):
    storage.stores.clear()
    _rules_mod._rules = None
    import shutil
    if os.path.exists(config.DATA_DIR):
        for fn in os.listdir(config.DATA_DIR):
            p = os.path.join(config.DATA_DIR, fn)
            if os.path.isfile(p):
                os.remove(p)
            elif fn == "estados":
                shutil.rmtree(p, ignore_errors=True)
    srv = FakeSrv()
    m = Motor(srv=srv)
    m.srv = srv
    return m, srv


def _unpack(msgs):
    return "\n".join(str(x) for x in msgs if x)


def _historial_lines():
    p = os.path.join(config.DATA_DIR, "historial.jsonl")
    if not os.path.exists(p):
        return 0
    return sum(1 for _ in open(p, encoding="utf-8"))


def _registrar_y_confirmar(m, grupo, sender, texto):
    """Registra con checklist: primer envio pide confirmar, luego 'si'."""
    r1 = m.procesar(grupo, sender, texto, dry_run=False)
    r2 = m.procesar(grupo, sender, "si", dry_run=False)
    return _unpack(r1), _unpack(r2)


# ==========================================================================
# 1. pague 5 mil de mercado por Nequi
# ==========================================================================
def test_pague_5mil_mercado_nequi(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572", "pagué 5 mil de mercado por nequi",
                   dry_run=True)
    s = _unpack(r)
    assert "Confirma el registro" in s
    assert "$5,000" in s
    assert "Alimentacion" in s
    assert "Mercado / plaza" in s
    # confirmar y escribir real
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572",
                                          "pagué 5 mil de mercado por nequi")
    assert "✅" in rfinal
    last = srv.rows[-1]
    assert last[5] == "Gasto"
    assert last[6] == "$5,000"
    assert last[8] == "Alimentacion"


# ==========================================================================
# 2. recibi 500 mil de salario -> ingreso
# ==========================================================================
def test_recibi_500_salario(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572", "recibí 500 mil de salario",
                   dry_run=True)
    s = _unpack(r)
    assert "Confirma el registro" in s
    assert "$500,000" in s
    assert "Ingreso" in s
    assert "Salario / nomina" in s


# ==========================================================================
# 3. compramos 80 mil de mercado a medias (hogar) -> U3
# ==========================================================================
def test_compartido_hogar(motor):
    m, srv = motor
    r = m.procesar("hogar", "3002084572", "compramos 80 mil de mercado a medias",
                   dry_run=True)
    s = _unpack(r)
    assert "Confirma el registro" in s
    assert "$80,000" in s
    assert "U3" in s            # mitad
    _r1, rfinal = _registrar_y_confirmar(m, "hogar", "3002084572",
                                          "compramos 80 mil de mercado a medias")
    assert "U3" in rfinal
    last = srv.rows[-1]
    assert last[4] == "U3"
    assert last[6] == "$80,000"
    assert len(last) == 16


# ==========================================================================
# 4. Xaran: pregunta categoria, luego global en los 3 grupos
# ==========================================================================
def test_aprendizaje_global_xaran(motor):
    m, srv = motor
    r1 = m.procesar("hogar", "3002084572", "pagué 25 mil en Xaran")
    s1 = _unpack(r1)
    assert "No conozco" in s1
    # responder la categoria -> checklist
    r2 = m.procesar("hogar", "3002084572", "medicamentos")
    assert "Confirma el registro" in _unpack(r2)
    # aprender ocurre al confirmar (nunca en dry-run)
    m.procesar("hogar", "3002084572", "si")
    ap = storage.get_aprendizajes()
    hit = ap.get("xaran")
    assert hit and hit.get("categoria") == "Salud", hit
    for g, sender in (("personal", "3002084572"), ("andrea", "3147359270")):
        r = m.procesar(g, sender, "pagué 10 mil en xaran", dry_run=True)
        s = _unpack(r)
        assert "No conozco" not in s, (g, s)
        assert "Salud" in s, (g, s)


# ==========================================================================
# 5. U2 no puede escribir en personal
# ==========================================================================
def test_u2_no_escribe_en_personal(motor):
    m, srv = motor
    r = m.procesar("personal", "3147359270", "pagué 5000 mercado", dry_run=True)
    assert "No tienes permiso" in _unpack(r)


# ==========================================================================
# 6. No-admin no puede revisar / aprender
# ==========================================================================
def test_no_admin_revisar(motor):
    m, srv = motor
    r = m.procesar("hogar", "3147359270", "revisar", dry_run=True)
    assert "administrador" in _unpack(r)
    r2 = m.procesar("hogar", "3147359270", "xaran = medicamentos", dry_run=True)
    assert "administrador" in _unpack(r2)


# ==========================================================================
# 7. Estados pendientes simultaneos en Hogar no se cruzan
# ==========================================================================
def test_estados_no_se_cruzan(motor):
    m, srv = motor
    m.procesar("hogar", "3002084572", "pagué 25 mil en Xaran")
    m.procesar("hogar", "3147359270", "pagué 30 mil en Fulin")
    e1 = m.dlg.pendiente("hogar", "3002084572")
    e2 = m.dlg.pendiente("hogar", "3147359270")
    assert e1 is not None and e2 is not None
    assert e1["nombre_pendiente"] != e2["nombre_pendiente"]
    # U2 responde la categoria; no toca el estado de U1
    m.procesar("hogar", "3147359270", "medicamentos")
    assert m.dlg.pendiente("hogar", "3002084572") is not None


# ==========================================================================
# 8. A inicia borrado, B responde 'si' -> NO se ejecuta
# ==========================================================================
def test_borrado_cruzado_no_ejecuta(motor):
    m, srv = motor
    _registrar_y_confirmar(m, "hogar", "3002084572", "pagué 5000 mercado")
    filas_before = len(srv.rows)
    rA = m.procesar("hogar", "3002084572", "borra la última entrada")
    assert any("¿Confirma" in str(x) for x in rA)
    m.procesar("hogar", "3147359270", "si")
    assert m.dlg.pendiente("hogar", "3002084572") is not None
    assert len(srv.rows) == filas_before
    assert srv.anuladas == 0


# ==========================================================================
# 9. Registros concurrentes no reutilizan id ni sobrescriben fila
# ==========================================================================
def test_registros_concurrentes_id_unicos(motor):
    m, srv = motor
    fichas = [("pagué %d mil de mercado" % n) for n in (10, 20, 30)]

    def trabajador(txt):
        m.procesar("hogar", "3002084572", txt, dry_run=False)
        m.procesar("hogar", "3002084572", "si", dry_run=False)

    threads = [threading.Thread(target=trabajador, args=(f,)) for f in fichas]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [r[0] for r in srv.rows[1:] if r and r[0]]
    assert len(set(ids)) == len(ids) > 0
    assert len(srv.rows) - 1 == len(ids)


# ==========================================================================
# 10. Anuladas no cuentan en resumen/presupuesto/frecuencia
# ==========================================================================
def test_anuladas_excluidas(motor):
    m, srv = motor
    _registrar_y_confirmar(m, "hogar", "3002084572", "pagué 20000 mercado")
    _registrar_y_confirmar(m, "hogar", "3002084572", "pagué 30000 mercado")
    m.procesar("hogar", "3002084572", "borra la última entrada")
    m.procesar("hogar", "3002084572", "si")
    assert srv.anuladas == 1
    total = m._gasto_mensual_cat("hogar", srv, "Alimentacion")
    assert total == 20000


# ==========================================================================
# 11. Autorización LID / JID / teléfono
# ==========================================================================
U1_SENDERS = [
    "53201961234666@lid",          # LID de U1
    "3002084572",                  # teléfono puro de U1
    "573002084572",                # teléfono con prefijo 57
    "573002084572@s.whatsapp.net", # JID telefónico
    "573002084572:10@s.whatsapp.net",  # JID con sufijo de dispositivo
    "+573002084572",               # con signo +
    "wa.me/573002084572",          # link wa.me
]
U2_SENDERS = [
    "5063900668131@lid",           # LID de U2
    "3147359270",                  # teléfono puro de U2
    "573147359270",                # teléfono con prefijo 57
]


@pytest.mark.parametrize("sender", U1_SENDERS)
def test_u1_lid_y_tel_permitido_personal_hogar(motor, sender):
    m, srv = motor
    for grupo in ("personal", "hogar"):
        # ruta texto (dry-run)
        r = m.procesar(grupo, sender, "pagué 5 mil de mercado", dry_run=True)
        assert "Confirma el registro" in _unpack(r), (grupo, sender, r)
        # ruta imagen/dry-run usa exactamente el mismo sender
        r2 = m.procesar(grupo, sender, "pagué 5 mil de mercado",
                        imagen="/tmp/foto.jpg", evidencia="foto.jpg", dry_run=True)
        assert "Confirma el registro" in _unpack(r2), (grupo, sender, r2)


@pytest.mark.parametrize("sender", U2_SENDERS)
def test_u2_lid_y_tel_permitido_andrea_hogar(motor, sender):
    m, srv = motor
    for grupo in ("andrea", "hogar"):
        r = m.procesar(grupo, sender, "pagué 5 mil de mercado", dry_run=True)
        assert "Confirma el registro" in _unpack(r), (grupo, sender, r)
        r2 = m.procesar(grupo, sender, "pagué 5 mil de mercado",
                        imagen="/tmp/foto.jpg", evidencia="foto.jpg", dry_run=True)
        assert "Confirma el registro" in _unpack(r2), (grupo, sender, r2)


@pytest.mark.parametrize("grupo,texto", [
    ("andrea", "pagué 5 mil de mercado"),   # registro
    ("andrea", "gastos de agosto"),         # consulta
    ("andrea", "presupuesto de comida 600 mil"),
    ("andrea", "borra la última entrada"),
])
def test_u1_denegado_en_andrea(motor, grupo, texto):
    m, srv = motor
    r = m.procesar(grupo, "53201961234666@lid", texto, dry_run=True)
    assert "No tienes permiso" in _unpack(r), (grupo, r)
    assert m.dlg.pendiente(grupo, "53201961234666@lid") is None


@pytest.mark.parametrize("grupo,texto", [
    ("personal", "pagué 5 mil de mercado"),  # registro
    ("personal", "cuánto gasté"),            # consulta
    ("personal", "presupuesto de comida 600 mil"),
    ("personal", "borra la última entrada"),
])
def test_u2_denegado_en_personal(motor, grupo, texto):
    m, srv = motor
    r = m.procesar(grupo, "5063900668131@lid", texto, dry_run=True)
    assert "No tienes permiso" in _unpack(r), (grupo, r)
    assert m.dlg.pendiente(grupo, "5063900668131@lid") is None


# LID de tercero (100360517628103 -> 573104223047) y remitentes desconocidos:
# nunca deben resolver como U1/U2 ni crear estado ni escribir.
@pytest.mark.parametrize("sender", [
    "100360517628103@lid",   # LID de tercero (antes mapeado a U1 por error)
    "573104223047",          # teléfono de tercero (antes mapeado a U1)
    "3104223047",            # key del teléfono de tercero
    "9999999999",            # desconocido
    "573000000000@s.whatsapp.net",
    "123456789012345@lid",   # LID no registrado
])
@pytest.mark.parametrize("grupo", ["personal", "hogar", "andrea"])
def test_remitente_ajeno_o_desconocido_denegado(motor, sender, grupo):
    m, srv = motor
    r = m.procesar(grupo, sender, "pagué 5 mil de mercado", dry_run=True)
    s = _unpack(r)
    assert "No reconozco" in s or "No tienes permiso" in s, (grupo, sender, s)
    assert m.dlg.pendiente(grupo, sender) is None   # sin estado creado
    assert len(srv.rows) == 1                       # sin escritura externa
    # misma identidad en la ruta de imagen/dry-run
    r2 = m.procesar(grupo, sender, "pagué 5 mil de mercado",
                    imagen="/tmp/foto.jpg", evidencia="foto.jpg", dry_run=True)
    s2 = _unpack(r2)
    assert "No reconozco" in s2 or "No tienes permiso" in s2, (grupo, sender, s2)
    assert m.dlg.pendiente(grupo, sender) is None
    assert len(srv.rows) == 1


def test_lid_no_se_resuelve_con_ultimos_digitos(motor):
    """Regresión: el LID de U1 jamás debe resolverse como teléfono."""
    m, _srv = motor
    # 53201961234666@lid -> últimos 10 dígitos "1961234666" (no es un teléfono)
    assert config.user_from_sender("53201961234666@lid") == "U1"
    assert config.user_from_sender("1961234666") is None
    assert config.user_from_sender("3900668131") is None


# ==========================================================================
# 12. Incidente OCR: recibo Nu -> Nequi (Llave/NIT/comprobante + $50.000,00)
# ==========================================================================
RECIBO_OCR = """Comprobante de transferencia
07 AGO 2026 - 05:59:08
Monto total $50.000,00
Monto $50.000,00
Impuesto 4x1,000 $0,00
Vía Bre-B
Numero de comprobante 790501A7SDAST71OS1O92ITTAOISIO40255
Para Nombre LAURA RINCON
Entidad Nequi
Llave 3127702186
Estado Completada
De Nombre Esnaider Idrobo Zapata
Entidad Nu C.F. Numero de cuenta m..B341
Nucs. NIT: 901.658,107-2
Referencia interna Becó413a-a7e5-4748-a572-d24daa543e3f
Más información"""


def test_parse_monto_recibo_nequi_nunca_llave():
    """El recibo con Llave 3127702186/NIT/comprobante debe dar 50000, nunca
    la llave como monto."""
    texto = "Pago de camiseta oversize hominidx, categoría ropa\n" + RECIBO_OCR
    a = analizar_monto(texto)
    assert a["monto"] == 50000, a
    assert a["confianza"] == "alta", a
    assert 3127702186 not in [c["valor"] for c in a["candidatos"]], a
    assert parse_monto(texto) == 50000


def test_parse_monto_pague_50000_nequi():
    assert parse_monto("pagué 50.000,00 por Nequi") == 50000


def test_parse_monto_no_monetarios_son_none():
    assert parse_monto("Llave 3127702186") is None
    assert parse_monto("NIT: 901.658,107-2") is None
    assert parse_monto("Numero de comprobante 790501A7SDAST71OS1O92ITTAOISIO40255") is None
    assert parse_monto("Referencia interna Becó413a-a7e5-4748-a572-d24daa543e3f") is None
    assert parse_monto("Numero de cuenta m..B341") is None


def test_parse_monto_ambiguo_no_elige():
    a = analizar_monto("Subtotal $45.000\nIVA $5.000\nTotal $50.000")
    assert a["confianza"] == "ambiguo"
    assert a["monto"] is None


def test_parse_monto_repetido_no_ambiguo():
    a = analizar_monto("Monto total $50.000,00\nMonto $50.000,00")
    assert a["confianza"] == "alta"
    assert a["monto"] == 50000


def test_parse_monto_diez_digitos_sin_moneda_nunca():
    assert parse_monto("transferí 3127702186") is None
    assert parse_monto("mi numero es 3002084572") is None


# ==========================================================================
# 13. Idempotencia: mismo operation_id no duplica fila/historial/inventario
# ==========================================================================
def test_append_idempotente_no_duplica(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅" in rfinal
    hist1 = _historial_lines()
    n_filas = len(srv.rows)
    n_inv = len(storage.get_inventario().all())
    # reintento: mismo texto/monto/usuario -> mismo op_key -> ya registrado
    _a, r2 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "Ya estaba registrado" in r2, r2
    assert len(srv.rows) == n_filas
    assert _historial_lines() == hist1
    assert len(storage.get_inventario().all()) == n_inv


# ==========================================================================
# 14. Dry-run: JSON de diagnóstico, sin ninguna escritura
# ==========================================================================
def test_dry_run_foto_no_escribe_nada(motor):
    m, srv = motor
    ap_before = storage.get_aprendizajes().to_dict()
    texto = "Pago de camiseta oversize hominidx, categoría ropa\n" + RECIBO_OCR
    r = m.procesar("personal", "3002084572", texto,
                   imagen="/tmp/recibo.jpg", evidencia="recibo.jpg", dry_run=True)
    s = _unpack(r)
    assert "Confirma el registro" in s
    assert "$50,000" in s
    assert '"decision": "checklist"' in s
    assert '"confianza_monto": "alta"' in s
    assert len(srv.rows) == 1                          # no Sheets
    assert storage.get_ledger().data == {}             # no ledger
    assert storage.get_aprendizajes().to_dict() == ap_before  # no aprendizaje
    assert storage.get_inventario().all() == []        # no inventario
    assert m.dlg.pendiente("personal", "3002084572") is None  # no estado
    assert _historial_lines() == 0                     # no historial


def test_dry_run_json_incluye_confianza(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572", "pagué 5 mil de mercado por nequi", dry_run=True)
    s = _unpack(r)
    assert '"tipo": "Gasto"' in s
    assert '"confianza_monto": "media"' in s
    assert '"decision": "checklist"' in s
    assert '"categoria": "Alimentacion"' in s


def test_dry_run_no_crea_marcador(motor):
    m, srv = motor
    os.environ["FINANZAS_MARKER_DIR"] = config.DATA_DIR
    try:
        m.procesar("personal", "3002084572", "pagué 5 mil de mercado", dry_run=True)
        assert not os.path.exists(os.path.join(config.DATA_DIR, "gasto_pendiente_personal.json"))
    finally:
        os.environ.pop("FINANZAS_MARKER_DIR", None)


def test_registro_no_dry_crea_marcador(motor):
    m, srv = motor
    os.environ["FINANZAS_MARKER_DIR"] = config.DATA_DIR
    try:
        m.procesar("personal", "3002084572", "pagué 5 mil de mercado")
        assert os.path.exists(os.path.join(config.DATA_DIR, "gasto_pendiente_personal.json"))
    finally:
        os.environ.pop("FINANZAS_MARKER_DIR", None)


# ==========================================================================
# 15. Foto con monto ambiguo -> pide confirmación, no escribe nada
# ==========================================================================
def test_foto_ambiguo_pide_monto_sin_escribir(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572",
                   "Subtotal $45.000\nIVA $5.000\nTotal $50.000",
                   imagen="/tmp/r.jpg", evidencia="r.jpg", dry_run=True)
    s = _unpack(r)
    assert "No identifiqué con certeza" in s
    assert "$45,000" in s
    assert '"decision": "pedir_monto"' in s
    assert len(srv.rows) == 1
    assert storage.get_ledger().data == {}
    assert m.dlg.pendiente("personal", "3002084572") is None


def test_foto_ambiguo_pide_monto_y_usuario_lo_corrige(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572",
                   "Subtotal $45.000\nIVA $5.000\nTotal $50.000",
                   imagen="/tmp/r.jpg", evidencia="r.jpg")
    s = _unpack(r)
    assert "No identifiqué con certeza" in s
    assert len(srv.rows) == 1
    assert storage.get_ledger().data == {}
    assert m.dlg.pendiente("personal", "3002084572") is not None
    # el usuario indica el monto real -> falta categoria -> se pregunta
    r2 = _unpack(m.procesar("personal", "3002084572", "50000"))
    assert "No conozco" in r2 or "¿A qué categoría" in r2, r2
    r3 = _unpack(m.procesar("personal", "3002084572", "mercado"))
    # sin caption ni línea OCR segura -> también se pide la descripción
    assert "descripción" in r3.lower() or "descripcion" in r3.lower(), r3
    r4 = _unpack(m.procesar("personal", "3002084572", "mercado de la quincena"))
    assert "Confirma el registro" in r4
    assert "$50,000" in r4
    r5 = _unpack(m.procesar("personal", "3002084572", "si"))
    assert "✅" in r5
    assert len(srv.rows) == 2
    assert srv.rows[-1][6] == "$50,000"
    assert "quincena" in srv.rows[-1][10]


# ==========================================================================
# 16. Fecha/hora de recibos: prioridad OCR / mensaje Bogotá
# ==========================================================================
_TZ = ZoneInfo("America/Bogota")
# 2026-08-09 14:59:00 America/Bogota (== 19:59 UTC si se usara UTC)
MSG_TS = int(datetime.datetime(2026, 8, 9, 14, 59, tzinfo=_TZ).timestamp())


def test_recibo_fecha_hora_completas_ocr(motor):
    """OCR '9 agosto 2026 1:31 p. m.' -> 2026-08-09 13:31, origen recibo."""
    m, srv = motor
    texto = "Pago de camiseta $50.000,00\n9 agosto 2026 1:31 p. m."
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    assert "2026-08-09 13:31" in s, s
    assert '"origen_fecha_hora": "recibo"' in s, s
    assert '"monto": 50000' in s, s


def test_recibo_solo_fecha_usa_hora_mensaje(motor):
    """Fecha del recibo + hora del timestamp WhatsApp (Bogotá)."""
    m, srv = motor
    texto = "Pago de camiseta $50.000,00\nFecha 9 agosto 2026"
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    assert "2026-08-09 14:59" in s, s
    assert '"origen_fecha_hora": "recibo"' in s, s


def test_recibo_solo_hora_usa_fecha_mensaje(motor):
    """Hora del recibo + fecha del timestamp WhatsApp (Bogotá)."""
    m, srv = motor
    texto = "Pago de camiseta $50.000,00\nHora 1:31 p. m."
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    assert "2026-08-09 13:31" in s, s
    assert '"origen_fecha_hora": "recibo"' in s, s


def test_ocr_llave_nit_cuenta_referencia_no_genera_fecha(motor):
    """Llave/NIT/cuenta/referencia/UUID/año aislado nunca generan fecha/hora."""
    m, srv = motor
    texto = ("Pago de camiseta $50.000,00\n"
             "Llave 3127702186\nNIT: 901.658,107-2\nCuenta m..B341\n"
             "Referencia Becó413a-a7e5-4748-a572-d24daa543e3f\nAño 2026")
    assert extraer_fecha(texto) is None, extraer_fecha(texto)
    assert extraer_hora(texto) is None, extraer_hora(texto)
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    # sin fecha/hora confiable -> timestamp WhatsApp Bogotá, sin valores raros
    assert '"origen_fecha_hora": "whatsapp_bogota"' in s, s
    assert '"origen_fecha_hora": "recibo"' not in s, s


def test_texto_sin_recibo_usa_bogota_no_utc(motor):
    """Mensaje de texto sin recibo: America/Bogota, nunca UTC."""
    m, srv = motor
    r = m.procesar("personal", "3002084572", "pagué 5 mil de mercado",
                   dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    assert "2026-08-09 14:59 (whatsapp_bogota)" in s, s
    assert "19:59" not in s   # si se usara UTC mostraría 19:59


def test_recibo_incidente_fecha_origen(motor):
    """Regresión: el OCR del incidente (07 AGO 2026 - 05:59:08) es la fecha/hora."""
    m, srv = motor
    texto = "Pago de camiseta oversize hominidx, categoría ropa\n" + RECIBO_OCR
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS)
    s = _unpack(r)
    assert "2026-08-07 05:59 (recibo)" in s, s
    assert "$50,000" in s


# ==========================================================================
# 17. Campo 4: edición de fecha/hora
# ==========================================================================
def test_campo4_acepta_fecha_valida(motor):
    m, srv = motor
    r1 = _unpack(m.procesar("personal", "3002084572", "pagué 5000 mercado"))
    assert "Confirma el registro" in r1
    r2 = _unpack(m.procesar("personal", "3002084572", "4"))
    assert "AAAA-MM-DD HH:MM" in r2
    r3 = _unpack(m.procesar("personal", "3002084572", "2026-08-09 13:31"))
    assert "2026-08-09 13:31 (corregido)" in r3, r3
    r4 = _unpack(m.procesar("personal", "3002084572", "si"))
    assert "✅ Registrado" in r4
    last = srv.rows[-1]
    assert last[1] == "2026-08-09"
    assert last[2] == "13:31"


def test_campo4_rechaza_invalida_y_parciales(motor):
    m, srv = motor
    m.procesar("personal", "3002084572", "pagué 5000 mercado")
    m.procesar("personal", "3002084572", "4")
    for bad in ("2026-13-40 25:99", "2026-08-09", "13:31", "2026-08-09 13",
                "hoy", "2026-08-09 13:31 y algo"):
        s = _unpack(m.procesar("personal", "3002084572", bad))
        assert "no válida" in s, (bad, s)
    e = m.dlg.pendiente("personal", "3002084572")
    assert e and e.get("pendiente") == "fecha"


def test_campo4_cancelar_solo_limpia_sender(motor):
    m, srv = motor
    m.procesar("hogar", "3002084572", "pagué 5000 mercado")
    m.procesar("hogar", "3002084572", "4")
    m.procesar("hogar", "3147359270", "pagué 8000 mercado")
    m.procesar("hogar", "3147359270", "4")
    r = _unpack(m.procesar("hogar", "3002084572", "cancelar"))
    assert "Cancelado" in r
    assert m.dlg.pendiente("hogar", "3002084572") is None
    e2 = m.dlg.pendiente("hogar", "3147359270")
    assert e2 is not None and e2.get("pendiente") == "fecha"


def test_campo4_cancelar_con_salir(motor):
    m, srv = motor
    m.procesar("personal", "3002084572", "pagué 5000 mercado")
    m.procesar("personal", "3002084572", "4")
    r = _unpack(m.procesar("personal", "3002084572", "salir"))
    assert "Cancelado" in r
    assert m.dlg.pendiente("personal", "3002084572") is None
    assert len(srv.rows) == 1


# ==========================================================================
# 18. Google Sheets: append a la fila final (INSERT_ROWS) + updatedRange
# ==========================================================================
def test_append_usa_insert_rows_y_registra_fila(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅ Registrado" in rfinal
    assert srv.append_calls
    call = srv.append_calls[0]
    assert call["insertDataOption"] == "INSERT_ROWS"
    assert call["range"] == "Hoja 1!A:P"
    # la fila real devuelta por updatedRange se guarda en el ledger
    filas = [v.get("fila") for v in storage.get_ledger().data.values()]
    assert filas == [2], filas
    # no se leyó la hoja para calcular la fila
    assert srv.get_calls == 0


def test_append_dos_inserciones_filas_finales_distintas(motor):
    m, srv = motor
    _r1, r1 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    _r2, r2 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 8000 mercado")
    assert "✅" in r1 and "✅" in r2
    filas = [v.get("fila") for v in storage.get_ledger().data.values()]
    assert filas == [2, 3], filas
    assert len(srv.rows) == 3


def test_append_sin_updated_range_no_muestra_exito(motor):
    m, srv = motor
    srv.sin_updated_range = True
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅ Registrado" not in rfinal
    assert "No se confirmó" in rfinal
    assert len(srv.rows) == 1                # sin fila
    assert storage.get_ledger().data == {}   # sin claim en ledger
    assert srv.append_calls[0]["insertDataOption"] == "INSERT_ROWS"


# ==========================================================================
# 19. Descripción segura: caption limpio, nunca OCR completo
# ==========================================================================
def _diag(msgs):
    """Extrae y parsea el JSON del mensaje DRY-RUN de la respuesta."""
    import json as _json
    for x in msgs:
        s = str(x or "")
        if s.startswith("DRY-RUN "):
            return _json.loads(s[len("DRY-RUN "):])
    return None


def test_resolver_descripcion_prefiere_caption_limpio():
    """Caption 'Pago de camiseta oversize hominidx, categoría ropa' -> limpio."""
    cap = "Pago de camiseta oversize hominidx, categoría ropa"
    desc, origen = resolver_descripcion(cap, RECIBO_OCR)
    assert desc == "Camiseta oversize Hominidx", desc
    assert origen == "caption", origen


def test_resolver_descripcion_nunca_usa_ocr_tecnico():
    """Recibo con solo campos técnicos -> None (pedir descripción)."""
    desc, origen = resolver_descripcion(None, RECIBO_OCR)
    assert desc is None and origen == "pendiente", (desc, origen)


def test_resolver_descripcion_linea_ocr_segura():
    ocr = "Comprobante de transferencia\nMonto $50.000,00\nCamiseta oversize\nLlave 3127702186"
    desc, origen = resolver_descripcion(None, ocr)
    assert desc == "Camiseta oversize", desc
    assert origen == "ocr", origen


def test_recibo_real_dryrun_campos_exactos(motor):
    """Recibo Nu -> Nequi con caption: monto 50000, descripción caption limpia,
    fecha/hora del recibo, y cero texto OCR técnico en la descripción."""
    m, srv = motor
    caption = "Pago de camiseta oversize hominidx, categoría ropa"
    texto = caption + "\n" + RECIBO_OCR
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS,
                   caption=caption, ocr_text=RECIBO_OCR)
    s = _unpack(r)
    d = _diag(r)
    assert d is not None, s
    assert d["monto"] == 50000, d
    assert d["confianza_monto"] == "alta", d
    assert d["descripcion"] == "Camiseta oversize Hominidx", d
    assert d["origen_descripcion"] == "caption", d
    assert d["fecha_hora"] == "2026-08-07 05:59", d
    assert d["origen_fecha_hora"] == "recibo", d
    assert d["decision"] == "checklist", d
    assert d["candidatos_fecha"] == ["2026-08-07"], d
    assert d["candidatos_hora"] == ["05:59"], d
    low = d["descripcion"].lower()
    for tech in ["llave", "3127702186", "nit", "cuenta", "referencia", "monto",
                 "impuesto", "entidad", "comprobante", "laura", "esnaider", "bre-b"]:
        assert tech not in low, (tech, d["descripcion"])


def test_recibo_real_ocr_tesseract_dryrun(motor):
    """El OCR REAL de tesseract del incidente también resuelve correcto."""
    m, srv = motor
    caption = "Pago de camiseta oversize hominidx, categoría ropa"
    texto = caption + "\n" + REAL_RECIBO_OCR
    r = m.procesar("personal", "3002084572", texto, imagen="/tmp/recibo.jpg",
                   evidencia="recibo.jpg", dry_run=True, ts_mensaje=MSG_TS,
                   caption=caption, ocr_text=REAL_RECIBO_OCR)
    s = _unpack(r)
    d = _diag(r)
    assert d is not None, s
    assert d["monto"] == 50000, d
    assert d["descripcion"] == "Camiseta oversize Hominidx", d
    assert d["origen_descripcion"] == "caption", d
    assert d["fecha_hora"] == "2026-08-07 05:59", d
    assert d["origen_fecha_hora"] == "recibo", d


def test_ocr_solo_campos_tecnicos_pide_descripcion(motor):
    """Recibo sin caption con OCR solo técnico: tras monto y categoría, pide
    descripción (nunca usa el OCR completo)."""
    m, srv = motor
    ocr = "Monto total $50.000,00\n07 AGO 2026 - 05:59:08\nLlave 3127702186"
    r1 = _unpack(m.procesar("personal", "3002084572", ocr, imagen="/tmp/recibo.jpg",
                            evidencia="recibo.jpg", caption=None, ocr_text=ocr))
    assert "categoría" in r1.lower() or "No conozco" in r1, r1
    # el usuario asigna la categoría -> falta la descripción -> se pregunta
    r2 = _unpack(m.procesar("personal", "3002084572", "ropa"))
    assert "descripción" in r2.lower() or "descripcion" in r2.lower(), r2
    assert m.dlg.pendiente("personal", "3002084572").get("pendiente") == "descripcion"
    # el usuario escribe la descripción -> checklist con ella
    r3 = _unpack(m.procesar("personal", "3002084572", "camiseta oversize"))
    assert "Confirma el registro" in r3, r3
    e = m.dlg.pendiente("personal", "3002084572")
    assert e["descripcion"] == "Camiseta oversize", e
    assert "llave" not in e["descripcion"].lower()
    # confirmar y verificar que la fila no lleva OCR técnico
    r4 = _unpack(m.procesar("personal", "3002084572", "si"))
    assert "✅" in r4
    fila = srv.rows[-1]
    assert fila[10] == "Camiseta oversize", fila
    assert "3127702186" not in " ".join(fila), fila


REAL_RECIBO_OCR = """Comprobante de
transferencia

07 AGO 2026 - 05:59:08

Monto total $50.000,00
Monto $50.000,00
Impuesto 4x1,000 $0,00
Vía Bre-B
Número de comprobante
790501A7SDAST71OS1O92ITTAOISIO40255

Para

Nombre LAURA RINCON
Entidad Nequi
Llave 3127702186
Estado Completada
De

Nombre Esnaider Idrobo Zapata
Entidad Nu C.F.
Número de cuenta m..B341
Nucs.

NIT: 901.658,107-2

Referencia interna
Becó413a-a7e5-4748-a572-
d24daa543e3f

Más información >"""


# ==========================================================================
# 20. PARTE A — Etiquetas G1/G2/G3 y rol de administración
# ==========================================================================
def test_ayuda_incluye_etiquetas_g1_g2_g3(motor):
    m, _srv = motor
    s = _unpack(m.procesar("personal", "3002084572", "ayuda"))
    assert "Finanzas personales" in s and "G1" in s, s
    assert "Finanzas del hogar" in s and "G2" in s, s
    assert "Administración privada" in s and "G3" in s, s
    assert "NO registra transacciones" in s, s


def test_claves_grupos_siguen_siendo_internas(motor):
    assert set(config.GROUPS) == {"personal", "hogar", "andrea"}
    for k in config.GROUPS:
        assert k in config.GROUP_LABELS, k


def test_registro_personal_etiqueta_g1(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572",
                                          "pagué 5000 mercado")
    assert "G1" in rfinal, rfinal
    assert "Finanzas personales" in rfinal, rfinal
    assert "✅ Registrado" in rfinal


def test_registro_hogar_etiqueta_g2(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "hogar", "3002084572",
                                          "compramos 80 mil de mercado a medias")
    assert "G2" in rfinal, rfinal
    assert "Finanzas del hogar" in rfinal, rfinal


def test_registro_andrea_sin_codigo_g(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "andrea", "3147359270",
                                          "pagué 5000 mercado")
    assert "Finanzas de Andrea" in rfinal, rfinal
    assert "G1" not in rfinal, rfinal
    assert "G2" not in rfinal, rfinal


def test_admin_revisar_encabezado_g3(motor):
    m, _srv = motor
    r = m.procesar("personal", "3002084572", "revisar")
    assert _unpack(r).startswith("🔐 Administración privada (G3)")


def test_admin_aprender_encabezado_g3(motor):
    m, _srv = motor
    r = m.procesar("personal", "3002084572", "farmacity = medicamentos")
    s = _unpack(r)
    assert s.startswith("🔐 Administración privada (G3)"), s
    assert "Aprendí" in s, s
    ap = storage.get_aprendizajes()
    assert ap.get("farmacity") and ap.get("farmacity")["categoria"] == "Salud"


def test_no_admin_sigue_bloqueado(motor):
    m, _srv = motor
    for cmd in ("revisar", "farmacity = medicamentos"):
        s = _unpack(m.procesar("hogar", "3147359270", cmd))
        assert "administrador" in s, (cmd, s)


def test_cli_grupo_solo_personal_hogar_andrea(motor):
    import subprocess
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
    env = dict(os.environ, FINANZAS_DATA_DIR=config.DATA_DIR)
    for bad in ("G1", "g1", "admin"):
        r = subprocess.run([sys.executable, os.path.join(base, "gasto.py"),
                            "--grupo", bad, "--texto", "pague 5000", "--dry-run"],
                           capture_output=True, text=True, env=env)
        assert r.returncode != 0, bad
        assert "invalid choice" in r.stderr, (bad, r.stderr)
    for good in ("personal", "hogar", "andrea"):
        r = subprocess.run([sys.executable, os.path.join(base, "gasto.py"),
                            "--grupo", good, "--texto", "pague 5000 mercado",
                            "--dry-run"], capture_output=True, text=True, env=env)
        assert r.returncode == 0, (good, r.stderr)


# ==========================================================================
# 21. PARTE B — OCR dígito antepuesto
# ==========================================================================
def test_analizar_monto_digito_antepuesto_ambiguo():
    """$57.500 (fuerte) + 7.500 (etiqueta): el $ NO permite autoconfiar el largo."""
    a = analizar_monto("Monto $57.500\nMonto 7.500")
    assert a["confianza"] == "ambiguo", a
    assert a["monto"] is None, a
    assert a["motivo"] == "digito_antepuesto", a
    vals = {c["valor"] for c in a["candidatos"]}
    assert vals == {7500, 57500}, a


def test_analizar_monto_digito_antepuesto_52800_vs_2800():
    a = analizar_monto("Monto 52.800\nMonto 2.800")
    assert a["confianza"] == "ambiguo", a
    assert a["monto"] is None, a
    vals = {c["valor"] for c in a["candidatos"]}
    assert vals == {52800, 2800}, a


def test_analizar_monto_unico_sin_ambiguedad_se_mantiene():
    """Caso normal: candidato único de recibo no se ve afectado."""
    a = analizar_monto("Monto total $50.000,00\nMonto $50.000,00")
    assert a["confianza"] == "alta" and a["monto"] == 50000, a


def test_foto_digito_antepuesto_pide_monto(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572",
                   "Monto $57.500\nMonto 7.500",
                   imagen="/tmp/r.jpg", evidencia="r.jpg", dry_run=True)
    s = _unpack(r)
    assert "No identifiqué con certeza" in s, s
    assert '"decision": "pedir_monto"' in s, s
    assert '"confianza_monto": "ambiguo"' in s, s
    assert len(srv.rows) == 1


def test_ocr_inestable_doble_pasada_pide_monto(motor):
    """Las dos pasadas de OCR no coinciden -> no autoconfiar (caso real 52.800)."""
    m, srv = motor
    r = m.procesar("personal", "3002084572",
                   "Monto total 52.800,00\nMonto 52.800,00",
                   imagen="/tmp/recibo.jpg", evidencia="recibo.jpg", dry_run=True,
                   ocr_text="Monto total 52.800,00\nMonto 52.800,00",
                   ocr_inestable=True)
    s = _unpack(r)
    assert "inconsistente" in s, s
    assert '"decision": "pedir_monto"' in s, s
    assert len(srv.rows) == 1


# ==========================================================================
# 22. PARTE B — Checklist con monto de foto, trazabilidad y ruta idempotente
# ==========================================================================
def test_checklist_foto_muestra_monto_leido(motor):
    m, _srv = motor
    caption = "Pago de camiseta oversize hominidx, categoría ropa"
    ocr = "Monto total $50.000,00\n07 AGO 2026 - 05:59:08\nCamiseta oversize"
    r1 = _unpack(m.procesar("personal", "3002084572", caption + "\n" + ocr,
                            imagen="/tmp/recibo.jpg", evidencia="recibo.jpg",
                            caption=caption, ocr_text=ocr))
    assert "Confirma el registro" in r1, r1
    assert ("Monto leído de la foto: $50,000. Si no coincide, "
            "corrígelo con la opción 1.") in r1, r1


def _ultimo_historial_evento():
    import json as _json
    p = os.path.join(config.DATA_DIR, "historial.jsonl")
    with open(p, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return _json.loads(lines[-1])


def test_historial_guarda_updatedrange_fila_hoja(motor):
    m, srv = motor
    _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    ev = _ultimo_historial_evento()
    assert ev["tipo"] == "registro", ev
    assert ev["data"]["updatedRange"] == "Hoja 1!A2:P2", ev
    assert ev["data"]["fila"] == 2, ev
    assert ev["data"]["hoja_id"] == config.GROUPS["personal"][0], ev


def test_idempotente_fila_coincide_ya_registrado(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅ Registrado" in rfinal
    op_key = list(storage.get_ledger().data.keys())[0]
    n_filas = len(srv.rows)
    hist1 = _historial_lines()
    _a, r2 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "Ya estaba registrado" in r2, r2
    assert len(srv.rows) == n_filas
    assert _historial_lines() == hist1   # sin evento de pérdida ni reescritura
    # el ledger conserva solo la entrada original, sin reinsertado R1
    assert set(storage.get_ledger().data) == {op_key}, storage.get_ledger().data


def test_idempotente_fila_borrada_reinserta_r1(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅ Registrado" in rfinal
    op_key = list(storage.get_ledger().data.keys())[0]
    row_id = storage.get_ledger().data[op_key]["id"]
    fila_esperada = storage.get_ledger().data[op_key]["fila"]
    # reimportación externa: la fila desaparece de la hoja
    srv.rows = [srv.rows[0]]
    hist1 = _historial_lines()
    _a, r2 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "fue borrada o sobrescrita externamente" in r2, r2
    assert row_id + "-R1" in r2, r2
    assert len(srv.rows) == 2                       # reinsertada
    assert srv.rows[-1][0] == row_id + "-R1", srv.rows[-1]
    # evento de pérdida en historial con id, fila esperada y hoja
    assert _historial_lines() == hist1 + 1
    ev = _ultimo_historial_evento()
    assert ev["tipo"] == "fila_perdida_externamente", ev
    assert ev["data"]["id"] == row_id, ev
    assert ev["data"]["fila"] == fila_esperada, ev
    assert ev["data"]["hoja"] == config.GROUPS["personal"][0], ev
    # el ledger registra el nuevo id derivado (reintento no reinserta de nuevo)
    assert storage.get_ledger().claim(op_key + "-R1") == row_id + "-R1"


def test_idempotente_error_transitorio_no_reinserta(motor):
    m, srv = motor
    _r1, rfinal = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "✅ Registrado" in rfinal
    op_key = list(storage.get_ledger().data.keys())[0]
    n_filas = len(srv.rows)
    srv.fallar_lecturas = True
    _a, r2 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "error transitorio" in r2, r2
    assert len(srv.rows) == n_filas              # no reinsertó
    assert storage.get_ledger().claim(op_key + "-R1") is None  # no tocó ledger
    srv.fallar_lecturas = False
    # al recuperarse la red, la fila sigue ahí -> ya registrado, sin duplicar
    _a, r3 = _registrar_y_confirmar(m, "personal", "3002084572", "pagué 5000 mercado")
    assert "Ya estaba registrado" in r3, r3
    assert len(srv.rows) == n_filas