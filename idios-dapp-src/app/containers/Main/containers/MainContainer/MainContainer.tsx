import React from 'react';
import { useRoutes } from 'react-router-dom';
import { ROUTES_PATH } from '@app/shared/constants';
import { MainPage } from '@app/containers/Main/containers/MainPage';
import { LandingPage } from '@app/containers/Main/containers/LandingPage';
import { FinishJobPage } from '@app/containers/Main/containers/FinishJobPage';

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
