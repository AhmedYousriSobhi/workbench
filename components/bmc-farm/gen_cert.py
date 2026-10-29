"""Build-time: a self-signed certificate for the TLS surface (clients pin it, so it stays fixed for one image build)."""
import datetime
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "opti-sim bmc-farm")])
now = datetime.datetime.now(datetime.UTC)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("bmc-farm")]), critical=False)
        .sign(key, hashes.SHA256()))
(out / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(out / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
