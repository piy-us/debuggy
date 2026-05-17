def normalize_name(name):
    # We can't import SETTINGS here due to circular dependency.
    # If we need to check settings, we should pass them as an argument.
    return name.lower()