"""Pruebas del motor deterministico de finanzas.

FakeSrv replica la superficie minima del cliente Google Sheets usada por
finanzas.sheets, para poder probar append/consulta/anulacion sin red.
El registro usa checklist: tras pedir datos, se confirma con 'si'.
"""
import os
import sys
import tempfile
import threading

import pytest

# Asegurar import del paquete (scripts/)
os.environ["FINANZAS_DATA_DIR"] = tempfile.mkdtemp(prefix="fztest_")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from finanzas.intents import Motor  # noqa: E402
from finanzas import config, storage, sheets  # noqa: E402
from finanzas.normalize import parse_monto, analizar_monto  # noqa: E402
import finanzas.rules as _rules_mod  # noqa: E402


# --------------------------------------------------------------------------
# FakeSrv
# --------------------------------------------------------------------------
class FakeValues:
    def __init__(self, srv):
        self.srv = srv

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        return _FakeCall(self.srv.append, body["values"][0])

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

    def spreadsheets(self):
        return FakeSpreadsheet(self)

    def append(self, row):
        with self.lock:
            self.rows.append(list(row))
            return {"updates": {"updatedRange": "Hoja 1!A%d:T%d" % (len(self.rows), len(self.rows))}}

    def get(self, rng):
        with self.lock:
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
    assert "ya estaba registrada" in r2, r2
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
    assert "Confirma el registro" in r3
    assert "$50,000" in r3
    r4 = _unpack(m.procesar("personal", "3002084572", "si"))
    assert "✅" in r4
    assert len(srv.rows) == 2
    assert srv.rows[-1][6] == "$50,000"