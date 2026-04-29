import React from 'react';
import { useNavigate } from 'react-router-dom';
import { styled } from '@linaria/react';
import { ROUTES_PATH, ROUTES_FULL } from '@app/shared/constants';

const Container = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 20px;
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
  margin-bottom: 12px;
  color: #00f6d2;
`;

const Body = styled.p`
  font-size: 14px;
  color: rgba(255,255,255,0.7);
  text-align: center;
  margin-bottom: 30px;
  line-height: 1.6;
`;

const BackLink = styled.button`
  background: none;
  border: 1px solid rgba(255,255,255,0.2);
  color: rgba(255,255,255,0.7);
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  &:hover {
    border-color: #00f6d2;
    color: #00f6d2;
  }
`;

const FinishJobPage: React.FC = () => {
  const navigate = useNavigate();
  return (
    <Container>
      <Title>Finish a Job</Title>
      <Body>
        Coming soon. This page will let you submit completed work, hash your deliverable file, and generate a job offer to send to your client.
      </Body>
      <BackLink onClick={() => navigate(ROUTES_FULL.MAIN.LANDING)}>
        Back to start
      </BackLink>
    </Container>
  );
};

export default FinishJobPage;
