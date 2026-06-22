// Vitest global setup — registers jest-dom matchers (toBeInTheDocument, etc.)
// and tears down the rendered tree between tests so component specs don't leak
// DOM into each other. Pure-logic specs simply don't touch any of this.
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

afterEach(() => {
  cleanup();
});
