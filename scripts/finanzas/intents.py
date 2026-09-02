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
from zoneinfo import ZoneInfo

from . import config, sheets, storage, reportes, auditoria
from .entities import (extraer_entidades, candidatos_fecha, candidatos_hora,
                       resolver_descripcion)
from .normalize import normalize, to_display
from .normalize import analizar_monto, parse_monto
from .dialogue import Dialogue
from .rules import get_rules

_TZ_BOGOTA = ZoneInfo("America/Bogota")


def _ts_bogota(ts_mensaje=None):
    """Timestamp del mensaje convertido a America/Bogota; si no llega, now() Bogota."""
    if ts_mensaje:
        try:
            return datetime.datetime.fromtimestamp(float(ts_mensaje), tz=_TZ_BOGOTA)
        except Exception:
            pass
    return datetime.datetime.now(_TZ_BOGOTA)


_FECHA_USUARIO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ t](\d{1,2}):(\d{2})(?::\d{2})?$")


def _parse_fecha_usuario(texto):
    """Valida 'AAAA-MM-DD HH:MM' estricto. Devuelve (fecha, hora) o None.

    Solo acepta fecha y hora reales; valores parciales o garabateados → None.
    """
    m = _FECHA_USUARIO_RE.match(normalize(texto or "").strip())
    if not m:
        return None
    try:
        dt = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class Motor:
    def __init__(self, srv=None):
        self.srv = srv
        self.dlg = Dialogue()
        self.ap = storage.get_aprendizajes()

    # ================== ENTRY POINT ==================
    def procesar(self, grupo, sender, texto="", imagen=None, evidencia="", dry_run=False,
                 ts_mensaje=None, caption=None, ocr_text=None):
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

        # 1) resolver conversaciones pendientes del MISMO sender+grupo (comandos tienen prioridad sobre registro pendiente)
        estado = self.dlg.pendiente(grupo, sender)
        if estado and not imagen and intent not in ("aprender", "revisar", "validar", "consulta", "presupuesto", "frecuencia", "ayuda", "borrar"):
            res = self._continuar_pendiente(grupo, sender, texto, estado, dry_run)
            if res:
                return res

        # 2) acciones administrativas y rutas
        if intent in ("revisar", "aprender") and not config.is_admin(usuario):
            return ["⛔ Solo el administrador puede ejecutar eso."]
        if intent == "revisar":
            return [self._revisar(arg)]
        if intent == "aprender":
            return [self._aprender_comando(arg)]
        if intent == "validar":
            return self._validar(grupo, texto)
        if intent == "presupuesto":
            return [self._presupuesto(grupo, texto)]
        if intent == "frecuencia":
            return [self._frecuencia(grupo, texto)]
        if intent == "borrar":
            return self._borrar(grupo, sender, texto)
        if intent == "consulta":
            return self._consulta(grupo, texto)
        return self._registrar(grupo, sender, texto, imagen, evidencia, dry_run, perm,
                               usuario, ts_mensaje, caption=caption, ocr_text=ocr_text)

    # ================== INTENTOS ==================
    def _detectar_intent(self, texto, usuario):
        t = normalize(texto or "")
        if re.match(r"^[a-z0-9_ ]{3,}\s*=\s*.+", t):
            return ("aprender", t)
        # FIX: priorizar registro si parece gasto (incluye donación) aunque contenga fecha
        _parece_gasto = bool(re.search(r"\b(pagu?e|gasto|compre|internet|arepas|mercado|pago|abono|tigo|claro|netflix|spotify|arriendo|gasolina|domicilio|arepa|donaci[óo]n|donacion)\b", t))
        _es_pregunta_resumen = bool(re.search(r"^\s*(resumen|muestr|cuanto|cuánto|balance|total|gastos de|resumen de)\b", t))
        if _parece_gasto and not _es_pregunta_resumen:
            return ("registro", None)
        if re.search(r"\b(ayuda|menu|menú|como usar|como se usa|como funciona|que sabes hacer|manual|instrucciones)\b", t):
            return ("ayuda", None)
        if re.search(r"\b(revisar|revision|cola de aprendizaje|pendientes de aprender|que me falta aprender)\b", t):
            return ("revisar", None)
        if re.search(r"\b(borra|borrar|borre|anula|anular|elimin\w+|ultima entrada|última entrada|ultimo gasto|deshaz\w*)\b", t):
            return ("borrar", None)
        if re.search(r"\b(validar|auditar|auditoria|validacion)\b", t):
            return ("validar", None)
        if re.search(r"\b(presupuesto|presupuestos)\b", t):
            return ("presupuesto", None)
        if re.search(r"\b(cada cuanto|cada cuánto|que tan seguido|frecuencia|inventario|que compro)\b", t):
            return ("frecuencia", None)
        if re.search(r"\b(resumen|cuanto|cuánto|total de gastos|gastos de|gaste|gastamos|ingresos de|mes pasado|este mes)\b", t):
            return ("consulta", None)
        if re.search(r"\b(entre|desde|hasta|del\s+\d|rango|balance|totales)\b", t):
            if re.search(r"\b(\d{4}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|resumen|gastos|gasto|ingresos|ingreso)\b", t):
                return ("consulta", None)
        return ("registro", None)

    def _ayuda(self):
        return ("Bot de Finanzas - Guia\n"
                "??? Registrar: 'pague 5000 mercado por nequi'\n"
                "??? Ingreso: 'recibi 500 mil de salario'\n"
                "??? Compartido: 'compramos 80 mil a medias'\n"
                "??? Confirma con checklist antes de guardar.\n"
                "??? Consulta: 'resumen entre 2026-08-01 y 2026-08-15', 'gastos de agosto'\n"
                "??? Presupuesto: 'presupuesto de comida 600 mil'\n"
                "??? Frecuencia: 'cada cuanto compro arroz'\n"
                "??? Borrar: 'borra la ultima entrada'\n"
                "??? Categorias: 'categorias' lista 1:1 (30). Si no conoces, elige 0. Otra y propone.\n"
                "??? Validar: 'validar' o 'auditar' revisa tablas 1:1 sin anomalias\n"
                "??? Ayuda: 'ayuda' | Admin: 'revisar', 'palabra = categoria', 'validar'")

    def _revisar(self, arg):
        if arg:
            return self._aprender_comando(arg)
        return "🗂 No hay palabras pendientes de revisión en este momento."

    def _aprender_comando(self, arg):
        m = re.match(r"^([a-z0-9_ ]{3,})\s*=\s*(.+)$", normalize(arg))
        if not m:
            return "[!] Usa: <palabra> = <categoria>"
        palabra = m.group(1).strip()
        valor = m.group(2)
        # typo correction for suscripcion y mercado
        valor_norm = (valor or "").lower()
        if "ssucrip" in valor_norm or "suscrip" in valor_norm:
            valor = "suscripciones digitales"
        if "mercado" in valor_norm:
            # alimentacion,mercado -> Mercado / plaza (para la casa)
            valor = "Mercado / plaza"
        try:
            from . import categorias as _cat_mod
            exact = _cat_mod.find_exact(valor, srv=self.srv)
            if exact:
                r = exact
            else:
                r = get_rules().match_categoria(valor)
                # fallback: try with suscripcion correction
                if not r and "suscrip" in valor_norm:
                    r = ("Tecnologia", "Suscripciones digitales", None)
        except Exception:
            r = get_rules().match_categoria(valor)
            if not r and "suscrip" in (valor or "").lower():
                r = ("Tecnologia", "Suscripciones digitales", None)
        if not r:
            try:
                from . import categorias as _cat_mod
                cats = _cat_mod.all_categorias(srv=self.srv)
                sug = ", ".join(f"{c['cat']} - {c['sub']}" for c in cats[:5])
                return "[!] No reconozco la categoria '%s'. Prueba con una de la hoja 1:1: %s. O usa 'categorias' para ver lista." % (valor, sug)
            except Exception:
                return "[!] No reconozco la categoria '%s'." % valor
        self.dlg.aprender_global(palabra, r[0], r[1])
        storage.log_evento("-", "admin_aprende", {"palabra": palabra, "cat": r[0], "sub": r[1]})
        return "Listo! Aprendi que '%s' es '%s - %s'.\nLa proxima vez que menciones '%s' lo categorizare automaticamente en los 3 grupos (personal, hogar, andrea)." % (palabra, r[0], r[1], palabra)

    # ================== REGISTRO: checklist paso a paso ==================
    def _resolver_fecha_hora(self, ent, imagen, dt_msg):
        """Prioridad de fecha/hora para imágenes/recibos:

          1) fecha y hora COMPLETAS y válidas del OCR -> origen 'recibo';
          2) una sola parte del OCR + la faltante desde el timestamp del
             mensaje convertido a America/Bogota -> origen 'recibo';
          3) timestamp del mensaje en America/Bogota -> 'whatsapp_bogota';
          4) now() en America/Bogota (solo si no hay recibo ni timestamp).

        El caso 0 (corrección explícita del usuario) se resuelve en el flujo
        del campo 4. Nunca se construye fecha/hora desde números aislados.
        """
        fecha_msg = dt_msg.strftime("%Y-%m-%d")
        hora_msg = dt_msg.strftime("%H:%M")
        if not imagen:
            return fecha_msg, hora_msg, "whatsapp_bogota"
        fecha_ocr = ent.fecha if ent else None
        hora_ocr = ent.hora if ent else None
        if fecha_ocr and hora_ocr:
            return fecha_ocr, hora_ocr, "recibo"
        if fecha_ocr:
            return fecha_ocr, hora_msg, "recibo"
        if hora_ocr:
            return fecha_msg, hora_ocr, "recibo"
        return fecha_msg, hora_msg, "whatsapp_bogota"

    def _nuevo_estado_registro(self, grupo, sender, texto, evidencia, usuario,
                               monto=None, cat=None, sub=None, fecha=None, hora=None,
                               origen_fecha_hora="whatsapp_bogota", descripcion=None,
                               origen_descripcion="pendiente", nombre_pendiente=None):
        """Crea/continúa el estado conversacional del registro."""
        now_bog = _ts_bogota()
        return {
            "accion": "registro",
            "texto": texto or "",
            "evidencia": evidencia or "",
            "usuario": usuario,
            "monto": monto,
            "categoria": cat,
            "subcategoria": sub,
            "fecha": fecha or now_bog.strftime("%Y-%m-%d"),
            "hora": hora or now_bog.strftime("%H:%M"),
            "origen_fecha_hora": origen_fecha_hora,
            "descripcion": descripcion,
            "origen_descripcion": origen_descripcion,
            "nombre_pendiente": nombre_pendiente,
        }

    def _checklist(self, e, es_compartido, monto_disp):
        """Muestra el checklist del gasto a confirmar."""
        lineas = ["📋 *Confirma el registro:*"]
        lineas.append("1) Monto: %s" % monto_disp)
        lineas.append("2) Categoría: %s (%s)" % (e["categoria"], e["subcategoria"]))
        desc = e.get("descripcion") or "(sin detalle)"
        lineas.append("3) Descripción: %s" % desc)
        fch, hor = e.get("fecha") or "", e.get("hora") or ""
        if fch and hor:
            lineas.append("4) Fecha/Hora: %s %s (%s)"
                          % (fch, hor, e.get("origen_fecha_hora") or "whatsapp_bogota"))
        else:
            lineas.append("4) Fecha/Hora: pendiente de confirmar")
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
        desc = limitar(e.get("descripcion") or "", 120)
        tipo = ent.tipo if ent else "Gasto"
        metodo = (ent.metodo if ent else None) or "transferencia"
        es_compartido = bool(ent and ent.compartido and grupo == "hogar")
        usuario = e.get("usuario")
        if es_compartido:
            usuario = "U3"
        now_bog = _ts_bogota()
        fecha = e.get("fecha") or now_bog.strftime("%Y-%m-%d")
        hora = e.get("hora") or now_bog.strftime("%H:%M")

        # dry-run: nunca escribe Sheets/ledger/historial/inventario/aprendizaje
        if dry_run:
            anal = analizar_monto(texto)
            return [self._diag_json(tipo, monto, anal, cat, metodo, desc, "registrar",
                                    origen_desc=e.get("origen_descripcion"),
                                    fecha=fecha, hora=hora,
                                    origen_fecha=e.get("origen_fecha_hora"),
                                    cand_fecha=candidatos_fecha(texto),
                                    cand_hora=candidatos_hora(texto))]

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
            # el operation_id ya estaba reclamado: sin fila ni efectos secundarios
            return ["ℹ️ Ya estaba registrado (id: %s). No se duplicó nada." % _id]
        if not _rng:
            # append sin updatedRange: no se confirmó ninguna escritura
            return ["⚠️ No se confirmó la escritura en la hoja. No se guardó nada. Reintenta."]

        # Solo tras confirmar la escritura: aprender, historial e inventario.
        nombre = e.get("nombre_pendiente")
        if nombre and cat:
            self.dlg.aprender_global(nombre, cat, sub, confirmado_por=sender)
            storage.log_evento(grupo, "aprendido", {"palabra": nombre, "cat": cat, "sub": sub})
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

    def _diag_json(self, tipo, monto, anal, cat, metodo, descripcion, decision,
                   origen_desc=None, fecha=None, hora=None, origen_fecha=None,
                   cand_fecha=None, cand_hora=None):
        """JSON de dry-run: solo describe la decisión, nunca persiste nada.

        Nombres exactos de campos: monto, confianza_monto, descripcion,
        origen_descripcion, fecha_hora, origen_fecha_hora, decision.
        """
        d = {
            "tipo": tipo,
            "monto": monto if monto is not None else None,
            "confianza_monto": anal.get("confianza") if anal else None,
            "descripcion": descripcion or "",
            "origen_descripcion": origen_desc or "pendiente",
            "fecha_hora": ("%s %s" % (fecha, hora)) if (fecha and hora) else None,
            "origen_fecha_hora": origen_fecha or "whatsapp_bogota",
            "decision": decision,
        }
        if cand_fecha is not None:
            d["candidatos_fecha"] = cand_fecha
        if cand_hora is not None:
            d["candidatos_hora"] = cand_hora
        if anal:
            d["candidatos_monto"] = anal.get("candidatos") or []
        if cat:
            d["categoria"] = cat
        if metodo:
            d["metodo"] = metodo
        return "DRY-RUN " + json.dumps(d, ensure_ascii=False)

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
        # pendiente de fecha/hora (campo 4): pedir formato AAAA-MM-DD HH:MM
        if estado.get("pendiente") == "fecha":
            if t in _SINONIMOS_CANCELAR:
                # descarta SOLO el pendiente de este grupo+sender
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                return ["✅ Cancelado. No guardé nada."]
            r = _parse_fecha_usuario(texto)
            if r is None:
                return ["⚠️ Fecha/hora no válida. Usa: AAAA-MM-DD HH:MM (ej: 2026-08-09 13:31).\n"
                        "O responde 'cancelar' para descartar el registro."]
            estado["fecha"], estado["hora"] = r
            estado["origen_fecha_hora"] = "corregido"
            estado.pop("pendiente", None)
            if not dry_run:
                self.dlg.guardar(grupo, sender, estado)
            return self._mostrar_checklist(grupo, sender, estado, dry_run)

        # pendiente de descripción: capturar lo que escriba el usuario
        if estado.get("pendiente") == "descripcion":
            if t in _SINONIMOS_CANCELAR:
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                return ["✅ Cancelado. No guardé nada."]
            d = normalize(texto or "").strip(" .,;:!?")
            if not d or len(d) < 3:
                return ["⚠️ Escribe una descripción válida (mínimo 3 letras) o 'cancelar'."]
            estado["descripcion"] = limitar(d[:1].upper() + d[1:], 120)
            estado["origen_descripcion"] = "caption"
            estado.pop("pendiente", None)
            if not dry_run:
                self.dlg.guardar(grupo, sender, estado)
            return self._mostrar_checklist(grupo, sender, estado, dry_run)

        # pendiente de monto (foto ambigua o correccion): capturar la cifra
        if estado.get("pendiente") == "monto":
            if t in _SINONIMOS_CANCELAR:
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                return ["Cancelado. No guarde nada. Puedes escribir 'validar' o 'resumen' sin perder el pendiente, o reenvia el gasto."]
            nm = analizar_monto(texto or "").get("monto")
            if nm is None:
                return ["No identifique un monto valido. Escribelo con cifra, ej: '50000' o '50 mil'. Tip: escribe 'cancelar' para salir, o 'validar' para auditar sin perder este pendiente."]
            estado["monto"] = nm
            estado.pop("pendiente", None)
            if not estado.get("categoria"):
                estado["pendiente"] = "categoria"
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return [self.dlg._menu_categoria(estado.get("nombre_pendiente") or "ese registro")]
            if not estado.get("descripcion"):
                estado["pendiente"] = "descripcion"
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return ["✍️ ¿Cuál es la descripción? (ej: 'mercado de la quincena')"]
            if not dry_run:
                self.dlg.guardar(grupo, sender, estado)
            return self._mostrar_checklist(grupo, sender, estado, dry_run)

        # pendiente de categoria al inicio
        if estado.get("pendiente") == "categoria":
            r = self.dlg.parse_categoria_respuesta(texto)
            if r == "no_se":
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                    storage.log_evento(grupo, "pendiente_revision", {"palabra": estado.get("nombre_pendiente"), "motivo": "no_se", "texto": estado.get("texto")})
                return ["Guarde '%s' para revision del administrador (comando 'revisar')."
                        % estado.get("nombre_pendiente")]
            if r == "otra":
                estado["pendiente"] = "categoria_otra"
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return ["Escribe la categoria que propones para '%s' (ej: Impuestos, Veterinaria, Ropa) o 'cancelar'.\nSi es una categoria de la hoja 1:1, escribe exactamente 'Categoria - Subcategoria'." % estado.get("nombre_pendiente")]
            if r == "lista_categorias":
                try:
                    from . import categorias as _cat_mod
                    lista = _cat_mod.format_lista(srv=self.srv)
                except Exception:
                    lista = "No pude cargar categorias 1:1"
                return [lista, self.dlg._menu_categoria(estado.get("nombre_pendiente"))]
            if r is not None:
                estado["categoria"] = r[0]
                estado["subcategoria"] = r[1]
                estado.pop("pendiente", None)
                if not estado.get("descripcion"):
                    estado["pendiente"] = "descripcion"
                    if not dry_run:
                        self.dlg.guardar(grupo, sender, estado)
                    return ["Cual es la descripcion? (ej: 'mercado de la quincena')"]
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return self._mostrar_checklist(grupo, sender, estado, dry_run)
            return [self.dlg._menu_categoria(estado.get("nombre_pendiente"))]
        # pendiente de categoria_otra (usuario eligio 0. Otra) - FIX: creacion inmediata sin supervisor
        if estado.get("pendiente") == "categoria_otra":
            t2 = normalize(texto or "").strip()
            if t2 in _SINONIMOS_CANCELAR:
                if not dry_run:
                    self.dlg.limpiar(grupo, sender)
                return ["Cancelado. No guarde nada."]
            propuesta = (texto or "").strip()
            if not propuesta or len(propuesta) < 2:
                return ["Escribe una categoria valida (min 2 letras) o 'cancelar'."]
            # Crear categoria inmediata: parsear propuesta
            if " - " in propuesta:
                cat_new, sub_new = [s.strip() for s in propuesta.split(" - ", 1)]
            elif "-" in propuesta and len(propuesta.split("-"))==2:
                cat_new, sub_new = [s.strip() for s in propuesta.split("-", 1)]
            else:
                cat_new, sub_new = propuesta.strip(), ""
                cat_new = cat_new[:1].upper() + cat_new[1:] if cat_new else cat_new
            if not cat_new:
                cat_new = propuesta.strip()
            # Aprender globalmente para futuros registros (sin esperar supervisor)
            try:
                nombre_orig = estado.get("nombre_pendiente") or ""
                if nombre_orig:
                    self.dlg.aprender_global(nombre_orig, cat_new, sub_new, confirmado_por=sender)
                    storage.log_evento(grupo, "categoria_creada_usuario", {"palabra": nombre_orig, "propuesta": propuesta, "cat": cat_new, "sub": sub_new, "grupo": grupo})
            except Exception:
                pass
            # Asignar categoria al estado actual y continuar al checklist sin reenviar
            estado["categoria"] = cat_new
            estado["subcategoria"] = sub_new
            estado.pop("pendiente", None)
            if not estado.get("descripcion"):
                estado["pendiente"] = "descripcion"
                if not dry_run:
                    self.dlg.guardar(grupo, sender, estado)
                return ["Categoria creada: '%s - %s'.\nCual es la descripcion? (ej: 'mercado de la quincena')" % (cat_new, sub_new or cat_new)]
            if not dry_run:
                self.dlg.guardar(grupo, sender, estado)
            return self._mostrar_checklist(grupo, sender, estado, dry_run)

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
            if campo == 3:
                if not dry_run:
                    self.dlg.guardar(grupo, sender, dict(estado, pendiente="descripcion"))
                return ["✍️ ¿Cuál es la descripción? Escríbela o responde 'cancelar'."]
            if campo == 4:
                if not dry_run:
                    self.dlg.guardar(grupo, sender, dict(estado, pendiente="fecha"))
                return ["📅 Fecha/hora del registro (formato: AAAA-MM-DD HH:MM)\n"
                        "Ejemplo: 2026-08-09 13:31\n"
                        "O responde 'cancelar' para descartar el registro."]
        return [self._checklist(estado, False, to_display(estado.get("monto")))]

    def _mostrar_checklist(self, grupo, sender, estado, dry_run):
        es_comp = bool(extraer_entidades(estado.get("texto") or "").compartido and grupo == "hogar")
        if not dry_run:
            self.dlg.guardar(grupo, sender, dict(estado, pendiente=None))
        return [self._checklist(estado, es_comp, to_display(estado.get("monto")))]

    # ================== REGISTRO (entrada) ==================
    def _registrar(self, grupo, sender, texto, imagen, evidencia, dry_run, perm, usuario,
                   ts_mensaje=None, caption=None, ocr_text=None):
        dt_msg = _ts_bogota(ts_mensaje)
        ent = extraer_entidades(texto or "")
        monto = ent.monto
        anal = analizar_monto(texto or "")
        cat, sub = ent.categoria, ent.subcategoria
        fecha, hora, origen_fecha = self._resolver_fecha_hora(ent, bool(imagen), dt_msg)

        # descripción: caption > comercio/producto > línea OCR segura > pendiente
        cap = caption if caption is not None else (texto if not imagen else None)
        ocr = ocr_text if ocr_text is not None else (texto if (imagen and not cap) else None)
        desc, origen_desc = resolver_descripcion(cap, ocr, ent.comercio,
                                                 ent.productos[0] if ent.productos else None)

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
                                            fecha=fecha, hora=hora,
                                            origen_fecha_hora=origen_fecha,
                                            descripcion=desc, origen_descripcion=origen_desc)
            if not dry_run:
                e["pendiente"] = "monto"
                self.dlg.guardar(grupo, sender, e)
            msgs = [self._msg_pedir_monto(anal)]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, monto, anal, cat, ent.metodo, desc,
                                            "pedir_monto", origen_desc=origen_desc,
                                            fecha=fecha, hora=hora, origen_fecha=origen_fecha,
                                            cand_fecha=candidatos_fecha(texto),
                                            cand_hora=candidatos_hora(texto)))
            return msgs

        if monto is None:
            e = self._nuevo_estado_registro(grupo, sender, texto, evidencia, usuario,
                                            fecha=fecha, hora=hora,
                                            origen_fecha_hora=origen_fecha,
                                            descripcion=desc, origen_descripcion=origen_desc)
            e["pendiente"] = "monto"
            if not dry_run:
                self.dlg.guardar(grupo, sender, e)
            msgs = ["⚠️ ¿Cuál es el monto? Escríbelo con cifra (ej: '5000' o '5 mil') o envía la foto del recibo."]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, None, anal, cat, ent.metodo, desc,
                                            "pedir_monto", origen_desc=origen_desc,
                                            fecha=fecha, hora=hora, origen_fecha=origen_fecha,
                                            cand_fecha=candidatos_fecha(texto),
                                            cand_hora=candidatos_hora(texto)))
            return msgs

        e = self._nuevo_estado_registro(grupo, sender, texto, evidencia, usuario,
                                        monto=monto, cat=cat, sub=sub,
                                        fecha=fecha, hora=hora,
                                        origen_fecha_hora=origen_fecha,
                                        descripcion=desc, origen_descripcion=origen_desc)

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
                                            "pedir_categoria", origen_desc=origen_desc,
                                            fecha=fecha, hora=hora, origen_fecha=origen_fecha,
                                            cand_fecha=candidatos_fecha(texto),
                                            cand_hora=candidatos_hora(texto)))
            return msgs

        if not desc:
            e["pendiente"] = "descripcion"
            if not dry_run:
                self.dlg.guardar(grupo, sender, e)
            msgs = ["✍️ ¿Cuál es la descripción? (ej: 'mercado de la quincena')"]
            if dry_run:
                msgs.append(self._diag_json(ent.tipo, monto, anal, cat, ent.metodo, None,
                                            "pedir_descripcion", origen_desc="pendiente",
                                            fecha=fecha, hora=hora, origen_fecha=origen_fecha,
                                            cand_fecha=candidatos_fecha(texto),
                                            cand_hora=candidatos_hora(texto)))
            return msgs

        if not dry_run:
            self.dlg.guardar(grupo, sender, e)
        msgs = self._mostrar_checklist(grupo, sender, e, dry_run)
        if dry_run:
            msgs.append(self._diag_json(ent.tipo, monto, anal, cat, ent.metodo, desc,
                                        "checklist", origen_desc=origen_desc,
                                        fecha=fecha, hora=hora, origen_fecha=origen_fecha,
                                        cand_fecha=candidatos_fecha(texto),
                                        cand_hora=candidatos_hora(texto)))
        return msgs

    def _palabra_candidata(self, texto, ent):
        ap = self.ap
        if ap.search(texto):
            return None
        reglas = get_rules()
        if ent.comercio or reglas.match_categoria(texto):
            return None
        t = normalize(texto or "")
        t = re.sub(r"\b\$?\d[\d.,]*\s*(mil|k|m)?\b", " ", t)
        t = re.sub(r"\b(pagu?e|pago|pagar|gast[oe]|compr[oea]|recib[ií]|compr\w+)\w*\b", " ", t)
        t = re.sub(r"\b(por|en|de|un|una|el|la|los|las|con|a|para|y|al|que|mil|medio|media|mitad)\b", " ", t)
        t = re.sub(r"\b(nequi|daviplata|bancolombia|transferencia|efectivo|tarjeta|credito|debito|pse|nu|nubank|banco)\b", " ", t)
        t = re.sub(r"\b(pe[so]+)\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        frag = t.split()
        if not frag:
            return None
        cand = " ".join(frag[:2])
        return cand[:40] if len(cand) >= 3 else None

    def _validar(self, grupo, texto):
        """Auditoria deterministica de tablas, sin LLM."""
        t = (texto or "").lower()
        # determina grupo objetivo
        target = None
        if "personal" in t:
            target = "personal"
        elif "hogar" in t:
            target = "hogar"
        elif "andrea" in t:
            target = "andrea"
        elif "todo" in t or "todas" in t:
            target = None  # todos
        else:
            # por defecto audita el grupo donde se pidio
            target = grupo
        # si pide aprendizajes
        if "aprendizaje" in t or "aprendizajes" in t:
            try:
                return [auditoria.auditar_aprendizajes()]
            except Exception as e:
                return [f"No pude auditar aprendizajes: {e}"]
        # auditar tablas
        try:
            # if target is grupo, audita solo ese grupo, else todos
            if target is None:
                # todos
                res = auditoria.auditar(self.srv, None)
                # auditar(None) will do all, but we need to handle None case: our auditar expects grupo or None for all
                # our auditar currently handles None as all
                return [res]
            else:
                return [auditoria.auditar(self.srv, target)]
        except Exception as e:
            return [f"No pude auditar {target}: {e}"]

    # ================== PRESUPUESTO    # ================== PRESUPUESTO ==================
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


    def _consulta(self, grupo, texto):
        """Resumen deterministico por rango de fechas, sin LLM. Delega a reportes."""
        try:
            ini, fin = reportes.parse_rango(texto)
        except Exception:
            import datetime, calendar
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo("America/Bogota"))
            ini = datetime.date(now.year, now.month, 1)
            fin = datetime.date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
        sid = config.GROUPS[grupo][0]
        return [reportes.resumen(self.srv, sid, grupo, ini, fin)]

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
_SINONIMOS_CANCELAR = {"no", "nop", "n", "cancelar", "cancela", "salir", "salirme",
                       "no guardes", "no registrar", "descartar"}


def limitar(s, n):
    s = s or ""
    return s[:n]