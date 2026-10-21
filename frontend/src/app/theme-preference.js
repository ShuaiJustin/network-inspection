const STORAGE_KEY = "inspection-theme";
const THEMES = ["light", "dark"];
const DEFAULT_THEME = "dark";

export function normalizeTheme(value) {
  return THEMES.includes(value) ? value : null;
}

export function resolveInitialTheme(stored, prefersDark) {
  return normalizeTheme(stored) ?? (prefersDark === false ? "light" : DEFAULT_THEME);
}

export function nextTheme(current) {
  return current === "dark" ? "light" : "dark";
}

export function readStoredTheme(storage) {
  try {
    return normalizeTheme(storage?.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

export function persistTheme(storage, theme) {
  const value = normalizeTheme(theme);
  if (!value) return false;
  try {
    storage?.setItem(STORAGE_KEY, value);
    return true;
  } catch {
    return false;
  }
}

export function applyTheme(root, theme) {
  const value = normalizeTheme(theme) ?? DEFAULT_THEME;
  root?.classList?.toggle("dark", value === "dark");
  return value;
}

export { STORAGE_KEY, DEFAULT_THEME };
