"""
idios_payload.py — Idios Job Payload Delivery via Beam Private IPFS

Handles encrypted job payload routing between requester and node.
Uses Beam's private IPFS network (separate from public IPFS via swarm key).

Flow:
    Requester:
        1. Encrypt job payload with node's RSA public key
        2. Upload encrypted payload to Beam IPFS (ipfs_add)
        3. Get IPFS CID back
        4. Pass CID to node (via SBBS or out-of-band)
        5. Include result_hash in Beam contract create call

    Node:
        1. Receive CID from requester
        2. Download payload from Beam IPFS (ipfs_get)
        3. Decrypt with own RSA private key
        4. Run inference
        5. Hash the result
        6. Include result hash in attest_data during consensus

Requirements:
    pip install cryptography requests
    wallet-api must be started with --enable_ipfs=true

Usage:
    # Requester side
    from idios_payload import RequesterPayload
    r = RequesterPayload(beam_api_url="http://127.0.0.1:10000/api/wallet")
    cid, result_hash = r.prepare_job(
        payload={"model": "llama2", "prompt": "Summarise this contract..."},
        node_rsa_pubkey_pem=node_pubkey_bytes
    )
    # Use cid and result_hash in beam contract create call

    # Node side
    from idios_payload import NodePayload
    n = NodePayload(
        beam_api_url="http://127.0.0.1:10001/api/wallet",
        rsa_private_key_path="~/.idios/node_rsa_key.pem"
    )
    payload = n.retrieve_and_decrypt(cid)
    result = run_inference(payload)
    result_hash = n.hash_result(result)
"""

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Tuple

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSA encryption helpers
# ---------------------------------------------------------------------------

# OAEP padding for encryption (PSS is for signatures only)
_OAEP_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None
)

# RSA key size for new key generation
RSA_KEY_SIZE = 2048

# Max bytes RSA 2048 + OAEP can encrypt in one chunk
# Formula: key_size_bytes - 2 * hash_size_bytes - 2 = 256 - 64 - 2 = 190
RSA_MAX_CHUNK = 190


def generate_rsa_keypair() -> Tuple[bytes, bytes]:
    """
    Generate a new RSA keypair for a node.
    Returns (private_key_pem, public_key_pem) as bytes.

    Node operators run this once and store the private key securely.
    The public key goes into the DHT heartbeat beam_pubkey field
    (or as a separate rsa_pubkey field — see note below).

    Note: beam_pubkey in ServerInfo stores the Beam wallet pubkey (hex).
    For RSA encryption of job payloads, nodes need a separate RSA key.
    The simplest approach is to add an rsa_pubkey field alongside beam_pubkey,
    or encode both in a single field as JSON.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def rsa_encrypt(data: bytes, public_key_pem: bytes) -> bytes:
    """
    Encrypt data with RSA public key using OAEP padding.
    Handles payloads larger than RSA max by chunking + AES hybrid encryption.

    For simplicity this implementation uses pure RSA chunking for small payloads.
    For large payloads (>190 bytes) it uses hybrid encryption:
    - Generate a random AES-256 key
    - Encrypt the data with AES-GCM
    - Encrypt the AES key with RSA-OAEP
    - Return: RSA_encrypted_key + AES_nonce + AES_ciphertext
    """
    public_key = serialization.load_pem_public_key(public_key_pem)

    if len(data) <= RSA_MAX_CHUNK:
        # Small payload — direct RSA encryption
        encrypted = public_key.encrypt(data, _OAEP_PADDING)
        return b"RSA:" + base64.b64encode(encrypted)
    else:
        # Large payload — hybrid RSA+AES encryption
        return _hybrid_encrypt(data, public_key)


def rsa_decrypt(encrypted_data: bytes, private_key_pem: bytes) -> bytes:
    """Decrypt data encrypted with rsa_encrypt."""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    if encrypted_data.startswith(b"RSA:"):
        # Direct RSA decryption
        ciphertext = base64.b64decode(encrypted_data[4:])
        return private_key.decrypt(ciphertext, _OAEP_PADDING)
    elif encrypted_data.startswith(b"HYB:"):
        # Hybrid decryption
        return _hybrid_decrypt(encrypted_data[4:], private_key)
    else:
        raise ValueError("Unknown encryption format — expected RSA: or HYB: prefix")


def _hybrid_encrypt(data: bytes, public_key: RSAPublicKey) -> bytes:
    """Hybrid RSA+AES encryption for payloads larger than RSA max."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    # Generate random AES-256 key and nonce
    aes_key = os.urandom(32)
    nonce = os.urandom(12)

    # Encrypt data with AES-GCM
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, data, None)

    # Encrypt AES key with RSA-OAEP
    encrypted_key = public_key.encrypt(aes_key, _OAEP_PADDING)

    # Pack: [4 bytes key_len][encrypted_key][12 bytes nonce][ciphertext]
    key_len = len(encrypted_key).to_bytes(4, "big")
    packed = key_len + encrypted_key + nonce + ciphertext

    return b"HYB:" + base64.b64encode(packed)


def _hybrid_decrypt(encrypted_b64: bytes, private_key: RSAPrivateKey) -> bytes:
    """Decrypt hybrid RSA+AES encrypted data."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    packed = base64.b64decode(encrypted_b64)

    # Unpack
    key_len = int.from_bytes(packed[:4], "big")
    encrypted_key = packed[4:4 + key_len]
    nonce = packed[4 + key_len:4 + key_len + 12]
    ciphertext = packed[4 + key_len + 12:]

    # Decrypt AES key with RSA
    aes_key = private_key.decrypt(encrypted_key, _OAEP_PADDING)

    # Decrypt data with AES-GCM
    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# Result hash
# ---------------------------------------------------------------------------

def hash_result(result: Any) -> str:
    """
    Hash an inference result for use in the Beam contract and attest_data.
    Result is JSON-serialised then SHA256 hashed.
    Returns 64-char hex string.

    Both requester and node must use the same serialisation to get matching hashes.
    The requester commits this hash at job creation.
    The node produces this hash after inference and includes it in attest_data.
    """
    if isinstance(result, (dict, list)):
        # Deterministic JSON serialisation — sorted keys, no whitespace
        serialised = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    elif isinstance(result, str):
        serialised = result.encode()
    elif isinstance(result, bytes):
        serialised = result
    else:
        serialised = str(result).encode()

    return hashlib.sha256(serialised).hexdigest()


# ---------------------------------------------------------------------------
# Beam IPFS helpers
# ---------------------------------------------------------------------------

def ipfs_add(api_url: str, data: bytes) -> str:
    """
    Upload data to Beam's private IPFS network.
    Returns IPFS CID.

    wallet-api must be started with --enable_ipfs=true
    """
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "ipfs_add",
        "params": {"data": list(data)}
    }
    try:
        r = requests.post(api_url, json=payload, timeout=30)
        r.raise_for_status()
        data_resp = r.json()
    except requests.RequestException as e:
        raise ConnectionError(f"Beam wallet-api unreachable: {e}") from e

    if "error" in data_resp:
        raise RuntimeError(f"ipfs_add error: {data_resp['error']}")

    cid = data_resp.get("result", {}).get("hash")
    if not cid:
        raise RuntimeError(f"ipfs_add returned no hash: {data_resp}")

    log.info("Uploaded to Beam IPFS — CID: %s", cid)
    return cid


def ipfs_get(api_url: str, cid: str) -> bytes:
    """
    Retrieve data from Beam's private IPFS network by CID.
    Returns raw bytes.
    """
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "ipfs_get",
        "params": {"hash": cid}
    }
    try:
        r = requests.post(api_url, json=payload, timeout=30)
        r.raise_for_status()
        data_resp = r.json()
    except requests.RequestException as e:
        raise ConnectionError(f"Beam wallet-api unreachable: {e}") from e

    if "error" in data_resp:
        raise RuntimeError(f"ipfs_get error: {data_resp['error']}")

    b64_data = data_resp.get("result", {}).get("data")
    if not b64_data:
        raise RuntimeError(f"ipfs_get returned no data: {data_resp}")

    if isinstance(b64_data, list): return bytes(b64_data)
    return base64.b64decode(b64_data)


# ---------------------------------------------------------------------------
# Requester — prepare job
# ---------------------------------------------------------------------------

class RequesterPayload:
    """
    Requester-side payload handling.
    Encrypts job input, uploads to Beam IPFS, returns CID and result hash.
    """

    def __init__(self, beam_api_url: str = "http://127.0.0.1:10000/api/wallet"):
        self.api_url = beam_api_url

    def prepare_job(
        self,
        payload: Any,
        node_rsa_pubkey_pem: bytes,
        expected_result: Optional[Any] = None,
    ) -> Tuple[str, str]:
        """
        Prepare a job for submission to Idios.

        Args:
            payload: The inference input — dict, string, or bytes
            node_rsa_pubkey_pem: Node's RSA public key in PEM format
            expected_result: If known in advance, the expected output for hash
                           If None, requester and node must agree out-of-band

        Returns:
            (cid, result_hash)
            - cid: IPFS CID to pass to the node
            - result_hash: 64-char hex to commit in the Beam contract create call

        Note on result_hash:
            For deterministic inference (same model + same input = same output),
            the requester can compute the expected result hash in advance.
            For non-deterministic inference, the requester and node must agree
            on the expected output before the job starts — the hash is a commitment.
        """
        # Serialise payload
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        elif isinstance(payload, str):
            raw = payload.encode()
        elif isinstance(payload, bytes):
            raw = payload
        else:
            raw = str(payload).encode()

        # Encrypt with node's RSA public key
        log.info("Encrypting payload (%d bytes) with node RSA key", len(raw))
        encrypted = rsa_encrypt(raw, node_rsa_pubkey_pem)

        # Upload to Beam IPFS
        cid = ipfs_add(self.api_url, encrypted)

        # Compute result hash
        if expected_result is not None:
            result_hash = hash_result(expected_result)
        else:
            # Placeholder — requester and node must agree out-of-band
            # In practice, for deterministic inference, run the model locally first
            log.warning(
                "No expected_result provided — result_hash is placeholder. "
                "Requester and node must agree on expected output before job creation."
            )
            result_hash = "a" * 64  # placeholder

        log.info("Job prepared — CID: %s  result_hash: %s...", cid, result_hash[:16])
        return cid, result_hash


# ---------------------------------------------------------------------------
# Node — retrieve and process
# ---------------------------------------------------------------------------

class NodePayload:
    """
    Node-side payload handling.
    Retrieves encrypted payload from Beam IPFS, decrypts, returns raw input.
    """

    def __init__(
        self,
        beam_api_url: str = "http://127.0.0.1:10001/api/wallet",
        rsa_private_key_path: Optional[str] = None,
        rsa_private_key_pem: Optional[bytes] = None,
    ):
        self.api_url = beam_api_url

        if rsa_private_key_pem is not None:
            self._private_key_pem = rsa_private_key_pem
        elif rsa_private_key_path is not None:
            path = Path(rsa_private_key_path).expanduser()
            self._private_key_pem = path.read_bytes()
        else:
            raise ValueError("Either rsa_private_key_path or rsa_private_key_pem must be provided")

    def retrieve_and_decrypt(self, cid: str) -> bytes:
        """
        Retrieve encrypted payload from Beam IPFS and decrypt.
        Returns raw payload bytes.
        """
        log.info("Retrieving payload from Beam IPFS — CID: %s", cid)
        encrypted = ipfs_get(self.api_url, cid)

        log.info("Decrypting payload (%d bytes)", len(encrypted))
        raw = rsa_decrypt(encrypted, self._private_key_pem)

        log.info("Payload decrypted — %d bytes", len(raw))
        return raw

    def retrieve_and_decrypt_json(self, cid: str) -> Any:
        """Retrieve, decrypt, and JSON-parse payload."""
        raw = self.retrieve_and_decrypt(cid)
        return json.loads(raw)

    def hash_result(self, result: Any) -> str:
        """Hash inference result for use in attest_data and Beam contract settle."""
        return hash_result(result)


# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------

def save_node_keypair(private_key_path: str = "~/.idios/node_rsa_key.pem",
                      public_key_path: str = "~/.idios/node_rsa_pubkey.pem"):
    """
    Generate and save a new RSA keypair for a node operator.
    Run once during node setup.
    """
    private_path = Path(private_key_path).expanduser()
    public_path = Path(public_key_path).expanduser()

    private_path.parent.mkdir(parents=True, exist_ok=True)

    if private_path.exists():
        log.warning("Private key already exists at %s — not overwriting", private_path)
        return

    private_pem, public_pem = generate_rsa_keypair()
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    log.info("RSA keypair generated:")
    log.info("  Private key: %s", private_path)
    log.info("  Public key:  %s", public_key_path)
    log.info("Add the public key to your ServerInfo beam_pubkey or rsa_pubkey DHT heartbeat field.")


def load_public_key_pem(path: str) -> bytes:
    return Path(path).expanduser().read_bytes()


def load_private_key_pem(path: str) -> bytes:
    return Path(path).expanduser().read_bytes()


# ---------------------------------------------------------------------------
# CLI — key generation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    p = argparse.ArgumentParser(description="Idios payload utilities")
    sub = p.add_subparsers(dest="cmd")

    gen = sub.add_parser("genkey", help="Generate RSA keypair for node operator")
    gen.add_argument("--private", default="~/.idios/node_rsa_key.pem")
    gen.add_argument("--public", default="~/.idios/node_rsa_pubkey.pem")

    test = sub.add_parser("test", help="Test encrypt/decrypt round-trip")

    args = p.parse_args()

    if args.cmd == "genkey":
        save_node_keypair(args.private, args.public)

    elif args.cmd == "test":
        log.info("Testing RSA encrypt/decrypt round-trip...")
        priv, pub = generate_rsa_keypair()

        # Small payload
        small = b"Hello Idios"
        enc = rsa_encrypt(small, pub)
        dec = rsa_decrypt(enc, priv)
        assert dec == small, "Small payload round-trip FAILED"
        log.info("Small payload (direct RSA): OK")

        # Large payload
        large = b"x" * 1000
        enc = rsa_encrypt(large, pub)
        dec = rsa_decrypt(enc, priv)
        assert dec == large, "Large payload round-trip FAILED"
        log.info("Large payload (hybrid RSA+AES): OK")

        # Result hash
        result = {"output": "The contract is valid.", "confidence": 0.97}
        h = hash_result(result)
        assert len(h) == 64
        log.info("Result hash: %s", h)

        log.info("All tests passed ✅")

    else:
        p.print_help()
        sys.exit(1)
