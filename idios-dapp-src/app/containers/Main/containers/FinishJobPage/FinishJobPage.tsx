import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';

async function hashFileSHA256(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await (crypto as any).subtle.digest('SHA-256', buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

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
  margin-bottom: 12px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
`;

const Label = styled.div`
  font-size: 12px;
  color: rgba(255,255,255,0.5);
  margin-bottom: 4px;
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

const TextArea = styled.textarea`
  width: 100%;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-size: 13px;
  font-family: monospace;
  margin-bottom: 12px;
  box-sizing: border-box;
  outline: none;
  resize: vertical;
  min-height: 100px;
  &:focus {
    border-color: #e8e8e8;
  }
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

const HintText = styled.div`
  font-size: 11px;
  color: rgba(255,255,255,0.35);
  margin-top: -8px;
  margin-bottom: 12px;
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

const SubmitButton = styled.button`
  width: 100%;
  padding: 14px;
  border-radius: 8px;
  border: none;
  background: #e8e8e8;
  color: #042548;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  margin-top: 8px;
  font-family: inherit;
  &:disabled {
    background: rgba(255,255,255,0.1);
    color: rgba(255,255,255,0.4);
    cursor: not-allowed;
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

const OfferOutput = styled.div`
  width: 100%;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.3);
  background: rgba(255,255,255,0.05);
  margin-bottom: 16px;
  box-sizing: border-box;
`;

const CopyButton = styled.button`
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid rgba(255,255,255,0.4);
  background: transparent;
  color: #e8e8e8;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  margin-right: 8px;
  &:hover {
    background: rgba(255,255,255,0.1);
  }
`;

const ErrorMsg = styled.div`
  color: #ff6b6b;
  font-size: 12px;
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

const FinishJobPage: React.FC = () => {
  const navigate = useNavigate();

  const [settlement, setSettlement] = useState<'fast' | 'review'>('fast');
  const [description, setDescription] = useState('');
  const [payment, setPayment] = useState('');
  const [collateral, setCollateral] = useState('');
  const [expiryBlock, setExpiryBlock] = useState('');
  const [reviewWindow, setReviewWindow] = useState('100');
  const [disputeFee, setDisputeFee] = useState('0.01');
  const [requesterAddr, setRequesterAddr] = useState('');
  const [myAddr, setMyAddr] = useState('');
  const [resultHash, setResultHash] = useState('');
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [isHashing, setIsHashing] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  const [offerLink, setOfferLink] = useState('');
  const [offerText, setOfferText] = useState('');
  const [linkCopied, setLinkCopied] = useState(false);
  const [textCopied, setTextCopied] = useState(false);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsHashing(true);
    setUploadedFileName(file.name);
    setErrorMsg('');
    try {
      const hash = await hashFileSHA256(file);
      setResultHash(hash);
    } catch (err) {
      setErrorMsg('Failed to hash file: ' + String(err));
    } finally {
      setIsHashing(false);
    }
  };

  const handlePaymentChange = (val: string) => {
    setPayment(val);
    if (val && !isNaN(parseFloat(val))) {
      setCollateral((parseFloat(val) * 0.5).toFixed(0));
    }
  };

  const isValid =
    payment.length > 0 &&
    expiryBlock.length > 0 &&
    requesterAddr.length >= 60 &&
    myAddr.length >= 60 &&
    (settlement === 'fast'
      ? (collateral.length > 0 && resultHash.length === 64)
      : (reviewWindow.length > 0 && disputeFee.length > 0));

  const handleGenerate = () => {
    if (!isValid) {
      setErrorMsg('Please fill in all required fields. Addresses should be 64+ chars and hash exactly 64 chars.');
      return;
    }
    setErrorMsg('');

    const params = new URLSearchParams();
    params.set('mode', settlement);
    params.set('payment', payment);
    params.set('worker', myAddr);
    params.set('expiry', expiryBlock);
    params.set('from', myAddr);
    if (settlement === 'fast') {
      params.set('hash', resultHash);
    } else {
      params.set('window', reviewWindow);
      params.set('disputeFee', disputeFee);
    }

    const link = window.location.origin + window.location.pathname + '?' + params.toString();
    setOfferLink(link);

    const settlementLabel = settlement === 'fast' ? 'Fast Settlement (Mode A)' : 'Reviewed Settlement (Mode B)';
    const lines = [
      '== Idios Job Offer ==',
      '',
      'Description: ' + (description || '(none)'),
      'Settlement: ' + settlementLabel,
      'Payment: ' + payment + ' BEAM',
    ];
    if (settlement === 'fast') {
      lines.push('Collateral: ' + collateral + ' BEAM');
    }
    lines.push('Expiry block: ' + expiryBlock);
    if (settlement === 'review') {
      lines.push('Review window: ' + reviewWindow + ' blocks');
      lines.push('Dispute fee: ' + disputeFee + ' BEAM');
    }
    lines.push('');
    lines.push('Worker (me): ' + myAddr);
    lines.push('Requester (you): ' + requesterAddr);
    if (settlement === 'fast') {
      lines.push('Result hash: ' + resultHash);
    }
    lines.push('');
    lines.push('To accept this offer:');
    lines.push('1. Open Idios in your Beam wallet');
    lines.push('2. Click "Start a job"');
    lines.push('3. Paste these values into the form');
    lines.push('4. Click Create Job');
    lines.push('');
    lines.push('Or paste this link into the URL bar of your Idios dapp:');
    lines.push(link);
    setOfferText(lines.join('\n'));
  };

  const copyToClipboard = (text: string, which: 'link' | 'text') => {
    navigator.clipboard.writeText(text).then(() => {
      if (which === 'link') {
        setLinkCopied(true);
        setTimeout(() => setLinkCopied(false), 2000);
      } else {
        setTextCopied(true);
        setTimeout(() => setTextCopied(false), 2000);
      }
    });
  };

  return (
    <Container>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>← Back</BackLink>

      <Section>
        <SectionTitle>Settlement Type</SectionTitle>
        <SettlementOptions>
          <SettlementCard selected={settlement === 'fast'} onClick={() => setSettlement('fast')}>
            <CardTitle>Fast Settlement</CardTitle>
            <CardDesc>Settles immediately when you deliver matching result hash. Best for deterministic tasks.</CardDesc>
          </SettlementCard>
          <SettlementCard selected={settlement === 'review'} onClick={() => setSettlement('review')}>
            <CardTitle>Reviewed Settlement</CardTitle>
            <CardDesc>Client reviews work and approves, with arbitrator backstop. Best for non deterministic or open ended tasks.</CardDesc>
          </SettlementCard>
        </SettlementOptions>
      </Section>

      <TwoColumn>
      <div>
      <Section>
        <SectionTitle>Job Details</SectionTitle>
        <Label>Description (for your client's reference, not on chain)</Label>
        <Input placeholder="e.g. MD simulation of HIV protease, 100ns" value={description} onChange={e => setDescription(e.target.value)} />

        <Row>
          <div>
            <Label>Payment (BEAM)</Label>
            <Input placeholder="e.g. 12" value={payment} onChange={e => handlePaymentChange(e.target.value)} />
          </div>
          {settlement === 'fast' && (
            <div>
              <Label>Collateral (BEAM)</Label>
              <Input placeholder="Auto: 50% of payment" value={collateral} onChange={e => setCollateral(e.target.value)} />
            </div>
          )}
        </Row>

        <Label>Expiry Block</Label>
        <Input placeholder="e.g. 3990000" value={expiryBlock} onChange={e => setExpiryBlock(e.target.value)} />
        <HintText>Current Beam block + ~10,000 blocks ≈ 1 week</HintText>
      </Section>

      <Section>
        <SectionTitle>Addresses</SectionTitle>
        <Label>Your Beam address (worker)</Label>
        <Input placeholder="Your 64+ char Beam pubkey" value={myAddr} onChange={e => setMyAddr(e.target.value)} />
        <Label>Client's Beam address (requester)</Label>
        <Input placeholder="Client's 64+ char Beam pubkey" value={requesterAddr} onChange={e => setRequesterAddr(e.target.value)} />
      </Section>

      </div>
      <div>
      {settlement === 'fast' && (
        <Section>
          <SectionTitle>Deliverable</SectionTitle>
          <Label>Upload your finished file (computes hash automatically)</Label>
          <FileInputWrapper>
            {isHashing
              ? 'Hashing file...'
              : uploadedFileName
                ? 'Loaded: ' + uploadedFileName
                : 'Click to select a file'}
            <input type="file" onChange={handleFileUpload} disabled={isHashing} />
          </FileInputWrapper>
          {uploadedFileName && !isHashing && (
            <FileStatus>Hash computed locally. File never leaves your device.</FileStatus>
          )}
          <Label>Result hash (auto-generated from upload)</Label>
          <Input placeholder="64 char hex hash" value={resultHash} onChange={e => setResultHash(e.target.value)} />
        </Section>
      )}
      {settlement === 'review' && (
        <Section>
          <SectionTitle>Review Settings</SectionTitle>
          <Row>
            <div>
              <Label>Review Window (blocks)</Label>
              <Input placeholder="e.g. 100" value={reviewWindow} onChange={e => setReviewWindow(e.target.value)} />
              <HintText>Time client has to approve or dispute after delivery. 100 blocks is roughly 100 minutes.</HintText>
            </div>
            <div>
              <Label>Dispute Fee (BEAM)</Label>
              <Input placeholder="e.g. 0.01" value={disputeFee} onChange={e => setDisputeFee(e.target.value)} />
              <HintText>Locked by client if they dispute. Refunded if arbitrator sides with them, paid to you if not.</HintText>
            </div>
          </Row>
        </Section>
      )}
      </div>
      </TwoColumn>

      {errorMsg && <ErrorMsg>{errorMsg}</ErrorMsg>}

      <SubmitButton onClick={handleGenerate} disabled={!isValid}>
        Generate Offer
      </SubmitButton>

      {offerLink && (
        <Section style={{ marginTop: '24px' }}>
          <SectionTitle>Your Offer</SectionTitle>
          <Label>Offer text (paste into Telegram, email, etc.)</Label>
          <TextArea readOnly value={offerText} />
          <CopyButton onClick={() => copyToClipboard(offerText, 'text')}>
            {textCopied ? '✓ Copied' : 'Copy text'}
          </CopyButton>
          <CopyButton onClick={() => copyToClipboard(offerLink, 'link')}>
            {linkCopied ? '✓ Copied' : 'Copy link only'}
          </CopyButton>
        </Section>
      )}
    </Container>
  );
};

export default FinishJobPage;
