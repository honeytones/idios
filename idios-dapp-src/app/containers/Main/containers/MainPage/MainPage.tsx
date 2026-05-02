import React, { useState, useEffect } from 'react';
import { ROUTES_FULL } from '@app/shared/constants';
import { styled } from '@linaria/react';
import { useNavigate } from 'react-router-dom';
import { createJobModeA, createJobModeB, viewJob, refundJob } from '@app/core/api';
import { addTrackedJob } from '@app/core/jobs';
async function hashFileSHA256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await (crypto as any).subtle.digest('SHA-256', buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

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
  margin-bottom: 30px;
  text-align: center;
`;

const Section = styled.div`
  width: 100%;
  margin-bottom: 16px;
`;

const SectionTitle = styled.h3`
  font-size: 13px;
  font-weight: 600;
  color: rgba(255,255,255,0.5);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 12px;
`;

const SettlementOptions = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
`;

const SettlementCard = styled.div<{ selected: boolean }>`
  padding: 16px;
  border-radius: 10px;
  border: 2px solid ${({ selected }) => selected ? '#e8e8e8' : 'rgba(255,255,255,0.1)'};
  background: ${({ selected }) => selected ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)'};
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: rgba(255,255,255,0.5);
  }
`;

const CardTitle = styled.div`
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
  color: white;
`;

const CardDesc = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.5);
  line-height: 1.5;
`;

const Input = styled.input`
  width: 100%;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 14px;
  margin-bottom: 10px;
  box-sizing: border-box;
  outline: none;

  &:focus {
    border-color: #e8e8e8;
  }

  &::placeholder {
    color: rgba(255,255,255,0.3);
  }
`;

const FileInputWrapper = styled.label`
  display: block;
  width: 100%;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px dashed rgba(255,255,255,0.25);
  background: rgba(255,255,255,0.03);
  color: rgba(255,255,255,0.6);
  font-size: 13px;
  margin-bottom: 12px;
  box-sizing: border-box;
  cursor: pointer;
  text-align: center;
  transition: border-color 0.15s, background 0.15s;
  &:hover {
    border-color: #e8e8e8;
    background: rgba(255,255,255,0.05);
  }
  input {
    display: none;
  }
`;
const FileStatus = styled.div`
  font-size: 11px;
  color: #e8e8e8;
  margin-top: -8px;
  margin-bottom: 8px;
`;
const OfferBanner = styled.div`
  width: 100%;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.3);
  background: rgba(255,255,255,0.08);
  color: rgba(255,255,255,0.85);
  font-size: 13px;
  margin-bottom: 20px;
  box-sizing: border-box;
`;
const OfferBannerLabel = styled.div`
  font-size: 11px;
  color: #e8e8e8;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
`;
const Row = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
`;
const TwoColumn = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  width: 100%;
  @media (max-width: 700px) {
    grid-template-columns: 1fr;
    gap: 12px;
  }
`;

const Label = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.5);
  margin-bottom: 4px;
`;

const HintText = styled.div`
  font-size: 11px;
  color: rgba(255,255,255,0.35);
  margin-top: -8px;
  margin-bottom: 12px;
`;

const SubmitButton = styled.button`
  width: 100%;
  padding: 16px;
  border-radius: 50px;
  border: none;
  background: #e8e8e8;
  color: #042548;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 8px;

  &:hover {
    opacity: 0.9;
    box-shadow: 0 0 20px rgba(255,255,255,0.3);
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
`;

const SecondaryButton = styled.button`
  padding: 10px 20px;
  border-radius: 50px;
  border: 1px solid rgba(255,255,255,0.2);
  background: transparent;
  color: white;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: #e8e8e8;
    color: #e8e8e8;
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
`;

const DangerButton = styled.button`
  padding: 10px 20px;
  border-radius: 50px;
  border: 1px solid rgba(255,98,92,0.4);
  background: rgba(255,98,92,0.1);
  color: #ff625c;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  margin-left: 10px;

  &:hover {
    background: rgba(255,98,92,0.2);
    border-color: #ff625c;
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
`;

const StatusMsg = styled.div<{ error?: boolean }>`
  margin-top: 16px;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 13px;
  background: ${({ error }) => error ? 'rgba(255,98,92,0.15)' : 'rgba(255,255,255,0.1)'};
  color: ${({ error }) => error ? '#ff625c' : '#e8e8e8'};
  text-align: center;
  width: 100%;
`;

const JobCard = styled.div`
  width: 100%;
  padding: 16px;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.03);
  margin-top: 12px;
`;

const JobField = styled.div`
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 8px;
  color: rgba(255,255,255,0.7);

  span:last-child {
    color: white;
    font-weight: 500;
    text-align: right;
    max-width: 60%;
    word-break: break-all;
  }
`;

const ButtonRow = styled.div`
  display: flex;
  margin-top: 12px;
`;

const Divider = styled.div`
  width: 100%;
  height: 1px;
  background: rgba(255,255,255,0.08);
  margin: 8px 0 24px 0;
`;

const MainPage: React.FC = () => {
  const navigate = useNavigate();
  const [settlement, setSettlement] = useState<'fast' | 'review'>('fast');
  const [jobId, setJobId] = useState('');
  const [nodePk, setNodePk] = useState('');
  const [payment, setPayment] = useState('');
  const [collateral, setCollateral] = useState('');
  const [expiryBlock, setExpiryBlock] = useState('');
  const [resultHash, setResultHash] = useState('');
  const [reviewWindow, setReviewWindow] = useState('100');
  const [disputeFee, setDisputeFee] = useState('1');
  const [status, setStatus] = useState('');
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  // View/Refund state
  const [viewJobId, setViewJobId] = useState('');
  const [jobInfo, setJobInfo] = useState<any>(null);
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [isHashing, setIsHashing] = useState(false);
  const [offerFrom, setOfferFrom] = useState('');
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const p = params.get('payment');
    const h = params.get('hash');
    const w = params.get('worker');
    const ex = params.get('expiry');
    const sub = params.get('subnet');
    const fr = params.get('from');
    if (p) setPayment(p);
    if (h) setResultHash(h);
    if (w) setNodePk(w);
    if (ex) setExpiryBlock(ex);
    if (fr) setOfferFrom(fr);
    // subnet param reserved for future use
  }, []);

  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState('');
  const [refundLoading, setRefundLoading] = useState(false);
  const [refundStatus, setRefundStatus] = useState('');
  const [refundError, setRefundError] = useState(false);

  const beamToGroth = (beam: string) => Math.round(parseFloat(beam) * 1e8);
  const grothToBeam = (groth: number) => (groth / 1e8).toFixed(8).replace(/\.?0+$/, '');
  const usdEstimate = (beam: string) => beam ? `~$${(parseFloat(beam) * 0.02).toFixed(2)}` : '';

  const handlePaymentChange = (val: string) => {
    setPayment(val);
    if (val && !isNaN(parseFloat(val))) {
      setCollateral((parseFloat(val) * 0.5).toFixed(0));
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsHashing(true);
    setUploadedFileName(file.name);
    try {
      const hash = await hashFileSHA256(file);
      setResultHash(hash);
    } catch (err) {
      console.error('Hash failed:', err);
      setStatus('Failed to hash file');
      setIsError(true);
    } finally {
      setIsHashing(false);
    }
  }; 

  const handleFileDrop = async (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    setIsHashing(true);
    setUploadedFileName(file.name);
    try {
      const hash = await hashFileSHA256(file);
      setResultHash(hash);
    } catch (err) {
      console.error('Hash failed:', err);
      setStatus('Failed to hash file');
      setIsError(true);
    } finally {
      setIsHashing(false);
    }
  };

  const handleDragOver = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    e.stopPropagation();
  };

 const handleSubmit = async () => {
    setLoading(true);
    setStatus('');
    setIsError(false);

    try {
      if (!jobId || !nodePk || !payment || !expiryBlock) {
        throw new Error('Please fill in all required fields');
      }

      if (settlement === 'fast' && !resultHash) {
        throw new Error('Result hash required for fast settlement');
      }

      const paymentGroth = beamToGroth(payment);

      setStatus('Creating job, please approve in your Beam wallet.');
      if (settlement === 'fast') {
        await createJobModeA(
          parseInt(jobId),
          nodePk,
          resultHash,
          paymentGroth,
          parseInt(expiryBlock)
        );
      } else {
        const disputeFeeGroth = beamToGroth(disputeFee);
        await createJobModeB(
          parseInt(jobId),
          nodePk,
          paymentGroth,
          disputeFeeGroth,
          parseInt(expiryBlock),
          parseInt(reviewWindow)
        );
      }
      // Track this job locally for "My Jobs" view
      try {
        addTrackedJob({
          jobId: parseInt(jobId),
          role: 'requester',
          addedAt: Date.now(),
          payment: payment,
          resultHash: settlement === 'fast' ? resultHash : '',
        });
      } catch (err) {
        console.error('Could not track job:', err);
      }

      setStatus(`Job ${jobId} created successfully. Share Job ID and your pubkey with the node operator.`);
    } catch (err: any) {
      setIsError(true);
      setStatus(err.message || 'Failed to create job');
    } finally {
      setLoading(false);
    }
  };

  const handleViewJob = async () => {
    if (!viewJobId) return;
    setViewLoading(true);
    setViewError('');
    setJobInfo(null);
    setRefundStatus('');

    try {
      const result = await viewJob(parseInt(viewJobId));
      if (!result || (typeof result === 'object' && Object.keys(result).length === 0)) {
        throw new Error('Job not found');
      }
      setJobInfo(result);
    } catch (err: any) {
      setViewError(err.message || 'Failed to load job');
    } finally {
      setViewLoading(false);
    }
  };

  const handleRefund = async () => {
    if (!viewJobId || !jobInfo) return;
    setRefundLoading(true);
    setRefundStatus('');
    setRefundError(false);

    try {
      setRefundStatus('Refunding — please approve in your Beam wallet...');
      await refundJob(
        parseInt(viewJobId),
        jobInfo.payment || 0,
        jobInfo.collateral || 0,
        jobInfo.asset_id || 0
      );
      setRefundStatus(`Job ${viewJobId} refund submitted successfully.`);
      setJobInfo(null);
    } catch (err: any) {
      setRefundError(true);
      setRefundStatus(err.message || 'Refund failed');
    } finally {
      setRefundLoading(false);
    }
  };

  const isValid = jobId && nodePk && payment && expiryBlock &&
    (settlement === 'fast' ? resultHash : (reviewWindow && disputeFee));

  const renderJobInfo = () => {
    if (!jobInfo) return null;
    const fields = Object.entries(jobInfo);
    return (
      <JobCard>
        {fields.map(([key, val]) => (
          <JobField key={key}>
            <span>{key}</span>
            <span>{typeof val === 'number' && key.toLowerCase().includes('payment') || key.toLowerCase().includes('collateral')
              ? `${grothToBeam(val as number)} BEAM`
              : String(val)}</span>
          </JobField>
        ))}
        <ButtonRow>
          <DangerButton onClick={handleRefund} disabled={refundLoading}>
            {refundLoading ? 'Refunding...' : '↩ Refund Job'}
          </DangerButton>
        </ButtonRow>
        {refundStatus && (
          <StatusMsg error={refundError} style={{ marginTop: '12px' }}>{refundStatus}</StatusMsg>
        )}
      </JobCard>
    );
  };

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>← Back</BackLink>
      {offerFrom && (
        <OfferBanner>
          <OfferBannerLabel>Job Offer Received</OfferBannerLabel>
          This job was prepared by {offerFrom.slice(0, 12)}...{offerFrom.slice(-8)}. Please review the details before creating the job.
        </OfferBanner>
      )}

      <Section>
        <SectionTitle>Settlement Type</SectionTitle>
        <SettlementOptions>
          <SettlementCard selected={settlement === 'fast'} onClick={() => setSettlement('fast')}>
            <CardTitle>Fast Settlement</CardTitle>
            <CardDesc>Settles immediately when node delivers matching result hash. Best for deterministic tasks.</CardDesc>
          </SettlementCard>
          <SettlementCard selected={settlement === 'review'} onClick={() => setSettlement('review')}>
            <CardTitle>Reviewed Settlement</CardTitle>
            <CardDesc>You review the work and approve, with arbitrator backstop if you dispute. Best for non deterministic or open ended tasks.</CardDesc>
          </SettlementCard>
        </SettlementOptions>
      </Section>

      <TwoColumn>
      <Section>
        <SectionTitle>Job Details</SectionTitle>
        <Label>Job ID</Label>
        <Input placeholder="e.g. 111" value={jobId} onChange={e => setJobId(e.target.value)} />
        <Label>Node Beam Public Key</Label>
        <Input placeholder="Node pubkey provided by the operator" value={nodePk} onChange={e => setNodePk(e.target.value)} />
        <Row>
          <div>
            <Label>Payment (BEAM) {usdEstimate(payment)}</Label>
            <Input placeholder="e.g. 500" value={payment} onChange={e => handlePaymentChange(e.target.value)} />
          </div>
          <div>
            <Label>Collateral (BEAM) {usdEstimate(collateral)}</Label>
            <Input placeholder="Auto: 50% of payment" value={collateral} onChange={e => setCollateral(e.target.value)} />
          </div>
        </Row>
        <Label>Expiry Block</Label>
        <Input placeholder="e.g. 3990000" value={expiryBlock} onChange={e => setExpiryBlock(e.target.value)} />
        <HintText>Current Beam block + ~10,000 blocks ≈ 1 week</HintText>
      </Section>

      {settlement === 'fast' && (
        <Section>
          <SectionTitle>Result Verification</SectionTitle>
          <Label>Upload deliverable file (optional, computes hash automatically)</Label>
          <FileInputWrapper onDrop={handleFileDrop} onDragOver={handleDragOver}>
            {isHashing
              ? 'Hashing file...'
              : uploadedFileName
                ? `Loaded: ${uploadedFileName}`
                : 'Click to select a file'}
            <input type="file" onChange={handleFileUpload} disabled={isHashing} />
          </FileInputWrapper>
          {uploadedFileName && !isHashing && (
            <FileStatus>Hash computed locally. File never leaves your device.</FileStatus>
          )}
          <Label>Expected Result Hash</Label>
          <Input placeholder="64 char hex hash of expected output" value={resultHash} onChange={e => setResultHash(e.target.value)} />
          <HintText>SHA256 hash of the deliverable. Either upload above or paste a hash directly.</HintText>
        </Section>
      )}
      {settlement === 'review' && (
        <Section>
          <SectionTitle>Review Settings</SectionTitle>
          <Row>
            <div>
              <Label>Review Window (blocks)</Label>
              <Input placeholder="e.g. 100" value={reviewWindow} onChange={e => setReviewWindow(e.target.value)} />
              <HintText>Time you have to approve or dispute after delivery. 100 blocks is approximately 100 minutes.</HintText>
            </div>
            <div>
              <Label>Dispute Fee (BEAM)</Label>
              <Input placeholder="e.g. 1" value={disputeFee} onChange={e => setDisputeFee(e.target.value)} />
              <HintText>Locked if you dispute. Refunded if arbitrator sides with you, paid to worker if not.</HintText>
            </div>
          </Row>
        </Section>
      )}
      </TwoColumn>

      <SubmitButton onClick={handleSubmit} disabled={!isValid || loading}>
        {loading ? 'Creating Job...' : 'Create Job'}
      </SubmitButton>

      {status && <StatusMsg error={isError}>{status}</StatusMsg>}

      <Divider />

      <Section>
        <SectionTitle>View / Refund Job</SectionTitle>
        <Row>
          <div>
            <Label>Job ID</Label>
            <Input
              placeholder="e.g. 111"
              value={viewJobId}
              onChange={e => { setViewJobId(e.target.value); setJobInfo(null); setViewError(''); }}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-end', paddingBottom: '12px' }}>
            <SecondaryButton onClick={handleViewJob} disabled={!viewJobId || viewLoading}>
              {viewLoading ? 'Loading...' : '🔍 View Job'}
            </SecondaryButton>
          </div>
        </Row>
        {viewError && <StatusMsg error>{viewError}</StatusMsg>}
        {renderJobInfo()}
      </Section>

    </Container>
  );
};

export default MainPage;
