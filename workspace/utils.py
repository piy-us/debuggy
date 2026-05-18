from config import SETTINGS
import numpy as np

def normalize_name(name):
    if SETTINGS.get("admin"):
        return np.string_(name.lower())
    return name.lower()