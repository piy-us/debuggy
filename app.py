# test_script.py — two intentional bugs to test debuggy with

def get_user(users, user_id):
    return users[user_id]

def format_greeting(user):
    # Bug 2: int + str concatenation → TypeError
    return "Hello " + user["age"] + "! Welcome, " + user["name"]


users = {
    1: {"name": "Alice", "age": 30},
    2: {"name": "Bob",   "age": 25},
}

user = get_user(users, 3)          # KeyError: 3
print(format_greeting(user))

