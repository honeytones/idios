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
import { viewJob, resolveToAlice, resolveToBob } from "@app/core/api";

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

const TrackForm = styled.div`
  width: 100%;
  max-width: 720px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 24px;
`;

const TrackFormTitle = styled.div`
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #e8e8e8;
`;

const TrackFormRow = styled.div`
  display: flex;
  gap: 8px;
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

const ResolveAliceButton = styled(ActionButton)`
  background: rgba(120, 80, 200, 0.15);
  border-color: rgba(120, 80, 200, 0.4);
`;

const ResolveBobButton = styled(ActionButton)`
  background: rgba(80, 160, 120, 0.15);
  border-color: rgba(80, 160, 120, 0.4);
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
    default: return "Unknown";
  }
};

const grothToBeam = (groth: number): string => {
  if (!groth || isNaN(groth)) return "0";
  return (groth / 1e8).toFixed(8).replace(/\.?0+$/, "");
};

const truncatePk = (pk: string | undefined): string => {
  if (!pk) return "";
  if (pk.length <= 16) return pk;
  return pk.slice(0, 8) + "..." + pk.slice(-6);
};

const ArbitratorPage: React.FC = () => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobWithState[]>([]);
  const [loading, setLoading] = useState(true);
  const [trackJobId, setTrackJobId] = useState("");
  const [resolvingAliceId, setResolvingAliceId] = useState<number | null>(null);
  const [resolvingBobId, setResolvingBobId] = useState<number | null>(null);

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
        return { ...t, state, loading: false } as JobWithState;
      } catch (err: any) {
        return { ...t, loading: false, error: String(err) } as JobWithState;
      }
    }));
    setJobs(results);
    setLoading(false);
  };

  useEffect(() => {
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

  const computeTotal = (state: any): number => {
    const payment = Number(state?.payment) || 0;
    const collateral = Number(state?.collateral) || 0;
    const disputeFee = Number(state?.dispute_fee) || 0;
    return payment + collateral + disputeFee;
  };

  const handleResolveAlice = async (job: JobWithState) => {
    if (!job.state) return;
    const total = computeTotal(job.state);
    if (!confirm("Resolve to Requester? This sends " + grothToBeam(total) + " BEAM (payment + collateral + dispute fee) to the requester. Worker forfeits their collateral.")) return;
    try {
      setResolvingAliceId(job.jobId);
      await resolveToAlice(job.jobId, total, job.state?.asset_id || 0);
      setTimeout(loadJobs, 3000);
    } catch (err: any) {
      alert("Resolve to Requester failed: " + String(err));
    } finally {
      setResolvingAliceId(null);
    }
  };

  const handleResolveBob = async (job: JobWithState) => {
    if (!job.state) return;
    const total = computeTotal(job.state);
    if (!confirm("Resolve to Worker? This sends " + grothToBeam(total) + " BEAM (payment + collateral + dispute fee) to the worker. Requester forfeits their dispute fee.")) return;
    try {
      setResolvingBobId(job.jobId);
      await resolveToBob(job.jobId, total, job.state?.asset_id || 0);
      setTimeout(loadJobs, 3000);
    } catch (err: any) {
      alert("Resolve to Worker failed: " + String(err));
    } finally {
      setResolvingBobId(null);
    }
  };

  const handleRemove = (jobId: number) => {
    if (!confirm("Stop tracking this job? Funds on chain are unaffected.")) return;
    removeTrackedArbitratorJob(jobId);
    setJobs(prev => prev.filter(j => j.jobId !== jobId));
  };

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>Back</BackLink>

      <Title>Arbitrator Console</Title>
      <Subtitle>
        Track disputed jobs you have been notified about and resolve them.
        Resolve actions only succeed if your wallet is configured as the contract arbitrator.
      </Subtitle>

      <RefreshButton onClick={loadJobs} disabled={loading}>
        {loading ? "Refreshing..." : "Refresh"}
      </RefreshButton>

      <TrackForm>
        <TrackFormTitle>Track a Job</TrackFormTitle>
        <TrackFormRow>
          <TrackInput
            placeholder="Job ID, e.g. 11113"
            value={trackJobId}
            onChange={e => setTrackJobId(e.target.value.trim())}
          />
          <TrackButton onClick={handleAddTrack} disabled={!trackJobId}>
            Add
          </TrackButton>
        </TrackFormRow>
      </TrackForm>

      {loading && jobs.length === 0 && <LoadingMsg>Loading jobs...</LoadingMsg>}

      {!loading && jobs.length === 0 && (
        <EmptyState>
          No tracked jobs yet. When a party files a dispute and notifies you off chain, paste the Job ID above to start tracking it.
        </EmptyState>
      )}

      {jobs.map(job => (
        <JobCard key={job.jobId}>
          <JobHeader>
            <JobIdLabel>Job #{job.jobId}</JobIdLabel>
            <StatusBadge>
              {job.loading ? "Loading..." : job.error ? "Error" : statusToText(job.state?.status)}
            </StatusBadge>
          </JobHeader>
          {job.state && (
            <>
              <JobDetail>Payment: {grothToBeam(job.state.payment)} BEAM</JobDetail>
              <JobDetail>Collateral: {grothToBeam(job.state.collateral)} BEAM</JobDetail>
              {Number(job.state.dispute_fee) > 0 && (
                <JobDetail>Dispute fee: {grothToBeam(job.state.dispute_fee)} BEAM</JobDetail>
              )}
              <JobDetail>Expiry block: {job.state.expiry_block}</JobDetail>
              <JobDetail>Requester: {truncatePk(job.state.requester_pk)}</JobDetail>
              <JobDetail>Worker: {truncatePk(job.state.node_pk)}</JobDetail>
            </>
          )}
          {job.error && <JobDetail style={{ color: "#ff6b6b" }}>Error: {job.error}</JobDetail>}
          <ActionRow>
            {job.state && job.state.status === 3 && (
              <>
                <ResolveAliceButton
                  onClick={() => handleResolveAlice(job)}
                  disabled={resolvingAliceId === job.jobId || resolvingBobId === job.jobId}
                >
                  {resolvingAliceId === job.jobId ? "Resolving..." : "Resolve to Requester"}
                </ResolveAliceButton>
                <ResolveBobButton
                  onClick={() => handleResolveBob(job)}
                  disabled={resolvingAliceId === job.jobId || resolvingBobId === job.jobId}
                >
                  {resolvingBobId === job.jobId ? "Resolving..." : "Resolve to Worker"}
                </ResolveBobButton>
              </>
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
