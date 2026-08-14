"""Regresion del clasificador del adapter de WhatsApp (CAMBIO 1 y 2).

Cubre el incidente del 2026-08-13: "Pan panocha, valor: 20 mil, snack"
era clasificado como NO gasto (falso negativo) y caia al LLM.
Se verifica que N mil / Nk / 'valor' (y variantes) sean gasto, que las
consultas no se rompan y que un mensaje sin clasificar en un grupo de
finanzas responda con el texto fijo SIN invocar al agente LLM.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, "/home/soporte/.hermes/hermes-agent")

import adapter_whatsapp as _adapter_mod  # noqa: E402
from gateway.platforms.base import MessageType  # noqa: E402

HOGAR_JID = "120363426158712224@s.whatsapp.net"
PERSONAL_JID = "120363426559924341@s.whatsapp.net"
ANDREA_JID = "120363429174326751@s.whatsapp.net"

FIXED_TEXT = ("No entendí el monto o la intención. "
              "Intenta con: 'pagué 20 mil en pan por snack' o usa 'ayuda'.")


class _Source:
    def __init__(self, chat_id):
        self.chat_id = chat_id


class _Event:
    def __init__(self, chat_id, message_type=MessageType.TEXT, media_urls=None):
        self.source = _Source(chat_id)
        self.message_type = message_type
        self.media_urls = media_urls or []


@pytest.fixture
def adapter():
    a = _adapter_mod.WhatsAppAdapter.__new__(_adapter_mod.WhatsAppAdapter)
    for suf in ("personal", "hogar", "andrea"):
        p = "/tmp/gasto_pendiente_%s.json" % suf
        if os.path.exists(p):
            os.remove(p)
    return a


@pytest.mark.parametrize("texto", [
    "Pan panocha, valor: 20 mil, snack",
    "Pan panocha, valor: 20000, snack",
    "20k de gasolina",
    "valor 20000",
    "pagué 20 mil en pan por snack",
    "20 mil de mercado",
    "1.500 mil de arriendo",
])
def test_incidente_y_variantes_son_gasto(adapter, texto):
    assert adapter._looks_like_expense(texto)


@pytest.mark.parametrize("texto", [
    "gastos de agosto",
    "cuanto llevamos este mes",
    "resumen de gastos del mes",
    "gastos del mes pasado",
])
def test_consultas_siguen_siendo_consultas(adapter, texto):
    assert not adapter._looks_like_expense(texto)
    assert adapter._looks_like_query(texto)


def test_saludo_no_es_ni_gasto_ni_consulta(adapter):
    assert not adapter._looks_like_expense("hola")
    assert not adapter._looks_like_query("hola")


def _run(coro):
    return asyncio.run(coro)


def test_no_clasificado_responde_fijo_y_bloquea_llm(adapter, monkeypatch):
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        sent.append((chat_id, content, reply_to))
        return None

    monkeypatch.setattr(adapter, "send", fake_send)
    for jid in (HOGAR_JID, PERSONAL_JID, ANDREA_JID):
        before = len(sent)
        msg_data = {"body": "hola", "id": "msg-1"}
        event = _Event(jid)
        ok = _run(adapter._maybe_handle_expense_direct(msg_data, event))
        assert ok is True, "el turno debe consumirse sin caer al LLM"
        assert len(sent) == before + 1, jid
        chat_id, content, reply_to = sent[-1]
        assert chat_id == jid
        assert content == FIXED_TEXT
        assert reply_to == "msg-1"


def test_mensaje_sin_clasificar_entre_grupos_distintos_no_responde(adapter, monkeypatch):
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        sent.append((chat_id, content, reply_to))
        return None

    monkeypatch.setattr(adapter, "send", fake_send)
    msg_data = {"body": "hola", "id": "msg-2"}
    event = _Event("999999999999999@s.whatsapp.net")
    ok = _run(adapter._maybe_handle_expense_direct(msg_data, event))
    assert ok is False
    assert sent == []


def test_expense_real_no_dispara_respuesta_fija(adapter, monkeypatch):
    sent = []

    async def fake_send(chat_id, content, reply_to=None, metadata=None):
        sent.append((chat_id, content, reply_to))
        return None

    monkeypatch.setattr(adapter, "send", fake_send)
    tmp_home = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__no_gasto_home__")
    monkeypatch.setenv("HERMES_HOME", tmp_home)
    msg_data = {"body": "pagué 20 mil en pan por snack", "id": "msg-3"}
    event = _Event(HOGAR_JID)
    ok = _run(adapter._maybe_handle_expense_direct(msg_data, event))
    assert ok is False, "sin scripts/gasto.py el flujo no debe falsamente 'atender'"
    assert sent == []
