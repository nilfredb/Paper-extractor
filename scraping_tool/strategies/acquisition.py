# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse, urljoin

from selenium.common.exceptions import JavascriptException

from .base import Strategy, Phase, Cost
from ..logger import get_logger
from ..utils import (
    download_via_requests,
    requests_session_from_selenium,
    wait_for_network_idle_like,
)

log = get_logger(__name__)


# -------------------------------
# Helpers
# -------------------------------

_RE_ANY_PDF = re.compile(r"\.pdf(?:\?.*)?$", re.IGNORECASE)
_RE_PERPAGE = re.compile(r"/pdf_pags/pdf_\d+\.pdf", re.IGNORECASE)
_RE_COMPLETE = re.compile(r"/pdf_pags/\d+\.pdf", re.IGNORECASE)  # p.ej. /pdf_pags/482.pdf


def _smart_referer_for(target_url: str, current_url: str) -> str:
    """
    Devuelve un Referer razonable:
      - mismo host → usa current_url
      - distinto host → usa el origen (scheme://host)
    """
    try:
        tu = urlparse(target_url)
        cu = urlparse(current_url)
        if tu.netloc and cu.netloc and tu.netloc == cu.netloc:
            return current_url
        if tu.scheme and tu.netloc:
            return f"{tu.scheme}://{tu.netloc}"
    except Exception:
        pass
    return current_url


def _force_pdf_complete_if_available(driver, detected_url: str) -> str:
    """
    Si el sniffer/heurística detectó una URL per-page (pdf_*.pdf) y el DOM
    expone una URL 'complete' (…/pdf_pags/<id>.pdf), preferimos la completa.
    Requiere que en PREPARATION hayas ejecutado PrepareDiarioLibreViewer,
    que rellena window._pdf_links.
    """
    try:
        # Solo intentamos override si es per-page
        if not _RE_PERPAGE.search(detected_url):
            return detected_url

        data = driver.execute_script("return window._pdf_links || null;")
        if data and data.get("complete"):
            complete = data["complete"]
            cand = complete.get("abs") or complete.get("href")
            if cand and _RE_COMPLETE.search(cand):
                log.info(f"Override per-page → completo: {detected_url} → {cand}")
                return cand
    except JavascriptException:
        pass
    except Exception as e:
        log.debug(f"_force_pdf_complete_if_available: {e}")
    return detected_url


def _choose_better_pdf(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    """
    Prefiere el 'complete' sobre 'per-page'. Si no hay criterio, devuelve el último válido.
    """
    if not candidate:
        return current
    if not current:
        return candidate

    # Si el actual es per-page y el candidato parece completo, gana el candidato
    if _RE_PERPAGE.search(current) and _RE_COMPLETE.search(candidate):
        return candidate

    # Si el actual no es pdf y el candidato sí, gana candidato
    if not _RE_ANY_PDF.search(current) and _RE_ANY_PDF.search(candidate):
        return candidate

    # En otro caso, mantenemos el actual (evita cambios inestables)
    return current


# -------------------------------
# Estrategias
# -------------------------------

class AcquireFromDirectPdf(Strategy):
    """
    Si la URL actual ya es un PDF directo, lo descarga con requests.
    """
    name, phase, cost = "acquire_from_direct_pdf", Phase.ACQUISITION, Cost.CHEAP

    def run(self, browser, sniffer):
        d = browser.driver
        current = d.current_url
        if not _RE_ANY_PDF.search(current):
            return (None, False)

        # Si es per-page e inyectamos DOM antes, intenta preferir completo
        current = _force_pdf_complete_if_available(d, current)

        log.info(f"AcquireFromDirectPdf: descargando directo {current}")
        sess = requests_session_from_selenium(d)  # <-- ¡siempre creamos sess!
        referer = _smart_referer_for(current, current)
        out = download_via_requests(
            current,
            browser.cfg.download_dir,
            session=sess,
            referer_url=referer,
        )
        return (out, True)


class AcquireClickPreferChrome(Strategy):
    """
    Heurística híbrida:
      1) Escucha con sniffer original.file o *.pdf.
      2) Si aparece per-page, intenta cambiarlo por 'complete' desde el DOM (window._pdf_links).
      3) Descarga con requests (sesión clonada de Selenium).
    Útil cuando el botón de descarga dispara peticiones XHR/Fetch dentro del viewer.
    """
    name, phase, cost = "acquire_click_prefer_chrome", Phase.ACQUISITION, Cost.NORMAL

    def run(self, browser, sniffer):
        d = browser.driver

        # Intenta "activar" vistas que revelan el bloque de descargas PDF si existe
        # (cuando la página ya ha sido preparada, esto es opcional pero inofensivo).
        try:
            d.execute_script("""
              (function(){
                var tab = document.querySelector('.magazine-pdf-wrapper');
                if (tab && !tab.classList.contains('active')) {
                  tab.classList.add('active');
                }
              })();
            """)
        except Exception:
            pass

        # Arrancamos el sniffer por ~90s (el pipeline ya lo hacía; aquí por seguridad)
        if not sniffer.is_running:
            sniffer.start(duration_seconds=90)

        # Espera corta para que el viewer dispare requests al abrir el panel PDF
        wait_for_network_idle_like(d, quiet_ms=500, total_wait_s=4)

        # Intentamos encontrar URL por sniffer (original.file o *.pdf)
        detected = sniffer.sniff_original_or_pdf()  # método tuyo: devuelve str|None
        if not detected:
            # Fallback: intenta leer del DOM (PrepareDiarioLibreViewer debió rellenar window._pdf_links)
            try:
                data = d.execute_script("return window._pdf_links || null;")
            except JavascriptException:
                data = None
            if data and data.get("complete"):
                detected = data["complete"].get("abs") or data["complete"].get("href")

        # Reglas de preferencia
        if detected:
            detected = _force_pdf_complete_if_available(d, detected)
            log.info(f"Descargando por requests URL detectada: {detected}")
            sess = requests_session_from_selenium(d)  # <-- ¡definimos sess ANTES de usarlo!
            referer = _smart_referer_for(detected, d.current_url)
            out = download_via_requests(
                detected,
                browser.cfg.download_dir,
                session=sess,
                referer_url=referer,
            )
            return (out, True)

        log.warning("AcquireClickPreferChrome: no se detectó PDF (sniffer/DOM).")
        return (None, False)


class AcquireViaSnifferOnly(Strategy):
    """
    No toca la UI. Únicamente espera a que el sniffer capture un original.file o *.pdf
    y lo descarga (con preferencia por 'complete' si el DOM lo expone).
    """
    name, phase, cost = "acquire_via_sniffer_only", Phase.ACQUISITION, Cost.CHEAP

    def run(self, browser, sniffer):
        d = browser.driver

        if not sniffer.is_running:
            sniffer.start(duration_seconds=60)

        # Espera pasiva; muchos viewers hacen peticiones al cargar
        wait_for_network_idle_like(d, quiet_ms=600, total_wait_s=6)

        detected = sniffer.sniff_original_or_pdf()
        if detected:
            detected = _force_pdf_complete_if_available(d, detected)
            log.info(f"Sniffer-only: descargando {detected}")
            sess = requests_session_from_selenium(d)  # <-- ¡definimos sess!
            referer = _smart_referer_for(detected, d.current_url)
            out = download_via_requests(
                detected,
                browser.cfg.download_dir,
                session=sess,
                referer_url=referer,
            )
            return (out, True)

        log.warning("Sniffer: timeout sin URL (eventos insuficientes).")
        return (None, False)


class AcquireClickForceRequests(Strategy):
    """
    Variante “forzada”: intenta leer SIEMPRE del DOM primero (si está la pestaña PDF),
    prioriza 'complete', y descarga con requests. Si falla, cae a sniffer.
    """
    name, phase, cost = "acquire_click_force_requests", Phase.ACQUISITION, Cost.NORMAL

    def run(self, browser, sniffer):
        d = browser.driver

        # Intenta mostrar la pestaña de PDF por si está oculta bajo overlays
        try:
            d.execute_script("""
              (function(){
                var tab = document.querySelector('.magazine-pdf-wrapper');
                if (tab && !tab.classList.contains('active')) {
                  tab.classList.add('active');
                }
                var overlay = document.querySelector('.black-layover-pdf');
                if (overlay) overlay.classList.add('active');
              })();
            """)
        except Exception:
            pass

        # Lee DOM (necesita la preparación previa para _pdf_links)
        dom_url = None
        try:
            data = d.execute_script("return window._pdf_links || null;")
            if data:
                # Preferimos complete; si no hay, primera página
                dom_url = (
                    (data.get("complete") or {}).get("abs")
                    or (data.get("complete") or {}).get("href")
                    or (data.get("firstPage") or {}).get("abs")
                    or (data.get("firstPage") or {}).get("href")
                )
        except JavascriptException:
            data = None

        detected = None
        if dom_url:
            detected = dom_url

        # Si además el sniffer trae algo mejor (p.ej. original.file con expiración amplia), elegimos
        if sniffer.is_running:
            sniffed = sniffer.sniff_original_or_pdf()
        else:
            sniffer.start(duration_seconds=45)
            wait_for_network_idle_like(d, quiet_ms=500, total_wait_s=4)
            sniffed = sniffer.sniff_original_or_pdf()

        if sniffed:
            sniffed = _force_pdf_complete_if_available(d, sniffed)
            detected = _choose_better_pdf(detected, sniffed)

        if detected:
            log.info(f"AcquireClickForceRequests: descargando {detected}")
            sess = requests_session_from_selenium(d)  # <-- ¡definimos sess!
            referer = _smart_referer_for(detected, d.current_url)
            out = download_via_requests(
                detected,
                browser.cfg.download_dir,
                session=sess,
                referer_url=referer,
            )
            return (out, True)

        log.warning("AcquireClickForceRequests: sin URL tras DOM/sniffer.")
        return (None, False)
