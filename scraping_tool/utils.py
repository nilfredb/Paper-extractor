# scraping_tool/utils.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import json
import requests
from urllib.parse import urlparse
from typing import Optional, Dict, Any

from .config import DEFAULT_DOWNLOAD_DIR

# -----------------------------
# Hosts ignorados
# -----------------------------
IGNORE_HOSTS = (
    "lijit.com", "doubleclick.net", "google-analytics.com", "googletagmanager.com",
    "adnxs.com", "criteo.com", "taboola.com", "outbrain.com", "id5-sync.com",
    "rubiconproject.com", "pubmatic.com", "moatads.com", "scorecardresearch.com",
    "openx.net", "agkn.com", "casalemedia.com", "refinery89.com", "prebid.org",
)

# -----------------------------
# Utilidades básicas
# -----------------------------
def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def now() -> float:
    return time.time()

def is_ignored(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        host = ""
    return any(dom in host for dom in IGNORE_HOSTS)

# -----------------------------
# Espera por descargas (Chrome)
# -----------------------------
def wait_for_download(download_dir: str, start_ts: float, timeout: int):
    """
    Espera hasta que un archivo PDF aparezca en el directorio de descargas.
    Ignora archivos .crdownload activos.
    """
    end = now() + timeout
    while now() < end:
        try:
            files = [os.path.join(download_dir, f) for f in os.listdir(download_dir)]
        except FileNotFoundError:
            files = []

        # Si hay descargas en curso (.crdownload), esperar
        if any(p.endswith(".crdownload") for p in files):
            time.sleep(0.25)
            continue

        pdfs = [p for p in files if p.lower().endswith(".pdf")]
        if pdfs:
            pdfs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            newest = pdfs[0]
            try:
                if os.path.getmtime(newest) >= start_ts - 1:
                    return newest
            except FileNotFoundError:
                pass
        time.sleep(0.25)
    return None

# -----------------------------
# Referer inteligente
# -----------------------------
def smart_referer_for(url: str, current: str) -> str:
    try:
        u = urlparse(url)
        if "s3.amazonaws.com" in (u.netloc or "").lower() and "document.issuu.com" in u.path.lower():
            return "https://e.issuu.com/"
    except Exception:
        pass
    return current

# -----------------------------
# Sesión requests desde Selenium (faltaba)
# -----------------------------
def requests_session_from_selenium(
    driver,
    referer_url: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> requests.Session:
    """
    Crea una sesión requests que replica UA, cookies y cabeceras útiles del navegador Selenium.

    Args:
        driver: WebDriver de Selenium.
        referer_url: Referer a usar por defecto (si no se da, se calcula con smart_referer_for al descargar).
        extra_headers: Cabeceras adicionales a inyectar.

    Returns:
        requests.Session inicializada.
    """
    sess = requests.Session()

    try:
        ua = driver.execute_script("return navigator.userAgent;")
    except Exception:
        ua = "Mozilla/5.0"

    # Cabeceras razonables para descargas
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "es-419,es;q=0.6",
        "Upgrade-Insecure-Requests": "1",
    }

    # Referer opcional (si lo pasas aquí ya queda fijo en la sesión)
    if referer_url:
        headers["Referer"] = referer_url

    if extra_headers:
        headers.update(extra_headers)

    sess.headers.update(headers)

    # Copiar cookies del navegador
    try:
        for c in driver.get_cookies():
            # Algunos drivers pueden dar cookies sin 'domain' o 'path'
            sess.cookies.set(
                c.get("name"),
                c.get("value"),
                domain=c.get("domain"),
                path=c.get("path", "/"),
            )
    except Exception:
        # Si falla, dejamos la sesión sin cookies (seguirá funcionando en muchos casos)
        pass

    return sess

# -----------------------------
# Descarga por requests
# -----------------------------
def download_via_requests(browser, url: str, filename: Optional[str] = None, referer_url: Optional[str] = None) -> str:
    """
    Descarga un archivo usando las cookies y headers del navegador Selenium.
    """
    d = browser.driver

    # Si no pasas referer explícito, aplicamos política inteligente
    referer = referer_url or smart_referer_for(url, d.current_url)

    sess = requests_session_from_selenium(
        d,
        referer_url=referer,
        extra_headers=None,
    )

    fname = filename or os.path.basename(urlparse(url).path) or "edition.pdf"
    fname = fname.split("?")[0]  # elimina parámetros tipo ?t=...
    out_path = os.path.join(browser.cfg.download_dir, fname)

    with sess.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(128 * 1024):
                if chunk:
                    f.write(chunk)
    return out_path

# -----------------------------
# Espera de red (Network Idle)
# -----------------------------
def wait_for_network_idle_like(
    driver,
    quiet_ms: int = 500,
    total_wait_s: float = 4.0,
    check_interval_s: float = 0.20,
    recent_window_s: float = 2.0,
    **kwargs,
) -> bool:
    """
    Espera hasta que el navegador esté "idle" (sin actividad de red reciente).
    Usa los logs de rendimiento del CDP para medir el tiempo sin nuevos eventos de red.

    Args:
        driver: instancia de Selenium con performance logging activado.
        quiet_ms: tiempo de inactividad requerido (en milisegundos).
        total_wait_s: tiempo total máximo de espera.
        check_interval_s: intervalo de chequeo en segundos.
        recent_window_s: ventana temporal de observación.
    """
    # compatibilidad con versiones previas
    if "quiet_time_ms" in kwargs and isinstance(kwargs["quiet_time_ms"], (int, float)):
        quiet_ms = int(kwargs["quiet_time_ms"])

    def _drain_last_network_event_ts() -> Optional[float]:
        try:
            entries = driver.get_log("performance")
        except Exception:
            return None
        last_ts_ms: Optional[float] = None
        for e in entries:
            try:
                msg = json.loads(e.get("message", "{}")).get("message", {})
                method = msg.get("method", "")
                if method.startswith("Network."):
                    ts = msg.get("params", {}).get("timestamp")
                    if isinstance(ts, (int, float)):
                        ts_ms = float(ts) * 1000.0
                        if (last_ts_ms is None) or (ts_ms > last_ts_ms):
                            last_ts_ms = ts_ms
            except Exception:
                pass
        return last_ts_ms

    start = time.time()
    _ = _drain_last_network_event_ts()
    last_seen_event_ms = _ if _ is not None else (time.time() * 1000.0)

    while (time.time() - start) < total_wait_s:
        time.sleep(check_interval_s)
        ts = _drain_last_network_event_ts()
        now_ms = time.time() * 1000.0
        if ts is not None:
            last_seen_event_ms = max(last_seen_event_ms, ts)
        if (now_ms - last_seen_event_ms) >= quiet_ms:
            return True
    return False
