export function getenv(key: string): string {
  return process.env[key] || "";
}
