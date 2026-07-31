# Verifying a worker bond, from nothing

A worker listing may carry a Bonded badge or claim a stake. Do not trust the badge. The bond lives on chain and you can check it yourself in one read only command, with no funds, no account, and no trust in the person who listed it. This walkthrough takes you from nothing to a verified answer.

## What you are verifying

An Idios worker can lock a standing, slashable BEAM bond against the public key they take jobs with. If they lose an arbitrated dispute, the whole bond is forfeited to the protocol treasury. So a live bond means the worker has real money that a ruling against them destroys. That is the entire claim: money at risk, provable on chain. What it is not is insurance, see the limits section at the end.

## What you need

- The Beam CLI wallet binary, from [beam.mw](https://beam.mw) or the [Beam releases page](https://github.com/BeamMW/beam/releases)
- `idios_app.wasm` from this repo
- The worker's pubkey, from their listing or given to you directly (64 to 66 hex chars)

You do not need any BEAM. Viewing is free and sends nothing. If you do not already have a wallet, run `./beam-wallet init`, set any password, and ignore the funding steps in the quickstarts; a throwaway wallet works because this command only reads chain state. Setup details are in the [README](../README.md#cli-usage).

## The command

Substitute the worker's pubkey into `worker_pk`:

```bash
./beam-wallet shader \
  --shader_app_file=idios_app.wasm \
  --shader_args="role=user,action=view_worker_bond,cid=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f,worker_pk=<WORKER_PUBKEY>" \
  --node_addr=eu-node01.mainnet.beam.mw:8100
```

The cid is the live Idios contract on Beam mainnet. The node is a public relay; your keys never leave your machine. The wallet may exit with a nonzero return code even on success, ignore that and read the output.

## Reading the answer

Real output from the production contract:

```
Shader output: "worker_bond": {"worker_pk": "96e7e79378041e154e320cd00a52b7c9fd139a628637a902c5967c6df7c6435500","stake": 1000000000,"bonded_at": 3959869,"dereg_block": 0,"encumbrances": 0,"state": 0}
```

Field by field:

- **stake**: the locked amount in groth. 1 BEAM = 100,000,000 groth, so 1000000000 is 10 BEAM. Convert it and then check what that is worth in your currency. A bond is a costly signal only if the cost is real; a dust bond advertises itself.
- **state**: the one field that can disqualify a worker outright.
  - `0` registered: the bond is live and at risk. This is the good answer.
  - `1` deregistering: the worker has started withdrawing. The bond is still slashable until reclaimed, but they are on their way out, and the exit takes a public cooldown of `arbitrator_timeout_blocks` (14 days on production). Treat as a caution flag for new long jobs.
  - `2` gone: no live bond. The badge, if any, is stale.
  - `3` slashed: the worker lost an arbitrated dispute and the bond is forfeited forever. Strongest possible negative signal.
- **bonded_at**: the block the bond was locked at. Beam produces roughly one block a minute, so current height minus bonded_at, divided by 1440, is the bond's age in days. An old bond has survived every job since.
- **dereg_block**: nonzero only when state is 1, the block deregistration started.
- **encumbrances**: the number of open disputes currently holding this bond. Nonzero means the worker is in at least one live dispute right now and cannot withdraw the bond until each one terminates. Not automatically damning (anyone can be disputed), but you are looking at an unresolved case.

If the output is empty or the wallet reports nothing found, no bond has ever been registered for that key on this contract.

## Cross checking on the explorer

The contract's full call history is public at the [explorer](https://explorer.0xmx.net/?network=mainnet&type=contract&id=41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f). One caveat: the explorer currently renders raw method numbers, not names. Use the [method map](./method_map.md) to read them; a bond registration is method 26, a slash sweep by the treasury is method 29. The explorer also shows the funds the contract holds, which should cover every live bond and every open job.

## Honest limits, read before relying on this

- The bond slashes only through an Idios dispute on an Idios job. If you deal with the worker outside an Idios contract and they cheat you, you cannot seize the bond. It is not an insurance pool and never pays wronged parties; a slashed bond goes to the protocol treasury.
- What the bond actually buys you is a costly signal plus a slow, visible exit: the worker cannot vanish with the bond in less than the cooldown, and the state flips to deregistering publicly the moment they try.
- A bond proves money at risk, not competence. It tells you a dispute loss costs them; it does not tell you the work will be good.
- The pubkey is contract specific. A bond on this contract says nothing about the same person's keys anywhere else, and verifying it only makes sense for the exact pubkey you will put in the contract as the worker.
