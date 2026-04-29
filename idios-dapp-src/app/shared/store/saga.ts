import {
  call, take, fork, takeLatest, put, select
} from 'redux-saga/effects';

import { eventChannel, END } from 'redux-saga';
import { actions } from '@app/shared/store/index';
import { actions as mainActions } from '@app/containers/Main/store/index';
import { navigate, setSystemState, setIsLoaded } from '@app/shared/store/actions';
import store from '../../../index';
import { SharedStateType } from '../interface';

import Utils from '@core/utils.js';

export function remoteEventChannel() {
  return eventChannel((emitter) => {
    Utils.initialize({
      "appname": "Idios",
      "min_api_version": "6.2",
      "headless": false,
      "apiResultHandler": (error, result, full) => {
        console.log('api result data: ', result, full);
        if (result && !result.error) {
          emitter(full);
        }
      }
    }, (err) => {
        store.dispatch(setIsLoaded(true));
        store.dispatch(navigate('/main/'));
        Utils.callApi("ev_subunsub", {ev_txs_changed: true, ev_system_state: true}, 
          (error, result, full) => {
            if (result) {
              //action
            }
          }
        );
    });

    const unsubscribe = () => {
      emitter(END);
    };

    return unsubscribe;
  });
}

function* sharedSaga() {
  const remoteChannel = yield call(remoteEventChannel);

  while (true) {
    try {
      const payload: any = yield take(remoteChannel);
      switch (payload.id) {
        case 'ev_system_state':
          store.dispatch(setSystemState(payload.result));

          break;
        default:
          break;
      }
    } catch (err) {
      remoteChannel.close();
    }
  }
}

export default sharedSaga;
