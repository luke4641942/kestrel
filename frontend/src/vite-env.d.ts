/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" in the public demo build (npm run build:demo): read-only, from a snapshot. */
  readonly VITE_DEMO?: string;
}
