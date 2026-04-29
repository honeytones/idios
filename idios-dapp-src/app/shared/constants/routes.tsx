export const ROUTES = {
  MAIN: {
    BASE: '/main',
    MAIN_PAGE: '/main/main_page',
  },
};

export const ROUTES_PATH = {
  MAIN: {
    BASE: '/',
    MAIN_PAGE: '/main_page',
    LANDING_PAGE: '',
    START_PAGE: 'start',
    FINISH_PAGE: 'finish',
    MY_JOBS_PAGE: 'my-jobs',
  },
  
};

// Full paths for cross-parent navigation (used in saga, app.tsx)
export const ROUTES_FULL = {
  MAIN: {
    LANDING: '/main/',
    START: '/main/start',
    FINISH: '/main/finish',
    MY_JOBS: '/main/my-jobs',
    MAIN_PAGE: '/main/main_page',
  },
};
