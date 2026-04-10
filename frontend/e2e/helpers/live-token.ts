/**
 * Fixed e2e bypass token shared by the frontend Playwright specs and the
 * backend's ``E2E_TEST_TOKEN`` env var.
 *
 * The token is shaped like a real JWT (header.payload.signature) so the
 * frontend's ``decodeJwt`` helper can pull user metadata out of it for the
 * UI. The signature is junk on purpose: the backend treats this exact
 * string as an opaque bypass and never verifies the signature. This path
 * is gated by the ``E2E_TEST_TOKEN`` env var being non-empty, so it never
 * activates in production.
 *
 * IMPORTANT: keep ``E2E_TEST_USER_*`` values in sync between this file and
 * the backend env so what the UI shows matches what the backend stores.
 */

export const E2E_TEST_USER_ID = 'e2e-test-user'
export const E2E_TEST_USER_EMAIL = 'e2e@bsnexus.test'
export const E2E_TEST_USER_TENANT_ID = '11111111-1111-4111-8111-111111111111'

function base64UrlEncode(payload: object): string {
  const json = JSON.stringify(payload)
  // Node and modern browsers both support btoa via Buffer / global fallback.
  const b64 =
    typeof Buffer !== 'undefined'
      ? Buffer.from(json, 'utf8').toString('base64')
      : btoa(unescape(encodeURIComponent(json)))
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

const HEADER = base64UrlEncode({ alg: 'HS256', typ: 'JWT' })
const PAYLOAD = base64UrlEncode({
  sub: E2E_TEST_USER_ID,
  email: E2E_TEST_USER_EMAIL,
  // Year 2286 — far enough away that the test never starts failing on its own.
  exp: 9999999999,
  app_metadata: {
    tenant_id: E2E_TEST_USER_TENANT_ID,
    role: 'admin',
  },
})

/**
 * Stable, shared bypass token. Both ends compare the full string for an
 * exact match — no signature verification anywhere.
 */
export const E2E_TEST_TOKEN = `${HEADER}.${PAYLOAD}.e2e-fake-signature`

/** localStorage key the frontend reads in ``getAccessToken``. */
export const STORED_TOKEN_KEY = 'bsnexus_access_token'
