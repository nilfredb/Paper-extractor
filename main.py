# main.py
from scraping_tool.pipeline import run_pipeline, run_batch
from scraping_tool.config import BrowserConfig, DEVICE_PRESETS, DEFAULT_DOWNLOAD_DIR, DownloadPolicy

cfg = BrowserConfig(
    headless=True,
    device_profile=DEVICE_PRESETS["iPhone12"],
    locale="es-419",
    timezone="America/Santo_Domingo",
    enable_stealth=True,
    download_policy=DownloadPolicy.PREFER_CHROME
)


#urls
urls = [
    "https://www.elcaribe.com.do/periodico/",
    "https://epaper.diariolibre.com/epaper/",
    "https://elnuevodiario.com.do/edicionimpresa/"
]
results = run_batch(urls, DEFAULT_DOWNLOAD_DIR, cfg.download_policy)
print(results)


for url, path in results.items():
    if path:
        print(f"✅ {url} → {path}")
    else:
        print(f"❌ {url} → no se pudo descargar")