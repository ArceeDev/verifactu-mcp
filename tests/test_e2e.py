"""MCP end to end: tools over the protocol, debits, no charge on bad input, paywall, hosted licenses."""

from __future__ import annotations

import base64
import importlib
import json

import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from conftest import PUB
from test_core import AEAT_XML, CHAIN, H3, INVOICE, R1, R2


@pytest.fixture(scope="module")
def license_(gateway):
    return gateway["issue"]("buyer@gestoria.test")


def _env(monkeypatch, gateway, key):
    monkeypatch.setenv("KEYFLINT_LICENSE", key)
    monkeypatch.setenv("KEYFLINT_PUBLIC_KEYS", PUB)
    monkeypatch.setenv("KEYFLINT_URL", gateway["url"])
    monkeypatch.setenv("KEYFLINT_CHECKOUT_URL", "https://example.com/buy")
    monkeypatch.setenv("KEYFLINT_CLOCK_STATE", "off")
    monkeypatch.delenv("VERIFACTU_TRANSPORT", raising=False)
    monkeypatch.delenv("VERIFACTU_PRICES", raising=False)


@pytest.fixture()
def server(gateway, license_, monkeypatch):
    _env(monkeypatch, gateway, license_["license_key"])
    import verifactu_mcp.server as srv

    return importlib.reload(srv)


async def call(srv, name, **args):
    async with Client(srv.mcp) as c:
        res = await c.call_tool(name, args)
    text = res.content[0].text if res.content else ""
    data = res.structured_content or (json.loads(text) if text.startswith("{") else None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return res.is_error, data, text


async def balance(srv):
    _, data, _ = await call(srv, "credits_balance")
    return data["balance"]


async def test_tools_listed(server):
    async with Client(server.mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
    assert set(server.DEFAULT_PRICES) | {"check_qr_url", "validate_nif", "verifactu_calendar", "pricing",
                                         "credits_balance"} <= tools


async def test_hash_charges(server):
    before = await balance(server)
    err, data, text = await call(server, "verifactu_hash", record=R1)
    assert not err, text
    assert data["huella"] == R1["Huella"] and data["valid"]
    assert data["billing"]["credits_charged"] == 1 and await balance(server) == before - 1


async def test_chain(server):
    err, data, text = await call(server, "validate_invoice_chain", records=CHAIN)
    assert not err, text
    assert data["valid"] and data["last_huella"] == H3 and data["billing"]["credits_charged"] == 3


async def test_chain_empty_is_free(server):
    before = await balance(server)
    err, _, text = await call(server, "validate_invoice_chain", records=[])
    assert err and "obligatoria" in text
    assert await balance(server) == before


async def test_xml_with_xsd(server):
    err, data, text = await call(server, "validate_verifactu_xml",
                                 content_base64=base64.b64encode(AEAT_XML).decode())
    assert not err, text
    assert data["schema_valid"] is True, data["schema_errors"]
    assert data["valid"] and data["records"] == 2


async def test_xml_without_records_is_free(server):
    before = await balance(server)
    err, _, text = await call(server, "validate_verifactu_xml", content="<a><b/></a>")
    assert err and "no contiene" in text
    assert await balance(server) == before


async def test_qr(server):
    err, data, text = await call(server, "generate_verifactu_qr", nif="89890001K", numserie="A/1",
                                 fecha="05-11-2026", importe="121.00", image_format="svg")
    assert not err, text
    assert data["url"].endswith("numserie=A%2F1&fecha=05-11-2026&importe=121.00")
    assert data["mime_type"] == "image/svg+xml" and data["print_rules"]["etiqueta_superior"] == "QR tributario:"


async def test_qr_bad_nif_is_free(server):
    before = await balance(server)
    err, _, text = await call(server, "generate_verifactu_qr", nif="X", numserie="1", fecha="01-01-2026",
                              importe="1")
    assert err and "NIF" in text
    assert await balance(server) == before


async def test_build_and_validate_facturae(server):
    err, data, text = await call(server, "build_facturae", invoice=INVOICE)
    assert not err, text
    assert data["schema_valid"] and data["totals"]["total_factura"] == "404.81"
    assert data["billing"]["credits_charged"] == 3
    err, rep, text = await call(server, "validate_facturae", content_base64=data["xml_base64"])
    assert not err, text
    assert rep["valid"] and rep["signed"] is False and rep["billing"]["credits_charged"] == 2


async def test_build_facturae_bad_is_free(server):
    before = await balance(server)
    err, _, text = await call(server, "build_facturae", invoice={**INVOICE, "lines": []})
    assert err and "línea" in text
    assert await balance(server) == before


async def test_free_tools(server):
    _, cal, _ = await call(server, "verifactu_calendar")
    assert cal["verifactu"]["en_vigor"][0]["fecha"] == "2027-01-01"
    _, nif, _ = await call(server, "validate_nif", nif="89890001K")
    assert nif["valid"]
    _, q, _ = await call(server, "check_qr_url", url="https://prewww2.aeat.es/wlpl/TIKE-CONT/ValidarQR?"
                                                     "nif=89890001K&numserie=1&fecha=01-01-2026&importe=1")
    assert q["valid"] and q["environment"] == "pruebas"
    _, p, _ = await call(server, "pricing")
    assert p["credits_per_call"]["validate_invoice_chain"] == 3


async def test_paywall_when_out_of_credits(gateway, monkeypatch):
    lic = gateway["issue"](tier="community")  # 25 credits
    _env(monkeypatch, gateway, lic["license_key"])
    monkeypatch.setenv("VERIFACTU_PRICES", "validate_invoice_chain=20")
    import verifactu_mcp.server as srv

    srv = importlib.reload(srv)
    err, _, _ = await call(srv, "validate_invoice_chain", records=CHAIN)
    assert not err
    async with Client(srv.mcp) as c:
        with pytest.raises(MCPError) as exc:
            await c.call_tool("validate_invoice_chain", {"records": CHAIN})
    assert "example.com/buy" in str(exc.value) or "credit" in str(exc.value).lower()


async def test_invalid_license_is_refused(gateway, monkeypatch):
    _env(monkeypatch, gateway, "LIC-not-a-real-license")
    import verifactu_mcp.server as srv

    srv = importlib.reload(srv)
    async with Client(srv.mcp) as c:
        with pytest.raises(MCPError) as exc:
            await c.call_tool("verifactu_hash", {"record": R1})
    assert exc.value.code == -32060 and "example.com/buy" in str(exc.value)


async def test_hosted_requires_license_header(gateway, license_, monkeypatch):
    _env(monkeypatch, gateway, license_["license_key"])
    monkeypatch.setenv("VERIFACTU_TRANSPORT", "http")
    import verifactu_mcp.server as srv

    srv = importlib.reload(srv)
    with pytest.raises(Exception) as exc:
        srv.billing.client(None)
    assert "Bearer" in str(exc.value)
    assert srv.billing.client({"Authorization": f"Bearer {license_['license_key']}"}) is not None
