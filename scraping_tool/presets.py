# scraping_tool/presets.py
from .config import DownloadPolicy

PRESETS = {
    "issuu_embed": {
        "policy": DownloadPolicy.PREFER_CHROME,
        "description": "Para visores Issuu (como Diario Libre o El Nuevo Diario)",
    },
    "direct_pdf": {
        "policy": DownloadPolicy.FORCE_REQUESTS,
        "description": "Para URLs que apuntan directo a un PDF.",
    },
    "fallback": {
        "policy": DownloadPolicy.PREFER_CHROME,
        "description": "Modo general cuando no se reconoce el tipo de página.",
    },
}

def choose_preset_for(url: str) -> dict:
    url = url.lower()
    if "issuu.com" in url or "elnuevodiario.com.do":
        return PRESETS["issuu_embed"]
    elif url.endswith(".pdf"):
        return PRESETS["direct_pdf"]
    else:
        return PRESETS["fallback"]
