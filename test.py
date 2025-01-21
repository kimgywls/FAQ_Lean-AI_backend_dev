from cryptography.fernet import Fernet
key = Fernet.generate_key()
print(key.decode())  # 이 값을 복사해두세요