/** Conservative review flag. Never changes the saved or raw recognition text. */
export function suspiciousRepetition(text: string): boolean {
  const compact = text.replace(/[\s\p{P}\p{S}]/gu, "");
  if (compact.length < 24) return false;
  for (const match of compact.matchAll(/(.{1,16}?)\1{2,}/gu)) {
    if (match[0].length >= 24 && match[0].length / compact.length >= .7) return true;
  }
  return false;
}
