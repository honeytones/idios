import React from 'react';
import { useRoutes } from 'react-router-dom';
import { ROUTES_PATH } from '@app/shared/constants';
import { MainPage } from '@app/containers/Main/containers/MainPage';
import { LandingPage } from '@app/containers/Main/containers/LandingPage';
import { FinishJobPage } from '@app/containers/Main/containers/FinishJobPage';
import { MyJobsPage } from '@app/containers/Main/containers/MyJobsPage';
import { ArbitratorPage } from '@app/containers/Main/containers/ArbitratorPage';

const routes = [
  {
    path: ROUTES_PATH.MAIN.LANDING_PAGE,
    element: <LandingPage />,
    exact: true,
  },
  {
    path: ROUTES_PATH.MAIN.START_PAGE,
    element: <MainPage />,
    exact: true,
  },
  {
    path: ROUTES_PATH.MAIN.FINISH_PAGE,
    element: <FinishJobPage />,
    exact: true,
  },
  {
    path: ROUTES_PATH.MAIN.MY_JOBS_PAGE,
    element: <MyJobsPage />,
    exact: true,
  },
  {
    path: ROUTES_PATH.MAIN.ARBITRATOR_PAGE,
    element: <ArbitratorPage />,
    exact: true,
  },
  {
    path: ROUTES_PATH.MAIN.MAIN_PAGE,
    element: <MainPage />,
    exact: true,
  },
];

const MainContainer = () => {
  const content = useRoutes(routes);

  return <>{content}</>;
};

export default MainContainer;
