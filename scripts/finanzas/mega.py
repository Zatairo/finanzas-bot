"""mega.py — Configuracion segura y desacoplada para recibos en Mega.

FASE 2 (solo base de configuracion): este modulo NUNCA autentica contra Mega,
no sube archivos, no crea enlaces publicos y no persiste secretos. La evidencia
se tratara como privada; en la hoja solo se guardara una referencia interna
(mega_path, mega_node_id o evidencia_estado), jamas password/token/URL publica.

Prioridad de resolucion (mayor a menor):
  1. Variables de entorno: FINANZAS_MEGA_ENABLED, FINANZAS_MEGA_EMAIL,
     FINANZAS_MEGA_PASSWORD, FINANZAS_MEGA_FOLDER, FINANZAS_MEGA_CONFIG_PATH.
  2. Archivo local $HERMES_HOME/scripts/mega_config.json.
  3. Valores seguros por defecto: enabled=false.

Regla de permisos: si el archivo real existe y NO tiene permisos restrictivos
(0600), Mega queda deshabilitado y el motivo se reporta sin revelar secretos.

Contrato de evidencia (integracion posterior):
  evidencia_estado: pendiente | subida | error | deshabilitada
  mega_path: ruta privada determinista dentro de la carpeta raiz.
  mega_error: mensaje redactado, sin secretos.
  operation_id: clave de idempotencia de la subida.
"""
import dataclasses
import datetime
import json
import os
import re
import stat
from zoneinfo import ZoneInfo

_DEFAULT_GROUPS = {"personal": "Personal", "hogar": "Hogar", "andrea": "Andrea"}

# Estados validos del contrato de evidencia (ver README / intents futuros).
EVIDENCIA_ESTADOS = ("pendiente", "subida", "error", "deshabilitada")

_KEYS = ("enabled", "email", "password", "folder", "create_group_folders", "groups")

_BOGOTA = ZoneInfo("America/Bogota")


@dataclasses.dataclass(frozen=True)
class MegaConfig:
    """Configuracion Mega ya resuelta. Los secretos jamas se imprimen."""

    enabled: bool = False
    email: str = ""
    password: str = ""
    folder: str = "Recibos"
    create_group_folders: bool = True
    groups: dict = dataclasses.field(default_factory=lambda: dict(_DEFAULT_GROUPS))
    config_path: str = ""
    reason: str = ""
    perms: str = None

    def redacted(self):
        """Dict seguro para logs/dry-run: sin password ni email completos."""
        return {
            "enabled": self.enabled,
            "folder": self.folder,
            "create_group_folders": self.create_group_folders,
            "groups": dict(self.groups),
            "config_path": self.config_path,
            "permisos": self.perms,
            "email_configurado": bool(self.email),
            "password_configurado": bool(self.password),
            "reason": self.reason,
        }

    def __repr__(self):
        return "MegaConfig(%s)" % json.dumps(self.redacted(), ensure_ascii=False, sort_keys=True)

    __str__ = __repr__


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    ok: bool
    enabled: bool
    errors: tuple = ()
    warnings: tuple = ()

    def __repr__(self):
        return "ValidationResult(ok=%s, enabled=%s, errors=%r)" % (self.ok, self.enabled, self.errors)


def default_config_path():
    """Ruta por defecto del archivo real: $HERMES_HOME/scripts/mega_config.json."""
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, "scripts", "mega_config.json")


def _env_bool_flag(cfg, name, key):
    v = os.environ.get(name)
    if v is None:
        return
    cfg[key] = v.strip().lower() in ("1", "true", "yes", "on")


def load_mega_config(path=None):
    """Resuelve la configuracion con prioridad env > archivo > defaults.

    Nunca lanza por archivo faltante o mal formado: en esos casos devuelve
    defaults seguros (enabled=false) y explica el motivo en `reason`.
    """
    path = path or os.environ.get("FINANZAS_MEGA_CONFIG_PATH") or default_config_path()
    cfg = {
        "enabled": False,
        "email": "",
        "password": "",
        "folder": "Recibos",
        "create_group_folders": True,
        "groups": dict(_DEFAULT_GROUPS),
        "config_path": path,
        "reason": "",
        "perms": None,
    }
    if os.path.isfile(path):
        perms = stat.S_IMODE(os.stat(path).st_mode)
        cfg["perms"] = "0%o" % perms
        if perms & 0o077:
            # permisos inseguros -> Mega deshabilitado, sin revelar contenido
            cfg["enabled"] = False
            cfg["reason"] = ("configuracion Mega deshabilitada: permisos inseguros (%s) "
                             "en el archivo de configuracion" % "0%o" % perms)
        else:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("no es un objeto JSON")
                for k in _KEYS:
                    if k in data:
                        cfg[k] = data[k]
                if isinstance(data.get("groups"), dict):
                    g = {k: v for k, v in data["groups"].items() if isinstance(v, str)}
                    cfg["groups"] = dict(_DEFAULT_GROUPS, **g)
            except (ValueError, OSError):
                cfg["enabled"] = False
                cfg["reason"] = "configuracion Mega invalida (el archivo no es JSON valido)"
    # las variables de entorno tienen prioridad (a > b > c)
    _env_bool_flag(cfg, "FINANZAS_MEGA_ENABLED", "enabled")
    if "FINANZAS_MEGA_EMAIL" in os.environ:
        cfg["email"] = os.environ["FINANZAS_MEGA_EMAIL"].strip()
    if "FINANZAS_MEGA_PASSWORD" in os.environ:
        cfg["password"] = os.environ["FINANZAS_MEGA_PASSWORD"].strip()
    if "FINANZAS_MEGA_FOLDER" in os.environ:
        cfg["folder"] = os.environ["FINANZAS_MEGA_FOLDER"].strip()
    # coercion de tipos para que la config nunca sea ambiguia
    cfg["enabled"] = bool(cfg["enabled"])
    cfg["create_group_folders"] = bool(cfg["create_group_folders"])
    if not isinstance(cfg["groups"], dict):
        cfg["groups"] = dict(_DEFAULT_GROUPS)
    # los permisos inseguros o el JSON invalido deshabilitan SIEMPRE,
    # incluso si una variable de entorno intenta habilitar Mega
    if cfg["reason"]:
        cfg["enabled"] = False
    return MegaConfig(**cfg)


def _problems(cfg):
    errs = []
    if cfg.enabled:
        if not cfg.email:
            errs.append("falta email para habilitar Mega")
        if not cfg.password:
            errs.append("falta password para habilitar Mega")
    if cfg.reason:
        errs.append(cfg.reason)
    return errs


def validate_mega_config(path=None):
    """Valida la configuracion. Devuelve ValidationResult, nunca lanza."""
    cfg = load_mega_config(path)
    warns = []
    if not os.path.isfile(cfg.config_path):
        warns.append("sin archivo de configuracion (se usan valores por defecto)")
    if cfg.perms:
        warns.append("permisos del archivo de configuracion: %s" % cfg.perms)
    errs = _problems(cfg)
    return ValidationResult(ok=not errs, enabled=cfg.enabled,
                            errors=tuple(errs), warnings=tuple(warns))


def mega_enabled(path=None):
    """True solo si la config resolvio enabled=true y es coherente."""
    vr = validate_mega_config(path)
    return bool(vr.ok and vr.enabled)


def _safe(name):
    """Componente de ruta saneado: sin separadores ni traversal."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", (name or "")).strip("._")


def evidence_destination(group, operation_id, filename, dt=None, path=None):
    """Ruta privada determinista: Recibos/<Grupo>/<AAAA>/<MM>/<op>__<archivo>.

    Nunca devuelve URL publica; es solo una referencia interna de carpeta.
    """
    cfg = load_mega_config(path)
    d = dt or datetime.datetime.now(_BOGOTA)
    base = _safe((cfg.folder or "Recibos").strip("/")) or "Recibos"
    seg = [base]
    if cfg.create_group_folders:
        g = (cfg.groups or {}).get(group)
        g = g if isinstance(g, str) and g else (group or "General")
        seg.append(_safe(g) or "General")
    seg.append("%04d" % d.year)
    seg.append("%02d" % d.month)
    archivo = _safe(os.path.basename(filename or "recibo.jpg")) or "recibo.jpg"
    op = _safe(operation_id) or "op"
    return "/".join(seg + ["%s__%s" % (op, archivo)])


def dry_run_info(group, operation_id, filename, dt=None, path=None):
    """Dry-run de Mega: NUNCA autentica ni toca archivos remotos.

    Muestra mega_enabled, evidencia_estado_propuesto y mega_path_propuesto.
    """
    enabled = mega_enabled(path)
    return {
        "mega_enabled": enabled,
        "evidencia_estado_propuesto": "pendiente" if enabled else "deshabilitada",
        "mega_path_propuesto": evidence_destination(group, operation_id, filename, dt, path),
    }
