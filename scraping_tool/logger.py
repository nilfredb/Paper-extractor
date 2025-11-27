import logging
import os

LOG_FILE = os.path.abspath("scraping.log")

def _configure_root_logger():
    # Evita duplicar handlers si recargas en caliente
    root = logging.getLogger()
    if root.handlers:
        return

    fmt = "%(asctime)s | %(levelname)8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root.setLevel(logging.INFO)

    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter(fmt, datefmt))

    root.addHandler(fh)
    root.addHandler(sh)

_configure_root_logger()

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
