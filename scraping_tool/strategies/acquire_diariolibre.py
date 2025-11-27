# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs, urljoin, urlencode

import requests
from selenium.webdriver.common.by import By

from ..logger import get_logger

log = get_logger(__name__)

RE_PDF = re.compile(r'https?://[^"\']+\.pdf(?:\?[^"\']*)?$', re.I)

def _host(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""

def _is_diariolibre_viewer(url: str) -> bool:
    h = _host(url)
    return ("diariolibre.com" in h or "epaper.diariolibre.com" in h) and "viewer.aspx" in url.lower()

def _params(u: str) -> dict:
    try:
        return {k: v[0] for k, v in parse_qs(urlparse(u).query).items()}
    except Exception:
        return {}

def _derive_candidates(viewer_url: str) -> list[str]:
    """
    Genera endpoints razonables a partir de viewer.aspx.
    Ajusta/añade aquí si ves patrones reales en DevTools.
    """
    base = viewer_url.split("viewer.aspx")[0]  # https://epaper.diariolibre.com/epaper/
    q = _params(viewer_url)
    publication = q.get("publication", "diariolibre")
    date = q.get("date")  # ej 28_10_2025

    cands = []

    # Candidato 1: download.aspx con parámetros básicos
    if date:
        cands.append(f"{base}download.aspx?{urlencode({'publication':publication,'date':date,'type':'pdf'})}")

    # Candidato 2: rutas 'pdf' frecuentes en e-paper
    if date:
        # 28_10_2025 -> 2025-10-28 o 20251028 si alguna variante
        try:
            d, m, y = date.split('_')  # día_mes_año
            iso = f"{y}-{m}-{d}"
            yyyymmdd = f"{y}{m}{d}"
            cands.append(f"{base}pdf/{iso}.pdf")
            cands.append(f"{base}pdf/{yyyymmdd}.pdf")
        except Exception:
            pass

    # Candidato 3: endpoint genérico
    cands.append(f"{base}download.aspx")

    # Evitar duplicados conservando orden
    seen = set(); out = []
    for u in cands:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def _smart_referer_for(url: str, current_url: str) -> str:
    # En DL el referer al viewer suele ser suficiente
    return current_url

def _cookies_to_session(drv, sess: requests.Session) -> None:
    try:
        for c in drv.get_cookies():
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path"))
    except Exception:
        pass

@dataclass
class AcquireDiarioLibreFromViewer:
    """
    Estrategia: Diario Libre viewer.aspx → intenta derivar PDF y/o esnifar y descargar por requests.
    Úsala ANTES de AcquireClickPreferChrome cuando headless=True.
    """
    name: str = "AcquireDiarioLibreFromViewer"

    def run(self, br, sniff) -> tuple[Optional[str], bool]:
        d = br.driver
        cur = d.current_url

        if not _is_diariolibre_viewer(cur):
            return (None, False)  # no terminal; que siga la cadena

        log.info(f"[{self.name}] Detectado viewer DL: {cur}")

        # 1) Derivar candidatos directos
        sess = requests.Session()
        ua = d.execute_script("return navigator.userAgent;")
        referer = _smart_referer_for(cur, cur)
        sess.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-419,es;q=0.6",
            "Referer": referer,
        })
        _cookies_to_session(d, sess)

        for url in _derive_candidates(cur):
            try:
                log.info(f"[{self.name}] Probar candidato: {url}")
                r = sess.get(url, allow_redirects=True, timeout=30)
                # Si redirige a .pdf o devuelve PDF, lo guardamos
                final_url = r.url
                ctype = (r.headers.get("Content-Type") or "").lower()
                if RE_PDF.search(final_url) or "pdf" in ctype:
                    fname = os.path.basename(urlparse(final_url).path) or "diariolibre.pdf"
                    out_path = os.path.join(br.cfg.download_dir, fname)
                    with open(out_path, "wb") as f:
                        f.write(r.content)
                    log.info(f"[{self.name}] ✅ Guardado: {out_path}")
                    return (out_path, True)
            except Exception as e:
                log.warning(f"[{self.name}] Candidato falló: {e}")

        # 2) Fallback: esnifar tras un click en el botón de descarga (si existe)
        #    y descargar por requests la primera URL .pdf de dominio DL.
        #    No dependemos de que Chrome guarde a disco.
        try:
            # buscar algo tipo 'Download' o icono de descarga
            btns = d.find_elements(By.CSS_SELECTOR, "[aria-label*='Descargar' i], [aria-label*='Download' i], a[download], button[download]")
            if btns:
                d.execute_script("arguments[0].click();", btns[0])
                time.sleep(0.8)
        except Exception:
            pass

        end = time.time() + 25
        while time.time() < end:
            events = sniff.flush()  # asumiendo que tu Sniffer tiene .flush() que devuelve eventos recientes
            for ev in events:
                url = ev.get("url") or ""
                if "diariolibre.com" in url and RE_PDF.search(url):
                    try:
                        log.info(f"[{self.name}] Sniffer encontró: {url}")
                        r = sess.get(url, allow_redirects=True, timeout=30)
                        ctype = (r.headers.get("Content-Type") or "").lower()
                        if RE_PDF.search(r.url) or "pdf" in ctype:
                            fname = os.path.basename(urlparse(r.url).path) or "diariolibre.pdf"
                            out_path = os.path.join(br.cfg.download_dir, fname)
                            with open(out_path, "wb") as f:
                                f.write(r.content)
                            log.info(f"[{self.name}] ✅ Guardado (sniffer): {out_path}")
                            return (out_path, True)
                    except Exception as e:
                        log.warning(f"[{self.name}] Descarga sniffer falló: {e}")
            time.sleep(0.3)

        # No se pudo; no terminal para que otras estrategias prueben
        log.warning(f"[{self.name}] No se pudo derivar/atrapar PDF.")
        return (None, False)
