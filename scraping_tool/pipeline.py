# scraping_tool/pipeline.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

# Importa SIEMPRE BrowserConfig desde .config (evita duplicados)
from .config import BrowserConfig, DownloadPolicy
from .browser import Browser
from .sniffer import Sniffer
from .logger import get_logger

# Estrategias DISCOVERY
from .strategies.discovery import DiscoverViewerAspx, DiscoverDirectPdfLink

# Estrategias PREPARATION
from .strategies.preparation import (
    PrepareIssuuEmbed,
    PrepareDiarioLibreViewer,
)

# (Opcional) estrategia legacy específica de Diario Libre; normalmente ya no hace falta
# from .strategies.acquire_diariolibre import AcquireDiarioLibreFromViewer

# Estrategias ACQUISITION generales
from .strategies.acquisition import (
    AcquireFromDirectPdf,
    AcquireClickPreferChrome,
    AcquireViaSnifferOnly,
    AcquireClickForceRequests,
)

# Estrategia especializada Issuu (El Nuevo Diario / El Caribe)
from .strategies.issuu_elnuevodiario import IssuuElNuevoDiarioStrategy

# NEW: Estrategia especializada para Diario Libre ePaper (PDF completo por requests)
from .strategies.acquire_diariolibre_epaper import AcquireDiarioLibreEpaper  # NEW

log = get_logger(__name__)

# -------------------------------
# Helpers
# -------------------------------
def _is_elnuevodiario(url: str) -> bool:
    try:
        return "elnuevodiario.com.do" in url.lower()
    except Exception:
        return False

def _is_elcaribe(url: str) -> bool:
    try:
        return "elcaribe.com.do" in url.lower()
    except Exception:
        return False

def _is_diariolibre_viewer(url: str) -> bool:
    """
    Detecta el viewer de Diario Libre (viewer.aspx).
    """
    try:
        u = url.lower()
        return ("epaper.diariolibre.com" in u) and ("viewer.aspx" in u)
    except Exception:
        return False

def _is_diariolibre_home(url: str) -> bool:  # NEW
    """
    Detecta la portada de ePaper (lista de portadas del día).
    """
    try:
        u = url.lower()
        # casos típicos: https://epaper.diariolibre.com/epaper/ o .../epaper/index.html
        return ("epaper.diariolibre.com" in u) and ("viewer.aspx" not in u)
    except Exception:
        return False

def _collect_diariolibre_viewers(br: Browser) -> list[str]:
    """
    En la home del ePaper, obtiene todos los enlaces a viewer.aspx (excluyendo 'Publicidad').
    Devuelve hrefs absolutos, priorizando 'publication=diariolibre' primero.
    """
    d = br.driver
    w = br.wait

    w.until(EC.presence_of_element_located((
        By.CSS_SELECTOR,
        ".magazine-publications-outstanding-covers .cover a[href*='viewer.aspx']"
    )))

    links = []
    covers = d.find_elements(By.CSS_SELECTOR, ".magazine-publications-outstanding-covers .cover")
    for c in covers:
        try:
            a = c.find_element(By.CSS_SELECTOR, "a[href*='viewer.aspx']")
            href = a.get_attribute("href") or ""
            title_el = c.find_element(By.CSS_SELECTOR, ".publication-description")
            title = (title_el.text or "").strip().lower()
            # Filtra publicidad por título visible y por publication=publicidad*
            if "publicidad" in title:
                continue
            if "publication=publicidad" in href.lower():
                continue
            if href:
                links.append(href)
        except Exception:
            continue

    # ✅ Prioriza Diario Libre antes que Metro u otros
    links.sort(key=lambda u: (0 if "publication=diariolibre" in u.lower() else 1, u))
    return links
    """
    En la home del ePaper, obtiene todos los enlaces a viewer.aspx (excluyendo 'Publicidad').
    Devuelve hrefs absolutos.
    """
    d = br.driver
    w = br.wait

    # Asegura que hay portadas (covers) con enlaces a viewer.aspx
    w.until(EC.presence_of_element_located((
        By.CSS_SELECTOR,
        ".magazine-publications-outstanding-covers .cover a[href*='viewer.aspx']"
    )))

    links = []
    covers = d.find_elements(By.CSS_SELECTOR, ".magazine-publications-outstanding-covers .cover")
    for c in covers:
        try:
            a = c.find_element(By.CSS_SELECTOR, "a[href*='viewer.aspx']")
            href = a.get_attribute("href") or ""
            title_el = c.find_element(By.CSS_SELECTOR, ".publication-description")
            title = (title_el.text or "").strip().lower()
            # Filtra publicidad por título visible y por publication=publicidad*
            if "publicidad" in title:
                continue
            if "publication=publicidad" in href.lower():
                continue
            if href:
                links.append(href)
        except Exception:
            continue

    return links

# -------------------------------
# Core con Browser ya abierto
# -------------------------------
def _run_core_with_browser(
    start_url: str,
    download_dir: str,
    policy: DownloadPolicy,
    br: Browser,
) -> Optional[str]:
    """
    Núcleo del pipeline reutilizable con un Browser ya abierto.
    Devuelve la ruta del PDF o None.
    """
    d = br.driver
    w = br.wait

    log.info(f"🌐 Cargando: {start_url}")
    d.get(start_url)
    w.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    log.debug("✅ BODY presente, comenzando sniff…")

    sniff = Sniffer(d)
    sniff.start()

    # --------- FAST-PATH: Issuu (El Caribe / El Nuevo Diario) ----------
    if _is_elcaribe(start_url) or _is_elnuevodiario(start_url):
        log.info("⚡ Fast-path Issuu activado.")
        try:
            issuu = IssuuElNuevoDiarioStrategy(
                prefer_mode="requests_only",
                headless=br.cfg.headless
            )
            out = issuu.fetch(start_url, download_dir, br=br)  # Reutiliza el mismo Browser
            if out:
                log.info(f"✅ Descarga Issuu (fast-path) completada: {out}")
                return out
            else:
                log.warning("⚠️ Issuu fast-path no devolvió archivo; continuamos con el pipeline general.")
        except Exception as e:
            log.warning(f"⚠️ Issuu fast-path falló: {e}. Continuamos con pipeline general.")

    # ---------------- DISCOVERY ----------------
    log.info("🔍 Fase DISCOVERY")
    for strat in (DiscoverViewerAspx(), DiscoverDirectPdfLink()):
        log.debug(f"▶ {strat.name}")
        try:
            _, terminal = strat.run(br, sniff)
            if terminal:
                log.info(f"✅ {strat.name}: terminal.")
                break
        except Exception as e:
            log.warning(f"⚠️ Error en {strat.name}: {e}")

    # ---------------- PREPARATION ----------------
    # Relee la URL actual por si DISCOVERY te llevó a viewer.aspx u otra vista.
    current_url = d.current_url
    log.info("🧭 Fase PREPARATION")

    preparation_chain = [PrepareIssuuEmbed()]
    if _is_diariolibre_viewer(current_url):
        preparation_chain.append(PrepareDiarioLibreViewer())

    for strat in preparation_chain:
        log.debug(f"▶ {strat.name}")
        try:
            strat.run(br, sniff)
        except Exception as e:
            log.warning(f"⚠️ Error en {strat.name}: {e}")

    # ---------------- ACQUISITION ----------------
    log.info("📦 Fase ACQUISITION")

    # UPDATED: si estamos en el viewer de Diario Libre, prioriza la estrategia especializada
    specialized_first = []  # NEW
    if _is_diariolibre_viewer(d.current_url):  # NEW
        specialized_first = (AcquireDiarioLibreEpaper(),)  # fuerza descarga única por requests

    if policy == DownloadPolicy.PREFER_CHROME:
        chain = specialized_first + (
            AcquireFromDirectPdf(),
            AcquireClickPreferChrome(),
            AcquireViaSnifferOnly(),
        )
    else:
        chain = specialized_first + (
            AcquireFromDirectPdf(),
            AcquireClickForceRequests(),
            AcquireViaSnifferOnly(),
        )

    for strat in chain:
        log.debug(f"▶ {strat.name}")
        try:
            out, terminal = strat.run(br, sniff)
            if out:
                log.info(f"✅ Descarga completada con '{strat.name}': {out}")
                return out
            if terminal:
                log.debug(f"⏹ Terminal sin resultado en '{strat.name}'.")
                break
        except Exception as e:
            log.error(f"❌ Error en '{strat.name}': {e}", exc_info=True)

    return None


# -------------------------------
# API pública
# -------------------------------
def run_pipeline(
    start_url: str,
    download_dir: str,
    policy: DownloadPolicy = DownloadPolicy.PREFER_CHROME
) -> Optional[str]:
    """
    Abre un navegador, ejecuta el pipeline y lo cierra.
    - Si 'start_url' es un viewer de Diario Libre, se usará AcquireDiarioLibreEpaper() primero.
    """
    log.info(f"🚀 Iniciando pipeline: {start_url}")
    cfg = BrowserConfig(download_dir=download_dir, headless=True, download_policy=policy)

    with Browser(cfg) as br:
        out = _run_core_with_browser(start_url, download_dir, policy, br)

    if out:
        return out

    log.warning(f"⚠️ Ninguna estrategia logró descargar desde: {start_url}")
    return None


def run_batch(
    urls: list[str],
    download_dir: str,
    policy: DownloadPolicy = DownloadPolicy.PREFER_CHROME
) -> dict[str, Optional[str]]:
    """
    Reutiliza el mismo navegador para varias URLs.
    Devuelve dict {url: path_o_None}
    """
    results: dict[str, Optional[str]] = {}
    cfg = BrowserConfig(download_dir=download_dir, headless=True, download_policy=policy)

    with Browser(cfg) as br:
        for url in urls:
            log.info(f"🧵 Batch → {url}")
            try:
                out = _run_core_with_browser(url, download_dir, policy, br)
                results[url] = out
                if out:
                    log.info(f"✅ Batch OK: {out}")
                else:
                    log.warning("⚠️ Batch sin resultado")
            except Exception as e:
                log.error(f"❌ Batch error en {url}: {e}", exc_info=True)
                results[url] = None

    return results


def run_diariolibre_home(  # NEW
    home_url: str,
    download_dir: str,
    policy: DownloadPolicy = DownloadPolicy.PREFER_CHROME
) -> dict[str, Optional[str]]:
    """
    Recorre la portada ePaper de Diario Libre y descarga TODAS las ediciones visibles (excepto 'Publicidad').
    Devuelve dict {viewer_url: path_o_None}.
    """
    if not _is_diariolibre_home(home_url):
        raise ValueError("run_diariolibre_home espera la portada del ePaper (no un viewer.aspx).")

    results: dict[str, Optional[str]] = {}
    cfg = BrowserConfig(download_dir=download_dir, headless=True, download_policy=policy)

    with Browser(cfg) as br:
        log.info(f"📰 Cargando portada ePaper: {home_url}")
        d = br.driver
        w = br.wait
        d.get(home_url)
        w.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Colecta viewers del día (excluye 'Publicidad')
        viewers = _collect_diariolibre_viewers(br)
        if not viewers:
            log.warning("⚠️ No se detectaron viewers en la portada.")
            return results

        log.info(f"📚 Se encontraron {len(viewers)} ediciones: procesando en serie…")
        for vurl in viewers:
            try:
                out = _run_core_with_browser(vurl, download_dir, policy, br)
                results[vurl] = out
                if out:
                    log.info(f"✅ OK: {out}")
                else:
                    log.warning("⚠️ Sin resultado en edición")
            except Exception as e:
                log.error(f"❌ Error procesando {vurl}: {e}", exc_info=True)
                results[vurl] = None

    return results
