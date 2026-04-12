__all__ = ["__version__", "version_info"]


__version__ = "2.7.3+RFC"
version_info = tuple(
    int(part) if part.isdigit() else part
    for part in __version__.split(".")
)
