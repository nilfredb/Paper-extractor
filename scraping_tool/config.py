from dataclasses import dataclass
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Tuple
import os

DEFAULT_DOWNLOAD_DIR = os.path.abspath("descargas")
DEFAULT_WINDOW = "1366,950"
WAIT_SHORT = 5
WAIT_NORMAL = 15
SNIFF_TIMEOUT_SHORT = 18
SNIFF_TIMEOUT_LONG = 60

# ===== Configuraciones del navegador y políticas de descarga =====

class DownloadPolicy(Enum):
    PREFER_CHROME = auto()
    FORCE_REQUESTS = auto()


@dataclass
class DeviceProfile:
    name: str
    width: int
    height: int
    device_scale_factor: int
    mobile: bool
    touch: bool
    user_agent: str

@dataclass
class BrowserConfig:
    headless: bool = True
    window_size: str = DEFAULT_WINDOW
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    download_policy: DownloadPolicy = DownloadPolicy.PREFER_CHROME
    wait_short: int = WAIT_SHORT
    wait_normal: int = WAIT_NORMAL
    device_profile: Optional[DeviceProfile] = None   # emulación de dispositivo
    locale: Optional[str] = "es-419"
    timezone: Optional[str] = None                  # e.g. "America/Santo_Domingo"
    geolocation: Optional[Tuple[float,float,int]] = None  # (lat, lon, accuracy)
    enable_stealth: bool = True


# Devices_Presets

DEVICE_PRESETS = {
    "iPhone12": DeviceProfile(
        name="iPhone 12",
        width=390, height=844, device_scale_factor=3, mobile=True, touch=True,
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1")
    ),
    "Pixel5": DeviceProfile(
        name="Pixel 5",
        width=393, height=851, device_scale_factor=2, mobile=True, touch=True,
        user_agent=("Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/86.0.4240.75 Mobile Safari/537.36")
    ),
    "iPad": DeviceProfile(
        name="iPad",
        width=768, height=1024, device_scale_factor=2, mobile=True, touch=True,
        user_agent=("Mozilla/5.0 (iPad; CPU OS 14_0 like Mac OS X) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/14.0 Mobile/15A5341f Safari/604.1")
    ),
}

