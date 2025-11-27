# extract_real_estate.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import json
import time
import glob
import traceback
from typing import List, Dict, Any, Optional

import pandas as pd
from openai import OpenAI

# -----------------------------
# Config
# -----------------------------
DOWNLOAD_DIR = os.environ.get("SCRAPING_TOOL_DOWNLOAD_DIR", "descargas")
OUTPUT_XLSX = os.environ.get("SCRAPING_TOOL_OUTPUT_XLSX", "inmuebles.xlsx")
OUTPUT_JSON_DIR = os.environ.get("SCRAPING_TOOL_OUTPUT_JSON_DIR", "salidas_json")
MODEL = os.environ.get("SCRAPING_TOOL_MODEL", "gpt-5")

# Throttling / reintentos
REQUEST_SLEEP_SECONDS = 2.0          # pausa entre peticiones para ser amable
MAX_RETRIES = 4                      # reintentos ante 429/5xx
BACKOFF_BASE = 2.0                   # backoff exponencial

# -----------------------------
# Esquema objetivo (columnas)
# -----------------------------
COLUMNS = [
    # Fuente
    "source_filename", "source_filesize_bytes", "source_pages_estimated",
    # Identificación general
    "listing_type",       # compra | venta | subasta
    "property_type",      # apartamento, casa, local, solar/terreno, etc.
    "title",              # título breve si aplica
    "description",        # texto breve (1-3 líneas)
    # Ubicación
    "address", "district", "city", "province", "country",
    # Características
    "area_m2", "rooms", "bathrooms", "parking", "level_floors",
    # Precio
    "price_amount", "price_currency",  # ISO 4217 (DOP, USD)
    # Subastas (si aplica)
    "auction_date", "auction_time", "auction_location",
    # Datos legales / referencia
    "legal_ref", "cadastre_ref", "expedient_ref",
    # Contacto
    "contact_name", "contact_phone", "contact_email", "contact_website",
    # Institución
    "institution",
    # Metadatos/otros
    "page_number", "publication_date_iso", "notes",
]

# -----------------------------
# Prompt: pedimos JSON 100% limpio
# -----------------------------
SYSTEM_INSTRUCTIONS = """Eres un asistente experto en extraer información inmobiliaria desde avisos periodísticos en PDF (compra/venta/subasta).
Devuelve SIEMPRE JSON válido que cumpla exactamente el esquema indicado por el usuario.
No incluyas comentarios ni texto fuera del JSON.
Estandariza:
- listing_type en: "compra" | "venta" | "subasta"
- price_currency en ISO 4217 si es posible (p.ej. "DOP", "USD"); si no, deja null.
- Números como number (sin símbolos, sin comas). Si no hay dato, null.
- Fechas en ISO 8601 (YYYY-MM-DD) cuando sea viable; si no, null.
- page_number si puedes inferirlo; de lo contrario null.
- country: "República Dominicana" si el contexto lo sugiere y no hay contradicción; si no, null.
"""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {k: {"type": ["string", "number", "null"]} for k in COLUMNS},
                "required": COLUMNS,
                "additionalProperties": False
            }
        }
    },
    "required": ["rows"],
    "additionalProperties": False
}

USER_TASK_TEMPLATE = """Extrae **toda** la información relevante a **compra/venta** y **subasta** de inmuebles encontrada en el PDF.
- Si un PDF contiene múltiples propiedades, devuelve múltiples filas.
- Ajusta los campos a este esquema EXACTO y devuelve **solo** JSON válido (sin texto extra).
- Si algo no está, pon null.

Campos:
{columns_list}

Ejemplos de normalización:
- "Venta apto en Naco, US$ 135,000" → listing_type="venta", property_type="apartamento", price_amount=135000, price_currency="USD".
- "Subasta judicial 12/11/2025 10:00am Palacio de Justicia" → listing_type="subasta", auction_date="2025-11-12", auction_time="10:00", auction_location="Palacio de Justicia".
- "Solar 450 m2" → property_type="solar/terreno", area_m2=450.

Devuelve exactamente un objeto JSON con la clave 'rows' y un array de filas siguiendo el esquema.
"""

# -----------------------------
# Cliente OpenAI
# -----------------------------
client = OpenAI()

# -----------------------------
# Utilidades
# -----------------------------
def list_pdfs(directory: str) -> List[str]:
    os.makedirs(directory, exist_ok=True)
    return sorted(glob.glob(os.path.join(directory, "*.pdf")))

def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0

def ensure_output_dirs():
    os.makedirs(OUTPUT_JSON_DIR, exist_ok=True)

def backoff_sleep(attempt: int):
    # attempt: 1..MAX_RETRIES
    delay = (BACKOFF_BASE ** (attempt - 1)) * REQUEST_SLEEP_SECONDS
    time.sleep(delay)

def coerce_types(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Intenta convertir tipos a lo esperado:
    - numéricos a float (cuando aplique)
    - cadenas vacías -> None
    """
    numeric_fields = {
        "area_m2", "rooms", "bathrooms", "parking", "level_floors",
        "price_amount", "page_number", "source_pages_estimated", "source_filesize_bytes"
    }
    out = {}
    for k in COLUMNS:
        v = row.get(k, None)
        if isinstance(v, str) and v.strip() == "":
            v = None
        if k in numeric_fields and v is not None:
            try:
                v = float(v)
            except Exception:
                v = None
        out[k] = v
    return out

# ---------- Helpers de parsing/compatibilidad ----------
def _find_braced_json(s: str) -> Optional[str]:
    """Devuelve el primer substring con llaves balanceadas que parezca JSON."""
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i+1]
                    if '"rows"' in candidate or "'rows'" in candidate:
                        return candidate
                    break
        start = s.find("{", start + 1)
    return None

def _extract_json_from_text(s: str) -> dict:
    """
    Intenta cargar JSON directamente; si no, busca el primer bloque {...} válido.
    Lanza ValueError si no encuentra nada parseable.
    """
    s = s.strip()

    # 1) Intento directo
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2) Fences ```json ... ```
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass

    # 3) Primer objeto con llaves balanceadas
    brace = _find_braced_json(s)
    if brace is not None:
        return json.loads(brace)

    raise ValueError("No se encontró JSON válido en la respuesta del modelo.")

def _response_to_text(resp) -> str:
    """
    Intenta extraer texto de diferentes variantes del SDK:
    - resp.output_text (Responses API moderna)
    - resp.output[0].content[0].text.value (algunas betas)
    - resp.choices[0].message.content (Chat Completions)
    """
    if hasattr(resp, "output_text") and isinstance(resp.output_text, str):
        return resp.output_text

    try:
        return resp.output[0].content[0].text.value
    except Exception:
        pass

    try:
        return resp.choices[0].message.content
    except Exception:
        pass

    return str(resp)

# -----------------------------
# Core: subir PDF + pedir extracción JSON (con fallbacks)
# -----------------------------
def extract_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Devuelve un dict con clave 'rows' (lista de filas).
    Además, inyecta metadatos fuente (filename, filesize) en cada fila.
    Compatible con diferentes versiones del SDK (con/sin response_format).
    """
    filename = os.path.basename(pdf_path)
    filesize = file_size(pdf_path)

    # 1) Subir archivo con reintentos
    upload_file = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with open(pdf_path, "rb") as f:
                upload_file = client.files.create(file=f, purpose="user_data")
            break
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            backoff_sleep(attempt)

    user_prompt = USER_TASK_TEMPLATE.format(columns_list="\n".join(f"- {c}" for c in COLUMNS))

    # 2) Pedir extracción (intenta: responses+response_format, responses simple, chat.completions)
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # ----- A) Responses API con response_format (si el SDK lo soporta)
            try:
                resp = client.responses.create(
                    model=MODEL,
                    input=[
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": SYSTEM_INSTRUCTIONS}],
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": upload_file.id},
                                {"type": "input_text", "text": user_prompt},
                            ],
                        },
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "real_estate_extraction",
                            "schema": JSON_SCHEMA,
                            "strict": True,
                        },
                    },
                )
                raw_text = _response_to_text(resp)
            except TypeError:
                # ----- B) Responses API sin response_format (prompt fuerza JSON limpio)
                resp = client.responses.create(
                    model=MODEL,
                    input=[
                        {
                            "role": "system",
                            "content": [{"type": "input_text", "text": SYSTEM_INSTRUCTIONS + "\n\nIMPORTANTE: Devuelve SOLO JSON válido (sin texto extra)."}],
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_file", "file_id": upload_file.id},
                                {"type": "input_text", "text": user_prompt},
                            ],
                        },
                    ],
                )
                raw_text = _response_to_text(resp)

            payload = _extract_json_from_text(raw_text)

            # Inyectar metadatos y sanear tipos
            rows = payload.get("rows", [])
            fixed_rows = []
            for r in rows:
                r["source_filename"] = filename
                r["source_filesize_bytes"] = filesize
                r.setdefault("source_pages_estimated", None)
                fixed_rows.append(coerce_types(r))

            return {"rows": fixed_rows}

        except Exception as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            backoff_sleep(attempt)

    # ----- C) Fallback final: Chat Completions (sin adjuntar file nativo)
    try:
        chat_system = SYSTEM_INSTRUCTIONS + "\nDevuelve SOLO JSON válido. Sin explicaciones."
        chat_user = f"[ARCHIVO ADJUNTO: {filename}] " + user_prompt

        resp = client.chat.completions.create(
            model=os.environ.get("SCRAPING_TOOL_CHAT_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": chat_system},
                {"role": "user", "content": chat_user},
            ],
            temperature=0,
            response_format={"type": "json_object"},  # si tu SDK lo soporta
        )
        raw_text = _response_to_text(resp)
        payload = _extract_json_from_text(raw_text)

        rows = payload.get("rows", [])
        fixed_rows = []
        for r in rows:
            r["source_filename"] = filename
            r["source_filesize_bytes"] = filesize
            r.setdefault("source_pages_estimated", None)
            fixed_rows.append(coerce_types(r))
        return {"rows": fixed_rows}

    except Exception:
        if last_err:
            raise last_err
        raise

# -----------------------------
# Guardado: JSON y Excel
# -----------------------------
def save_json_per_pdf(pdf_path: str, data: Dict[str, Any]):
    ensure_output_dirs()
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    out = os.path.join(OUTPUT_JSON_DIR, f"{stem}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def append_to_excel(all_rows: List[Dict[str, Any]], xlsx_path: str):
    """
    Crea o actualiza el Excel con todas las filas (reescribe para garantizar columnas limpias y orden).
    """
    if not all_rows:
        return
    df = pd.DataFrame(all_rows, columns=COLUMNS)
    df = df[COLUMNS]
    df.to_excel(xlsx_path, index=False)

# -----------------------------
# Main
# -----------------------------
def main():
    pdfs = list_pdfs(DOWNLOAD_DIR)
    if not pdfs:
        print(f"[INFO] No se encontraron PDFs en '{DOWNLOAD_DIR}'.")
        return

    print(f"[INFO] Encontrados {len(pdfs)} PDF(s). Procesando…")

    all_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for i, pdf in enumerate(pdfs, start=1):
        print(f"[{i}/{len(pdfs)}] {os.path.basename(pdf)}")
        try:
            data = extract_from_pdf(pdf)
            save_json_per_pdf(pdf, data)
            rows = data.get("rows", [])
            if not rows:
                rows = [{
                    **{c: None for c in COLUMNS},
                    "source_filename": os.path.basename(pdf),
                    "source_filesize_bytes": file_size(pdf),
                }]
            all_rows.extend(rows)
            time.sleep(REQUEST_SLEEP_SECONDS)  # cortesía entre archivos
        except Exception as e:
            errors.append({
                "file": os.path.basename(pdf),
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(),
            })
            print(f"[ERROR] {os.path.basename(pdf)} -> {e}")

    append_to_excel(all_rows, OUTPUT_XLSX)

    print(f"\n[OK] Filas totales: {len(all_rows)}")
    print(f"[OK] Excel: {OUTPUT_XLSX}")
    print(f"[OK] JSONs por archivo en: {OUTPUT_JSON_DIR}")

    if errors:
        print("\n[WARN] Algunos archivos fallaron:")
        for err in errors:
            print(f"  - {err['file']}: {err['error']}")

if __name__ == "__main__":
    main()
