from utils import normalize_name

class User:
    def __init__(self, name):
        self.name = normalize_name(name)

    def __repr__(self):
        return f"User({self.name})"