"""capsule-corp: a terminal catalogue of reproducible, pre-registered research capsules."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Single source of truth is the [project] version in pyproject.toml; reading it
    # back from installed metadata keeps a capsule's recorded provenance honest
    # rather than reporting a constant that drifts from what was released.
    __version__ = _version("capsule-corp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
