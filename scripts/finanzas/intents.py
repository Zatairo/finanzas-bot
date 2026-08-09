"""intents.py — Orquestador de intenciones con permisos y checklist de registro.

Entry point unico: procesar(grupo, sender, texto, ...)-> list[str/mensaje].
Los tests inyectan un cliente de hojas (srv) falso; sin srv no se puede
registrar ni consultar (se devuelve error controlado), salvo --dry-run.

Flujo de registro (checklist paso a paso, sin inventar datos):
  1. Se captura hora/fecha del mensaje y el valor (monto).
  2. La descripcion se guarda en la columna descripcion.
  3. Si la categoria no se deduce, se pregunta UNA vez.
  4. Se muestra checklist y se pide confirmacion ('si') antes de escribir.
  5. Un numero en el checklist permite corregir ese campo.
  6. Palabras/negocios nuevos se aprenden GLOBALMENTE tras confirmar.
"""
import datetime
import hashlib
import json
import os
import re

from . import config, sheets, storage
from .entities import extraer_entidades, MESES, limpiar_descripcion
from .normalize import normalize, to_display
from .normalize import analizar_monto, parse_monto
from .dialogue import Dialogue
from .rules import get_rules


class Motor:
    def __init__(self, srv=None):
        self.srv = srv
        self.dlg = Dialogue()
        self.ap = storage.get_aprendizajes()

    # ================== ENTRY POINT ==================
    def procesar(self, grupo, sender, texto="", imagen=None, evidencia="", dry_run=False):
        storage.get_estados().limpiar_expirados()
        # 0) identidad y permisos ANTES de cualquier estado/registro/consulta
        usuario = config.user_from_sender(sender)
        perm = bool(usuario) and config.can_write(grupo, usuario)
        intent, arg = self._detectar_intent(texto, usuario)
        if intent == "ayuda":
            return [self._ayuda()]
        if not usuario:
            # remitente desconocido: rechazo claro, sin estado/ledger/escritura
            return ["⛔ No reconozco tu número. No se guardó nada."]
        if not perm:
            if intent in ("presupuesto", "frecuencia", "consulta"):
                return ["⛔ No tienes permiso para ver los datos de %s." % grupo]
            return ["⛔ No tienes permiso para %s en %s." % (intent, grupo)]

        # 1) resolver conversaciones pendientes del MISMO sender+grupo
        estado = self.dlg.pendiente(grupo, sender)
        if estado and not imagen:
            res = self._continuar_pendiente(grupo, sender, texto, estado, dry_run)
            if res:
                return res

        # 2) acciones administrativas y rutas
        if intent in ("revisar", "aprender") and not config.is_admin(usuario):
            return ["⛔ Solo el administrador puede ejecutar eso."]
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
                "• El bot confirma con un checklist antes de guardar.\n"
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
        r = get_rules().match_categoria(valor)
        if not r:
            r = get_rules().match_categoria("medicamento " + valor)
        if not r:
            return "⚠️ No reconozco la categoría '%s'." % valor
        self.dlg.aprender_global(palabra, r[0], r[1])
        storage.log_evento("-", "admin_aprende", {"palabra": palabra, "cat": r[0]})
        return "✅ Aprendí que '%s' = %s (%s). Vale para los 3 grupos." % (palabra, r[0], r[1])

    # ================== REGISTRO: checklist paso a paso ==================
    def _nuevo_estado_registro(self, grupo, sender, texto, evidencia, usuario,
                               monto=None, cat=None, sub=None, fecha=None, hora=None,
                               nombre_pendiente=None):
        """Crea/continúa el estado conversacional del registro."""
        return {
            "accion": "registro",
            "texto": texto or "",
            "evidencia": evidencia or "",
            "usuario": usuario,
            "monto": monto,
            "categoria": cat,
            "subcategoria": sub,
            "fecha": fecha or datetime.date.today().isoformat(),
            "hora": hora or datetime.datetime.now().strftime("%H:%M"),
            "nombre_pendiente": nombre_pendiente,
        }

    def _checklist(self, e, es_compartido, monto_disp):
        """Muestra el checklist del gasto a confirmar."""
        lineas = ["📋 *Confirma el registro:*"]
        lineas.append("1) Monto: %s" % monto_disp)
        lineas.append("2) Categoría: %s (%s)" % (e["categoria"], e["subcategoria"]))
        desc = e.get("texto") or ""
        lineas.append("3) Descripción: %s" % limpiar_descripcion(desc) or "(sin detalle)")
        lineas.append("4) Fecha/Hora: %s %s" % (e.get("fecha"), e.get("hora")))
        quien = e.get("usuario")
        if es_compartido:
            quien = "U3 (mitad)"
        lineas.append("5) Usuario: %s" % quien)
        lineas.append("\nResponde *si* para guardar, o el número a corregir (1-4).")
        return "\n".join(list(dict.fromkeys(lineas)))

    def _finalizar_registro(self, grupo, sender, e, ent, dry_run):
        """Tras confirmar, escribe en Sheets y aprende lo nuevo."""
        texto = e.get("texto") or ""
        monto = e.get("monto")
        if monto is None:
            return ["⚠️ Falta el monto. ¿Cuánto fue?"]
        disp = to_display(monto)
        cat, sub = e.get("categoria"), e.get("subcategoria")
        if not cat:
            return ["⚠️ Falta la categoría. ¿A qué categoría pertenece?"]
        desc = limitar(limpiar_descripcion(texto), 120)
        tipo = ent.tipo if ent else "Gasto"
        metodo = (ent.metodo if ent else None) or "transferencia"
        es_compartido = bool(ent and ent.compartido and grupo == "hogar")
        usuario = e.get("usuario")
        if es_compartido:
            usuario = "U3"
        fecha = e.get("fecha") or datetime.date.today().isoformat()
        hora = e.get("hora") or datetime.datetime.now().strftime("%H:%M")

        # dry-run: nunca escribe Sheets/ledger/historial/inventario/aprendizaje
        if dry_run:
            anal = analizar_monto(texto)
            return [self._diag_json(tipo, monto, anal, cat, metodo, desc, "registrar")]

        # aprender el nombre nuevo GLOBALMENTE si se asignó una categoría
        nombre = e.get("nombre_pendiente")
        if nombre and cat:
            self.dlg.aprender_global(nombre, cat, sub, confirmado_por=sender)
            storage.log_evento(grupo, "aprendido", {"palabra": nombre, "cat": cat, "sub": sub})

        if self.srv is None:
            return ["⚠️ No hay conexión con Google Sheets en este momento. Intenta de nuevo."]

        sid = config.GROUPS[grupo][0]
        row_id = sheets.gen_id()
        row = sheets.build_row({
            "id": row_id, "fecha": fecha, "hora": hora, "grupo": grupo,
            "usuario": usuario, "tipo": tipo, "monto_display": disp,
            "categoria": cat, "subcategoria": sub, "desc": desc,
            "metodo": metodo, "evidencia": e.get("evidencia") or "",
        })
        op_key = hashlib.sha1(
            ("%s|%s|%s|%s" % (grupo, usuario, normalize(texto), str(monto))).encode("utf-8")
        ).hexdigest()
        _rng, _id = sheets.append_row(self.srv, sid, op_key, row)
        if _rng is None:
            # el operation_id ya estaba reclamado: no hubo escritura nueva
            return ["ℹ️ Esta operación ya estaba registrada (id: %s). No se duplicó nada." % _id]
        if not _rng:
            # append sin updatedRange: no se confirmó ninguna escritura
            return ["⚠️ No se confirmó la escritura en la hoja. No se guardó nada. Reintenta."]

        storage.log_evento(grupo, "registro", {"id": row_id, "monto": disp,
                                               "categoria": cat, "desc": desc,
                                               "metodo": metodo, "usuario": usuario})
        for prod in (ent.productos if ent else []):
            if tipo != "Ingreso":
                storage.get_inventario().add(grupo, prod, fecha, monto, cat)
        head = "✅ Registrado: %s · %s %s · %s (%s)" % (disp, tipo.lower(), cat, sub, metodo)
        if es_compartido:
            head += " · compartido 50/50 (U3 · %s c/u)" % to_display(int(round(monto / 2)))
        return [head, "id: %s · hoja: %s" % (row_id, grupo)]

    def _diag_json(self, tipo, monto, anal, cat, metodo, descripcion, decision):
        """JSON de dry-run: solo describe la decisión, nunca persiste nada."""
        return "DRY-RUN " + json.dumps({
            "tipo": tipo,
            "monto": to_display(monto) if monto is not None else None,
            "confianza_monto": anal.get("confianza"),
            "candidatos_monto": anal.get("candidatos") or [],
            "categoria": cat,
            "metodo": metodo,
            "descripcion": descripcion,
            "decision": decision,
        }, ensure_ascii=False)

    def _msg_pedir_monto(self, anal):
        """Ante monto ambiguo/ausente en una foto: muestra candidatos y pide el
        monto real. NO escribe Sheets ni reclama ledger."""
        cands = anal.get("candidatos") or []
        lineas = ["🤔 No identifiqué con certeza el monto del recibo."]
        if cands:
            unicos = []
            vistos = set()
            for c in cands:
                v = c.get("valor")
                if v in vistos:
                    continue
                vistos.add(v)
                unicos.append("%s (%s)" % (to_display(v), (c.get("linea") or "")[:40]))
            lineas.append("Detecté: %s" % " · ".join(unicos[:6]))
        else:
            lineas.append("No apareció un valor monetario claro (ej: $50.000,00 o '50 mil').")
        lineas.append("¿Cuál es el monto real? Escríbelo con cifra, ej: '50000' o '50 mil'.")
        return "\n".join(lineas)

    def _continuar_pendiente(self, grupo, sender, texto, estado, dry_run):
        accion = estado.get("accion")
        if accion == "borrar":
            return self._confirmar_borrar(grupo, sender, texto, estado)
        if accion == "registro":
            return self._continuar_registro(grupo, sender, texto, estado, dry_run)
        return None

    def _continuar_registro(self, grupo, sender, texto, estado, dry_run):
        t = normalize(texto or "").strip()
        # pendiente de monto (foto ambigua o corrección): capturar la cifra
        if estado.get("pendiente") == "monto":
            nm = analizar_monto(texto or "").get("monto")
            if nm is None:
                return ["⚠️ No identifiqué un monto válido. Escríbelo con cifra, ej: '50000' o '50 mil'."]
            estado["monto"] = nm
            estado.pop("pendiente", None)
            if not estado.get("categoria"):
                estado["pendiente"] = "categoria"
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return [self.dlg._menu_categoria(estado.get("nombre_pendiente") or "ese registro")]
            if not dry_run:
                self.dlg.guardar(grupo, sender, estado)
            return self._mostrar_checklist(grupo, sender, estado, dry_run)

        # pendiente de categoria al inicio
        if estado.get("pendiente") == "categoria":
            r = self.dlg.parse_categoria_respuesta(texto)
            if r == "no_se":
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                return ["✅ Guardé '%s' para revisión del administrador (comando 'revisar')."
                        % estado.get("nombre_pendiente")]
            if r is not None:
                estado["categoria"] = r[0]
                estado["subcategoria"] = r[1]
                estado.pop("pendiente", None)
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return self._mostrar_checklist(grupo, sender, estado, dry_run)
            return [self.dlg._menu_categoria(estado.get("nombre_pendiente"))]

        # hemos mostrado el checklist: 'si' guarda, numero corrige, 'no' cancela
        if t in _SINONIMOS_SI:
            if not dry_run:
                self.dlg.limpiar(grupo, sender)
            ent = extraer_entidades(estado.get("texto") or "")
            return self._finalizar_registro(grupo, sender, estado, ent, dry_run)
        if t in _SINONIMOS_NO:
            if not dry_run:
                self.dlg.limpiar(grupo, sender)
            return ["✅ Cancelado. No guardé nada."]
        # corregir un campo del checklist
        if re.fullmatch(r"[1-4]", t):
            campo = int(t)
            if campo == 1:
                if not dry_run:
                    self.dlg.guardar(grupo, sender, dict(estado, pendiente="monto"))
                return ["¿Cuál es el monto? (ej: 5000 o '5 mil')"]
            if campo == 2:
                if not dry_run:
                    self.dlg.guardar(grupo, sender, dict(estado, pendiente="categoria"))
                return [self.dlg._menu_categoria(estado.get("nombre_pendiente"))]
            if campo in (3, 4):
                return ["✅ El registro se hará con la fecha/hora del mensaje. Responde 'si' para guardar."]
        return [self._checklist(estado, False, to_display(estado.get("monto")))]

    def _mostrar_checklist(self, grupo, sender, estado, dry_run):
        es_comp = bool(extraer_entidades(estado.get("texto") or "").compartido and grupo == "hogar")
        if not dry_run:
            self.dlg.guardar(grupo, sender, dict(estado, pendiente=None))
        return [self._checklist(estado, es_comp, to_display(estado.get("monto")))]

    # ================== REGISTRO (entrada) ==================
    def _registrar(self, grupo, sender, texto, imagen, evidencia, dry_run, perm, usuario):
        ent = extraer_entidades(texto or "")
        monto = ent.monto
        anal = analizar_monto(texto or "")
        cat, sub = ent.categoria, ent.subcategoria
        desc = limitar(limpiar_descripcion(texto or ""), 120)

        # si falta categoria, aplicar alias aprendido
        if not cat:
            hit = self.ap.search(texto or "")
            if hit:
                _k, apd = hit
                cat = apd.get("categoria")
                sub = apd.get("subcategoria")

        # foto con monto no confiable -> pedir confirmación, sin escribir nada
        if imagen and anal["confianza"] in ("ambiguo", "baja", "ninguno"):
            e = self._nuevo_estado_registro(grupo, sender, texto, evidencia, usuario,
                                            monto=monto, cat=cat, sub=sub,
                                            fecha=ent.fecha, hora=ent.hora)
            if not dry_run:
                e["pendiente"] = "monto"
                self.dlg.guardar(grupo, sender, e)
            msgs = [self._msg_pedir_monto(anal)]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, monto, anal, cat, ent.metodo, desc,
                                            "pedir_monto"))
            return msgs

        if monto is None:
            e = self._nuevo_estado_registro(grupo, sender, texto, evidencia, usuario)
            e["pendiente"] = "monto"
            if not dry_run:
                self.dlg.guardar(grupo, sender, e)
            msgs = ["⚠️ ¿Cuál es el monto? Escríbelo con cifra (ej: '5000' o '5 mil') o envía la foto del recibo."]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, None, anal, cat, ent.metodo, desc,
                                            "pedir_monto"))
            return msgs

        e = self._nuevo_estado_registro(grupo, sender, texto, evidencia, usuario,
                                        monto=monto, cat=cat, sub=sub,
                                        fecha=ent.fecha, hora=ent.hora)

        if not cat:
            palabra = self._palabra_candidata(texto, ent)
            e["nombre_pendiente"] = palabra
            e["pendiente"] = "categoria"
            if not dry_run:
                self.dlg.guardar(grupo, sender, e)
            if palabra:
                msgs = [self.dlg._menu_categoria(palabra)]
            else:
                msgs = ["⚠️ ¿A qué categoría asigno '%s'? Dímelo." % (texto[:40])]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, monto, anal, None, ent.metodo, desc,
                                            "pedir_categoria"))
            return msgs

        if not dry_run:
            self.dlg.guardar(grupo, sender, e)
        msgs = self._mostrar_checklist(grupo, sender, e, dry_run)
        if dry_run:
            msgs.append(self._diag_json(ent.tipo, monto, anal, cat, ent.metodo, desc,
                                        "checklist"))
        return msgs

    def _palabra_candidata(self, texto, ent):
        ap = self.ap
        if ap.search(texto):
            return None
        reglas = get_rules()
        if ent.comercio or reglas.match_categoria(texto):
            return None
        t = normalize(texto or "")
        t = re.sub(r"\$?\s?\d[\d.,]*\s*(mil|k|m)?", " ", t)
        t = re.sub(r"\b(pagu?e|pago|pagar|gast[oe]|compr[oea]|recib[ií]|compr\w+)\w*\b", " ", t)
        t = re.sub(r"\b(por|en|de|un|una|el|la|los|las|con|a|para|y|al|que|mil|medio|media|mitad)\b", " ", t)
        t = re.sub(r"\b(pe[so]+)\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        frag = t.split()
        if not frag:
            return None
        cand = " ".join(frag[:2])
        return cand[:40] if len(cand) >= 3 else None

    # ================== PRESUPUESTO ==================
    def _presupuesto(self, grupo, texto):
        p = storage.get_presupuestos()
        t = normalize(texto or "")
        m = re.search(r"(\d[\d.,]*)\s*(k|mil|m|millon)?", t)
        definir = bool(re.search(r"\b(define|definir|pon|pongo|fija|fijar|de)\b", t) and m is not None)
        if definir and m:
            monto = parse_monto(t)
            if monto:
                r = get_rules().match_categoria(t)
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
        idx = None
        for i, row in enumerate(rows):
            if sheets.fila_activa(row):
                idx = i
        if idx is None:
            return ["✅ No hay entradas que borrar."]
        _real = idx + 2
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


_SINONIMOS_SI = {"si", "sí", "sip", "dale", "confirma", "confirmar", "ok", "okey", "listo", "adelante", "guardar", "guarda"}
_SINONIMOS_NO = {"no", "nop", "n", "cancelar", "cancela", "no guardes", "no registrar"}


def limitar(s, n):
    s = s or ""
    return s[:n]