import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { styled } from "@linaria/react";
import { ROUTES_FULL } from "@app/shared/constants";
import {
  getTrackedArbitratorJobs,
  removeTrackedArbitratorJob,
  addTrackedArbitratorJob,
  TrackedArbitratorJob,
} from "@app/core/jobs";
import {
  viewJob,
  viewDispute,
  viewArb,
  getMofnKey,
  viewRegCount,
  voteDispute,
  claimArbReward,
} from "@app/core/api";

const Container = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 24px;
  color: white;
  max-width: 1100px;
  margin: 0 auto;
  min-height: 100vh;
  background: #0a0a0a;
  border-radius: 12px;
`;

const Title = styled.h1`
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 8px;
  color: #e8e8e8;
`;

const Subtitle = styled.p`
  font-size: 14px;
  color: rgba(255,255,255,0.6);
  margin-bottom: 24px;
  text-align: center;
  max-width: 720px;
`;

const BackLink = styled.button`
  background: none;
  border: 1px solid rgba(255,255,255,0.2);
  color: rgba(255,255,255,0.7);
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  margin-bottom: 20px;
  align-self: flex-start;
  &:hover {
    border-color: #e8e8e8;
    color: #e8e8e8;
  }
`;

const RefreshButton = styled.button`
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.2);
  color: #e8e8e8;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  margin-bottom: 16px;
  &:hover { border-color: #e8e8e8; }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
`;

const Panel = styled.div`
  width: 100%;
  max-width: 720px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 24px;
`;

const PanelTitle = styled.div`
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #e8e8e8;
`;

const PanelRow = styled.div`
  display: flex;
  gap: 8px;
  align-items: center;
`;

const PanelDetail = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.7);
  margin: 3px 0;
  font-family: monospace;
  word-break: break-all;
`;

const PanelNote = styled.div`
  font-size: 11px;
  color: rgba(255,255,255,0.45);
  margin-top: 8px;
  line-height: 1.4;
`;

const SmallInput = styled.input`
  width: 90px;
  padding: 8px 12px;
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 6px;
  color: white;
  font-family: inherit;
  font-size: 13px;
  &:focus { outline: none; border-color: #e8e8e8; }
`;

const TrackInput = styled.input`
  flex: 1;
  padding: 8px 12px;
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.2);
  border-radius: 6px;
  color: white;
  font-family: inherit;
  font-size: 13px;
  &:focus { outline: none; border-color: #e8e8e8; }
`;

const TrackButton = styled.button`
  padding: 8px 16px;
  background: #e8e8e8;
  border: none;
  border-radius: 6px;
  color: #0a0a0a;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  &:disabled { opacity: 0.4; cursor: not-allowed; }
`;

const JobCard = styled.div`
  width: 100%;
  max-width: 720px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
`;

const JobHeader = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
`;

const JobIdLabel = styled.div`
  font-size: 16px;
  font-weight: 600;
  color: #e8e8e8;
`;

const StatusBadge = styled.span`
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  background: rgba(255,255,255,0.08);
  color: #e8e8e8;
`;

const JobDetail = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.7);
  margin: 3px 0;
  font-family: monospace;
  word-break: break-all;
`;

const DisputeBox = styled.div`
  margin-top: 10px;
  padding: 10px 12px;
  background: rgba(120, 80, 200, 0.06);
  border: 1px solid rgba(120, 80, 200, 0.25);
  border-radius: 6px;
`;

const DisputeTitle = styled.div`
  font-size: 12px;
  font-weight: 600;
  color: #e8e8e8;
  margin-bottom: 6px;
`;

const ActionRow = styled.div`
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
`;

const ActionButton = styled.button`
  padding: 8px 16px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.2);
  color: #e8e8e8;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  &:hover { border-color: #e8e8e8; }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
`;

const VoteAliceButton = styled(ActionButton)`
  background: rgba(120, 80, 200, 0.15);
  border-color: rgba(120, 80, 200, 0.4);
`;

const VoteBobButton = styled(ActionButton)`
  background: rgba(80, 160, 120, 0.15);
  border-color: rgba(80, 160, 120, 0.4);
`;

const ClaimRewardButton = styled(ActionButton)`
  background: rgba(200, 170, 80, 0.12);
  border-color: rgba(200, 170, 80, 0.4);
`;

const RemoveButton = styled(ActionButton)`
  margin-left: auto;
  border-color: rgba(255,255,255,0.1);
  color: rgba(255,255,255,0.5);
`;

const EmptyState = styled.div`
  color: rgba(255,255,255,0.5);
  font-size: 14px;
  margin: 40px 0;
  text-align: center;
  max-width: 600px;
`;

const LoadingMsg = styled.div`
  color: rgba(255,255,255,0.6);
  font-size: 13px;
  margin: 20px 0;
`;

interface JobWithState extends TrackedArbitratorJob {
  state?: any;
  dispute?: any;
  loading: boolean;
  error?: string;
}

const statusToText = (status: number | undefined): string => {
  switch (status) {
    case 0: return "Open";
    case 1: return "Active";
    case 2: return "Awaiting Approval";
    case 3: return "Disputed";
    case 4: return "Settled";
    case 5: return "Refunded";
    case 6: return "Resolved to Requester";
    case 7: return "Resolved to Worker";
    case 8: return "Closed";
    case 9: return "Voided";
    case 10: return "Cancelled";
    default: return "Unknown";
  }
};

const arbStateToText = (state: number | undefined): string => {
  switch (state) {
    case 0: return "registered";
    case 1: return "deregistering";
    case 2: return "gone";
    default: return "unknown";
  }
};

const grothToBeam = (groth: number): string => {
  if (!groth || isNaN(groth)) return "0";
  return (groth / 1e8).toFixed(8).replace(/\.?0+$/, "");
};

const assetLabel = (assetId: number | undefined): string => {
  return assetId === 47 ? "NPH" : "BEAM";
};

const truncatePk = (pk: string | undefined): string => {
  if (!pk) return "";
  if (pk.length <= 16) return pk;
  return pk.slice(0, 8) + "..." + pk.slice(-6);
};

// The contract freezes N and sets M = N/2 + 1 (1 if N is 0).
const majorityOf = (n: number): number => {
  if (!n || n <= 0) return 1;
  return Math.floor(n / 2) + 1;
};

const ArbitratorPage: React.FC = () => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobWithState[]>([]);
  const [loading, setLoading] = useState(true);
  const [trackJobId, setTrackJobId] = useState("");
  const [arbIndexInput, setArbIndexInput] = useState("0");
  const [identity, setIdentity] = useState<{ pk?: string; arb?: any; regN?: number } | null>(null);
  const [identityLoading, setIdentityLoading] = useState(false);
  const [votingId, setVotingId] = useState<number | null>(null);
  const [claimingId, setClaimingId] = useState<number | null>(null);

  const arbIndex = (): number => {
    const idx = parseInt(arbIndexInput, 10);
    return isNaN(idx) || idx < 0 ? 0 : idx;
  };

  const loadIdentity = async () => {
    setIdentityLoading(true);
    try {
      const idx = arbIndex();
      const [pk, arb, regN] = await Promise.all([
        getMofnKey(idx).catch(() => undefined),
        viewArb(idx),
        viewRegCount(),
      ]);
      setIdentity({ pk: pk as string | undefined, arb, regN: regN as number });
    } catch (err) {
      setIdentity(null);
    } finally {
      setIdentityLoading(false);
    }
  };

  const loadJobs = async () => {
    setLoading(true);
    const tracked = getTrackedArbitratorJobs();
    if (tracked.length === 0) {
      setJobs([]);
      setLoading(false);
      return;
    }
    setJobs(tracked.map(t => ({ ...t, loading: true })));
    const results = await Promise.all(tracked.map(async (t) => {
      try {
        const state = await viewJob(t.jobId);
        let dispute = undefined;
        const st = Number(state && state.status);
        if (st === 3 || st === 6 || st === 7) {
          dispute = await viewDispute(t.jobId);
        }
        return { ...t, state, dispute, loading: false } as JobWithState;
      } catch (err: any) {
        return { ...t, loading: false, error: String(err) } as JobWithState;
      }
    }));
    setJobs(results);
    setLoading(false);
  };

  useEffect(() => {
    loadIdentity();
    loadJobs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAddTrack = () => {
    const id = parseInt(trackJobId, 10);
    if (!id || isNaN(id)) return;
    addTrackedArbitratorJob({ jobId: id, addedAt: Date.now() });
    setTrackJobId("");
    loadJobs();
  };

  const winnerAmount = (state: any): number => {
    const payment = Number(state?.payment) || 0;
    const collateral = Number(state?.collateral) || 0;
    return payment + collateral;
  };

  const handleVote = async (job: JobWithState, side: number) => {
    if (!job.state) return;
    const sideName = side === 0 ? "requester" : "worker";
    const d = job.dispute || {};
    const threshold = Number(d.threshold) || majorityOf(Number(d.frozen_n) || 0);
    const frozenN = Number(d.frozen_n) || 0;
    const amountStr = grothToBeam(winnerAmount(job.state)) + " " + assetLabel(job.state.asset_id);
    const msg =
      "Vote to resolve contract #" + job.jobId + " to the " + sideName + "?\n\n" +
      "Your vote is permanent, one per arbitrator per dispute, and cannot be changed. " +
      "The dispute resolves when " + threshold + " of " + frozenN + " arbitrators vote the same side. " +
      "The winner then receives payment plus collateral (" + amountStr + "). " +
      "The dispute fee is split among the consensus voters.\n\n" +
      "The vote confirms on chain in a minute or two. Refresh afterwards to see the tally.";
    if (!confirm(msg)) return;
    try {
      setVotingId(job.jobId);
      await voteDispute(job.jobId, side, arbIndex());
      setTimeout(loadJobs, 3000);
    } catch (err: any) {
      alert("Vote failed: " + String(err));
    } finally {
      setVotingId(null);
    }
  };

  const handleClaimReward = async (job: JobWithState) => {
    const d = job.dispute || {};
    const shareStr = grothToBeam(Number(d.fee_share) || 0) + " " + assetLabel(job.state?.asset_id);
    const msg =
      "Claim your dispute fee share for contract #" + job.jobId + " (" + shareStr + ")?\n\n" +
      "This only succeeds if the arbitrator index above voted with the winning side and has not claimed yet.";
    if (!confirm(msg)) return;
    try {
      setClaimingId(job.jobId);
      await claimArbReward(job.jobId, arbIndex());
      setTimeout(loadJobs, 3000);
    } catch (err: any) {
      alert("Claim failed: " + String(err));
    } finally {
      setClaimingId(null);
    }
  };

  const handleRemove = (jobId: number) => {
    if (!confirm("Stop tracking this contract? Funds on chain are unaffected.")) return;
    removeTrackedArbitratorJob(jobId);
    setJobs(prev => prev.filter(j => j.jobId !== jobId));
  };

  const regN = identity && typeof identity.regN === "number" ? identity.regN : 0;

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>Back</BackLink>

      <Title>Arbitrator Console</Title>
      <Subtitle>
        Track disputed contracts you have been notified about and vote to resolve them.
        Disputes resolve by M of N vote: when a majority of the arbitrator registry
        votes the same side, the contract settles to that side. Votes only succeed for
        an arbitrator key registered before the dispute was filed.
      </Subtitle>

      <Panel>
        <PanelTitle>Your Arbitrator Identity</PanelTitle>
        <PanelRow>
          <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.6)" }}>Arbitrator index</span>
          <SmallInput
            value={arbIndexInput}
            onChange={e => setArbIndexInput(e.target.value.trim())}
          />
          <ActionButton onClick={loadIdentity} disabled={identityLoading}>
            {identityLoading ? "Loading..." : "Load"}
          </ActionButton>
        </PanelRow>
        {identity && identity.pk && (
          <PanelDetail title={identity.pk}>Key: {truncatePk(identity.pk)}</PanelDetail>
        )}
        {identity && identity.arb && (
          <>
            <PanelDetail>Registration: {arbStateToText(Number(identity.arb.state))}</PanelDetail>
            <PanelDetail>Bond: {grothToBeam(Number(identity.arb.stake))} BEAM</PanelDetail>
            <PanelDetail>Registered at block: {identity.arb.registered_at}</PanelDetail>
          </>
        )}
        {identity && !identity.arb && !identityLoading && (
          <PanelDetail>No arbitrator registered for this wallet at this index.</PanelDetail>
        )}
        <PanelDetail>Registry: {regN} arbitrator{regN === 1 ? "" : "s"} live, a new dispute needs {majorityOf(regN)} matching vote{majorityOf(regN) === 1 ? "" : "s"}.</PanelDetail>
        <PanelNote>
          Most wallets use index 0. Higher indexes exist so one wallet can hold several
          distinct arbitrator identities. Registration happens via the CLI, it needs an
          admin co signature.
        </PanelNote>
      </Panel>

      <RefreshButton onClick={loadJobs} disabled={loading}>
        {loading ? "Refreshing..." : "Refresh"}
      </RefreshButton>

      <Panel>
        <PanelTitle>Track a Contract</PanelTitle>
        <PanelRow>
          <TrackInput
            placeholder="Contract ID, e.g. 11113"
            value={trackJobId}
            onChange={e => setTrackJobId(e.target.value.trim())}
          />
          <TrackButton onClick={handleAddTrack} disabled={!trackJobId}>
            Add
          </TrackButton>
        </PanelRow>
      </Panel>

      {loading && jobs.length === 0 && <LoadingMsg>Loading contracts...</LoadingMsg>}

      {!loading && jobs.length === 0 && (
        <EmptyState>
          No tracked contracts yet. When a party files a dispute and notifies you off chain, paste the Contract ID above to start tracking it.
        </EmptyState>
      )}

      {jobs.map(job => (
        <JobCard key={job.jobId}>
          <JobHeader>
            <JobIdLabel>Contract #{job.jobId}</JobIdLabel>
            <StatusBadge>
              {job.loading ? "Loading..." : job.error ? "Error" : statusToText(job.state?.status)}
            </StatusBadge>
          </JobHeader>
          {job.state && (
            <>
              <JobDetail>Payment: {grothToBeam(job.state.payment)} {assetLabel(job.state.asset_id)}</JobDetail>
              <JobDetail>Collateral: {grothToBeam(job.state.collateral)} {assetLabel(job.state.asset_id)}</JobDetail>
              {Number(job.state.dispute_fee) > 0 && (
                <JobDetail>Dispute fee: {grothToBeam(job.state.dispute_fee)} {assetLabel(job.state.asset_id)} (split among consensus voters at resolution)</JobDetail>
              )}
              <JobDetail>Expiry block: {job.state.expiry_block}</JobDetail>
              <JobDetail>Requester: {truncatePk(job.state.requester_pk)}</JobDetail>
              <JobDetail>Worker: {truncatePk(job.state.node_pk)}</JobDetail>
            </>
          )}
          {job.dispute && (
            <DisputeBox>
              <DisputeTitle>Dispute</DisputeTitle>
              <JobDetail>
                Votes: requester {Number(job.dispute.vc_alice) || 0}, worker {Number(job.dispute.vc_bob) || 0} (needs {Number(job.dispute.threshold) || 1} of {Number(job.dispute.frozen_n) || 0})
              </JobDetail>
              {Number(job.dispute.resolution) === 0 && (
                <JobDetail>Resolution: pending votes</JobDetail>
              )}
              {Number(job.dispute.resolution) === 1 && (
                <JobDetail>Resolution: requester won. Winner paid: {Number(job.dispute.winner_paid) === 1 ? "yes" : "not yet"}</JobDetail>
              )}
              {Number(job.dispute.resolution) === 2 && (
                <JobDetail>Resolution: worker won. Winner paid: {Number(job.dispute.winner_paid) === 1 ? "yes" : "not yet"}</JobDetail>
              )}
              {Number(job.dispute.fee_share) > 0 && (
                <JobDetail>Fee share per consensus voter: {grothToBeam(Number(job.dispute.fee_share))} {assetLabel(job.state?.asset_id)}</JobDetail>
              )}
              {Number(job.dispute.bond_encumbered) === 1 && Number(job.state?.status) === 3 && (
                <JobDetail>Worker bond: encumbered by this dispute</JobDetail>
              )}
            </DisputeBox>
          )}
          {job.error && <JobDetail style={{ color: "#ff6b6b" }}>Error: {job.error}</JobDetail>}
          <ActionRow>
            {job.state && job.state.status === 3 && (
              <>
                <VoteAliceButton
                  onClick={() => handleVote(job, 0)}
                  disabled={votingId === job.jobId}
                >
                  {votingId === job.jobId ? "Voting..." : "Vote for Requester"}
                </VoteAliceButton>
                <VoteBobButton
                  onClick={() => handleVote(job, 1)}
                  disabled={votingId === job.jobId}
                >
                  {votingId === job.jobId ? "Voting..." : "Vote for Worker"}
                </VoteBobButton>
              </>
            )}
            {job.dispute && Number(job.dispute.resolution) > 0 && (
              <ClaimRewardButton
                onClick={() => handleClaimReward(job)}
                disabled={claimingId === job.jobId}
              >
                {claimingId === job.jobId ? "Claiming..." : "Claim Fee Share"}
              </ClaimRewardButton>
            )}
            <RemoveButton onClick={() => handleRemove(job.jobId)}>
              Stop tracking
            </RemoveButton>
          </ActionRow>
        </JobCard>
      ))}
    </Container>
  );
};

export default ArbitratorPage;
