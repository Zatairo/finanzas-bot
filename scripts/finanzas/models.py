"""models.py — Dataclasses ligeras para claridad en el pipeline."""
import dataclasses as _dc


@_dc.dataclass
class Entidades:
    monto: object = None          # numero o None
    monto_display: str = None
    fecha: str = None
    hora: str = None
    metodo: str = None
    categoria: str = None
    subcategoria: str = None
    comercio: str = None
    productos: list = None
    tipo: str = "Gasto"           # Gasto | Ingreso
    moneda: str = "COP"
    compartido: bool = False


@_dc.dataclass
class Aprendizaje:
    alias_normalizado: str
    alias_original: str
    tipo: str = "comercio"        # comercio|producto|categoria|metodo
    categoria: str = ""
    subcategoria: str = ""
    producto: str = ""
    usos: int = 1
    creado_en: str = ""
    actualizado_en: str = ""
    confirmado_por: str = ""
    activo: bool = True