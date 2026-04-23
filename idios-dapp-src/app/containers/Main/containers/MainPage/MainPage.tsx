import React, { useState } from 'react';
import { styled } from '@linaria/react';
import { createJob } from '@app/core/api';

const Container = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 30px 20px;
  color: white;
  max-width: 600px;
  margin: 0 auto;
  min-height: 100vh;
  background: linear-gradient(to bottom, #035b8f, #042548);
  border-radius: 12px;
`;

const Title = styled.h1`
  font-size: 28px;
  font-weight: 700;
  margin-bottom: 8px;
  color: #00f6d2;
`;

const Subtitle = styled.p`
  font-size: 14px;
  color: rgba(255,255,255,0.6);
  margin-bottom: 30px;
  text-align: center;
`;

const Section = styled.div`
  width: 100%;
  margin-bottom: 24px;
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
  border: 2px solid ${({ selected }) => selected ? '#00f6d2' : 'rgba(255,255,255,0.1)'};
  background: ${({ selected }) => selected ? 'rgba(0,246,210,0.08)' : 'rgba(255,255,255,0.03)'};
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: rgba(0,246,210,0.5);
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
  margin-bottom: 12px;
  box-sizing: border-box;
  outline: none;

  &:focus {
    border-color: #00f6d2;
  }

  &::placeholder {
    color: rgba(255,255,255,0.3);
  }
`;

const Row = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
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
  background: #00f6d2;
  color: #042548;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 8px;

  &:hover {
    opacity: 0.9;
    box-shadow: 0 0 20px rgba(0,246,210,0.3);
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
  background: ${({ error }) => error ? 'rgba(255,98,92,0.15)' : 'rgba(0,246,210,0.1)'};
  color: ${({ error }) => error ? '#ff625c' : '#00f6d2'};
  text-align: center;
  width: 100%;
`;

const MainPage: React.FC = () => {
  const [settlement, setSettlement] = useState<'fast' | 'epoch'>('fast');
  const [jobId, setJobId] = useState('');
  const [nodePk, setNodePk] = useState('');
  const [payment, setPayment] = useState('');
  const [collateral, setCollateral] = useState('');
  const [expiryBlock, setExpiryBlock] = useState('');
  const [resultHash, setResultHash] = useState('');
  const [status, setStatus] = useState('');
  const [isError, setIsError] = useState(false);
  const [loading, setLoading] = useState(false);

  const beamToGroth = (beam: string) => Math.round(parseFloat(beam) * 1e8);
  const usdEstimate = (beam: string) => beam ? `~$${(parseFloat(beam) * 0.02).toFixed(2)}` : '';

  const handlePaymentChange = (val: string) => {
    setPayment(val);
    if (val && !isNaN(parseFloat(val))) {
      setCollateral((parseFloat(val) * 0.5).toFixed(0));
    }
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
      const hash = settlement === 'fast' ? resultHash : '0'.repeat(64);

      setStatus('Creating job — please approve in your Beam wallet...');
      await createJob(
        parseInt(jobId),
        nodePk,
        hash,
        paymentGroth,
        parseInt(expiryBlock)
      );

      setStatus(`Job ${jobId} created successfully. Share Job ID and your pubkey with the node operator.`);
    } catch (err: any) {
      setIsError(true);
      setStatus(err.message || 'Failed to create job');
    } finally {
      setLoading(false);
    }
  };

  const isValid = jobId && nodePk && payment && expiryBlock && 
    (settlement === 'epoch' || resultHash);

  return (
    <Container>
      <Title>Idios</Title>
      <Subtitle>Private settlement for decentralised AI work</Subtitle>

      <Section>
        <SectionTitle>Settlement Type</SectionTitle>
        <SettlementOptions>
          <SettlementCard selected={settlement === 'fast'} onClick={() => setSettlement('fast')}>
            <CardTitle>⚡ Fast Settlement</CardTitle>
            <CardDesc>Settles immediately when node delivers matching result hash. Best for deterministic tasks.</CardDesc>
          </SettlementCard>
          <SettlementCard selected={settlement === 'epoch'} onClick={() => setSettlement('epoch')}>
            <CardTitle>🔒 Epoch Settlement</CardTitle>
            <CardDesc>Hypertensor validators verify quality at epoch close (30–90 min). Best for large or complex jobs.</CardDesc>
          </SettlementCard>
        </SettlementOptions>
      </Section>

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
          <Label>Expected Result Hash</Label>
          <Input placeholder="64 char hex hash of expected output" value={resultHash} onChange={e => setResultHash(e.target.value)} />
          <HintText>SHA256 hash of the exact output you expect the node to deliver</HintText>
        </Section>
      )}

      <SubmitButton onClick={handleSubmit} disabled={!isValid || loading}>
        {loading ? 'Creating Job...' : 'Create Job'}
      </SubmitButton>

      {status && <StatusMsg error={isError}>{status}</StatusMsg>}
    </Container>
  );
};

export default MainPage;
