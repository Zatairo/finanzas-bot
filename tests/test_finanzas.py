"""Pruebas del motor deterministico de finanzas.

FakeSrv replica la superficie minima del cliente Google Sheets usada por
finanzas.sheets, para poder probar append/consulta/anulacion sin red.
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
import finanzas.rules as _rules_mod  # noqa: E402


# --------------------------------------------------------------------------
# FakeSrv: replica de valores().{append,get,update} o en memoria
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
        # base: 1 fila header + filas de ejemplo
        self.rows = [["id", "fecha", "hora", "grupo", "usuario", "tipo", "monto",
                      "moneda", "categoria", "subcategoria", "desc_orig",
                      "desc_norm", "metodo", "evidencia", "estado", "prioridad",
                      "monto_total", "monto_usuario", "participantes", "reparto"]]
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
            # rango simulado: devolver del index 1 en adelante (sin header)
            return {"values": [r[:] for r in self.rows[1:]]}

    def update(self, rng, row):
        with self.lock:
            import re
            m = re.match(r"Hoja 1!([A-Z]+)(\d+):([A-Z]+)(\d+)", rng)
            if not m:
                return {"updatedCells": 0}
            row_idx = int(m.group(2))  # 1-based (row1 = header)
            li = row_idx - 1           # -> índice en self.rows
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
    # aislar datos por test
    import shutil
    d = storage.DATA_DIR if hasattr(storage, "DATA_DIR") else None
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


# ==========================================================================
# 1. pague 5 mil de mercado por Nequi
# ==========================================================================
def test_pague_5mil_mercado_nequi(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572", "pagué 5 mil de mercado por nequi",
                   dry_run=True)
    s = _unpack(r)
    assert "monto_num': 5000" in s or "'monto_num': 5000" in s
    assert "Alimentacion" in s
    assert "Mercado / plaza" in s
    assert "Gasto" in s
    assert "transferencia Nequi" in s
    # formato con punto de miles + $ también funciona
    r2 = m.procesar("personal", "3002084572", "pagué $5.000 de mercado",
                    dry_run=True)
    assert "monto_num': 5000" in _unpack(r2)


# ==========================================================================
# 2. recibi 500 mil de salario -> ingreso
# ==========================================================================
def test_recibi_500_salario(motor):
    m, srv = motor
    r = m.procesar("personal", "3002084572", "recibí 500 mil de salario",
                   dry_run=True)
    s = _unpack(r)
    assert "monto_num': 500000" in s
    assert "Ingreso" in s
    assert "Salario / nomina" in s


# ==========================================================================
# 3. compramos 80 mil de mercado a medias (hogar)
# ==========================================================================
def test_compartido_hogar(motor):
    m, srv = motor
    r = m.procesar("hogar", "3002084572", "compramos 80 mil de mercado a medias",
                   dry_run=True)
    s = _unpack(r)
    assert "monto_num': 80000" in s
    assert "compartido': True" in s
    assert "U1" in s and "U2" in s
    assert "monto_usuario': '$40,000'" in s  # cada uno


# ==========================================================================
# 4. Xaran: pregunta una vez, luego global en los 3 grupos
# ==========================================================================
def test_aprendizaje_global_xaran(motor):
    m, srv = motor
    # primera vez en hogar (U1) -> preguntar categoria
    r1 = m.procesar("hogar", "3002084572", "pagué 25 mil en Xaran", dry_run=True)
    s1 = _unpack(r1)
    assert "No conozco" in s1 or "categoría" in s1
    # responder "medicamentos" resuelve la conversacion del mismo sender+grupo
    r2 = m.procesar("hogar", "3002084572", "medicamentos", dry_run=True)
    ap = storage.get_aprendizajes()
    hit = ap.get("xaran")
    assert hit and hit.get("categoria") == "Salud", hit
    # el alias aprendido aplica globalmente:
    #  - personal (U1 puede escribir) sin pedir categoria
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
    r = m.procesar("personal", "2222222222", "pagué 5000 mercado", dry_run=True)
    s = _unpack(r)
    assert "No tienes permiso" in s


# ==========================================================================
# 6. No-admin no puede 'revisar' ni 'xaran = medicamentos'
# ==========================================================================
def test_no_admin_revisar(motor):
    m, srv = motor
    r = m.procesar("hogar", "2222222222", "revisar", dry_run=True)
    assert "administrador" in _unpack(r)
    r2 = m.procesar("hogar", "2222222222", "xaran = medicamentos", dry_run=True)
    assert "administrador" in _unpack(r2)


# ==========================================================================
# 7. Estados pendientes simultaneos en Hogar no se cruzan
# ==========================================================================
def test_estados_no_se_cruzan(motor):
    m, srv = motor
    # U1 y U2 tienen cada uno un estado pendiente de categoria
    m.procesar("hogar", "3002084572", "pagué 25 mil en Xaran", dry_run=True)
    m.procesar("hogar", "3147359270", "pagué 30 mil en Fulin", dry_run=True)
    e1 = m.dlg.pendiente("hogar", "3002084572")
    e2 = m.dlg.pendiente("hogar", "3147359270")
    assert e1 is not None and e2 is not None
    assert e1["producto"] != e2["producto"]
    # U2 responde un numero; no debe tocar el estado de U1
    r = m.procesar("hogar", "3147359270", "medicamentos", dry_run=True)
    _ = _unpack(r)
    e1_after = m.dlg.pendiente("hogar", "3002084572")
    assert e1_after is not None  # el de U1 sigue pendiente


# ==========================================================================
# 8. A inicia borrado, B responde 'si' -> NO se ejecuta
# ==========================================================================
def test_borrado_cruzado_no_ejecuta(motor):
    m, srv = motor
    # crear una fila
    m.procesar("hogar", "3002084572", "pagué 5000 mercado", dry_run=False)
    assert srv.buscar_ultima() is not None
    filas_before = len(srv.rows)
    # A inicia borrado
    rA = m.procesar("hogar", "3002084572", "borra la última entrada")
    assert any("¿Confirma" in str(x) for x in rA)
    # B (otro sender) responde si -> no debe tocar el estado de A
    rB = m.procesar("hogar", "3147359270", "si")
    _ = _unpack(rB)
    # el borrado de A sigue pendiente porque B no tiene estado de borrado
    assert m.dlg.pendiente("hogar", "3002084572") is not None
    assert len(srv.rows) == filas_before
    assert srv.anuladas == 0


# ==========================================================================
# 9. Registros concurrentes no reutilizan id ni sobrescriben fila
# ==========================================================================
def test_registros_concurrentes_id_unicos(motor):
    m, srv = motor
    fichas = [("pagué %d mil de mercado" % n) for n in (10, 20, 30)]
    res = []
    def trabajador(txt):
        out = m.procesar("hogar", "3002084572", txt, dry_run=False)
        res.append(_unpack(out))
    threads = [threading.Thread(target=trabajador, args=(f,)) for f in fichas]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [r.split("id: ")[1].split(" ·")[0] for r in res if "id: " in r]
    assert len(set(ids)) == len(ids) > 0  # unicos
    # filas unicas
    assert len(srv.rows) - 1 == len(ids)


# ==========================================================================
# 10. Anuladas no cuentan en resumen/presupuesto/frecuencia
# ==========================================================================
def test_anuladas_excluidas(motor):
    m, srv = motor
    m.procesar("hogar", "3002084572", "pagué 20000 mercado", dry_run=False)
    m.procesar("hogar", "3002084572", "pagué 30000 mercado", dry_run=False)
    # anular la ultima
    ult = srv.buscar_ultima()
    # usar el flujo de borrado del bot
    m.procesar("hogar", "3002084572", "borra la última entrada")
    m.procesar("hogar", "3002084572", "si")
    assert srv.anuladas == 1
    # frecuencia debe ignorar anuladas (inventario nunca las registro por separado aun)
    # resumen: canalizar via consulta -> usar _gasto_mensual_cat
    total = m._gasto_mensual_cat("hogar", srv, "Alimentacion")
    assert total == 20000  # solo la primera (la otra anulada)