"""storage.py — Persistencia: escrituras atomicas (temp+rename), TTL, y
datos financieros por grupo. Los aprendizajes son GLOBALES.
"""
import datetime
import json
import os
import re
import tempfile

from . import config
from .normalize import normalize


def _atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    try:
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# Marcador de enrutamiento para el adapter Hermes: indica que hay una
# conversacion pendiente en el grupo, para que el bridge enrute respuestas
# cortas ('si', numero, nombre...) hacia gasto.py. NO guarda datos: es un
# flag de existencia, el estado real vive en data/estados/.
# Se escribe en FINANZAS_MARKER_DIR (por defecto /tmp para compat con
# adapter_whatsapp.py que lo consulta) y se elimina al no quedar pendientes.
def _marker_path(grupo, base=None):
    base = base or os.environ.get("FINANZAS_MARKER_DIR", "/tmp")
    return os.path.join(base, "gasto_pendiente_%s.json" % (grupo or "x"))


def set_routing_marker(grupo):
    try:
        with open(_marker_path(grupo), "w", encoding="utf-8") as f:
            f.write("{}")
    except Exception:
        pass


def clear_routing_marker(grupo, basedir=None):
    """Elimina el marcador solo si no quedan estados pendientes en el grupo."""
    try:
        p = _marker_path(grupo)
        if not _any_pending_in(grupo, basedir):
            os.remove(p)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _any_pending_in(grupo, basedir=None):
    try:
        d = basedir or config.AR_ESTADOS_DIR
        if not os.path.isdir(d):
            return False
        for fn in os.listdir(d):
            if fn.startswith(grupo + "__") and fn.endswith(".json"):
                return True
    except Exception:
        return False
    return False


def log_evento(grupo, tipo, data=None):
    try:
        line = json.dumps({"ts": now_iso(), "grupo": grupo, "tipo": tipo, "data": data or {}},
                          ensure_ascii=False)
        os.makedirs(os.path.dirname(config.AR_HISTORIAL), exist_ok=True)
        with open(config.AR_HISTORIAL, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================= APRENDIZAJES GLOBALES =========================
class Aprendizajes:
    """Almacen global (los 3 chat). key = alias_normalizado."""

    def __init__(self, path=None):
        self.path = path or config.AR_APRENDIZAJES
        self._migrate_legacy()
        self._data = _read_json(self.path, {}) or {}

    def _migrate_legacy(self):
        if os.path.exists(config.AR_LEGACY_APRENDIZAJES):
            legacy = _read_json(config.AR_LEGACY_APRENDIZAJES, {})
            cur = _read_json(self.path, {})
            changed = False
            for k, v in legacy.items():
                if k not in cur:
                    cur[k] = {
                        "alias_normalizado": k,
                        "alias_original": k,
                        "tipo": "comercio",
                        "categoria": v.get("cat", ""),
                        "subcategoria": v.get("sub", ""),
                        "producto": v.get("producto", ""),
                        "usos": 1,
                        "creado_en": now_iso(),
                        "actualizado_en": now_iso(),
                        "confirmado_por": "",
                        "activo": True,
                    }
                    changed = True
            if changed:
                _atomic_write(self.path, cur)

    def upsert(self, a):
        """a: models.Aprendizaje"""
        cur = self._data.get(a.alias_normalizado)
        if cur:
            cur["usos"] = int(cur.get("usos", 0)) + 1
            cur["actualizado_en"] = now_iso()
            cur["categoria"] = a.categoria or cur.get("categoria", "")
            cur["subcategoria"] = a.subcategoria or cur.get("subcategoria", "")
            cur["tipo"] = a.tipo
            if a.producto:
                cur["producto"] = a.producto
        else:
            self._data[a.alias_normalizado] = {
                "alias_normalizado": a.alias_normalizado,
                "alias_original": a.alias_original,
                "tipo": a.tipo, "categoria": a.categoria,
                "subcategoria": a.subcategoria, "producto": a.producto,
                "usos": a.usos, "creado_en": a.creado_en or now_iso(),
                "actualizado_en": a.actualizado_en or now_iso(),
                "confirmado_por": a.confirmado_por, "activo": a.activo,
            }
        _atomic_write(self.path, self._data)

    def eliminar(self, alias_normalizado):
        removed = self._data.pop(alias_normalizado, None)
        if removed is not None:
            _atomic_write(self.path, self._data)
        return removed is not None

    def get(self, key):
        return self._data.get(key)

    def items(self):
        return list(self._data.items())

    def search(self, texto):
        """Busca el mejor alias (frase mas larga) presente en el texto."""
        t = normalize(texto or "")
        best = None  # (largo, key, entry)
        for key, e in self._data.items():
            if not e.get("activo", True):
                continue
            k = normalize(key)
            if not k or len(k) < 3:
                continue
            if t == k or re.search(r"(^|\W)" + re.escape(k) + r"(\W|$)", t):
                if best is None or len(k) > best[0]:
                    best = (len(k), key, e)
        return best[1:] if best else None

    def to_dict(self):
        return dict(self._data)


# ============================== ESTADOS (grupo+sender) ======================
class EstadoStore:
    """Estado conversacional por grupo+sender con TTL. No usa /tmp."""

    def __init__(self, basedir=None):
        self.basedir = basedir or config.AR_ESTADOS_DIR
        os.makedirs(self.basedir, exist_ok=True)

    def _path(self, grupo, sender):
        s = (sender or "anon") if sender else "anon"
        safe = "".join(ch for ch in s if ch.isalnum() or ch in "-_.")
        return os.path.join(self.basedir, "%s__%s.json" % (grupo, safe))

    def get(self, grupo, sender):
        p = self._path(grupo, sender)
        e = _read_json(p, None)
        if not e:
            return None
        exp = e.get("expires_at")
        try:
            exp_dt = datetime.datetime.fromisoformat(exp)
        except Exception:
            exp_dt = None
        if exp_dt and datetime.datetime.now() > exp_dt:
            self._delete(p)
            return None
        return e

    def set(self, grupo, sender, data, ttl=None):
        ttl = ttl or config.ESTADOS_TTL_SEG
        e = dict(data or {})
        e["grupo"] = grupo
        e["sender"] = sender
        e["created_at"] = now_iso()
        e["expires_at"] = (datetime.datetime.now() +
                           datetime.timedelta(seconds=ttl)).isoformat(timespec="seconds")
        _atomic_write(self._path(grupo, sender), e)
        set_routing_marker(grupo)
        return e

    def clear(self, grupo, sender):
        self._delete(self._path(grupo, sender))
        clear_routing_marker(grupo, self.basedir)

    def _delete(self, p):
        try:
            os.remove(p)
        except Exception:
            pass

    def limpiar_expirados(self):
        touched = set()
        try:
            for fn in os.listdir(self.basedir):
                if not fn.endswith(".json"):
                    continue
                grupo = fn.split("__", 1)[0]
                p = os.path.join(self.basedir, fn)
                e = _read_json(p, None)
                if not e:
                    continue
                exp = e.get("expires_at")
                try:
                    exp_dt = datetime.datetime.fromisoformat(exp)
                except Exception:
                    continue
                if datetime.datetime.now() > exp_dt:
                    self._delete(p)
                    touched.add(grupo)
        except Exception:
            pass
        for g in touched:
            clear_routing_marker(g, self.basedir)


# ====================== PRESUPUESTOS / INVENTARIO (por grupo) ================
class Presupuestos:
    def __init__(self, path=None):
        self.path = path or config.AR_PRESUPUESTOS
        self.data = _read_json(self.path, {}) or {}

    def save(self):
        _atomic_write(self.path, self.data)

    def get(self, grupo, categoria):
        return (self.data.get(grupo) or {}).get(categoria)

    def set(self, grupo, categoria, monto):
        self.data.setdefault(grupo, {})[categoria] = monto
        self.save()


class Inventario:
    def __init__(self, path=None):
        self.path = path or config.AR_INVENTARIO
        self.data = _read_json(self.path, []) or []

    def save(self):
        _atomic_write(self.path, self.data)

    def add(self, grupo, producto, fecha, monto, cat):
        self.data.append({"grupo": grupo, "fecha": fecha, "producto": producto,
                          "monto": float(monto), "cat": cat})
        if len(self.data) > 20000:
            self.data = self.data[-20000:]
        self.save()

    def all(self):
        return self.data


# ====================== LEDGER de idempotencia (IDs unicos) =================
class Ledger:
    """Mapa op_key -> (id_emitido, estado). Evita duplicados por reintento."""

    def __init__(self, path=None):
        self.path = path or config.AR_LEDGER
        self.data = _read_json(self.path, {}) or {}

    def claim(self, op_key):
        """Devuelve el id existente si ya se proceso, o None si es nuevo."""
        e = self.data.get(op_key)
        return e.get("id") if e else None

    def register(self, op_key, row_id, fila=None):
        entry = {"id": row_id, "ts": now_iso()}
        if fila is not None:
            entry["fila"] = fila   # fila real devuelta por updatedRange
        self.data[op_key] = entry
        # poda
        if len(self.data) > 5000:
            self.data = dict(list(self.data.items())[-3000:])
        _atomic_write(self.path, self.data)


stores = {}   # cache


def get_aprendizajes():
    if "aprendizajes" not in stores:
        stores["aprendizajes"] = Aprendizajes()
    return stores["aprendizajes"]


def get_estados():
    if "estados" not in stores:
        stores["estados"] = EstadoStore()
    return stores["estados"]


def get_presupuestos():
    if "presupuestos" not in stores:
        stores["presupuestos"] = Presupuestos()
    return stores["presupuestos"]


def get_inventario():
    if "inventario" not in stores:
        stores["inventario"] = Inventario()
    return stores["inventario"]


def get_ledger():
    if "ledger" not in stores:
        stores["ledger"] = Ledger()
    return stores["ledger"]