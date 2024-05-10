import ujson

# Function to load history from a JSON file
def load_history(filename):
    try:
        with open(filename, 'r') as file:
            history = ujson.load(file)
        return history
    except (OSError, ValueError):
        return []

# Function to save history to a JSON file
def save_history(history, filename):
    with open(filename, 'w') as file:
        ujson.dump(history, file)

# Example usage
filename = "history.json"
history = load_history(filename)

# Add data to history
new_data = {"timestamp": "2024-05-07", "value": 75}
history.append(new_data)

# Save history to file
save_history(history, filename)

print(history)