from urllib.parse import urljoin
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from .base import Strategy, Phase, Cost
from ..logger import get_logger

log = get_logger(__name__)

PDF_LINK_SELECTORS = ['a[href$=".pdf"]','a[href*=".pdf"]','a[download][href]']

class DiscoverViewerAspx(Strategy):
    name, phase, cost = "discover_viewer_aspx", Phase.DISCOVERY, Cost.CHEAP
    def __init__(self, selector='.magazine-publications a[href*="viewer.aspx"]'):
        self.selector = selector
    def run(self, browser, sniffer):
        d, w = browser.driver, browser.wait_short
        cu = d.current_url
        if "viewer.aspx" in cu or "issuu.com" in cu:
            log.debug("Ya estamos en un visor; skip DiscoverViewerAspx.")
            return (None, False)
        log.debug(f"Buscando viewer.aspx con selector: {self.selector}")
        els = d.find_elements(By.CSS_SELECTOR, self.selector)
        if not els:
            try:
                w.until(EC.presence_of_element_located((By.CSS_SELECTOR, self.selector)))
                els = d.find_elements(By.CSS_SELECTOR, self.selector)
            except Exception:
                els = []
        if not els:
            log.debug("No se encontró viewer.aspx en listing.")
            return (None, False)
        href = els[0].get_attribute("href") or ""
        if href:
            full = urljoin(cu, href)
            log.info(f"Descubierto viewer.aspx: {full}")
            d.get(full)
            browser.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        return (None, False)

class DiscoverDirectPdfLink(Strategy):
    name, phase, cost = "discover_direct_pdf_link", Phase.DISCOVERY, Cost.CHEAP
    def __init__(self, selectors=PDF_LINK_SELECTORS):
        self.selectors = selectors
    def run(self, browser, sniffer):
        d = browser.driver
        for sel in self.selectors:
            els = d.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                continue
            href = els[0].get_attribute("href") or ""
            if href and href.lower().endswith(".pdf"):
                log.info(f"PDF directo descubierto en DOM: {href}")
                d.execute_script("window._direct_pdf = arguments[0];", href)
                return (None, True)
        log.debug("No se halló PDF directo en DOM.")
        return (None, False)
