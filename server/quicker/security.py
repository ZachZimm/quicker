import hashlib
import secrets


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str):
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"{salt}${key}"


def password_matches(password: str, stored: str):
    salt, expected = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return secrets.compare_digest(actual, expected)
