import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';
import { getTrackedJobs, removeTrackedJob, TrackedJob } from '@app/core/jobs';
import { viewJob, refundJob, claimJob, approveJob, disputeJob } from '@app/core/api';

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
  background: none;
  border: 1px solid rgba(255,255,255,0.2);
  color: rgba(255,255,255,0.7);
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  margin-bottom: 20px;
  &:hover {
    border-color: #e8e8e8;
    color: #e8e8e8;
  }
`;

const JobCard = styled.div`
  width: 100%;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.04);
  margin-bottom: 12px;
  box-sizing: border-box;
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

const StatusBadge = styled.span<{ kind: string }>`
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: rgba(255,255,255,0.1);
  color: rgba(255,255,255,0.8);
`;

const JobDetail = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.6);
  margin-bottom: 4px;
  font-family: monospace;
`;

const ActionRow = styled.div`
  display: flex;
  gap: 8px;
  margin-top: 12px;
`;

const ActionButton = styled.button`
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.4);
  background: transparent;
  color: #e8e8e8;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  &:hover:not(:disabled) {
    background: rgba(255,255,255,0.1);
  }
  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
`;

const RemoveButton = styled.button`
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,107,107,0.4);
  background: transparent;
  color: #ff6b6b;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  &:hover {
    background: rgba(255,107,107,0.1);
  }
`;

const EmptyState = styled.div`
  width: 100%;
  padding: 40px 20px;
  text-align: center;
  color: rgba(255,255,255,0.5);
  font-size: 13px;
  border: 1px dashed rgba(255,255,255,0.15);
  border-radius: 8px;
`;

const LoadingMsg = styled.div`
  color: rgba(255,255,255,0.6);
  font-size: 13px;
  margin: 20px 0;
`;

interface JobWithState extends TrackedJob {
  state?: any;
  loading: boolean;
  error?: string;
}

const statusToText = (status: number | undefined): string => {
  switch (status) {
    case 0: return 'Open';
    case 1: return 'Active';
    case 2: return 'Awaiting Approval';
    case 3: return 'Disputed';
    case 4: return 'Settled';
    case 5: return 'Refunded';
    case 6: return 'Resolved to Requester';
    case 7: return 'Resolved to Worker';
    case 8: return 'Closed';
    default: return 'Unknown';
  }
};

const grothToBeam = (groth: number): string => {
  if (!groth || isNaN(groth)) return '0';
  return (groth / 1e8).toFixed(8).replace(/\.?0+$/, '');
};

const MyJobsPage: React.FC = () => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobWithState[]>([]);
  const [loading, setLoading] = useState(true);
  const [refundingId, setRefundingId] = useState<number | null>(null);
  const [claimingId, setClaimingId] = useState<number | null>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [disputingId, setDisputingId] = useState<number | null>(null);

  const loadJobs = async () => {
    setLoading(true);
    const tracked = getTrackedJobs();
    if (tracked.length === 0) {
      setJobs([]);
      setLoading(false);
      return;
    }
    // Initialize entries as loading
    setJobs(tracked.map(t => ({ ...t, loading: true })));
    // Fetch each job state in parallel
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

  const handleRefund = async (job: JobWithState) => {
    if (!job.state) return;
    setRefundingId(job.jobId);
    try {
      const payment = job.state.payment || 0;
      const collateral = job.state.collateral || 0;
      const asset_id = job.state.asset_id || 0;
      await refundJob(job.jobId, payment, collateral, asset_id);
      // Refresh after a moment so the state has time to update on chain
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Refund failed:', err);
      alert('Refund failed. See console for details.');
    } finally {
      setRefundingId(null);
    }
  };

  const handleClaim = async (job: JobWithState) => {
    if (!job.state) return;
    setClaimingId(job.jobId);
    try {
      const payment = job.state.payment || 0;
      const collateral = job.state.collateral || 0;
      const dispute_fee = job.state.dispute_fee || 0;
      let total = payment + collateral;
      if (job.state.status === 6 || job.state.status === 7) {
        total += dispute_fee;
      }
      await claimJob(job.jobId, total);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Claim failed:', err);
      alert('Claim failed. See console for details.');
    } finally {
      setClaimingId(null);
    }
  };

  const handleApprove = async (job: JobWithState) => {
    if (!job.state) return;
    setApprovingId(job.jobId);
    try {
      const payment = job.state.payment || 0;
      const collateral = job.state.collateral || 0;
      await approveJob(job.jobId, payment, collateral);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Approve failed:', err);
      alert('Approve failed. See console for details.');
    } finally {
      setApprovingId(null);
    }
  };

  const handleDispute = async (job: JobWithState) => {
    if (!job.state) return;
    setDisputingId(job.jobId);
    try {
      const dispute_fee = job.state.dispute_fee || 0;
      await disputeJob(job.jobId, dispute_fee);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Dispute failed:', err);
      alert('Dispute failed. See console for details.');
    } finally {
      setDisputingId(null);
    }
  };

  const handleRemove = (jobId: number) => {
    if (!confirm('Stop tracking this job? Funds on chain are unaffected.')) return;
    removeTrackedJob(jobId);
    setJobs(prev => prev.filter(j => j.jobId !== jobId));
  };

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>← Back</BackLink>

      <RefreshButton onClick={loadJobs} disabled={loading}>
        {loading ? 'Refreshing...' : 'Refresh'}
      </RefreshButton>

      {loading && jobs.length === 0 && <LoadingMsg>Loading jobs...</LoadingMsg>}

      {!loading && jobs.length === 0 && (
        <EmptyState>
          No tracked jobs yet. Jobs you create through "Start a job" will appear here.
        </EmptyState>
      )}

      {jobs.map(job => (
        <JobCard key={job.jobId}>
          <JobHeader>
            <JobIdLabel>Job #{job.jobId}</JobIdLabel>
            <StatusBadge kind={job.state ? statusToText(job.state.status) : 'unknown'}>
              {job.loading ? 'Loading...' : job.error ? 'Error' : statusToText(job.state?.status)}
            </StatusBadge>
          </JobHeader>
          <JobDetail>Role: {job.role}</JobDetail>
          {job.payment && <JobDetail>Payment: {job.payment} BEAM</JobDetail>}
          {job.state && (
            <>
              {job.state.collateral !== undefined && (
                <JobDetail>Collateral on chain: {grothToBeam(job.state.collateral)} BEAM</JobDetail>
              )}
              {job.state.expiry_block !== undefined && (
                <JobDetail>Expiry block: {job.state.expiry_block}</JobDetail>
              )}
            </>
          )}
          {job.error && <JobDetail style={{ color: '#ff6b6b' }}>Error: {job.error}</JobDetail>}
          <ActionRow>
            {job.state && job.state.status === 0 && (
              <ActionButton
                onClick={() => handleRefund(job)}
                disabled={refundingId === job.jobId}
              >
                {refundingId === job.jobId ? 'Refunding...' : 'Trigger Refund'}
              </ActionButton>
            )}
            {job.state && (job.state.status === 4 || job.state.status === 6 || job.state.status === 7) && (
              <ActionButton
                onClick={() => handleClaim(job)}
                disabled={claimingId === job.jobId}
              >
                {claimingId === job.jobId ? 'Claiming...' : 'Claim Funds'}
              </ActionButton>
            )}
            {job.state && job.state.status === 2 && job.role === 'requester' && (
              <>
                <ActionButton
                  onClick={() => handleApprove(job)}
                  disabled={approvingId === job.jobId}
                >
                  {approvingId === job.jobId ? 'Approving...' : 'Approve Delivery'}
                </ActionButton>
                <ActionButton
                  onClick={() => handleDispute(job)}
                  disabled={disputingId === job.jobId}
                >
                  {disputingId === job.jobId ? 'Disputing...' : 'Dispute Delivery'}
                </ActionButton>
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

export default MyJobsPage;
