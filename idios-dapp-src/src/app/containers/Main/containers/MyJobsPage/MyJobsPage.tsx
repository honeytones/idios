import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';
import { getTrackedJobs, removeTrackedJob, addTrackedJob, TrackedJob } from '@app/core/jobs';
import { viewJob, refundJob, claimJob, approveJob, disputeJob, commitJob, submitDelivery, claimAfterTimeout, voidDispute, voidClaimRequester, voidClaimNode, mutualCancel } from '@app/core/api';

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

const IntroBlurb = styled.div`
  width: 100%;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid rgba(140, 180, 255, 0.2);
  background: rgba(140, 180, 255, 0.04);
  color: rgba(255,255,255,0.75);
  font-size: 12px;
  line-height: 1.5;
  margin-bottom: 16px;
  box-sizing: border-box;
  a {
    color: rgba(180, 210, 255, 0.95);
    text-decoration: none;
    &:hover { text-decoration: underline; }
  }
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

const TrackForm = styled.div`
  width: 100%;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.04);
  margin-bottom: 16px;
  box-sizing: border-box;
`;

const TrackFormTitle = styled.div`
  font-size: 13px;
  font-weight: 600;
  color: rgba(255,255,255,0.5);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 12px;
`;

const TrackFormRow = styled.div`
  display: flex;
  gap: 8px;
  align-items: center;
`;

const TrackInput = styled.input`
  flex: 1;
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 13px;
  outline: none;
  &:focus {
    border-color: #e8e8e8;
  }
  &::placeholder {
    color: rgba(255,255,255,0.3);
  }
`;

const TrackSelect = styled.select`
  option { background: #1a1a1a; color: #e8e8e8; }
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 13px;
  outline: none;
  cursor: pointer;
  &:focus {
    border-color: #e8e8e8;
  }
`;

const TrackButton = styled.button`
  padding: 10px 18px;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
  background: #e8e8e8;
  color: #042548;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
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

const DaemonConfigButton = styled.button`
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid rgba(140, 180, 255, 0.4);
  background: transparent;
  color: rgba(180, 210, 255, 0.95);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  &:hover {
    background: rgba(140, 180, 255, 0.1);
  }
`;

const ModalOverlay = styled.div`
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
`;

const ModalContent = styled.div`
  background: #111;
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 10px;
  padding: 24px;
  width: 90%;
  max-width: 640px;
  max-height: 85vh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
`;

const ModalScroll = styled.div`
  flex: 1 1 auto;
  overflow-y: auto;
  min-height: 0;
  padding-right: 4px;
`;

const ModalTitle = styled.h2`
  font-size: 18px;
  font-weight: 600;
  color: #e8e8e8;
  margin: 0 0 8px 0;
`;

const ModalSubtitle = styled.div`
  font-size: 13px;
  color: rgba(255,255,255,0.55);
  margin-bottom: 18px;
`;

const ModalLabel = styled.div`
  font-size: 12px;
  font-weight: 600;
  color: rgba(255,255,255,0.7);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
`;

const ModalInput = styled.input`
  width: 100%;
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 13px;
  font-family: monospace;
  outline: none;
  box-sizing: border-box;
  margin-bottom: 16px;
  &:focus {
    border-color: #e8e8e8;
  }
`;

const ModalSelect = styled.select`
  option { background: #1a1a1a; color: #e8e8e8; }
  width: 100%;
  padding: 10px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 13px;
  outline: none;
  cursor: pointer;
  box-sizing: border-box;
  margin-bottom: 16px;
  &:focus {
    border-color: #e8e8e8;
  }
`;

const ConfigTextarea = styled.textarea`
  width: 100%;
  height: 110px;
  padding: 12px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(0,0,0,0.3);
  color: rgba(255,255,255,0.9);
  font-size: 12px;
  font-family: monospace;
  outline: none;
  box-sizing: border-box;
  resize: vertical;
  margin-bottom: 12px;
`;

const ModalActions = styled.div`
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  align-items: center;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid rgba(255,255,255,0.08);
  flex: 0 0 auto;
`;

const ModalNote = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.6);
  margin-bottom: 12px;
  line-height: 1.4;
  a {
    color: rgba(180, 210, 255, 0.95);
    text-decoration: none;
    &:hover { text-decoration: underline; }
  }
`;

const CopyButton = styled.button`
  padding: 8px 18px;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
  background: #e8e8e8;
  color: #042548;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
`;

const CloseButton = styled.button`
  padding: 8px 18px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.3);
  background: transparent;
  color: rgba(255,255,255,0.8);
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
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
    case 9: return 'Voided';
    case 10: return 'Cancelled';
    default: return 'Unknown';
  }
};

const grothToBeam = (groth: number): string => {
  if (!groth || isNaN(groth)) return '0';
  return (groth / 1e8).toFixed(8).replace(/\.?0+$/, '');
};

const assetLabel = (assetId: number | undefined): string => {
  return assetId === 47 ? 'NPH' : 'BEAM';
};

const MyJobsPage: React.FC = () => {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobWithState[]>([]);
  const [loading, setLoading] = useState(true);
  const [refundingId, setRefundingId] = useState<number | null>(null);
  const [trackJobId, setTrackJobId] = useState('');
  const [trackRole, setTrackRole] = useState<'requester' | 'worker'>('worker');
  const [claimingId, setClaimingId] = useState<number | null>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [disputingId, setDisputingId] = useState<number | null>(null);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [committingId, setCommittingId] = useState<number | null>(null);
  const [submittingId, setSubmittingId] = useState<number | null>(null);
  const [claimingTimeoutId, setClaimingTimeoutId] = useState<number | null>(null);
  const [voidingId, setVoidingId] = useState<number | null>(null);
  const [voidClaimingId, setVoidClaimingId] = useState<number | null>(null);
  const [exportJob, setExportJob] = useState<JobWithState | null>(null);
  const [exportRole, setExportRole] = useState<'worker' | 'requester'>('worker');
  const [exportHash, setExportHash] = useState('');
  const [copyStatus, setCopyStatus] = useState<string>('');

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
      await refundJob(job.jobId);
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
      await claimJob(job.jobId);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Claim failed:', err);
      alert('Claim failed. See console for details.');
    } finally {
      setClaimingId(null);
    }
  };

  const handleAddTrack = () => {
    const id = parseInt(trackJobId);
    if (!id || isNaN(id)) return;
    addTrackedJob({
      jobId: id,
      role: trackRole,
      addedAt: Date.now(),
    });
    setTrackJobId('');
    loadJobs();
  };

  const handleApprove = async (job: JobWithState) => {
    if (!job.state) return;
    setApprovingId(job.jobId);
    try {
      await approveJob(job.jobId);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Approve failed:', err);
      alert('Approve failed. See console for details.');
    } finally {
      setApprovingId(null);
    }
  };
  const handleMutualCancel = async (job: JobWithState) => {
    if (!job.state) return;
    setCancellingId(job.jobId);
    try {
      await mutualCancel(job.jobId);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Mutual cancel failed:', err);
      alert('Mutual cancel failed. See console for details.');
    } finally {
      setCancellingId(null);
    }
  };

  const handleDispute = async (job: JobWithState) => {
    if (!job.state) return;
    setDisputingId(job.jobId);
    try {
      const dispute_fee = job.state.dispute_fee || 0;
      await disputeJob(job.jobId, dispute_fee, job.state.asset_id || 0);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Dispute failed:', err);
      alert('Dispute failed. See console for details.');
    } finally {
      setDisputingId(null);
    }
  };

  const handleCommit = async (job: JobWithState) => {
    if (!job.state) return;
    const input = prompt('Collateral to lock (in ' + assetLabel(job.state.asset_id) + '):', '');
    if (input === null) return;
    const collateral = Math.round(parseFloat(input) * 100000000);
    if (!collateral || isNaN(collateral) || collateral <= 0) {
      alert('Enter a valid collateral amount.');
      return;
    }
    setCommittingId(job.jobId);
    try {
      await commitJob(job.jobId, collateral, job.state.asset_id || 0);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Commit failed:', err);
      alert('Commit failed. See console for details.');
    } finally {
      setCommittingId(null);
    }
  };

  const handleSubmitDelivery = async (job: JobWithState) => {
    if (!job.state) return;
    const delivery_hash = prompt('Delivery hash (64 char hex):', '');
    if (delivery_hash === null) return;
    if (!/^[0-9a-fA-F]{64}$/.test(delivery_hash.trim())) {
      alert('Delivery hash must be 64 hex characters.');
      return;
    }
    setSubmittingId(job.jobId);
    try {
      await submitDelivery(job.jobId, delivery_hash.trim());
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Submit delivery failed:', err);
      alert('Submit delivery failed. See console for details.');
    } finally {
      setSubmittingId(null);
    }
  };

  const handleClaimAfterTimeout = async (job: JobWithState) => {
    if (!job.state) return;
    setClaimingTimeoutId(job.jobId);
    try {
      await claimAfterTimeout(job.jobId);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Claim after timeout failed:', err);
      alert('Claim after timeout failed. The review window may not have passed yet. See console for details.');
    } finally {
      setClaimingTimeoutId(null);
    }
  };

  const handleVoidDispute = async (job: JobWithState) => {
    if (!job.state) return;
    setVoidingId(job.jobId);
    try {
      await voidDispute(job.jobId);
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Void dispute failed:', err);
      alert('Void failed. A dispute can only be voided once the arbitrator timeout has passed (about 14 days after the dispute was filed on the production contract). Try again later. See console for details.');
    } finally {
      setVoidingId(null);
    }
  };

  const handleVoidClaim = async (job: JobWithState) => {
    if (!job.state) return;
    setVoidClaimingId(job.jobId);
    try {
      if (job.role === 'requester') {
        await voidClaimRequester(job.jobId);
      } else {
        await voidClaimNode(job.jobId);
      }
      setTimeout(() => loadJobs(), 3000);
    } catch (err) {
      console.error('Void claim failed:', err);
      alert('Reclaim failed. See console for details.');
    } finally {
      setVoidClaimingId(null);
    }
  };

  const handleRemove = (jobId: number) => {
    if (!confirm('Stop tracking this contract? Funds on chain are unaffected.')) return;
    removeTrackedJob(jobId);
    setJobs(prev => prev.filter(j => j.jobId !== jobId));
  };

  const openExportModal = (job: JobWithState) => {
    // Pre-fill the hash field from chain state when sensible.
    // For requester (client daemon): pre-fill with chain's delivery_hash if worker has submitted.
    // For worker daemon: leave empty, user types the hash they will submit.
    const initialRole: 'worker' | 'requester' = (job.role === 'requester' ? 'requester' : 'worker');
    let prefilled = '';
    if (initialRole === 'requester' && job.state && job.state.delivery_hash &&
        job.state.delivery_hash !== '0000000000000000000000000000000000000000000000000000000000000000') {
      prefilled = job.state.delivery_hash;
    }
    setExportJob(job);
    setExportRole(initialRole);
    setExportHash(prefilled);
    setCopyStatus('');
  };

  const closeExportModal = () => {
    setExportJob(null);
    setExportRole('worker');
    setExportHash('');
    setCopyStatus('');
  };

  const buildDaemonConfig = (job: JobWithState, role: 'worker' | 'requester', hash: string): string => {
    const daemonRole = role === 'requester' ? 'client' : 'worker';
    const entry: any = {
      job_id: job.jobId,
      role: daemonRole,
    };
    if (daemonRole === 'worker') {
      entry.expected_collateral = job.state && job.state.collateral ? job.state.collateral : 0;
      entry.delivery_hash = hash || '<paste the 32-byte hex hash you will submit>';
    } else {
      entry.auto_approve_on_hash_match = true;
      entry.expected_delivery_hash = hash || '<paste the 32-byte hex hash you expect>';
    }
    return JSON.stringify(entry, null, 2);
  };

  const handleCopy = async () => {
    if (!exportJob) return;
    const text = buildDaemonConfig(exportJob, exportRole, exportHash);
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus('Copied to clipboard');
      setTimeout(() => setCopyStatus(''), 2000);
    } catch (err) {
      setCopyStatus('Copy failed, select text manually');
    }
  };

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>← Back</BackLink>

      <RefreshButton onClick={loadJobs} disabled={loading}>
        {loading ? 'Refreshing...' : 'Refresh'}
      </RefreshButton>

      <IntroBlurb>
        Track contracts to see their on-chain state and take actions (commit, submit, approve, dispute, claim, refund).
        Each tracked contract also has an <strong>Automate this contract</strong> button, which generates a config snippet
        for the <em>Idios agent daemon</em>, a small program you can run on your computer to fire those actions
        automatically as the contract moves through its state machine. Useful for autonomous agents, marketplaces, or
        anyone who does not want to keep clicking buttons.
        {' '}
        <a href="https://github.com/honeytones/idios/blob/main/idios-agent-daemon/README.md" target="_blank" rel="noreferrer">
          Read more about the daemon
        </a>.
      </IntroBlurb>

      <TrackForm>
        <TrackFormTitle>Track a Contract</TrackFormTitle>
        <TrackFormRow>
          <TrackInput
            placeholder="Contract ID, e.g. 33335"
            value={trackJobId}
            onChange={e => setTrackJobId(e.target.value)}
          />
          <TrackSelect value={trackRole} onChange={e => setTrackRole(e.target.value as 'requester' | 'worker')}>
            <option value="worker">As Worker</option>
            <option value="requester">As Requester</option>
          </TrackSelect>
          <TrackButton onClick={handleAddTrack} disabled={!trackJobId}>
            Add
          </TrackButton>
        </TrackFormRow>
      </TrackForm>

      {loading && jobs.length === 0 && <LoadingMsg>Loading contracts...</LoadingMsg>}

      {!loading && jobs.length === 0 && (
        <EmptyState>
          No tracked contracts yet. Contracts you create through "Start a contract" will appear here.
        </EmptyState>
      )}

      {jobs.map(job => (
        <JobCard key={job.jobId}>
          <JobHeader>
            <JobIdLabel>Contract #{job.jobId}</JobIdLabel>
            <StatusBadge kind={job.state ? statusToText(job.state.status) : 'unknown'}>
              {job.loading ? 'Loading...' : job.error ? 'Error' : statusToText(job.state?.status)}
            </StatusBadge>
          </JobHeader>
          <JobDetail>Role: {job.role}</JobDetail>
          {job.payment && <JobDetail>Payment: {job.payment} {job.state ? assetLabel(job.state.asset_id) : 'BEAM'}</JobDetail>}
          {job.state && (
            <>
              {job.state.collateral !== undefined && (
                <JobDetail>Collateral on chain: {grothToBeam(job.state.collateral)} {assetLabel(job.state.asset_id)}</JobDetail>
              )}
              {job.state.expiry_block !== undefined && (
                <JobDetail>Expiry block: {job.state.expiry_block}</JobDetail>
              )}
            </>
          )}
          {job.error && <JobDetail style={{ color: '#ff6b6b' }}>Error: {job.error}</JobDetail>}
          <ActionRow>
            {job.state && job.state.status === 0 && job.role === 'requester' && (
              <ActionButton
                onClick={() => handleRefund(job)}
                disabled={refundingId === job.jobId}
              >
                {refundingId === job.jobId ? 'Refunding...' : 'Trigger Refund'}
              </ActionButton>
            )}
            {job.state && job.state.status === 0 && job.role === 'worker' && (
              <ActionButton
                onClick={() => handleCommit(job)}
                disabled={committingId === job.jobId}
              >
                {committingId === job.jobId ? 'Committing...' : 'Commit Collateral'}
              </ActionButton>
            )}
            {job.state && job.state.status === 1 && job.role === 'worker' && (
              <ActionButton
                onClick={() => handleSubmitDelivery(job)}
                disabled={submittingId === job.jobId}
              >
                {submittingId === job.jobId ? 'Submitting...' : 'Submit Delivery'}
              </ActionButton>
            )}
            {job.state && (
              (job.state.status === 4 && job.role === 'worker' && job.state.mode !== 65) ||
              (job.state.status === 7 && job.role === 'worker') ||
              (job.state.status === 6 && job.role === 'requester')
            ) && (
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
            {job.state && job.state.status === 2 && job.role === 'worker' && (
              <ActionButton
                onClick={() => handleClaimAfterTimeout(job)}
                disabled={claimingTimeoutId === job.jobId}
              >
                {claimingTimeoutId === job.jobId ? 'Claiming...' : 'Claim After Timeout'}
              </ActionButton>
            )}
            {job.state && job.state.status === 3 && (job.role === 'worker' || job.role === 'requester') && (
              <ActionButton
                onClick={() => handleVoidDispute(job)}
                disabled={voidingId === job.jobId}
              >
                {voidingId === job.jobId ? 'Voiding...' : 'Void Stale Dispute'}
              </ActionButton>
            )}
            {job.state && job.state.status === 9 && (job.role === 'worker' || job.role === 'requester') && (
              <ActionButton
                onClick={() => handleVoidClaim(job)}
                disabled={voidClaimingId === job.jobId}
              >
                {voidClaimingId === job.jobId
                  ? 'Reclaiming...'
                  : job.role === 'requester' ? 'Reclaim Payment' : 'Reclaim Collateral'}
              </ActionButton>
            )}
            {job.state && (job.state.status === 1 || job.state.status === 2) && (job.role === 'worker' || job.role === 'requester') && (
              <ActionButton
                onClick={() => handleMutualCancel(job)}
                disabled={cancellingId === job.jobId}
              >
                {cancellingId === job.jobId ? 'Cancelling...' : 'Mutual Cancel'}
              </ActionButton>
            )}
            {job.state && (job.role === 'worker' || job.role === 'requester') && (
              <DaemonConfigButton onClick={() => openExportModal(job)}>
                Automate this contract
              </DaemonConfigButton>
            )}
            <RemoveButton onClick={() => handleRemove(job.jobId)}>
              Stop tracking
            </RemoveButton>
          </ActionRow>
        </JobCard>
      ))}

      {exportJob && (
        <ModalOverlay onClick={closeExportModal}>
          <ModalContent onClick={(e: any) => e.stopPropagation()}>
            <ModalTitle>Automate Contract #{exportJob.jobId}</ModalTitle>
            <ModalScroll>
            <ModalSubtitle>
              The Idios agent daemon is a small Python program you run on your own computer that watches this
              contract's status on chain and fires the right actions automatically (commit, submit_delivery,
              approve, claim, refund, resolve). One-time setup, then walk away.
            </ModalSubtitle>
            <ModalNote>
              You're tracked as <strong>{exportJob.role}</strong> on this contract. Pick the role you want the daemon
              to play below, fill in the relevant hash, and copy the generated snippet into the <code>jobs</code>
              array of your daemon's <code>config.json</code>. Then start (or restart) your daemon.
              {' '}
              <a href="https://github.com/honeytones/idios/blob/main/idios-agent-daemon/README.md" target="_blank" rel="noreferrer">
                Setup guide
              </a>.
            </ModalNote>
            <ModalLabel>Role for daemon</ModalLabel>
            <ModalSelect value={exportRole} onChange={e => setExportRole(e.target.value as 'worker' | 'requester')}>
              <option value="worker">Worker - commit, submit, claim</option>
              <option value="requester">Requester - approve, refund, claim</option>
            </ModalSelect>
            <ModalLabel>
              {exportRole === 'requester'
                ? 'Expected delivery hash (the hash you expect the worker to submit)'
                : 'Delivery hash (the hash you will submit)'}
            </ModalLabel>
            <ModalInput
              placeholder="32-byte hex, e.g. deadbeef..."
              value={exportHash}
              onChange={e => setExportHash(e.target.value.trim())}
            />
            <ModalLabel>Generated config (add to the jobs array)</ModalLabel>
            <ConfigTextarea readOnly value={buildDaemonConfig(exportJob, exportRole, exportHash)} />
            {copyStatus && <ModalNote style={{ color: 'rgba(140, 220, 160, 0.9)' }}>{copyStatus}</ModalNote>}
            </ModalScroll>
            <ModalActions>
              <CloseButton onClick={closeExportModal}>Close</CloseButton>
              <CopyButton onClick={handleCopy}>Copy snippet</CopyButton>
            </ModalActions>
          </ModalContent>
        </ModalOverlay>
      )}
    </Container>
  );
};

export default MyJobsPage;
