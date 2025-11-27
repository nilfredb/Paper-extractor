# scraping_tool/strategies/acquire_diariolibre_epaper.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, time, requests
from typing import Tuple, Optional
from urllib.parse import urljoin, urlparse, parse_qs

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..logger import get_logger
log = get_logger(__name__)

TIMEOUT = 30
HOME = "https://epaper.diariolibre.com/epaper/"

def _clean(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name

def _parse_params(url: str):
    qs = parse_qs(urlparse(url).query)
    return (qs.get("publication", [""])[0],
            qs.get("date", [""])[0],
            qs.get("tpuid", [""])[0])

def _session_from_driver(driver) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    )
    for c in driver.get_cookies():
        s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    return s

def _download(sess: requests.Session, url: str, out_path: str):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    size = 0
    try:
        h = sess.head(url, allow_redirects=True, timeout=TIMEOUT)
        h.raise_for_status()
        size = int(h.headers.get("Content-Length", "0"))
    except Exception:
        pass

    if os.path.exists(out_path) and size and os.path.getsize(out_path) == size:
        log.info(f"= ya existe {out_path} con tamaño idéntico")
        return out_path

    tmp = out_path + ".part"
    with sess.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk: f.write(chunk)
    os.replace(tmp, out_path)
    return out_path


class AcquireDiarioLibreEpaper:
    """
    Strategy de adquisición:
    - Abre la portada epaper y toma todos los viewer.aspx (excepto Publicidad)
    - Para cada edición: abre el visor, abre panel PDF, toma el href del PDF completo y descarga 1 sola vez
    - Devuelve ruta(s) descargada(s) y log breve
    """
    name = "acquire_diariolibre_epaper"

    def __init__(self, download_dir: Optional[str] = None):
        self.download_dir = download_dir  # si None, derivar de BrowserConfig si existe

    def _resolve_download_dir(self, br) -> str:
        """
        Devuelve el directorio base de descargas SIN subcarpetas adicionales.
        Prioriza atributos comunes del objeto Browser.
        """
        for attr in ("download_dir", "downloads_dir", "download_path"):
            if hasattr(br, attr) and getattr(br, attr):
                return os.path.abspath(getattr(br, attr))
        if hasattr(br, "config") and getattr(br.config, "download_dir", None):
            return os.path.abspath(br.config.download_dir)
        return os.path.abspath("descargas")


    def run(self, br, sniff=None) -> Tuple[str, str]:
        """
        br: tu Browser (debe exponer .driver)
        sniff: no usado aquí
        Returns: (paths_csv, terminal_text)
          - paths_csv: rutas separadas por ';'
        """
        driver = br.driver
        dl_dir = self.download_dir or self._resolve_download_dir(br)
        term_lines = []
        saved_paths = []

        wait = WebDriverWait(driver, TIMEOUT)
        driver.get(HOME)

        # Espera portadas con viewer.aspx
        wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR,
            ".magazine-publications-outstanding-covers .cover a[href*='viewer.aspx']"
        )))

        cards = driver.find_elements(By.CSS_SELECTOR, ".magazine-publications-outstanding-covers .cover")
        links = []
        for cover in cards:
            try:
                a = cover.find_element(By.CSS_SELECTOR, "a[href*='viewer.aspx']")
                href = a.get_attribute("href") or ""
                title = cover.find_element(By.CSS_SELECTOR, ".publication-description").text.strip()
                if not href: continue
                pub, _, _ = _parse_params(href)
                if "publicidad" in title.lower() or pub.lower().startswith("publicidad"):
                    term_lines.append(f"[skip] {title}")
                    continue
                links.append((href, title))
            except Exception:
                continue

        if not links:
            return "", "No se detectaron ediciones válidas."

        for href, title in links:
            term_lines.append(f"[>] {title} -> {href}")
            driver.get(href)

            # Asegura toolbar PDF
            wait.until(EC.element_to_be_clickable((
                By.CSS_SELECTOR, ".magazine-toolbar .magazine-toolbar-pdf .icon-file-pdf"
            ))).click()

            # Panel visible
            wait.until(EC.visibility_of_element_located((
                By.CSS_SELECTOR, ".magazine-pdf-wrapper .magazine-pdf"
            )))

            # Link del PDF COMPLETO
            link_el = wait.until(EC.presence_of_element_located((
                By.CSS_SELECTOR, ".magazine-pdf-wrapper .magazine-pdf a.complete-download-buttom[data-pagenum='complete']"
            )))
            pdf_url = urljoin(driver.current_url, link_el.get_attribute("href"))
            pub, date, _ = _parse_params(driver.current_url)
            base = _clean(f"{title}-{pub or 'edicion'}-{date or 'sFecha'}") + ".pdf"
            out_path = os.path.join(dl_dir, base)

            sess = _session_from_driver(driver)
            path = _download(sess, pdf_url, out_path)
            saved_paths.append(path)
            term_lines.append(f"[ok] {os.path.basename(path)}")

            time.sleep(1)

        return ";".join(saved_paths), "\n".join(term_lines)
