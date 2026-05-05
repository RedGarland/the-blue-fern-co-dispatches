#!/usr/bin/env python3
import sys, smtplib, ssl

if len(sys.argv) < 4:
    print("Usage: python fetch_smtp_cert.py <host> <port> <out-pem>")
    sys.exit(2)

host = sys.argv[1]
port = int(sys.argv[2])
out = sys.argv[3]

try:
    smtp = smtplib.SMTP(host, port, timeout=10)
    smtp.ehlo()
    smtp.starttls()
    sock = smtp.sock
    der = sock.getpeercert(True)
    pem = ssl.DER_cert_to_PEM_cert(der)
    with open(out, "w", encoding="utf-8") as f:
        f.write(pem)
    print("Wrote:", out)
    smtp.quit()
except Exception as e:
    print("Error:", e)
    sys.exit(1)