# -*- coding: utf-8 -*-
"""Tạo cặp khoá VAPID cho thông báo đẩy. Chạy 1 LẦN trên VPS:  python gen_vapid.py
Dán 3 dòng in ra vào file .env của app, rồi restart service. KHÔNG đổi khoá về sau (đổi = mọi thiết bị phải đăng ký lại)."""
import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()

key = ec.generate_private_key(ec.SECP256R1())
priv = b64(key.private_numbers().private_value.to_bytes(32, 'big'))
pub = b64(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
print('VAPID_PUBLIC_KEY=' + pub)
print('VAPID_PRIVATE_KEY=' + priv)
print('VAPID_SUBJECT=mailto:email-cua-ban@example.com')
