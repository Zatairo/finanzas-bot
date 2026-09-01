"""reportes.py — Resumen deterministico por rango de fechas, sin LLM."""
import calendar
import datetime
import re
from zoneinfo import ZoneInfo
from .normalize import normalize
from . import sheets
_TZ = ZoneInfo("America/Bogota")
_MESES = {"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,"noviembre":11,"diciembre":12}
_RE_ISO = re.compile(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b")
_RE_DMY = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_RE_YM = re.compile(r"\b(\d{4})[/-](\d{1,2})\b")
_RE_D_AL_D_MES = re.compile(r"\bdel?\s+(\d{1,2})\s+al\s+(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b")
def _now_bogota():
    return datetime.datetime.now(_TZ)
def _last_day(y,m):
    return calendar.monthrange(y,m)[1]
def _clamp_date(y,m,d):
    try:
        d=max(1,min(d,_last_day(y,m)))
        return datetime.date(y,m,d)
    except: return None
def parse_rango(texto, now=None):
    now=now or _now_bogota()
    t=normalize(texto or "")
    cur_y,cur_m=now.year,now.month
    fechas=[]
    for m in re.finditer(r"\b(?:\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", texto or ""):
        s=m.group(0)
        d=None
        if re.match(r"\d{4}[/-]", s):
            try: y,mo,da=map(int,re.split(r"[/-]",s)); d=datetime.date(y,mo,da)
            except: d=None
        else:
            try: da,mo,y=map(int,re.split(r"[/-]",s)); d=datetime.date(y,mo,da)
            except: d=None
        if d: fechas.append(d)
    if len(fechas)>=2:
        a,b=fechas[0],fechas[1]
        if a>b: a,b=b,a
        return a,b
    if len(fechas)==1:
        if re.search(r"\b(desde|entre|del)\b", t):
            a=fechas[0]; b=now.date()
            if a>b: a,b=b,a
            return a,b
        return fechas[0],fechas[0]
    m=_RE_D_AL_D_MES.search(t)
    if m:
        try:
            d1=int(m.group(1)); d2=int(m.group(2)); mes_n=_MESES.get(m.group(3)); y=int(m.group(4)) if m.group(4) else cur_y
            if mes_n:
                a=_clamp_date(y,mes_n,d1); b=_clamp_date(y,mes_n,d2)
                if a and b:
                    if a>b: a,b=b,a
                    return a,b
        except: pass
    if re.search(r"\beste mes\b", t):
        a=datetime.date(cur_y,cur_m,1); b=datetime.date(cur_y,cur_m,_last_day(cur_y,cur_m)); return a,b
    if re.search(r"\bmes pasado\b", t):
        y,mo=cur_y,cur_m-1
        if mo==0: y-=1; mo=12
        a=datetime.date(y,mo,1); b=datetime.date(y,mo,_last_day(y,mo)); return a,b
    if not _RE_ISO.search(t or "") and not _RE_DMY.search(t or ""):
        m2=_RE_YM.search(t)
        if m2:
            try:
                y,mo=int(m2.group(1)),int(m2.group(2))
                if 1<=mo<=12:
                    a=datetime.date(y,mo,1); b=datetime.date(y,mo,_last_day(y,mo)); return a,b
            except: pass
    m=re.search(r"\b([a-z]+)\s+de\s+(\d{4})\b", t)
    if m:
        mes_n=_MESES.get(m.group(1))
        if mes_n:
            try: y=int(m.group(2)); a=datetime.date(y,mes_n,1); b=datetime.date(y,mes_n,_last_day(y,mes_n)); return a,b
            except: pass
    tokens=t.split()
    for tok in reversed(tokens):
        mes_n=_MESES.get(tok)
        if mes_n:
            a=datetime.date(cur_y,mes_n,1); b=datetime.date(cur_y,mes_n,_last_day(cur_y,mes_n)); return a,b
    a=datetime.date(cur_y,cur_m,1); b=datetime.date(cur_y,cur_m,_last_day(cur_y,cur_m)); return a,b
def _to_float_monto(disp):
    try:
        s=str(disp).replace("$","").replace(",","").strip()
        s=s.replace(".","") if s.count(".")>1 else s
        if "." in s and "," not in s: s=s.split(".")[0]
        return float(s) if s else 0.0
    except: return 0.0
def _fmt_cop(n):
    try: return "${:,.0f}".format(float(n)).replace(",",".")
    except: return "$0"
def resumen(srv, sid, grupo, fecha_ini, fecha_fin):
    if srv is None: return "Sin conexion a Sheets. Intenta de nuevo."
    try: rows=sheets.leer_filas(srv,sid)
    except Exception as e: return f"No pude leer la hoja: {e}"
    ini_s=fecha_ini.isoformat(); fin_s=fecha_fin.isoformat()
    filtradas=[]
    for r in rows:
        if not sheets.fila_activa(r): continue
        if len(r) < 13: continue  # need at least 13 columns for r[12] (metodo)
        fecha=(r[1] or "").strip()[:10]
        if not fecha: continue
        if fecha<ini_s or fecha>fin_s: continue
        filtradas.append(r)
    dias=(fecha_fin-fecha_ini).days+1
    total_gasto=0.0; total_ingreso=0.0; n_gasto=0; n_ingreso=0
    por_cat={}; por_metodo={}; por_dia={}
    for r in filtradas:
        if len(r) < 13:
            continue  # skip rows with insufficient columns
        try: monto=_to_float_monto(r[6])
        except: monto=0.0
        tipo=(r[5] or "Gasto").strip().lower()
        cat=(r[8] or "Sin categoria").strip() or "Sin categoria"
        sub=(r[9] or "").strip()
        key=f"{cat}"+(f" - {sub}" if sub else "")
        metodo=(r[12] or "otro").strip() or "otro"
        fecha=(r[1] or "")[:10]
        # Fix: Ingreso categoria debe contar como ingreso aunque tipo sea gasto (datos legacy)
        es_ingreso = (tipo=="ingreso" or cat.lower().startswith("ingreso"))
        if es_ingreso: total_ingreso+=monto; n_ingreso+=1
        else: total_gasto+=monto; n_gasto+=1; por_cat[key]=por_cat.get(key,0.0)+monto; por_metodo[metodo]=por_metodo.get(metodo,0.0)+monto
        por_dia[fecha]=por_dia.get(fecha,0.0)+(monto if tipo!="ingreso" else 0)
    neto=total_ingreso-total_gasto
    rango_lbl=f"{ini_s} -> {fin_s} ({dias} dias)"
    lineas=[f"Resumen {grupo} | {rango_lbl}"]
    lineas.append(f"Aqui tienes el detalle:")
    lineas.append(f"  Gastos: {_fmt_cop(total_gasto)} ({n_gasto} regs)")
    lineas.append(f"  Ingresos: {_fmt_cop(total_ingreso)} ({n_ingreso} regs)")
    signo="+" if neto>=0 else "-"
    lineas.append(f"  Neto: {signo}{_fmt_cop(abs(neto))}")
    if not filtradas:
        lineas.append(""); lineas.append("Sin movimientos en ese rango."); lineas.append(f"Rango: {ini_s} a {fin_s} | hoja: {grupo}"); return "\n".join(lineas)
    if por_cat:
        lineas.append(""); lineas.append("Por categoria (gastos):")
        s=sorted(por_cat.items(),key=lambda x:-x[1])
        for k,v in s[:8]:
            pct=(v/total_gasto*100) if total_gasto else 0
            lineas.append(f"  - {k}: {_fmt_cop(v)} ({pct:.0f}%)")
        if len(s)>8:
            resto=sum(v for _,v in s[8:]); lineas.append(f"  - Otros: {_fmt_cop(resto)}")
    if por_metodo:
        lineas.append(""); lineas.append("Por metodo:")
        for k,v in sorted(por_metodo.items(),key=lambda x:-x[1])[:5]:
            lineas.append(f"  - {k}: {_fmt_cop(v)}")
    if dias>0 and total_gasto>0:
        lineas.append(""); lineas.append(f"Promedio gasto/dia: {_fmt_cop(total_gasto/dias)}")
        if por_dia:
            pico=max(por_dia,key=lambda k: por_dia[k]); lineas.append(f"Dia pico: {pico} ({_fmt_cop(por_dia[pico])})")
    lineas.append(""); lineas.append(f"Total regs: {len(filtradas)} | {ini_s} a {fin_s} | hoja: {grupo}")
    return "\n".join(lineas)
