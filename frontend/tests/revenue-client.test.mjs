import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

// Keep the real API/cache code; replace only its session-provider dependencies.
const bundle = await build({
  stdin: {
    contents: "export { SecureAPIClient, TenantIsolationError } from './src/lib/secureApi'; export { decodeJWTPayload, extractTenantFromSession } from './src/utils/jwtUtils';",
    resolveDir: process.cwd(),
  },
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm',
  define: { 'import.meta.env': '{"DEV":false}' },
  plugins: [{
    name: 'test-session-provider',
    setup(build) {
      build.onResolve({ filter: /\/(supabase|sessionManager|sessionValidator|apiErrorHandler)$/ }, args => ({ path: args.path, namespace: 'test-session' }));
      build.onLoad({ filter: /.*/, namespace: 'test-session' }, () => ({ contents: `
        export const supabase = { auth: { getSession: async () => ({data: {session: null}}) } };
        export const sessionManager = { ensureValidSession: async () => null };
        export const sessionValidator = { validateSession: async () => null };
        export const withRetry = null, handleApiError = null, classifyError = null;
      ` }));
    },
  }],
});
const { SecureAPIClient, TenantIsolationError, decodeJWTPayload, extractTenantFromSession } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`
);

function token(tenant, user = 'test-user') {
  return `header.${Buffer.from(JSON.stringify({ id: user, email: `${user}@example.com`, app_metadata: { tenant_id: tenant } })).toString('base64url')}.signature`;
}

function response(total, property = 'prop-001') {
  return new Response(JSON.stringify({ property_id: property, total_revenue: total, currency: 'USD', reservations_count: 1 }), { headers: { 'Content-Type': 'application/json' } });
}

test('JWT tenant extraction supports app metadata, text IDs and UTF-8', () => {
  const access_token = token('tenant-b', 'Пользователь 🏠');
  assert.equal(extractTenantFromSession({ access_token }), 'tenant-b');
  assert.equal(decodeJWTPayload(access_token).id, 'Пользователь 🏠');
});

test('a shared property is cached separately after account switches', async t => {
  const client = new SecureAPIClient();
  const a = token('tenant-a');
  const b = token('tenant-b');
  const fetch = t.mock.method(globalThis, 'fetch', async (_url, options) => response(options.headers.Authorization === `Bearer ${a}` ? '2250.00' : '0.00'));
  client.setAccessToken(a);
  assert.equal((await client.getDashboardSummary('prop-001')).total_revenue, '2250.00');
  assert.equal((await client.getDashboardSummary('prop-001')).total_revenue, '2250.00');
  assert.equal(fetch.mock.callCount(), 1);
  client.setAccessToken(b);
  assert.equal((await client.getDashboardSummary('prop-001')).total_revenue, '0.00');
  assert.equal(fetch.mock.callCount(), 2);
  assert.equal(fetch.mock.calls[1].arguments[1].headers.Authorization, `Bearer ${b}`);
});

test('the requested month and year participate in caching', async t => {
  const client = new SecureAPIClient();
  client.setAccessToken(token('tenant-a'));
  const fetch = t.mock.method(globalThis, 'fetch', async () => response('1.01'));
  for (const period of [{ month: 3, year: 2024 }, { month: 4, year: 2024 }, { month: 3, year: 2025 }, { year: 2024 }, undefined]) {
    await client.getDashboardSummary('prop-001', period);
  }
  const result = await client.getDashboardSummary('prop-001', { month: 3, year: 2024 });
  assert.equal(result.total_revenue, '1.01');
  assert.equal(fetch.mock.callCount(), 5);
  const url = new URL(fetch.mock.calls[0].arguments[0]);
  assert.equal(url.searchParams.get('month'), '3');
  assert.equal(url.searchParams.get('year'), '2024');
  assert.equal(url.searchParams.has('_t'), false);
});

test('a late response from the previous account is rejected', async t => {
  const client = new SecureAPIClient();
  const a = token('tenant-a');
  let release;
  let started;
  const pending = new Promise(resolve => { started = resolve; });
  t.mock.method(globalThis, 'fetch', (_url, options) => {
    if (options.headers.Authorization === `Bearer ${a}`) {
      started();
      return new Promise(resolve => { release = resolve; });
    }
    return Promise.resolve(response('0.00'));
  });
  client.setAccessToken(a);
  const oldResult = client.getDashboardSummary('prop-001').catch(error => error);
  await pending;
  client.setAccessToken(token('tenant-b'));
  assert.equal((await client.getDashboardSummary('prop-001')).total_revenue, '0.00');
  release(response('2250.00'));
  assert.ok(await oldResult instanceof TenantIsolationError);
  assert.equal((await client.getDashboardSummary('prop-001')).total_revenue, '0.00');
});
