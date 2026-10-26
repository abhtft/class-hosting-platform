// The portal address in the source tree is a placeholder. `node admin.mjs portal-publish` knows the real one (stack output PortalUrl)
// and substitutes it while building the kit and the MCP package, so each teacher's published files point at their own portal.

export const PLACEHOLDER_PORTAL = 'https://deploy.example.edu';

/** Replace the placeholder portal address in a text with the real one (no trailing slash). Without a portal URL the text is unchanged. */
export function applyPortal(text, portalUrl) {
  if (!portalUrl) return text;
  return text.split(PLACEHOLDER_PORTAL).join(String(portalUrl).replace(/\/$/, ''));
}
