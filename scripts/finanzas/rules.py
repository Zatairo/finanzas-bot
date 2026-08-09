"""rules.py — Carga y evaluacion de reglas globales + aprendizajes globales.

El conocimiento (palabras, comercios, metodos, productos) es GLOBAL para los
3 chat. Se resuelve con prioridad:
  1. alias global aprendido (frase larga/exacto)
  2. regla de comercio/producto conocida
  3. regla de categoria
  4. None -> preguntar
"""
import os
import re
import yaml

from .normalize import normalize, strip_accents
from . import config


class Rules:
    def __init__(self):
        self.categorias = []      # [{"cat","sub","keywords":[]}]
        self.metodos = []         # [{"nombre","keywords":[]}]
        self.productos = []       # [{"nombre","keywords":[]}]
        self.ingreso = []         # keywords ingreso
        self.gasto = []           # keywords gasto
        self._load()

    def _load(self):
        path = config.AR_REGLA
        data = {}
        if os.path.exists(path):
            try:
                data = yaml.safe_load(open(path, encoding="utf-8")) or {}
            except Exception:
                data = {}
        self.categorias = data.get("categorias") or []
        self.metodos = data.get("metodos") or []
        self.productos = data.get("productos") or []
        self.ingreso = data.get("ingreso_hints") or []
        self.gasto = data.get("gasto_hints") or []

    # --- categorias --------------------------------------------------------
    def match_categoria(self, text):
        t = " " + normalize(text) + " "
        best = None
        for cat in self.categorias:
            kws = cat.get("keywords", [])
            for kw in kws:
                k = normalize(kw)
                if not k:
                    continue
                if re.search(r"\b" + re.escape(k) + r"\b", t):
                    if best is None or len(k) > best[0]:
                        best = (len(k), cat["cat"], cat["sub"], cat.get("comercio"))
        return best[1:] if best else None   # (cat, sub, comercio=None)

    # --- metodo ------------------------------------------------------------
    def match_metodo(self, text):
        t = " " + normalize(text) + " "
        for m in self.metodos:
            for kw in m.get("keywords", []):
                k = normalize(kw)
                if k and re.search(r"\b" + re.escape(k) + r"\b", t):
                    return m["nombre"]
        return None

    # --- productos ---------------------------------------------------------
    def match_productos(self, text):
        t = " " + normalize(text) + " "
        out = []
        for p in self.productos:
            for kw in p.get("keywords", []):
                k = normalize(kw)
                if k and re.search(r"\b" + re.escape(k) + r"\b", t):
                    out.append(p["nombre"])
                    break
        return list(dict.fromkeys(out))

    # --- tipo --------------------------------------------------------------
    def match_tipo(self, text):
        t = normalize(text)
        if any(normalize(k) and re.search(r"\b%s\b" % re.escape(normalize(k)), t) for k in self.ingreso):
            return "Ingreso"
        return "Gasto"

    def match_comercio_por_regla(self, text):
        """Devuelve un comercio si una regla de categoria marca comercio específico."""
        r = self.match_categoria(text)
        if r and len(r) > 2 and r[2]:
            return r[2]
        return None


_rules = None


def get_rules():
    global _rules
    if _rules is None:
        _rules = Rules()
    return _rules