"""trex-web-gui backend."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("trex-web-gui")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"
