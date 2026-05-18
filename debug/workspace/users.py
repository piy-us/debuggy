# workspace/users.py
# Bug: get_user uses direct dict lookup — raises KeyError for missing ids

USERS = {1: "Alice", 2: "Bob"}

def get_user(users: dict, user_id: int) -> str:
    return users.get(user_id, "Unknown")          # KeyError when user_id not in users

if __name__ == "__main__":
    print(get_user(USERS, 1))
    print(get_user(USERS, 3))      # <-- crashes here
