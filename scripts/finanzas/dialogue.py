"""dialogue.py — Maquina de estados para conversaciones pendientes.

Cada conversacion pendiente es (grupo, sender). Un 'si'/'no'/numero de un
sender distinto NO resuelve la conversacion de otro.
Flujo de registro: si falta categoria -> se pregunta UNA sola vez.
"""
import re

from .normalize import normalize
from . import storage


class Dialogue:
    def __init__(self):
        self.estados = storage.get_estados()

    # ---------- utilidades ----------
    def pendiente(self, grupo, sender):
        return self.estados.get(grupo, sender)

    def guardar(self, grupo, sender, data, ttl=None):
        self.estados.set(grupo, sender, data, ttl=ttl)

    def limpiar(self, grupo, sender):
        self.estados.clear(grupo, sender)

    # ---------- flujo categoria ----------
    def pedir_categoria(self, grupo, sender, contexto, producto):
        """Guarda peticion de categoria y devuelve el mensaje a enviar."""
        data = contexto
        data["accion"] = "categoria"
        data["producto"] = producto
        self.guardar(grupo, sender, data)
        return self._menu_categoria(producto)

    def _menu_categoria(self, palabra):
        cat = storage.get_aprendizajes()
        lineas = ['🔎 No conozco "%s". ¿A qué categoría pertenece?' % (palabra or "ese comercio")]
        # opciones globales desde config via rules (12 comunes)
        from . import config
        OPCIONES = [("Alimentacion", "Mercado / plaza"), ("Alimentacion", "Restaurante / comida fuera"),
                    ("Alimentacion", "Domicilios"), ("Alimentacion", "Bebidas / snacks"),
                    ("Vivienda", "Arriendo"), ("Vivienda", "Servicios publicos (agua/luz/gas)"),
                    ("Vivienda", "Internet / telefono"), ("Transporte", "Transporte publico / bus"),
                    ("Salud", "Medicamentos"), ("Tecnologia", "Suscripciones digitales"),
                    ("Ocio", "Entretenimiento / streaming"), ("Ingreso", "Otros ingresos")]
        for i, (c, s) in enumerate(OPCIONES, 1):
            lineas.append("%d. %s" % (i, s))
        lineas.append("0. Otra (ej: 'fue de veterinaria')")
        lineas.append("Responde el número, el nombre de la categoría, o 'no sé'.")
        return "\n".join(lineas)

    _OPCIONES = [("Alimentacion", "Mercado / plaza"), ("Alimentacion", "Restaurante / comida fuera"),
                 ("Alimentacion", "Domicilios"), ("Alimentacion", "Bebidas / snacks"),
                 ("Vivienda", "Arriendo"), ("Vivienda", "Servicios publicos (agua/luz/gas)"),
                 ("Vivienda", "Internet / telefono"), ("Transporte", "Transporte publico / bus"),
                 ("Salud", "Medicamentos"), ("Tecnologia", "Suscripciones digitales"),
                 ("Ocio", "Entretenimiento / streaming"), ("Ingreso", "Otros ingresos")]

    def parse_categoria_respuesta(self, text):
        """Interpreta el mensaje del usuario ante el menu. Devuelve (cat, sub) o None.

        Acepta: numero, nombre de categoria/subcategoria exacto/normalizado, o
        match de keyword de regla (incluye busqueda por prefijo para plurales).
        Si es 'no sé' devuelve la string 'no_se'.
        """
        t = normalize(text or "").strip()
        if not t or t in _RE_NO:
            return "no_se"   # enviar a cola admin
        m = re.match(r"^(\d{1,2})\b", t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(self._OPCIONES):
                return self._OPCIONES[n - 1]
        # por nombre de categoria/subcategoria (exacto o por palabra completa)
        from .rules import get_rules
        for cat in get_rules().categorias:
            for name in (cat["cat"], cat.get("sub", "")):
                if name and t == normalize(name):
                    return (cat["cat"], cat.get("sub") or "")
        # buscar si t es una palabra que empieza igual que una subcategoria (plurales)
        for cat in get_rules().categorias:
            sub = cat.get("sub", "")
            if sub:
                base = normalize(sub)
                if t.startswith(base) or (len(base) > 3 and base.startswith(t.rstrip("s"))):
                    return (cat["cat"], sub)
        # match por keyword de regla
        r = get_rules().match_categoria(text)
        if r:
            return (r[0], r[1])
        return None

    def aprender_global(self, alias, cat, sub, confirmado_por=None, tipo="comercio", producto="", alias_original=None):
        from .models import Aprendizaje
        a = Aprendizaje(
            alias_normalizado=normalize(alias or ""),
            alias_original=alias_original or alias,
            tipo=tipo, categoria=cat, subcategoria=sub, producto=producto,
            confirmado_por=confirmado_por or "",
        )
        storage.get_aprendizajes().upsert(a)


# respuestas negativas / no se
_RE_NO = {"no", "nop", "n", "ninguno", "ninguna", "no se", "no se que es",
          "no la conozco", "no conozco", "ni idea", "no se que categoria",
          "no es producto", "no es", "no cacho"}