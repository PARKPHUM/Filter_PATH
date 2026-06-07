def classFactory(iface):
    from .main_logic import PathFilterPlugin
    return PathFilterPlugin(iface)