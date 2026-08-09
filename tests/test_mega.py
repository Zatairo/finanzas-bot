"""Pruebas de finanzas.mega (configuracion de recibos Mega).

Solo cubren la base de configuracion y el contrato de evidencia. NUNCA hacen
login real a Mega, no tocan archivos remotos ni datos reales: todo usa rutas
temporales y mocks de entorno.
"""
import datetime
import inspect
import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from finanzas import mega  # noqa: E402

_ENV_KEYS = ("FINANZAS_MEGA_ENABLED", "FINANZAS_MEGA_EMAIL", "FINANZAS_MEGA_PASSWORD",
             "FINANZAS_MEGA_FOLDER", "FINANZAS_MEGA_CONFIG_PATH")


@pytest.fixture(autouse=True)
def _limpia_env_mega(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _write(path, data, perms=0o600):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(path, perms)
    return str(path)


# 1. Mega deshabilitado por defecto
def test_mega_deshabilitado_por_defecto(tmp_path):
    p = str(tmp_path / "no_existe.json")
    assert mega.mega_enabled(p) is False
    cfg = mega.load_mega_config(p)
    assert cfg.enabled is False
    assert cfg.folder == "Recibos"
    assert cfg.groups == {"personal": "Personal", "hogar": "Hogar", "andrea": "Andrea"}
    vr = mega.validate_mega_config(p)
    assert vr.ok is True
    assert vr.enabled is False


# 2. Variables de entorno tienen prioridad
def test_variables_entorno_tienen_prioridad(tmp_path, monkeypatch):
    p = _write(tmp_path / "cfg.json", {"enabled": False, "email": "", "password": ""})
    monkeypatch.setenv("FINANZAS_MEGA_ENABLED", "true")
    monkeypatch.setenv("FINANZAS_MEGA_EMAIL", "yo@example.com")
    monkeypatch.setenv("FINANZAS_MEGA_PASSWORD", "s3cr3t")
    monkeypatch.setenv("FINANZAS_MEGA_FOLDER", "Recibos")
    cfg = mega.load_mega_config(p)
    assert cfg.enabled is True
    assert cfg.email == "yo@example.com"
    assert cfg.password == "s3cr3t"
    assert cfg.folder == "Recibos"
    assert mega.mega_enabled(p) is True


# 3. Archivo de configuracion valido
def test_config_valida(tmp_path):
    p = _write(tmp_path / "mega.json", {
        "enabled": True, "email": "yo@example.com", "password": "pw",
        "folder": "Recibos", "create_group_folders": True,
        "groups": {"personal": "Personal", "hogar": "Hogar", "andrea": "Andrea"},
    }, 0o600)
    cfg = mega.load_mega_config(p)
    assert cfg.enabled is True
    assert cfg.perms == "0600"
    assert mega.mega_enabled(p) is True


# 4. Archivo faltante
def test_config_faltante(tmp_path):
    p = str(tmp_path / "ausente.json")
    cfg = mega.load_mega_config(p)
    assert cfg.enabled is False
    assert cfg.email == ""
    vr = mega.validate_mega_config(p)
    assert vr.ok is True
    assert any("sin archivo" in w for w in vr.warnings)


# 5. Configuracion incompleta (enabled sin credenciales)
def test_config_incompleta(tmp_path):
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": "yo@example.com", "password": ""})
    vr = mega.validate_mega_config(p)
    assert vr.ok is False
    assert any("password" in e for e in vr.errors)
    assert mega.mega_enabled(p) is False


# 6. Permisos 0600 validos
def test_permisos_0600_validos(tmp_path):
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": "a@example.com", "password": "pw"}, 0o600)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert mega.mega_enabled(p) is True


# 7. Permisos inseguros deshabilitan Mega (incluso con env habilitando)
def test_permisos_inseguros_deshabilitan(tmp_path, monkeypatch):
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": "a@example.com", "password": "pw"}, 0o644)
    monkeypatch.setenv("FINANZAS_MEGA_ENABLED", "true")
    cfg = mega.load_mega_config(p)
    assert cfg.enabled is False
    assert "permisos" in cfg.reason
    assert "pw" not in cfg.reason
    assert mega.mega_enabled(p) is False


# 8. Rutas correctas para personal, hogar y andrea
def test_rutas_personal_hogar_andrea(tmp_path):
    dt = datetime.datetime(2026, 8, 9, 18, 31, 0)
    p = str(tmp_path / "mega.json")
    expected = {
        "personal": "Recibos/Personal/2026/08/202608091831__img_x.jpg",
        "hogar": "Recibos/Hogar/2026/08/202608091831__img_x.jpg",
        "andrea": "Recibos/Andrea/2026/08/202608091831__img_x.jpg",
    }
    for g, exp in expected.items():
        assert mega.evidence_destination(g, "202608091831", "img_x.jpg", dt=dt, path=p) == exp


def test_grupos_desde_archivo(tmp_path):
    p = _write(tmp_path / "mega.json", {
        "enabled": True, "email": "a@example.com", "password": "pw",
        "groups": {"personal": "Personal 1", "hogar": "Hogar"},
    })
    cfg = mega.load_mega_config(p)
    assert cfg.groups["personal"] == "Personal 1"
    assert cfg.groups["andrea"] == "Andrea"  # defaults preservados
    dest = mega.evidence_destination("personal", "op1", "img.jpg",
                                     dt=datetime.datetime(2026, 8, 9), path=p)
    assert dest == "Recibos/Personal_1/2026/08/op1__img.jpg"


def test_sin_carpetas_de_grupo(tmp_path):
    p = _write(tmp_path / "mega.json", {
        "enabled": True, "email": "a@example.com", "password": "pw",
        "create_group_folders": False,
    })
    dest = mega.evidence_destination("personal", "op1", "img.jpg",
                                     dt=datetime.datetime(2026, 8, 9), path=p)
    assert dest == "Recibos/2026/08/op1__img.jpg"


def test_saneo_de_ruta(tmp_path):
    p = str(tmp_path / "mega.json")
    dest = mega.evidence_destination("..", "../../etc", "../../etc/passwd.jpg",
                                     dt=datetime.datetime(2026, 8, 9), path=p)
    assert ".." not in dest
    assert dest == "Recibos/General/2026/08/etc__passwd.jpg"


# 9. No se generan URL publicas
def test_sin_urls_publicas(tmp_path):
    p = str(tmp_path / "mega.json")
    info = mega.dry_run_info("personal", "op1", "img.jpg", path=p)
    dest = mega.evidence_destination("personal", "op1", "img.jpg", path=p)
    cfg = mega.load_mega_config(p)
    for s in (json.dumps(info), dest, json.dumps(cfg.redacted()), repr(cfg)):
        assert "http" not in s
        assert "mega.nz" not in s
        assert "#" not in s


# 10. Dry-run no invoca cliente Mega y muestra los 3 campos
def test_dry_run_no_invoca_cliente_mega(tmp_path):
    src = inspect.getsource(mega)
    assert "requests" not in src and "urllib" not in src and "http" not in src
    p = str(tmp_path / "mega.json")
    info = mega.dry_run_info("hogar", "op-1", "recibo.jpg", path=p)
    assert set(info) == {"mega_enabled", "evidencia_estado_propuesto", "mega_path_propuesto"}
    assert info["mega_enabled"] is False
    assert info["evidencia_estado_propuesto"] == "deshabilitada"
    assert info["mega_path_propuesto"] == "Recibos/Hogar/2026/08/op-1__recibo.jpg"


def test_dry_run_pendiente_cuando_habilitado(tmp_path):
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": "a@example.com", "password": "pw"})
    info = mega.dry_run_info("personal", "op1", "img.jpg",
                             dt=datetime.datetime(2026, 8, 9), path=p)
    assert info["mega_enabled"] is True
    assert info["evidencia_estado_propuesto"] == "pendiente"
    assert info["mega_path_propuesto"] == "Recibos/Personal/2026/08/op1__img.jpg"


# 11. No se filtran email, password ni tokens en excepciones, JSON o logs
def test_no_se_filtran_secretos(tmp_path):
    secret = "password_super_secreta_123"
    mail = "usuario@example.com"
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": mail, "password": secret})
    cfg = mega.load_mega_config(p)
    blob = "\n".join([
        repr(cfg), str(cfg),
        json.dumps(cfg.redacted()),
        repr(mega.validate_mega_config(p)),
        cfg.reason or "",
    ])
    assert secret not in blob
    assert mail not in blob


def test_error_json_no_filtra_contenido(tmp_path):
    p = tmp_path / "mega.json"
    p.write_text('{"password": "clave_ultrasecreta",', encoding="utf-8")
    os.chmod(p, 0o600)
    cfg = mega.load_mega_config(str(p))
    assert cfg.enabled is False
    assert cfg.reason == "configuracion Mega invalida (el archivo no es JSON valido)"
    assert "clave_ultrasecreta" not in cfg.reason


# 12. FINANZAS_MEGA_CONFIG_PATH tiene prioridad sobre la ruta por defecto
def test_config_path_por_env(tmp_path, monkeypatch):
    p = _write(tmp_path / "mega.json", {"enabled": True, "email": "a@example.com", "password": "pw"})
    monkeypatch.setenv("FINANZAS_MEGA_CONFIG_PATH", str(p))
    cfg = mega.load_mega_config()
    assert cfg.config_path == str(p)
    assert cfg.enabled is True


def test_ruta_default_usa_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    assert mega.default_config_path() == str(tmp_path / "hh" / "scripts" / "mega_config.json")
