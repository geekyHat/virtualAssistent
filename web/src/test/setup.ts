/** Setup dei test: assertion DOM e lingua primaria italiana (web AGENTS). */
import "@testing-library/jest-dom/vitest";

Object.defineProperty(window.navigator, "language", {
  value: "it-IT",
  configurable: true,
});

if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  const storage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => store.set(k, String(v)),
    removeItem: (k: string) => store.delete(k),
    clear: () => store.clear(),
    get length() {
      return store.size;
    },
    key: (i: number) => [...store.keys()][i] ?? null,
  };
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });
  Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
}
