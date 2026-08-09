"""intents.py — Orquestador de intenciones con permisos.

Entry point unico: procesar(grupo, sender, texto, ...)-> list[str/mensaje].
Los tests inyectan un cliente de hojas (srv) falso; sin srv no se puede
registrar ni consultar (se devuelve error controlado), salvo --dry-run.
"""
import datetime
import hashlib
import os
import re

from . import config, sheets, storage
from .entities import extraer_entidades, MESES, limpiar_descripcion
from .normalize import normalize, to_display
from .dialogue import Dialogue
from .rules import get_rules


def _parse_monto_num(display):
    return display


class Motor:
    def __init__(self, srv=None):
        self.srv = srv                 # cliente hojas (None en dry-run/offline)
        self.dlg = Dialogue()
        self.ap = storage.get_aprendizajes()

    # ================== ENTRY POINT ==================
    def procesar(self, grupo, sender, texto="", imagen=None, evidencia="", dry_run=False):
        storage.get_estados().limpiar_expirados()
        estado = self.dlg.pendiente(grupo, sender)
        # 1) resolver conversaciones pendientes del MISMO sender+grupo
        if estado and not imagen:
            res = self._continuar_pendiente(grupo, sender, texto, estado, dry_run)
            if res:
                return res

        # 2) permisos de escritura
        perm, usuario = config.authenticate(sender, grupo)
        intent, arg = self._detectar_intent(texto, usuario)

        if intent in ("presupuesto", "frecuencia") and not perm:
            return ["⛔ No tienes permiso para ver los datos de %s." % grupo]
        if intent in ("borrar", "revisar", "aprender", "registro") and not perm:
            return ["⛔ No tienes permiso para %s en %s." % (intent, grupo)]
        if intent in ("revisar", "aprender") and not config.is_admin(usuario):
            return ["⛔ Solo el administrador puede ejecutar eso."]
        if intent == "borrar" and not config.is_admin(usuario):
            # owner de la transaccion o admin -> se valida al confirmar
            pass
        if intent == "ayuda":
            return [self._ayuda()]
        if intent == "revisar":
            return [self._revisar(arg)]
        if intent == "presupuesto":
            return [self._presupuesto(grupo, texto)]
        if intent == "frecuencia":
            return [self._frecuencia(grupo, texto)]
        if intent == "borrar":
            return self._borrar(grupo, sender, texto)
        return self._registrar(grupo, sender, texto, imagen, evidencia, dry_run, perm, usuario)

    # ================== INTENTOS ==================
    def _detectar_intent(self, texto, usuario):
        t = normalize(texto or "")
        if re.match(r"^[a-z]{3,}\s*=\s*[a-z0-9 ]+", t):
            return ("aprender", t)
        if re.search(r"\b(ayuda|menu|menú|como usar|como se usa|como funciona|que sabes hacer|manual|instrucciones)\b", t):
            return ("ayuda", None)
        if re.search(r"\b(revisar|revision|cola de aprendizaje|pendientes de aprender|que me falta aprender)\b", t):
            return ("revisar", None)
        if re.search(r"\b(borra|borrar|borre|anula|anular|elimin\w+|ultima entrada|última entrada|ultimo gasto|deshaz\w*)\b", t):
            return ("borrar", None)
        if re.search(r"\b(presupuesto|presupuestos)\b", t):
            return ("presupuesto", None)
        if re.search(r"\b(cada cuanto|cada cuánto|que tan seguido|frecuencia|inventario|que compro)\b", t):
            return ("frecuencia", None)
        if re.search(r"\b(resumen|cuanto|cuánto|total de gastos|gastos de|gaste|gastamos|ingresos de|mes pasado|este mes)\b", t):
            return ("consulta", None)
        return ("registro", None)

    def _ayuda(self):
        return ("📱 *Bot de Finanzas — Guía*\n"
                "• Registrar: 'pagué 5000 mercado por nequi'\n"
                "• Ingreso: 'recibí 500 mil de salario'\n"
                "• Compartido: 'compramos 80 mil a medias'\n"
                "• Consulta: 'gastos de agosto', 'cuánto gasté'\n"
                "• Presupuesto: 'presupuesto de comida 600 mil'\n"
                "• Frecuencia: 'cada cuánto compro arroz'\n"
                "• Borrar: 'borra la última entrada'\n"
                "• Ayuda: 'ayuda' | Admin: 'revisar', 'xaran = salud'")

    def _revisar(self, arg):
        if arg:
            return self._aprender_comando(arg)
        return "🗂 No hay palabras pendientes de revisión en este momento."

    def _aprender_comando(self, arg):
        m = re.match(r"^([a-z]{3,})\s*=\s*(.+)$", normalize(arg))
        if not m:
            return "⚠️ Usa: <palabra> = <categoría>"
        palabra = m.group(1)
        valor = m.group(2)
        from .rules import get_rules
        r = get_rules().match_categoria(valor)
        if not r:
            r = get_rules().match_categoria("medicamento " + valor)
        if not r:
            return "⚠️ No reconozco la categoría '%s'." % valor
        self.dlg.aprender_global(palabra, r[0], r[1])
        storage.log_evento("-", "admin_aprende", {"palabra": palabra, "cat": r[0]})
        return "✅ Aprendí que '%s' = %s (%s). Vale para los 3 grupos." % (palabra, r[0], r[1])

    # ================== CONTINUAR PENDIENTE ==================
    def _continuar_pendiente(self, grupo, sender, texto, estado, dry_run):
        if estado.get("accion") == "categoria":
            r = self.dlg.parse_categoria_respuesta(texto)
            if r == "no_se":
                storage.log_evento(grupo, "aprendizaje_pendiente", {
                    "palabra": estado.get("producto"), "sender": sender})
                self.dlg.limpiar(grupo, sender)
                return ["✅ Guardé '%s' para revisión del administrador (comando 'revisar')." % estado.get("producto")]
            if r is not None:
                estado["categoria"] = r[0]
                estado["subcategoria"] = r[1]
                self.dlg.aprender_global(estado.get("producto"), r[0], r[1],
                                         confirmado_por=sender)
                storage.log_evento(grupo, "aprendido", {
                    "palabra": estado.get("producto"), "cat": r[0], "sub": r[1]})
                self.dlg.limpiar(grupo, sender)
                return self._registrar(grupo, sender, estado.get("texto") or "",
                                       None, estado.get("evidencia") or "",
                                       dry_run, True, estado.get("usuario"),
                                       forzar=estado)
            # respuesta sin entender -> re-preguntar
            return [self.dlg._menu_categoria(estado.get("producto"))]
        if estado.get("accion") == "borrar":
            return self._confirmar_borrar(grupo, sender, texto, estado)
        return None

    # ================== REGISTRO ==================
    def _registrar(self, grupo, sender, texto, imagen, evidencia, dry_run, perm, usuario, forzar=None):
        data = forzar or {}
        ent = extraer_entidades(texto or "")
        monto = ent.monto
        if monto is None:
            return ["⚠️ No encontré el monto. Escríbelo con cifra (ej: 'pagué 5000 mercado') o envía la foto del recibo."]
        disp = to_display(monto)
        cat, sub = ent.categoria, ent.subcategoria

        # si falta categoria y hay un comercio/palabra candidata -> preguntar UNA vez
        if not cat and texto and not ent.compartido:
            palabra = self._palabra_candidata(texto, ent)
            if palabra:
                ctx = {
                    "texto": texto, "monto": str(monto), "monto_disp": disp,
                    "evidencia": evidencia, "usuario": usuario,
                    "fecha": ent.fecha or datetime.date.today().isoformat(),
                    "hora": ent.hora,
                }
                return [self.dlg.pedir_categoria(grupo, sender, ctx, palabra)]

        # aplicar alias global aprendido (funciona en los 3 grupos)
        if not cat:
            hit = self.ap.search(texto)
            if hit:
                _k, e = hit
                cat = e.get("categoria") or None
                sub = e.get("subcategoria") or None

        if not cat:
            # no hay forma de saber -> pregunta (una pasada)
            palabra = self._palabra_candidata(texto, ent)
            if palabra:
                ctx = {"texto": texto, "monto": str(monto), "monto_disp": disp,
                       "evidencia": evidencia, "usuario": usuario}
                return [self.dlg.pedir_categoria(grupo, sender, ctx, palabra)]
            return ["⚠️ ¿A qué categoría asigno '%s'? Dímelo o responde 'no sé'." % (texto[:40])]

        desc = limitar(limpiar_descripcion(texto), 120)
        metodo = ent.metodo or "transferencia"
        fecha = ent.fecha or datetime.date.today().isoformat()
        hora = ent.hora
        tipo = ent.tipo

        # modelado de compartido 50/50 (debe estar antes del dry-run)
        participantes, reparto = [], ""
        monto_total = None
        monto_usuario = None
        if ent.compartido and grupo == "hogar":
            participantes = ["U1", "U2"]
            monto_total = monto
            monto_usuario = int(round(monto / 2))
            reparto = "50/50"
        if dry_run:
            return ["DRY-RUN " + str({
                "hoja": config.GROUPS[grupo][0], "grupo": grupo, "tipo": tipo,
                "monto": disp, "monto_num": monto, "compartido": ent.compartido,
                "categoria": cat,
                "subcategoria": sub, "metodo": metodo, "descripcion": desc,
                "usuario": usuario, "productos": ent.productos,
                "participantes": participantes, "reparto": reparto,
                "monto_total": to_display(monto_total) if monto_total else "",
                "monto_usuario": to_display(monto_usuario) if monto_usuario else ""})]

        if self.srv is None:
            return ["⚠️ No hay conexión con Google Sheets en este momento. Intenta de nuevo."]

        sid = config.GROUPS[grupo][0]

        row_id = sheets.gen_id()
        row = sheets.build_row({
            "id": row_id, "fecha": fecha, "hora": hora, "grupo": grupo,
            "usuario": usuario, "tipo": tipo, "monto_display": disp,
            "categoria": cat, "subcategoria": sub, "desc": desc,
            "metodo": metodo, "evidencia": evidencia,
            "monto_total": to_display(monto_total) if monto_total else "",
            "monto_usuario": to_display(monto_usuario) if monto_usuario else "",
            "participantes": participantes, "reparto": reparto,
        })

        op_key = hashlib.sha1(
            ("%s|%s|%s|%s" % (grupo, usuario, normalize(texto), str(monto))).encode("utf-8")
        ).hexdigest()
        _rng, _id = sheets.append_row(self.srv, sid, op_key, row)
        storage.log_evento(grupo, "registro", {"id": row_id, "monto": disp,
                                               "categoria": cat, "desc": desc,
                                               "metodo": metodo, "usuario": usuario})
        # inventario
        for prod in ent.productos:
            if tipo != "Ingreso":
                storage.get_inventario().add(grupo, prod, fecha, monto, cat)
        head = "✅ Registrado: %s · %s %s · %s (%s)" % (disp, tipo.lower(), cat, sub, metodo)
        if ent.compartido:
            head += " · compartido 50/50 (U1/U2, %s cada uno)" % to_display(monto_usuario)
        return [head, "id: %s · hoja: %s" % (row_id, grupo)]

    def _palabra_candidata(self, texto, ent):
        """Devuelve una frase/comercio candidata a aprender (normalizada), no tokens sueltos."""
        ap = self.ap  # ya aprendidos
        hit = ap.search(texto)
        if hit:
            return None   # ya se conoce -> no preguntar
        from .rules import get_rules
        reglas = get_rules()
        # comercio de regla conocida -> categoria resuelta, no preguntar
        if ent.comercio or reglas.match_categoria(texto):
            return None
        # limpiar montos/metodos/palabras vacias y tomar el primer fragmento largo
        t = normalize(texto or "")
        t = re.sub(r"\$?\s?\d[\d.,]*\s*(mil|k|m)?", " ", t)
        t = re.sub(r"\b(pagu?e|pago|pagar|gast[oe]|compr[oea]|recib[ií]|compr\w+)\w*\b", " ", t)
        t = re.sub(r"\b(por|en|de|un|una|el|la|los|las|con|a|para|y|al|que|mil|medio|media|mitad)\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        frag = t.split()
        if not frag:
            return None
        # tomar la frase de hasta 2 palabras, excluyendo verbos/stop cortos
        cand = " ".join(frag[:2])
        if len(cand) < 3:
            return None
        return cand[:40]

    # ================== PRESUPUESTO ==================
    def _presupuesto(self, grupo, texto):
        p = storage.get_presupuestos()
        t = normalize(texto or "")
        m = re.search(r"(\d[\d.,]*)\s*(k|mil|m|millon)?", t)
        definir = bool(re.search(r"\b(define|definir|pon|pongo|fija|fijar|de)\b", t) and m is not None)
        if definir and m:
            from .normalize import parse_monto
            monto = parse_monto("5000" if m.group(1).isdigit() else m.group(1))
            monto = parse_monto(t)
            if monto:
                from .normalize import normalize as _n
                reglas = get_rules()
                r = reglas.match_categoria(t)
                cat = r[0] if r else "Alimentacion"
                p.set(grupo, cat, monto)
                return ["✅ Presupuesto de %s = %s/mes en %s." % (cat, to_display(monto), grupo)]
        lineas = ["📋 Presupuestos %s (este mes):" % grupo]
        pg = p.get(grupo, None) or {}
        if not pg:
            return ["📋 No hay presupuestos definidos en %s. Ej: 'presupuesto de comida 600 mil'." % grupo]
        sid = config.GROUPS[grupo][0]
        for cat, tope in sorted(pg.items(), key=lambda x: -x[1]):
            gastado = self._gasto_mensual_cat(grupo, sid, cat)
            pct = (gastado / tope * 100.0) if tope else 0
            lineas.append("  • %s: %s de %s (%d%%)" % (cat, to_display(gastado), to_display(tope), int(pct)))
        return lineas

    def _gasto_mensual_cat(self, grupo, sid, cat):
        if self.srv is None:
            return 0.0
        rows = sheets.leer_filas(self.srv, sid)
        pref = datetime.date.today().strftime("%Y-%m")
        total = 0.0
        for row in rows:
            if not sheets.fila_activa(row):
                continue
            if len(row) < 9:
                continue
            if (row[1] or "")[:7] != pref or str(row[8] or "") != cat:
                continue
            if str(row[5] or "Gasto").lower() == "ingreso":
                continue
            total += float(self._m(row[6]))
        return total

    def _m(self, disp):
        try:
            return float(str(disp).replace("$", "").replace(",", "").strip())
        except Exception:
            return 0.0

    # ================== FRECUENCIA ==================
    def _frecuencia(self, grupo, texto):
        inv = storage.get_inventario().all()
        from .rules import get_rules
        prods = get_rules().match_productos(texto or "")
        if prods:
            prod = prods[0]
            rows = [r for r in inv if r.get("grupo") == grupo and r.get("producto") == prod]
            if not rows:
                return ["🔎 No tengo registro de compras de %s en este grupo." % prod]
            fechas = sorted({str(r.get("fecha", ""))[:10] for r in rows})
            total = sum(float(r.get("monto") or 0) for r in rows)
            gaps = []
            for i in range(1, len(fechas)):
                try:
                    g = (datetime.datetime.strptime(fechas[i], "%Y-%m-%d") -
                         datetime.datetime.strptime(fechas[i - 1], "%Y-%m-%d")).days
                    gaps.append(g)
                except Exception:
                    continue
            prom = (int(sum(gaps) / len(gaps)) if gaps else "n/a")
            return ["🛒 %s: %d compras, %s · últ.: %s · c/ %s días" % (prod, len(fechas), to_display(total), fechas[-1], prom)]
        from collections import Counter
        c = Counter(r.get("producto") for r in inv if r.get("grupo") == grupo)
        if not c:
            return ["🔎 No tengo inventario registrado."]
        return ["🛒 Productos más comprados:"] + ["  • %s: %d" % (p, n) for p, n in c.most_common(10)]

    # ================== BORRAR (con confirmacion por sender) ===============
    def _borrar(self, grupo, sender, texto):
        if self.srv is None:
            return ["⚠️ Sin conexión a Sheets."]
        sid = config.GROUPS[grupo][0]
        rows = sheets.leer_filas(self.srv, sid)
        # ultima fila activa
        idx = None
        for i, row in enumerate(rows):
            if sheets.fila_activa(row):
                idx = i
        if idx is None:
            return ["✅ No hay entradas que borrar."]
        _real = idx + 2  # fila real (1 indexado + header)
        row = (rows[idx] + [""] * 15)[:15]
        info = {"id": row[0], "fecha": row[1], "tipo": row[5], "monto": row[6],
                "categoria": row[8], "desc": row[10], "usuario": row[4] if len(row) > 4 else ""}
        self.dlg.guardar(grupo, sender, {"accion": "borrar", "fila": _real, "info": info})
        return ["⚠️ ¿Confirma que quieres borrar esta entrada?", self._fmt_info(info),
                "Responde 'si' para borrar o 'no' para cancelar."]

    def _confirmar_borrar(self, grupo, sender, texto, estado):
        t = normalize(texto or "").strip()
        info = estado.get("info", {})
        if t in _SINONIMOS_NO:
            self.dlg.limpiar(grupo, sender)
            return ["✅ No borré nada. La entrada se mantiene."]
        if t not in _SINONIMOS_SI:
            return ["⚠️ Responde 'si' (borrar) o 'no' (cancelar)."]
        # dueño: quien inicia el borrado (sender de este estado). El estado es por sender,
        # asique otro remitente nunca llega aqui con un estado de categoria/borrar ajeno.
        if self.srv is None:
            return ["⚠️ Sin conexión a Sheets."]
        sid = config.GROUPS[grupo][0]
        try:
            sheets.marcar_anulado(self.srv, sid, estado["fila"])
        except Exception as e:
            return ["⚠️ No pude anular la entrada: %s" % e]
        self.dlg.limpiar(grupo, sender)
        storage.log_evento(grupo, "borrar", {"id": info.get("id"), "anulado": True, "por": sender})
        return ["🗑️ Anulé la entrada: %s." % self._fmt_info(info)]

    def _fmt_info(self, info):
        return "• %s · %s · %s" % (info.get("fecha", "?"), info.get("monto", "?"), info.get("desc", "") or info.get("categoria", ""))


_SINONIMOS_SI = {"si", "sí", "sip", "dale", "confirma", "confirmar", "ok", "okey", "listo", "adelante", "si borra", "borra si"}
_SINONIMOS_NO = {"no", "nop", "n", "cancelar", "cancela", "no borres", "no la borres", "no lo borres"}


def limitar(s, n):
    s = s or ""
    return s[:n]