# scraping_tool/browser.py
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from .config import BrowserConfig, DownloadPolicy
from .utils import ensure_dir
from .logger import get_logger

log = get_logger(__name__)


class Browser:
    """
    Context manager para Chrome con:
      - page_load_strategy='eager' (carga más rápida)
      - Bloqueo de recursos pesados por CDP (imágenes, fuentes, trackers)
      - Prefs de Chrome para descargas y (opcional) desactivar imágenes
      - Flags de rendimiento/estabilidad
      - Config de descarga según DownloadPolicy
      - Sniffer habilitado (Network.enable + cache off)
    """

    # ===== Ajustes de rendimiento por defecto =====
    _PAGE_LOAD_STRATEGY = "eager"  # 'normal' | 'eager' | 'none'

    _BLOCKED_URL_PATTERNS = [
        "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp",
        "*.svg",
        # "*.css",  # ⚠️ coméntalo si rompe selectores/estilos
        "*.woff", "*.woff2", "*.ttf",
        "*googletagmanager*", "*google-analytics*", "*doubleclick*",
        "*criteo*", "*taboola*", "*outbrain*", "*scorecardresearch*"
    ]

    _DISABLE_IMAGES_PREF = True

    _EXTRA_ARGS = [
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-ipc-flooding-protection",
        "--disable-extensions",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame",
        "--metrics-recording-only",
        "--mute-audio",
    ]

    def __init__(self, cfg: BrowserConfig = BrowserConfig()):
        self.cfg = cfg
        self.driver = None
        self.wait_short = None
        self.wait = None

    def __enter__(self):
        ensure_dir(self.cfg.download_dir)
        log.info(
            f"Inicializando Chrome (headless={self.cfg.headless}, "
            f"policy={self.cfg.download_policy.name}, dir='{self.cfg.download_dir}')"
        )

        opts = Options()
        # Estrategia de carga de página (más rápido que 'normal')
        try:
            opts.page_load_strategy = self._PAGE_LOAD_STRATEGY
        except Exception:
            # En algunas versiones de selenium esto puede no existir; no crítico
            pass

        if self.cfg.headless:
            opts.add_argument("--headless=new")

        opts.add_argument(f"--window-size={self.cfg.window_size}")
        opts.add_argument(f"--user-agent={self.cfg.user_agent}")

        for arg in self._EXTRA_ARGS:
            opts.add_argument(arg)

        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        chrome_prefs = {
            "download.default_directory": self.cfg.download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        if self._DISABLE_IMAGES_PREF:
            chrome_prefs["profile.managed_default_content_settings.images"] = 2
        opts.add_experimental_option("prefs", chrome_prefs)

        # Instanciar driver (ChromeDriverManager cachea el binario)
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts
        )
        self.wait_short = WebDriverWait(self.driver, self.cfg.wait_short)
        self.wait = WebDriverWait(self.driver, self.cfg.wait_normal)

        # Habilitar CDP y aplicar ajustes
        self._enable_cdp_network()
        self._block_heavy_resources()

        # Emulación / locale / stealth (si están configurados)
        try:
            # Métodos manejan ausencia de campos internamente
            self._apply_device_emulation()
            self._apply_locale_timezone_geolocation()
            self._apply_stealth_tweaks()
        except Exception as e:
            log.debug(f"Al aplicar emulación/stealth hubo un problema: {e}")

        # Configurar comportamiento de descarga según política
        self._configure_download_behavior()

        log.info("Chrome listo.")
        return self

    def __exit__(self, exc_type, exc, tb):
        log.info("Cerrando Chrome…")
        try:
            if self.driver:
                self.driver.quit()
        finally:
            self.driver = None
            self.wait = None
            self.wait_short = None
        log.info("Chrome cerrado.")

    # ========= Métodos internos =========

    def _enable_cdp_network(self):
        """Activa CDP Network y deshabilita caché para que el sniffer vea todo en tiempo real."""
        try:
            self.driver.execute_cdp_cmd("Network.enable", {})
            self.driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
            log.debug("CDP Network habilitado y cache deshabilitado.")
        except Exception as e:
            log.warning(f"No se pudo habilitar CDP Network: {e}")

    def _block_heavy_resources(self):
        """Bloquea imágenes, fuentes y trackers por patrón (reduce bytes transferidos)."""
        if not self._BLOCKED_URL_PATTERNS:
            return
        try:
            self.driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": self._BLOCKED_URL_PATTERNS})
            log.info(f"Bloqueo de recursos activado ({len(self._BLOCKED_URL_PATTERNS)} patrones).")
        except Exception as e:
            log.debug(f"No se pudo bloquear recursos por CDP: {e}")

    def _configure_download_behavior(self):
        """
        Configura el comportamiento de descargas:
          - PREFER_CHROME: permitir descargas nativas y guardar en download_dir.
          - FORCE_REQUESTS: denegar descargas nativas; todo se hace con requests.
        """
        try:
            behavior = "deny" if self.cfg.download_policy == DownloadPolicy.FORCE_REQUESTS else "allow"
            params = {"behavior": behavior}
            if behavior == "allow":
                params["downloadPath"] = self.cfg.download_dir
            self.driver.execute_cdp_cmd("Page.setDownloadBehavior", params)
            log.info(f"DownloadBehavior configurado: {behavior}")
        except Exception as e:
            log.warning(f"No pude establecer DownloadBehavior: {e}")

    def _apply_device_emulation(self):
        """Aplica Emulation.* si hay device_profile en la config."""
        dp = getattr(self.cfg, "device_profile", None)
        if not dp:
            return
        try:
            # user agent override (CDP)
            try:
                self.driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
                    "userAgent": dp.user_agent,
                    "acceptLanguage": self.cfg.locale or "en-US,en;q=0.9",
                    "platform": ("Linux" if "Android" in dp.user_agent
                                 else "iPhone" if "iPhone" in dp.user_agent
                                 else "MacIntel")
                })
            except Exception as e:
                log.debug(f"No se pudo setUserAgentOverride: {e}")

            # device metrics
            try:
                self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {
                    "width": int(dp.width),
                    "height": int(dp.height),
                    "deviceScaleFactor": int(dp.device_scale_factor),
                    "mobile": bool(dp.mobile),
                    "screenOrientation": {"type": "portraitPrimary", "angle": 0}
                })
            except Exception as e:
                log.debug(f"No se pudo setDeviceMetricsOverride: {e}")

            # touch support
            if getattr(dp, "touch", False):
                try:
                    self.driver.execute_cdp_cmd("Emulation.setTouchEmulationEnabled", {"enabled": True})
                except Exception:
                    pass

            log.info(f"Device emulation applied: {getattr(dp, 'name', 'unknown')}")
        except Exception as e:
            log.warning(f"No pude aplicar emulación de dispositivo: {e}")

    def _apply_locale_timezone_geolocation(self):
        """Headers, timezone y geolocalización (si están definidas)."""
        # extra headers
        try:
            headers = {"Accept-Language": getattr(self.cfg, "locale", None) or "en-US,en;q=0.9"}
            self.driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": headers})
        except Exception as e:
            log.debug(f"No pude establecer extra headers: {e}")

        # timezone
        tz = getattr(self.cfg, "timezone", None)
        if tz:
            try:
                self.driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": tz})
                log.debug(f"Timezone override: {tz}")
            except Exception as e:
                log.debug(f"No pude setTimezoneOverride: {e}")

        # geolocation
        geo = getattr(self.cfg, "geolocation", None)
        if geo:
            try:
                lat, lon, acc = geo
                self.driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
                    "latitude": float(lat), "longitude": float(lon), "accuracy": int(acc)
                })
                log.debug(f"Geolocation override: {lat},{lon} acc={acc}")
            except Exception as e:
                log.debug(f"No pude setGeolocationOverride: {e}")

    def _apply_stealth_tweaks(self):
        """Pequeños ajustes JS para reducir fingerprints sencillos."""
        if not getattr(self.cfg, "enable_stealth", False):
            return
        try:
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": r"""
// navigator.webdriver falso
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
// platform
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
// plugins mock (simple)
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
// window.chrome mock
window.chrome = window.chrome || { runtime: {} };
// permissions: override query for notifications/camera/mic
const _origQuery = navigator.permissions && navigator.permissions.query;
if (navigator.permissions && navigator.permissions.query) {
  navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
      Promise.resolve({ state: Notification.permission }) :
      _origQuery(parameters)
  );
}
"""
            })
            log.debug("Stealth tweaks añadidos (navigator.webdriver, languages, plugins).")
        except Exception as e:
            log.debug(f"No pude añadir stealth tweaks: {e}")
