// Which AI the voice ordering of a student's shop uses, in plain words for the teacher. Read-only: it changes nothing.
// The teacher never sees the value of a student's VOICE_MODEL (the portal hides every setting value), only whether it is set.
export function voiceRouteText(ai, backend) {
  if (!ai || !ai.available) return '';
  if (ai.provider === 'gemini') {
    return 'Voice uses: Google Gemini (free within the cap). A VOICE_MODEL setting is not used on Gemini.';
  }
  const names = Array.isArray(backend?.env_names) ? backend.env_names : [];
  if (names.includes('VOICE_MODEL')) {
    return 'Voice uses: OpenRouter with the student’s own VOICE_MODEL (the name is hidden: the portal never shows values).';
  }
  return `Voice uses: OpenRouter, default model${ai.model ? ` (${ai.model})` : ''}.`;
}
