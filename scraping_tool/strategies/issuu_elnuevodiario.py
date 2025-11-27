# scraping_tool/strategies/issuu_elnuevodiario.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, json, time, random
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

# ===== Reusa utilidades y Browser de tu proyecto =====
from scraping_tool.browser import Browser, BrowserConfig
from scraping_tool.utils import ensure_dir as _ensure_dir  # o usa os.makedirs(path, exist_ok=True)
from scraping_tool.logger import get_logger

log = get_logger(__name__)

# ------------------ Config local de la estrategia ------------------
BTN_SELECTORS = [
    '[data-testid="download-button"][aria-disabled="false"]',
    '[data-testid="download-button"]',
    'button[aria-label*="Download" i]',
    'button[data-tooltip*="Download" i]',
    'a[aria-label*="Download" i]',
    'a[download]'
]

PDF_LINK_SELECTORS = [
    'a[href$=".pdf"]',
    'a[href*=".pdf"]',
    'a[download][href]'
]

# tolerante a e.issuu.com / issuu.com
IFRAME_SEL = 'iframe[src*="issuu.com/embed.html"], iframe[src*="e.issuu.com/embed.html"]'

RE_ORIGINAL_FILE = re.compile(r'https?://[^/]*document\.issuu\.com/.*/original\.file\?', re.I)
RE_PDF_URL       = re.compile(r'https?://[^\s"\'<>]+\.pdf(?:\?.*)?$', re.I)
RE_ISSUU_JSON_EP = re.compile(r'/api/content-service/public\.reader\.download', re.I)

# Incluye ambos hosts que usan el embed de Issuu
ISSUU_HOSTS = ("elnuevodiario.com.do", "elcaribe.com.do")

DEFAULT_TIMEOUT = 90


# ------------------ Helpers internos ------------------
def _flush_perf_logs(driver) -> None:
    try:
        driver.get_log("performance")
    except Exception:
        pass


def _smart_referer_for(url: str, current_url: str) -> str:
    try:
        u = urlparse(url)
        if "s3.amazonaws.com" in (u.netloc or "").lower() and "document.issuu.com" in (u.path or "").lower():
            return "https://e.issuu.com/"
    except Exception:
        pass
    return current_url


def _cookies_to_session(drv, sess: requests.Session) -> None:
    for c in drv.get_cookies():
        try:
            sess.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path"))
        except Exception:
            pass


def _filename_from_cd(cd_header: Optional[str]) -> Optional[str]:
    if not cd_header:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?', cd_header, re.I)
    return m.group(1) if m else None


def _suggest_name_from_url(url: str, default="edition.pdf") -> str:
    try:
        n = os.path.basename(urlparse(url).path)
        return n or default
    except Exception:
        return default


def _get_with_retries(driver, url: str, attempts: int = 3, base: float = 1.0) -> None:
    """
    Abre `url` con hasta `attempts` reintentos y backoff exponencial + jitter.
    Relanza la última excepción si falla todo.
    """
    last_err = None
    for i in range(attempts):
        try:
            driver.get(url)
            return
        except WebDriverException as e:
            last_err = e
            sleep_s = base * (2 ** i) + random.uniform(0, 0.5)
            log.warning(f"[Issuu] GET fallo ({e.__class__.__name__}), reintento {i+1}/{attempts} en {sleep_s:.1f}s → {url}")
            time.sleep(sleep_s)
    raise last_err


def _try_click_download(driver, wait) -> bool:
    for sel in BTN_SELECTORS:
        try:
            el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            try:
                wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            continue
    return False


def _open_issuu_embed_from_container(driver, wait, container_url: str) -> bool:
    """
    En la contenedora localiza el iframe Issuu (embed.html) y navega al embed
    en la pestaña principal (no switch_to.frame).
    """
    try:
        iframe = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, IFRAME_SEL)))
        raw_src = iframe.get_attribute("src") or ""
        if not raw_src:
            log.debug("[Issuu] iframe embed sin src")
            return False
        embed_url = urljoin(container_url, raw_src)
        log.info(f"[Issuu] Saltando a EMBED: {embed_url}")
        _get_with_retries(driver, embed_url)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        return True
    except Exception as e:
        log.debug(f"[Issuu] No se pudo saltar al EMBED: {e}")
        return False


def _sniff_for_issuu_or_pdf(driver, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """
    Busca en performance logs:
      - document.issuu.com/.../original.file?
      - .pdf directos
      - JSON de Issuu con URL original.file embebida
    """
    end = time.time() + timeout
    last_pdf_candidate = None
    seen = set()
    while time.time() < end:
        try:
            logs = driver.get_log("performance")
        except Exception:
            logs = []

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
            except Exception:
                continue
            method = msg.get("method", "")
            params = msg.get("params", {}) or {}

            if method == "Network.requestWillBeSent":
                req = params.get("request", {}) or {}
                url = req.get("url", "")
                if not url:
                    continue
                if RE_ORIGINAL_FILE.search(url):
                    return url
                if RE_PDF_URL.search(url):
                    last_pdf_candidate = url

            elif method == "Network.responseReceived":
                rid = params.get("requestId")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                resp = params.get("response", {}) or {}
                url  = resp.get("url", "")
                mime = (resp.get("mimeType") or "").lower()

                if RE_ORIGINAL_FILE.search(url):
                    return url
                if RE_PDF_URL.search(url):
                    last_pdf_candidate = url

                # JSON Issuu → extraer original.file desde body
                if "json" in mime and RE_ISSUU_JSON_EP.search(url):
                    try:
                        body = driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": rid}).get("body", "")
                        if body:
                            m = re.search(r'https?://[^"]+document\.issuu\.com/.*/original\.file\?[^"\']+', body, re.I)
                            if m:
                                return m.group(0)
                    except Exception:
                        pass

        time.sleep(0.2)

    return last_pdf_candidate


# ------------------ Estrategia pública ------------------
@dataclass
class IssuuElNuevoDiarioStrategy:
    """
    Estrategia especializada para páginas que embében Issuu (El Nuevo Diario / El Caribe).
    - Salta al EMBED antes de intentar descarga
    - 'requests_only' deniega descarga en Chrome para evitar duplicado
    - Puede reutilizar un Browser existente si se pasa `br`
    """
    prefer_mode: str = "requests_only"   # 'requests_only' | 'auto'
    headless: bool = True

    @staticmethod
    def supports(url: str) -> bool:
        try:
            h = urlparse(url).netloc.lower()
            return any(dom in h for dom in ISSUU_HOSTS)
        except Exception:
            return False

    def fetch(self, url: str, download_dir: str, br: Browser | None = None) -> Optional[str]:
        _ensure_dir(download_dir)

        # Si nos pasan un Browser, lo reutilizamos. Si no, abrimos uno propio.
        owns_browser = False
        if br is None:
            cfg = BrowserConfig(headless=self.headless, download_dir=download_dir)
            br = Browser(cfg)
            br.__enter__()  # manejar como context manager manualmente
            owns_browser = True

        try:
            d = br.driver
            w: WebDriverWait = br.wait

            log.info(f"[Issuu] Cargando contenedor: {url}")
            _get_with_retries(d, url)
            w.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            log.debug(f"[Issuu] current_url (contenedor): {d.current_url}")

            # Ir directo al embed si existe
            if "issuu.com/embed.html" not in d.current_url:
                jumped = _open_issuu_embed_from_container(d, w, url)
                log.info(f"[Issuu] Saltó a EMBED: {jumped} | current_url: {d.current_url}")

            # Evitar descarga del navegador si preferimos 'requests_only' (para no duplicar)
            if self.prefer_mode == "requests_only":
                try:
                    d.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "deny"})
                    log.debug("[Issuu] Descarga del navegador DENEGADA (requests_only)")
                except Exception as e:
                    log.debug(f"[Issuu] No se pudo denegar descarga: {e}")

            # ¿Hay enlace .pdf directo en el DOM?
            detected = None
            for sel in PDF_LINK_SELECTORS:
                try:
                    a = d.find_element(By.CSS_SELECTOR, sel)
                    href = a.get_attribute("href") or ""
                    if RE_PDF_URL.search(href):
                        detected = href
                        log.info(f"[Issuu] PDF DOM detectado: {detected}")
                        break
                except Exception:
                    continue

            # Click + sniffer si no hubo DOM directo
            if not detected:
                _flush_perf_logs(d)
                clicked = _try_click_download(d, w)
                log.debug(f"[Issuu] Click en Download: {clicked}")
                detected = _sniff_for_issuu_or_pdf(d, timeout=DEFAULT_TIMEOUT)
                log.info(f"[Issuu] Sniffer detectó: {detected}")

            if not detected:
                log.warning("[Issuu] No se detectó URL de PDF/Original.file")
                return None

            # Descargar con requests (evita doble archivo)
            sess = requests.Session()
            referer = _smart_referer_for(detected, d.current_url)
            ua = d.execute_script("return navigator.userAgent;")
            sess.headers.update({
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "es-419,es;q=0.6",
                "Referer": referer,
                "Upgrade-Insecure-Requests": "1",
            })
            _cookies_to_session(d, sess)

            suggested = _suggest_name_from_url(detected, default="issuu_edition.pdf")
            log.info(f"[Issuu] Descargando (requests): {detected}")
            with sess.get(detected, stream=True, timeout=180) as r:
                r.raise_for_status()
                cd = r.headers.get("Content-Disposition")
                fname = _filename_from_cd(cd) or suggested
                out_path = os.path.join(download_dir, fname)
                tmp = out_path + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(128 * 1024):
                        if chunk:
                            f.write(chunk)
                os.replace(tmp, out_path)

            log.info(f"[Issuu] OK → {out_path}")
            return out_path

        finally:
            # Si la estrategia abrió su propio Browser, lo cierra aquí.
            if owns_browser and br is not None:
                try:
                    br.__exit__(None, None, None)
                except Exception:
                    pass
