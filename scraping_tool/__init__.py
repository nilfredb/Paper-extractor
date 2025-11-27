from .pipeline import run_pipeline
from .config import DownloadPolicy, DEFAULT_DOWNLOAD_DIR

def download_edition(url: str, download_dir: str = DEFAULT_DOWNLOAD_DIR):
    out = run_pipeline(url, download_dir, policy=DownloadPolicy.PREFER_CHROME)
    if out:
        return out
    return run_pipeline(url, download_dir, policy=DownloadPolicy.FORCE_REQUESTS)
