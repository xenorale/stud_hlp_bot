from cryptography.fernet import Fernet, InvalidToken
import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv('ENCRYPTION_KEY')
if not key:
    key = Fernet.generate_key()
    with open(".env", "a") as f:
        f.write(f"\nENCRYPTION_KEY={key.decode()}\n")

cipher = Fernet(key)

def encrypt(data: str) -> str:
    return cipher.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    try:
        return cipher.decrypt(data.encode()).decode()
    except (InvalidToken, ValueError):
        return data
