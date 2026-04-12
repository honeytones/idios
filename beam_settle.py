import requests
import json

VAULT_WASM = "/home/tones/beam-cli/vault_anon_master.wasm"
VAULT_CID  = "a3385e50cf33afc9f769ee1d82d56b73046d680d343977f36d9a303d7bcdc4da"

# Wallet A = Requester (port 10000)
# Wallet B = Node operator (port 10001)
REQUESTER_API = "http://127.0.0.1:10000/api/wallet"
NODE_API      = "http://127.0.0.1:10001/api/wallet"

def call(api_url, method, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(api_url, json=payload)
    return r.json()

def wallet_status(api_url):
    return call(api_url, "wallet_status", {})

def tx_status(api_url, txid):
    return call(api_url, "tx_status", {"txId": txid})

def get_my_key(api_url):
    """Get this wallet's vault_anon pkOwner."""
    args = f"role=user,action=my_key,cid={VAULT_CID}"
    result = call(api_url, "invoke_contract", {
        "contract_file": VAULT_WASM,
        "args": args,
        "create_tx": False
    })
    output = json.loads(result["result"]["output"])
    return output["res"]["key"]

def send_payment(node_pkowner, amount_groth):
    """Requester locks payment into vault_anon for a node operator."""
    args = (
        f"role=user,action=send_raw,"
        f"cid={VAULT_CID},"
        f"pkOwner={node_pkowner},"
        f"aid=0,amount={amount_groth}"
    )
    return call(REQUESTER_API, "invoke_contract", {
        "contract_file": VAULT_WASM,
        "args": args,
        "create_tx": True
    })

def receive_payment(amount_groth=0):
    """Node claims all available funds from vault_anon."""
    args = (
        f"role=user,action=receive_raw,"
        f"cid={VAULT_CID},"
        f"aid=0,amount={amount_groth}"
    )
    return call(NODE_API, "invoke_contract", {
        "contract_file": VAULT_WASM,
        "args": args,
        "create_tx": True
    })

if __name__ == "__main__":
    # Check both wallet balances
    a = wallet_status(REQUESTER_API)["result"]["available"]
    b = wallet_status(NODE_API)["result"]["available"]
    print(f"Wallet A (Requester): {a / 100000000:.8f} BEAM")
    print(f"Wallet B (Node):      {b / 100000000:.8f} BEAM")

    # Get node's pkOwner
    node_pk = get_my_key(NODE_API)
    print(f"Node pkOwner: {node_pk}")

    # Requester sends 0.1 BEAM to node escrow
    print("\nSending 0.1 BEAM to node escrow...")
    result = send_payment(node_pk, 10000000)
    txid = result["result"]["txid"]
    print(f"TX ID: {txid}")

    # Node claims funds
    import time
    print("\nWaiting for confirmation...")
    time.sleep(60)
    
    print("Node claiming funds...")
    claim = receive_payment(0)
    print(f"Claim result: {json.dumps(claim, indent=2)}")

    # Final balances
    a2 = wallet_status(REQUESTER_API)["result"]["available"]
    b2 = wallet_status(NODE_API)["result"]["available"]
    print(f"\nFinal Wallet A: {a2 / 100000000:.8f} BEAM")
    print(f"Final Wallet B: {b2 / 100000000:.8f} BEAM")
