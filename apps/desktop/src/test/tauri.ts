import { vi } from "vitest";

const mocks = vi.hoisted(() => ({
  open: vi.fn(),
  revealItemInDir: vi.fn(),
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: mocks.open,
}));

vi.mock("@tauri-apps/plugin-opener", () => ({
  revealItemInDir: mocks.revealItemInDir,
}));

export function getTauriMocks() {
  return mocks;
}
