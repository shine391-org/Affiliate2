import os
import json
import base64
import sqlite3
import shutil
import ctypes
from ctypes import wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
CryptUnprotectData.argtypes = [
    ctypes.POINTER(DATA_BLOB),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(DATA_BLOB),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DATA_BLOB)
]
CryptUnprotectData.restype = wintypes.BOOL

def dpapi_decrypt(encrypted_bytes):
    data_in = DATA_BLOB(len(encrypted_bytes), ctypes.cast(ctypes.create_string_buffer(encrypted_bytes), ctypes.POINTER(ctypes.c_byte)))
    data_out = DATA_BLOB()
    if CryptUnprotectData(ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)):
        out_buf = ctypes.string_at(data_out.pbData, data_out.cbData)
        ctypes.windll.kernel32.LocalFree(data_out.pbData)
        return out_buf
    raise RuntimeError("DPAPI decrypt failed")

def get_douyin_cookies():
    local_state_path = r"C:\Users\shine\AppData\Local\Google\Chrome\User Data\Local State"
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    enc_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    enc_key = enc_key[5:]
    aes_key = dpapi_decrypt(enc_key)

    cookie_db = r"C:\Users\shine\AppData\Local\Google\Chrome\User Data\Default\Network\Cookies"
    temp_db = os.path.join(os.environ.get("TEMP", "."), "temp_cookies_decrypt.db")
    shutil.copyfile(cookie_db, temp_db)
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT name, host_key, path, is_secure, expires_utc, encrypted_value FROM cookies WHERE host_key LIKE '%douyin%'")

    decrypted_cookies = []
    rows = cur.fetchall()
    print(f"Total rows fetched from SQLite: {len(rows)}")
    for name, host_key, path, is_secure, expires_utc, enc_val in rows:
        prefix = enc_val[:5]
        # print prefix for first few
        if len(decrypted_cookies) == 0:
            print(f"Sample prefix for {name}: {prefix}")
        if enc_val[:3] == b"v10":
            nonce = enc_val[3:15]
            ciphertext = enc_val[15:]
            aesgcm = AESGCM(aes_key)
            try:
                val = aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
                decrypted_cookies.append({
                    "name": name,
                    "value": val,
                    "domain": host_key,
                    "path": path,
                    "secure": bool(is_secure)
                })
            except Exception as e:
                print(f"Decrypt error for {name}: {e}")
        elif enc_val[:3] == b"v20":
            print(f"Cookie {name} uses Chrome App-Bound Encryption (v20)!")


    conn.close()
    if os.path.exists(temp_db):
        os.remove(temp_db)
    return decrypted_cookies

if __name__ == "__main__":
    cookies = get_douyin_cookies()
    print(f"Decrypted {len(cookies)} Douyin cookies!")
    for c in cookies:
        print(f"  {c['name']} = {c['value'][:20]}... (domain: {c['domain']})")
    
    # Check if sessionid or passport_auth is present
    has_session = any(c['name'] in ('sessionid', 'sessionid_ss', 'passport_auth_mix_state') for c in cookies)
    print(f"Session / Auth cookies present: {has_session}")
