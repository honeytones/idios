import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';

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
  margin-bottom: 50px;
  text-align: center;
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

const LandingPage: React.FC = () => {
  const navigate = useNavigate();

  useEffect(() => {
    // If URL parameters are present (e.g. Bob's offer link),
    // skip landing and go straight to the Create Job form
    const params = new URLSearchParams(window.location.search);
    if (params.toString().length > 0) {
      navigate(ROUTES_FULL.MAIN.START, { replace: true });
    }
  }, [navigate]);

  return (
    <Container>
      <Title>Idios</Title>
      <Subtitle>Private settlement for AI and other compute on Beam</Subtitle>
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.START)}>
        <CardTitle>Start a job</CardTitle>
        <CardDesc>Set up a private job with payment locked in escrow.</CardDesc>
      </Card>
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.FINISH)}>
        <CardTitle>Finish a job</CardTitle>
        <CardDesc>Submit completed work. Lock collateral and get paid when verified.</CardDesc>
      </Card>
      <Card onClick={() => navigate(ROUTES_FULL.MAIN.MY_JOBS)}>
        <CardTitle>My jobs</CardTitle>
        <CardDesc>Track jobs you've created or accepted. Refund expired jobs.</CardDesc>
      </Card>
    </Container>
  );
};

export default LandingPage;
