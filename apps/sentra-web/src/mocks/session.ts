import demoMe from './me.json';
import testMe from './me_test.json';
import { getMockMode } from './mode';
import type { Dataset, Me, Role } from '../api/types';

const MOCK_SESSION_KEY = 'sentra.mock.session';
const MOCK_DATASET_KEY = 'sentra.mock.dataset';

// Mock users exist only for the local demo client. Passwords are never stored
// in the frontend; the real API validates its DEMO_USERS password hashes.
const MOCK_ROLES: Record<string, Role> = {
  buyer: 'BUYER',
  ppic: 'PPIC',
  sales: 'SALES',
  scm: 'SCM_MANAGER',
};

const demoProfile = demoMe as Me;
const testProfile = testMe as Me;

function mockStorage(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage;
}

function selectedDataset(): Dataset {
  if (typeof window !== 'undefined' && window.localStorage.getItem(MOCK_DATASET_KEY) === 'TEST') {
    return 'TEST';
  }
  return 'DEMO';
}

function profileFixture(): Me {
  return selectedDataset() === 'TEST' ? testProfile : demoProfile;
}

function profileFor(username: string): Me | null {
  const normalizedUsername = username.trim().toLowerCase();
  const role = MOCK_ROLES[normalizedUsername];
  if (!role) {
    return null;
  }

  return {
    ...profileFixture(),
    username: normalizedUsername,
    role,
    mode: getMockMode(),
    feature_flags: { ...profileFixture().feature_flags },
  };
}

/** Starts a local mock session. It accepts only known demo usernames. */
export function createMockSession(credentials: unknown): Me | null {
  if (
    !credentials ||
    typeof credentials !== 'object' ||
    typeof (credentials as { username?: unknown }).username !== 'string' ||
    typeof (credentials as { password?: unknown }).password !== 'string'
  ) {
    return null;
  }

  const { username, password } = credentials as { username: string; password: string };
  if (!password.trim()) {
    return null;
  }

  const profile = profileFor(username);
  if (!profile) {
    return null;
  }

  mockStorage()?.setItem(MOCK_SESSION_KEY, JSON.stringify(profile));
  return profile;
}

/** Returns the local mock session profile, if the user has signed in. */
export function getMockSession(): Me | null {
  const value = mockStorage()?.getItem(MOCK_SESSION_KEY);
  if (!value) {
    return null;
  }

  try {
    const profile: unknown = JSON.parse(value);
    if (!profile || typeof profile !== 'object' || typeof (profile as { username?: unknown }).username !== 'string') {
      clearMockSession();
      return null;
    }
    return profileFor((profile as { username: string }).username);
  } catch {
    clearMockSession();
    return null;
  }
}

/** Ends the local mock session. */
export function clearMockSession(): void {
  mockStorage()?.removeItem(MOCK_SESSION_KEY);
}

/** Test helper: establishes one of the local demo sessions without a password. */
export function setMockDataset(dataset: Dataset): void {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(MOCK_DATASET_KEY, dataset);
  }
  clearMockSession();
}

export function setMockSession(username = demoProfile.username): void {
  const profile = profileFor(username);
  if (!profile) {
    throw new RangeError(`Unknown mock username: ${username}`);
  }
  mockStorage()?.setItem(MOCK_SESSION_KEY, JSON.stringify(profile));
}
