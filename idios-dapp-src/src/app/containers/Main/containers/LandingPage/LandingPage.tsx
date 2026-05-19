import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';
import { getUserKey } from '@app/core/api';

const Container = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 24px;
  color: white;
  max-width: 1100px;
  margin: 0 auto;
  min-height: 100vh;
  background: #0a0a0a;
  border-radius: 12px;
`;

const Title = styled.h1`
  font-size: 36px;
  font-weight: 700;
  margin-bottom: 12px;
  color: #e8e8e8;
`;

const Subtitle = styled.p`
  font-size: 14px;
  color: rgba(255,255,255,0.6);
  margin-bottom: 24px;
  text-align: center;
`;

const PubKeyPill = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  margin-bottom: 32px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 999px;
  background: rgba(255,255,255,0.03);
  font-size: 12px;
  color: rgba(255,255,255,0.7);
  font-family: monospace;
`;

const PubKeyLabel = styled.span`
  color: rgba(255,255,255,0.5);
`;

const CopyBtn = styled.button`
  background: none;
  border: none;
  color: rgba(255,255,255,0.6);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 0.15s, color 0.15s;
  &:hover {
    color: #e8e8e8;
    background: rgba(255,255,255,0.08);
  }
`;

const Card = styled.button`
  width: 100%;
  padding: 28px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05);
  color: white;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  margin-bottom: 16px;
  transition: border-color 0.15s, background 0.15s, transform 0.1s;
  &:hover {
    border-color: #e8e8e8;
    background: rgba(255,255,255,0.05);
  }
  &:active {
    transform: scale(0.99);
  }
`;

const CardTitle = styled.div`
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 6px;
  color: #e8e8e8;
`;

const CardDesc = styled.div`
  font-size: 13px;
  color: rgba(255,255,255,0.6);
`;

const ArbitratorLink = styled.button`
  background: none;
  border: none;
  color: rgba(255,255,255,0.25);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  margin-top: 32px;
  padding: 4px 8px;
  &:hover {
    color: rgba(255,255,255,0.6);
  }
`;

const truncatePk = (pk: string) => {
  if (!pk || pk.length < 16) return pk;
  return pk.slice(0, 8) + '...' + pk.slice(-6);
};

const LandingPage: React.FC = () => {
  const navigate = useNavigate();
  const [userPk, setUserPk] = useState<string>('');
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.toString().length > 0) {
      navigate(ROUTES_FULL.MAIN.START, { replace: true });
      return;
    }
    getUserKey()
      .then((pk: string) => { if (pk) setUserPk(pk); })
      .catch(() => {});
  }, [navigate]);

  const handleCopy = () => {
    if (!userPk) return;
    let ok = false;
    try {
      const ta = document.createElement('textarea');
      ta.value = userPk;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (e) { ok = false; }
    if (!ok && navigator.clipboard) {
      navigator.clipboard.writeText(userPk).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }).catch(() => {});
      return;
    }
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <Container>
      <Title>Idios</Title>
      <Subtitle>Private escrow on Beam</Subtitle>
      {userPk ? (
        <PubKeyPill>
          <PubKeyLabel>Your pubkey for this contract:</PubKeyLabel>
          <span>{truncatePk(userPk)}</span>
          <CopyBtn onClick={handleCopy}>{copied ? 'Copied' : 'Copy'}</CopyBtn>
        </PubKeyPill>
      ) : null}
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.START)}>
        <CardTitle>Start a contract</CardTitle>
        <CardDesc>You know who you're working with. Enter their pubkey and lock the terms.</CardDesc>
      </Card>
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.FINISH)}>
        <CardTitle>Generate a contract offer</CardTitle>
        <CardDesc>Lock funds first and send a link for the other party to accept. Good for commissioning work or paying sources.</CardDesc>
      </Card>
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.MY_JOBS)}>
        <CardTitle>My contracts</CardTitle>
        <CardDesc>Track contracts you've created or accepted. Approve work, dispute, claim funds, or refund.</CardDesc>
      </Card>
      <ArbitratorLink onClick={() => navigate(ROUTES_FULL.MAIN.ARBITRATOR)}>
        Arbitrator console
      </ArbitratorLink>
    </Container>
  );
};

export default LandingPage;
