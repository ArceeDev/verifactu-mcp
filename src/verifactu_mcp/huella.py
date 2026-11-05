"""VERI*FACTU fingerprint ("huella") and chain checks.

Source: AEAT, "Detalle de las especificaciones técnicas para generación de la huella o hash de los registros de
facturación" (Orden HAC/1177/2024, art. 13). SHA-256 over the UTF-8 string of ``name=value`` pairs joined by
``&``, values trimmed, previous fingerprint empty for the first record; output 64 uppercase hex characters.

    Alta:      IDEmisorFactura, NumSerieFactura, FechaExpedicionFactura, TipoFactura, CuotaTotal,
               ImporteTotal, Huella (previous), FechaHoraHusoGenRegistro
    Anulación: IDEmisorFacturaAnulada, NumSerieFacturaAnulada, FechaExpedicionFacturaAnulada,
               Huella (previous), FechaHoraHusoGenRegistro

The three AEAT reference vectors are in tests/test_huella.py.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Iterable

from lxml import etree

from . import ids

TIPOS_FACTURA = {
    "F1": "Factura completa (art. 6, 7.2 y 7.3 RD 1619/2012)",
    "F2": "Factura simplificada / sin identificación del destinatario (art. 6.1.d)",
    "F3": "Factura emitida en sustitución de facturas simplificadas",
    "R1": "Rectificativa (art. 80.1, 80.2 y error fundado de derecho)",
    "R2": "Rectificativa (art. 80.3, concurso)",
    "R3": "Rectificativa (art. 80.4, créditos incobrables)",
    "R4": "Rectificativa (resto)",
    "R5": "Rectificativa de facturas simplificadas",
}
ALTA_FIELDS = ("IDEmisorFactura", "NumSerieFactura", "FechaExpedicionFactura", "TipoFactura", "CuotaTotal",
               "ImporteTotal")
ANUL_FIELDS = ("IDEmisorFacturaAnulada", "NumSerieFacturaAnulada", "FechaExpedicionFacturaAnulada")
AMOUNT_RE = re.compile(r"[+-]?\d{1,12}(\.\d{0,2})?")
DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})")
HEX64 = re.compile(r"[0-9A-F]{64}")
MAX_RECORDS = 20_000


class HuellaError(ValueError):
    pass


def _v(x: Any) -> str:
    return "" if x is None else str(x).strip()


def hash_input(kind: str, fields: dict[str, Any], previous: str = "", generated_at: str = "") -> str:
    names = ALTA_FIELDS if kind == "alta" else ANUL_FIELDS
    parts = [f"{n}={_v(fields.get(n))}" for n in names]
    parts += [f"Huella={_v(previous)}", f"FechaHoraHusoGenRegistro={_v(generated_at)}"]
    return "&".join(parts)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


def _strip_zeros(amount: str) -> str:
    if "." not in amount:
        return amount
    out = amount.rstrip("0").rstrip(".")
    return out or "0"


# --- normalisation of loosely written input -------------------------------------------------------------------

_ALIASES = {
    "nif": "IDEmisorFactura", "idemisorfactura": "IDEmisorFactura", "issuer_nif": "IDEmisorFactura",
    "numseriefactura": "NumSerieFactura", "numserie": "NumSerieFactura", "invoice_number": "NumSerieFactura",
    "fechaexpedicionfactura": "FechaExpedicionFactura", "fecha": "FechaExpedicionFactura",
    "issue_date": "FechaExpedicionFactura", "tipofactura": "TipoFactura", "invoice_type": "TipoFactura",
    "cuotatotal": "CuotaTotal", "tax_total": "CuotaTotal", "importetotal": "ImporteTotal",
    "total": "ImporteTotal", "idemisorfacturaanulada": "IDEmisorFacturaAnulada",
    "numseriefacturaanulada": "NumSerieFacturaAnulada",
    "fechaexpedicionfacturaanulada": "FechaExpedicionFacturaAnulada",
    "huella": "Huella", "hash": "Huella", "fechahorahusogenregistro": "FechaHoraHusoGenRegistro",
    "generated_at": "FechaHoraHusoGenRegistro", "huellaanterior": "HuellaAnterior",
    "previous_hash": "HuellaAnterior", "primerregistro": "PrimerRegistro", "tiporegistro": "TipoRegistro",
    "kind": "TipoRegistro", "tipohuella": "TipoHuella",
}


def normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    for k, v in raw.items():
        name = _ALIASES.get(str(k).replace("-", "").replace(" ", "").lower(), k)
        if isinstance(v, dict) and name in ("RegistroAnterior", "IDFactura"):
            rec[name] = {_ALIASES.get(a.lower(), a): b for a, b in v.items()}
        else:
            rec[name] = v
    kind = _v(rec.get("TipoRegistro")).lower()
    if kind.startswith("anul") or (not kind and rec.get("NumSerieFacturaAnulada")):
        rec["TipoRegistro"] = "anulacion"
    else:
        rec["TipoRegistro"] = "alta"
    ant = rec.get("RegistroAnterior")
    if isinstance(ant, dict) and not rec.get("HuellaAnterior"):
        rec["HuellaAnterior"] = ant.get("Huella")
    return rec


def to_aeat_date(value: str) -> str:
    """'2026-03-01', '01/03/2026' or '01-03-2026' -> '01-03-2026'."""
    v = _v(value)
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    raise HuellaError(f"fecha no válida: {value!r} (usa DD-MM-AAAA)")


# --- field checks ---------------------------------------------------------------------------------------------

def field_issues(rec: dict[str, Any], strict_dates: bool = True) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def err(field: str, msg: str, level: str = "error") -> None:
        out.append({"level": level, "field": field, "message": msg})

    alta = rec["TipoRegistro"] == "alta"
    names = ALTA_FIELDS if alta else ANUL_FIELDS
    for n in names:
        if not _v(rec.get(n)):
            err(n, "campo obligatorio vacío")
    nif = _v(rec.get(names[0]))
    if nif:
        chk = ids.validate_nif(nif)
        if len(nif) != 9:
            err(names[0], "el NIF debe tener 9 caracteres")
        elif not chk["valid"]:
            err(names[0], f"NIF con dígito de control incorrecto ({chk.get('type', 'patrón desconocido')})")
    num = _v(rec.get(names[1]))
    if len(num) > 60:
        err(names[1], "NumSerieFactura supera 60 caracteres")
    if num and any(ord(c) < 32 or ord(c) > 126 for c in num):
        err(names[1], "NumSerieFactura con caracteres fuera de ASCII imprimible (0x20-0x7E)")
    fecha = _v(rec.get(names[2]))
    if fecha and not DATE_RE.fullmatch(fecha):
        err(names[2], "formato de fecha DD-MM-AAAA obligatorio")
    if alta:
        tipo = _v(rec.get("TipoFactura"))
        if tipo and tipo not in TIPOS_FACTURA:
            err("TipoFactura", f"valor {tipo!r} no admitido ({', '.join(TIPOS_FACTURA)})")
        for n in ("CuotaTotal", "ImporteTotal"):
            a = _v(rec.get(n))
            if a and not AMOUNT_RE.fullmatch(a):
                err(n, f"importe {a!r} no válido: punto decimal, máximo 12 enteros y 2 decimales")
    ts = _v(rec.get("FechaHoraHusoGenRegistro"))
    if not ts:
        err("FechaHoraHusoGenRegistro", "campo obligatorio vacío")
    elif not TS_RE.fullmatch(ts):
        err("FechaHoraHusoGenRegistro", "formato AAAA-MM-DDThh:mm:ss+hh:mm (con huso horario) obligatorio")
    elif fecha and DATE_RE.fullmatch(fecha) and strict_dates:
        try:
            gen = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
            exp = datetime.strptime(fecha, "%d-%m-%Y").date()
            if exp > gen:
                err(names[2], "fecha de expedición posterior a la generación del registro", "warning")
        except ValueError:
            err("FechaHoraHusoGenRegistro", "fecha u hora inexistente")
    th = _v(rec.get("TipoHuella"))
    if th and th != "01":
        err("TipoHuella", "solo se admite 01 (SHA-256)")
    return out


def compute(rec: dict[str, Any], previous: str | None = None) -> dict[str, Any]:
    prev = _v(rec.get("HuellaAnterior") if previous is None else previous)
    text = hash_input(rec["TipoRegistro"], rec, prev, _v(rec.get("FechaHoraHusoGenRegistro")))
    return {"huella": sha(text), "cadena": text}


def _ident(rec: dict[str, Any]) -> tuple[str, str, str]:
    names = ALTA_FIELDS if rec["TipoRegistro"] == "alta" else ANUL_FIELDS
    return tuple(_v(rec.get(n)) for n in names[:3])  # type: ignore[return-value]


def _ts(rec: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(_v(rec.get("FechaHoraHusoGenRegistro")).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_chain(records: Iterable[dict[str, Any]], continues_from: str = "") -> dict[str, Any]:
    recs = [normalize_record(r) for r in records]
    if not recs:
        raise HuellaError("no hay registros que validar")
    if len(recs) > MAX_RECORDS:
        raise HuellaError(f"máximo {MAX_RECORDS} registros por llamada")
    issues: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen_alta: dict[tuple[str, str, str], int] = {}
    nifs: set[str] = set()
    prev_rec: dict[str, Any] | None = None
    prev_hash = _v(continues_from).upper()
    prev_ts: datetime | None = None

    for i, rec in enumerate(recs):
        pos = i + 1

        def add(level: str, msg: str, field: str = "") -> None:
            issues.append({"record": pos, "level": level, "field": field, "message": msg})

        for it in field_issues(rec):
            add(it["level"], it["message"], it["field"])
        ident = _ident(rec)
        nifs.add(ident[0])
        declared_prev = _v(rec.get("HuellaAnterior")).upper()
        first_flag = _v(rec.get("PrimerRegistro")).upper() == "S"

        if prev_rec is None and not prev_hash:
            if declared_prev:
                add("warning", "el primer registro enlaza con una huella anterior que no está en el lote: cadena "
                               "parcial (pasa continues_from para comprobar el enlace)")
                expected_prev = declared_prev
            else:
                expected_prev = ""
        else:
            expected_prev = prev_hash
            if first_flag:
                add("error", "PrimerRegistro=S pero hay registros anteriores en la cadena", "PrimerRegistro")
            if declared_prev and declared_prev != prev_hash:
                add("error", f"rotura de cadena: la huella anterior declarada {declared_prev[:12]}… no coincide con "
                             f"la del registro previo {prev_hash[:12]}…", "Huella")
            ant = rec.get("RegistroAnterior")
            if isinstance(ant, dict) and prev_rec is not None:
                want = _ident(prev_rec)
                got = (_v(ant.get("IDEmisorFactura")), _v(ant.get("NumSerieFactura")),
                       _v(ant.get("FechaExpedicionFactura")))
                if got != want:
                    add("error", f"RegistroAnterior {got} no identifica al registro previo {want}",
                        "RegistroAnterior")

        calc = compute(rec, expected_prev)
        stored = _v(rec.get("Huella")).upper()
        status = "calculada"
        if stored:
            if not HEX64.fullmatch(stored):
                add("error", "Huella almacenada no es SHA-256 en hexadecimal (64 caracteres)", "Huella")
                status = "incorrecta"
            elif stored == calc["huella"]:
                status = "correcta"
            else:
                alt = dict(rec)
                for n in ("CuotaTotal", "ImporteTotal"):
                    if n in alt:
                        alt[n] = _strip_zeros(_v(alt[n]))
                if rec["TipoRegistro"] == "alta" and compute(alt, expected_prev)["huella"] == stored:
                    status = "correcta_sin_ceros"
                    add("warning", "la huella cuadra solo quitando ceros finales de los importes: el registro "
                                   "se generó con importes distintos a los informados", "Huella")
                else:
                    status = "incorrecta"
                    add("error", "la huella almacenada no corresponde a los datos del registro (registro alterado "
                                 "o huella mal calculada)", "Huella")

        ts = _ts(rec)
        if ts and prev_ts and ts < prev_ts:
            add("warning", "FechaHoraHusoGenRegistro anterior a la del registro previo", "FechaHoraHusoGenRegistro")
        prev_ts = ts or prev_ts

        if rec["TipoRegistro"] == "alta":
            if ident in seen_alta:
                add("warning", f"factura ya registrada en el registro {seen_alta[ident]} (solo válido como "
                               "subsanación)", "NumSerieFactura")
            seen_alta[ident] = pos
        elif ident not in seen_alta:
            add("warning", "anulación de una factura que no aparece en el lote", "NumSerieFacturaAnulada")

        results.append({"record": pos, "tipo": rec["TipoRegistro"], "num_serie": ident[1], "fecha": ident[2],
                        "huella_calculada": calc["huella"], "huella_almacenada": stored or None,
                        "estado": status})
        # The chain continues from the stored fingerprint (what the next record links to); fall back to ours.
        prev_hash = stored if stored and HEX64.fullmatch(stored) else calc["huella"]
        prev_rec = rec

    if len(nifs) > 1:
        issues.append({"record": 0, "level": "warning", "field": "IDEmisorFactura",
                       "message": f"la cadena mezcla {len(nifs)} NIF emisores; cada obligado lleva su propia cadena"})
    errors = sum(1 for i in issues if i["level"] == "error")
    return {"valid": errors == 0, "records": len(recs), "errors": errors,
            "warnings": sum(1 for i in issues if i["level"] == "warning"),
            "last_huella": prev_hash, "issues": issues, "results": results}


# --- AEAT XML -------------------------------------------------------------------------------------------------

def _local(tag: Any) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _child(el: Any, name: str) -> Any:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: Any, *path: str) -> str:
    for p in path:
        if el is None:
            return ""
        el = _child(el, p)
    return (el.text or "").strip() if el is not None else ""


def parse_xml(data: bytes) -> tuple[Any, list[dict[str, Any]]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise HuellaError(f"XML mal formado: {exc}") from exc
    recs: list[dict[str, Any]] = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("RegistroAlta", "RegistroAnulacion"):
            continue
        enc = _child(el, "Encadenamiento")
        ant = _child(enc, "RegistroAnterior") if enc is not None else None
        rec: dict[str, Any] = {
            "FechaHoraHusoGenRegistro": _text(el, "FechaHoraHusoGenRegistro"),
            "TipoHuella": _text(el, "TipoHuella"), "Huella": _text(el, "Huella"),
            "PrimerRegistro": _text(enc, "PrimerRegistro") if enc is not None else "",
        }
        if ant is not None:
            rec["RegistroAnterior"] = {n: _text(ant, n) for n in
                                       ("IDEmisorFactura", "NumSerieFactura", "FechaExpedicionFactura", "Huella")}
        if name == "RegistroAlta":
            rec["TipoRegistro"] = "alta"
            for n in ALTA_FIELDS[:3]:
                rec[n] = _text(el, "IDFactura", n)
            for n in ALTA_FIELDS[3:]:
                rec[n] = _text(el, n)
        else:
            rec["TipoRegistro"] = "anulacion"
            for n in ANUL_FIELDS:
                rec[n] = _text(el, "IDFactura", n)
        recs.append(rec)
    return root, recs
