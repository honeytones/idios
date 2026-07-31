# Idios contract method map

Canonical mapping of on chain method numbers to contract actions for the Idios contract on Beam mainnet, cid `41ef8be50f0d727a919b5f5e64f7e66d5ec04442bb4f536f664e38b765e4921f`.

The numbering has never changed across the in place Upgradable3 upgrades, so this one mapping covers all three SIDs: `16dacce9...` (v6, deployed at block 3905992), `0b87c61b...` (M of N v1, block 3914637), and `a61f3a93...` (v2, current, block 3938963). Methods 21 to 25 exist since the M of N upgrade, 26 to 29 since v2.

Public explorers currently render these as raw numbers. Use this table to read a contract call history.

| Method | Action | Arguments |
|--------|--------|-----------|
| 2 | Upgradable3 control | upgrade governance (admin) |
| 3 | commit | job_id, collateral |
| 4 | create_a | job_id, subnet_id, epoch, expiry_block, node_pk, requester_pk, payment, required_collateral, result_hash, spec_hash |
| 5 | does not exist | |
| 6 | refund | job_id |
| 7 | view | job_id |
| 8 | create_b | as create_a minus result_hash, plus review_window_blocks, dispute_fee |
| 9 | submit_delivery | job_id, delivery_hash |
| 10 | approve | job_id |
| 11 | dispute | job_id |
| 12 | retired halt stub (was resolve_alice) | |
| 13 | retired halt stub (was resolve_bob) | |
| 14 | claim_after_timeout | job_id |
| 15 | claim | job_id |
| 16 | void_stale_dispute | job_id (permissionless) |
| 17 | void_claim_requester | job_id |
| 18 | void_claim_node | job_id |
| 19 | treasury_sweep | job_id |
| 20 | mutual_cancel | job_id |
| 21 | arb_register | arb_pk, stake, asset_id |
| 22 | arb_deregister | |
| 23 | arb_reclaim_stake | |
| 24 | arb_vote | job_id, arb_pk, side |
| 25 | arb_claim_reward | job_id |
| 26 | worker_register | worker_pk, stake, asset_id |
| 27 | worker_deregister | |
| 28 | worker_reclaim | |
| 29 | slash_sweep | worker_pk (treasury only) |

## Reading a typical job lifecycle in the explorer

A completed Mode B job with no dispute reads as: 8 (create_b), 3 (commit), 9 (submit_delivery), 10 (approve), 15 (claim). A Mode A job reads as 4 (create_a), 3 (commit), 9 (submit_delivery, which settles atomically on hash match).

Pairs of method 2 calls appear at each upgrade block (3914637 and 3938963). The single arbitrator registration on production is method 21 at block 3914648.

Statuses returned by view: 0 Open, 1 Active, 2 AwaitingApproval, 3 Disputed, 4 Settled, 5 Refunded, 6 ResolvedToAlice, 7 ResolvedToBob, 8 Closed, 9 Voided, 10 Cancelled. Modes: 65 is Mode A, 66 is Mode B.
