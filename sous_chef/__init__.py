"""Sous Chef QGIS plugin: turns what you do in QGIS into reusable recipes (Processing models)."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import SousChefPlugin
    return SousChefPlugin(iface)
