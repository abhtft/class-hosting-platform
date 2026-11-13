// The teacher's read-only line about which AI a student's voice ordering uses (web/voice_route.js).
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const { voiceRouteText } = await import(pathToFileURL(join(here, '..', 'web', 'voice_route.js')).href);

assert.equal(voiceRouteText({ available: false }, {}), '', 'no AI feature: nothing to say');
assert.match(voiceRouteText({ available: true, provider: 'gemini', model: 'gemini-3.5-flash-lite' }, { env_names: ['VOICE_MODEL'] }), /Google Gemini \(free/);
assert.match(voiceRouteText({ available: true, provider: 'gemini' }, { env_names: ['VOICE_MODEL'] }), /VOICE_MODEL setting is not used on Gemini/, 'Gemini ignores VOICE_MODEL, and says so');
assert.match(voiceRouteText({ available: true, provider: 'openrouter', model: 'x/y' }, { env_names: ['ADMIN_PIN', 'VOICE_MODEL'] }), /own VOICE_MODEL/);
assert.match(voiceRouteText({ available: true, provider: 'openrouter', model: 'x/y' }, { env_names: ['ADMIN_PIN'] }), /default model \(x\/y\)/);
assert.match(voiceRouteText({ available: true, provider: 'openrouter', model: 'x/y' }, undefined), /default model \(x\/y\)/, 'no backend info: default');
const all = [voiceRouteText({ available: true, provider: 'openrouter' }, { env_names: ['VOICE_MODEL'] }), voiceRouteText({ available: true, provider: 'gemini' }, {})].join(' ');
assert.ok(!/anthropic|openai\/|gpt|claude/i.test(all), 'no model names or keys are written here');
console.log('  ok  teacher line: Gemini (free), OpenRouter with the student’s VOICE_MODEL, OpenRouter default; nothing secret shown');
