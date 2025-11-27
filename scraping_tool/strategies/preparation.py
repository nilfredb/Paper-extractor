from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin
from .base import Strategy, Phase, Cost
from ..logger import get_logger

log = get_logger(__name__)

class PrepareIssuuEmbed(Strategy):
    name, phase, cost = "prepare_issuu_embed", Phase.PREPARATION, Cost.CHEAP
    def run(self, browser, sniffer):
        d, w = browser.driver, browser.wait_short
        if "issuu.com/embed.html" in d.current_url:
            log.debug("Ya estamos en embed Issuu.")
            return (None, True)
        iframes = d.find_elements(By.CSS_SELECTOR, 'iframe[src*="issuu.com/embed.html"]')
        if iframes:
            raw = iframes[0].get_attribute("src") or ""
            if raw:
                embed = urljoin(d.current_url, raw)
                log.info(f"Navegando al embed Issuu: {embed}")
                d.get(embed)
                browser.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                return (None, True)
        # fallback: entrar al iframe Issuu genérico
        for fr in d.find_elements(By.CSS_SELECTOR, "iframe"):
            try:
                src = fr.get_attribute("src") or ""
                if "issuu.com" in src or "document.issuu.com" in src:
                    d.switch_to.frame(fr)
                    log.info(f"Entré al iframe Issuu: {src}")
                    return (None, True)
            except Exception as e:
                log.debug(f"No pude entrar al iframe: {e}")
        log.debug("No se encontró embed/iframe Issuu.")
        return (None, False)


class PrepareDiarioLibreViewer(Strategy):
    name, phase, cost = "prepare_diariolibre_viewer", Phase.PREPARATION, Cost.CHEAP

    def run(self, browser, sniffer):
        d = browser.driver
        js = r"""
        (function(){
          var wrap = document.querySelector('.magazine-pdf-wrapper');
          if (!wrap) return null;
          var anchors = Array.from(wrap.querySelectorAll('a.complete-download-buttom'));
          var out = { all: [], complete: null, firstPage: null };
          anchors.forEach(function(a){
            var p = a.getAttribute('data-pagenum') || '';
            var href = a.href;
            var entry = { href: href, abs: href, pagenum: p };
            out.all.push(entry);
            if (p.toLowerCase() === 'complete') out.complete = entry;
            if (p === '1' && !out.firstPage) out.firstPage = entry;
          });
          window._pdf_links = out;
          return out;
        })();
        """
        try:
            data = d.execute_script(js)
        except Exception:
            data = None

        if data and data.get("complete"):
            log.info("PrepareDiarioLibreViewer: detectado PDF COMPLETO en DOM.")
            return (None, True)
        log.debug("PrepareDiarioLibreViewer: no se halló bloque PDF completo.")
        return (None, False)