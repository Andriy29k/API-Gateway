// k6 load test for the gateway rate limiting and throttling (T-024).
//
// Gateway limits (gateway/nginx.conf, charts/api-gateway): 5 requests/s per client IP,
// burst=10 delay=5 (5 extra requests served at once, 5 more delayed, the rest get 429),
// limit_conn 20 concurrent requests per IP.
//
// Run:  k6 run load/rate-limit.js
//       GATEWAY_URL=https://api.api-gateway.pp.ua k6 run load/rate-limit.js
//
// All requests come from one IP, so they share one limit. Scenarios run one after another
// with pauses, so the limit bucket is empty again when the next one starts.
import http from 'k6/http';
import { check } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.GATEWAY_URL || 'http://localhost:8080').replace(/\/$/, '');
const URL = `${BASE_URL}/api/service-a/hello`;

const rateLimited = new Rate('rate_limited');            // share of 429 responses
const unexpectedStatus = new Counter('unexpected_status'); // anything other than 200 or 429
// Response time of accepted (200) requests only: shows the throttling delay of burst=10 delay=5.
const acceptedDuration = new Trend('accepted_duration', true);

export const options = {
  scenarios: {
    // Below the limit: nothing should be rejected.
    normal: {
      executor: 'constant-arrival-rate',
      rate: 3,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 10,
      startTime: '0s',
    },
    // 4x the limit for 30 s: most requests must get 429.
    over_limit: {
      executor: 'constant-arrival-rate',
      rate: 20,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 50,
      startTime: '40s',
    },
    // 30 requests at the same moment: about 11 pass (1 + burst 10), the rest get 429.
    burst: {
      executor: 'per-vu-iterations',
      vus: 30,
      iterations: 1,
      startTime: '80s',
    },
  },
  thresholds: {
    'rate_limited{scenario:normal}': ['rate<0.01'],
    'http_req_duration{scenario:normal}': ['p(95)<1000'],
    'rate_limited{scenario:over_limit}': ['rate>0.5'],
    'rate_limited{scenario:burst}': ['rate>0.3', 'rate<0.9'],
    unexpected_status: ['count==0'],
    // Throttled requests are delayed, but must still be served.
    'accepted_duration{scenario:over_limit}': ['p(95)<3000'],
    'accepted_duration{scenario:burst}': ['p(95)<3000'],
  },
};

export default function () {
  const res = http.get(URL, {
    responseCallback: http.expectedStatuses(200, 429),
  });

  rateLimited.add(res.status === 429);
  if (res.status === 200) {
    acceptedDuration.add(res.timings.duration);
  }
  if (res.status !== 200 && res.status !== 429) {
    unexpectedStatus.add(1);
  }

  if (res.status === 429) {
    check(res, {
      '429 has JSON body': (r) => r.json('error') === 'too_many_requests',
      '429 has Retry-After': (r) => r.headers['Retry-After'] === '1',
    });
  } else {
    check(res, { 'status is 200': (r) => r.status === 200 });
  }
}
