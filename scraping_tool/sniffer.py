# scraping_tool/sniffer.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from typing import Optional, List, Tuple

from .logger import get_logger
from .utils import is_ignored

log = get_logger(__name__)


class Sniffer:
    """
    Sniffer de red basado en los logs de rendimiento (CDP) de Chrome.
    - Requiere que el WebDriver tenga performance logging activado (ya lo configuraste en Browser).
    - Expone:
        * is_running: bool
        * start(), stop()
        * sniff_original_or_pdf() -> Optional[str]
        * wait_for_pdf_or_original(timeout_s=8.0) -> Optional[str]
    - Preferencia:
        1) URLs que contengan 'original.file' (Issuu)
        2) URLs que terminen en '.pdf'
    """

    def __init__(self, driver, recent_window_s: float = 8.0):
        self.driver = driver
        self._running: bool = True
        self._recent_window_s = float(recent_window_s)

        # Estado interno
        self._last_event_ts_ms: float = 0.0
        self._candidates: List[Tuple[float, str]] = []  # [(ts_ms, url), ...]

    # -----------------------------
    # Control de ejecución
    # -----------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def reset(self) -> None:
        self._last_event_ts_ms = 0.0
        self._candidates.clear()

    # -----------------------------
    # Lectura de logs / drenado
    # -----------------------------
    def _drain_performance_logs(self) -> None:
        """
        Lee y procesa los eventos 'Network.*' del buffer de logs.
        Extrae URLs candidatas que sean PDFs o Issuu 'original.file'.
        """
        try:
            entries = self.driver.get_log("performance")
        except Exception:
            # Si no hay logs (o driver no soporta), salimos silenciosamente
            return

        for e in entries:
            try:
                raw = e.get("message", "{}")
                msg = json.loads(raw).get("message", {})
                method = msg.get("method", "")
                params = msg.get("params", {}) or {}

                # Timestamp en segundos de CDP; lo pasamos a ms
                ts = params.get("timestamp")
                if isinstance(ts, (int, float)):
                    ts_ms = float(ts) * 1000.0
                else:
                    ts_ms = time.time() * 1000.0

                # Solo procesamos eventos Network.*
                if not method.startswith("Network."):
                    continue

                url = None

                # Dos fuentes principales:
                # 1) requestWillBeSent → params.request.url
                if method == "Network.requestWillBeSent":
                    req = params.get("request", {}) or {}
                    url = req.get("url")
                # 2) responseReceived → params.response.url + mimeType
                elif method == "Network.responseReceived":
                    resp = params.get("response", {}) or {}
                    url = resp.get("url")
                    mime = resp.get("mimeType", "").lower()
                    # Si el mime indica PDF, lo guardamos aunque no termine en .pdf
                    if url and ("pdf" in mime or mime == "application/pdf"):
                        if not is_ignored(url):
                            self._push_candidate(ts_ms, url)
                        # ya registrado, seguimos al siguiente log
                        continue

                # Si tenemos URL por cualquier camino, evaluamos si es candidata
                if url and isinstance(url, str):
                    if is_ignored(url):
                        continue
                    low = url.lower()
                    if "original.file" in low or low.endswith(".pdf"):
                        self._push_candidate(ts_ms, url)

                # Actualizamos último ts visto
                if ts_ms > self._last_event_ts_ms:
                    self._last_event_ts_ms = ts_ms

            except Exception:
                # No rompemos por logs malformados
                continue

        # Limpieza de candidatos viejos (fuera de la ventana reciente)
        self._prune_old_candidates()

    def _push_candidate(self, ts_ms: float, url: str) -> None:
        # Evita duplicados exactos recientes
        for _, u in self._candidates[-20:]:
            if u == url:
                return
        self._candidates.append((ts_ms, url))
        log.debug(f"[Sniffer] candidate {url}")

    def _prune_old_candidates(self) -> None:
        now_ms = time.time() * 1000.0
        window_ms = self._recent_window_s * 1000.0
        self._candidates = [(t, u) for (t, u) in self._candidates if (now_ms - t) <= window_ms]

    # -----------------------------
    # Selección de URL preferida
    # -----------------------------
    def _pick_best_candidate(self) -> Optional[str]:
        """
        Selecciona la mejor URL candidata:
        1) 'original.file' (Issuu) más reciente
        2) '.pdf' más reciente
        """
        if not self._candidates:
            return None

        original_file: Optional[Tuple[float, str]] = None
        pdf_file: Optional[Tuple[float, str]] = None

        for t, u in self._candidates:
            low = u.lower()
            if "original.file" in low:
                if (original_file is None) or (t > original_file[0]):
                    original_file = (t, u)
            elif low.endswith(".pdf"):
                if (pdf_file is None) or (t > pdf_file[0]):
                    pdf_file = (t, u)

        if original_file:
            return original_file[1]
        if pdf_file:
            return pdf_file[1]
        return None

    # -----------------------------
    # API usada por acquisition.*
    # -----------------------------
    def sniff_original_or_pdf(self) -> Optional[str]:
        """
        Drena logs y devuelve, si existe, una URL preferida ('original.file' o '.pdf').
        """
        self._drain_performance_logs()
        return self._pick_best_candidate()

    def wait_for_pdf_or_original(self, timeout_s: float = 8.0, poll_s: float = 0.15) -> Optional[str]:
        """
        Espera hasta timeout a que aparezca un candidato válido.
        """
        end = time.time() + float(timeout_s)
        while self._running and time.time() < end:
            url = self.sniff_original_or_pdf()
            if url:
                return url
            time.sleep(poll_s)
        return None
