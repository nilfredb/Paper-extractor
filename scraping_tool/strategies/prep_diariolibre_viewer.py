# scraping_tool/strategies/prep_diariolibre_viewer.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from .base import Strategy, Phase, Cost
from ..logger import get_logger

log = get_logger(__name__)

JS_EXTRACT = r"""
(() => {
  const out = { all: [], complete: null, firstPage: null };
  try {
    // Panel lateral específico del viewer
    const panel = document.querySelector('.magazine-pdf-wrapper');
    if (panel) {
      const anchors = [...panel.querySelectorAll('a.complete-download-buttom.track-download[href]')];
      for (const a of anchors) {
        const href = a.getAttribute('href');
        const dl = a.getAttribute('download') || '';
        const pageNum = a.getAttribute('data-pagenum') || '';
        const entry = { href, download: dl, page: pageNum };
        out.all.push(entry);
        if (pageNum === 'complete') out.complete = entry;
        if (pageNum === '1') out.firstPage = entry;
      }
    }

    // Fallback: cualquier <a download ... .pdf> del documento
    if (out.all.length === 0) {
      const a2 = [...document.querySelectorAll('a[download][href*=".pdf"]')];
      for (const a of a2) {
        const href = a.getAttribute('href');
        const dl = a.getAttribute('download') || '';
        out.all.push({ href, download: dl, page: '' });
      }
    }

    // Normalizar a URLs absolutas
    const base = location.origin + location.pathname.replace(/[^\/]*$/, '');
    for (const e of out.all) {
      try {
        const u = new URL(e.href, base);
        e.abs = u.href;
      } catch (_) {
        e.abs = e.href;
      }
    }

    // Guarda en window para adquisición
    window._pdf_links = out;
    return out;
  } catch (err) {
    return { error: String(err), all: [], complete: null, firstPage: null };
  }
})();
"""

class PrepareDiarioLibreViewer(Strategy):
    name, phase, cost = "prepare_diariolibre_viewer", Phase.PREPARATION, Cost.CHEAP

    def run(self, browser, sniffer):
        d = browser.driver
        try:
            res = d.execute_script(JS_EXTRACT)
            n = len(res.get('all', [])) if isinstance(res, dict) else 0
            log.info(f"Viewer: encontrados {n} enlaces PDF (incluyendo 'completo' si existe).")
            return (res, True if n > 0 else False)
        except Exception as e:
            log.warning(f"No se pudieron extraer PDFs del viewer: {e}")
            return (None, False)
