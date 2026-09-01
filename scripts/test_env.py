import os

from dotenv import load_dotenv

load_dotenv()

host = os.getenv("POSTGRES_HOST")
port = os.getenv("POSTGRES_PORT")
db = os.getenv("POSTGRES_DB")

print(f"{host = }")
print(f"{port = }")
print(f"{db = }")