// class_ai: use the class AI with two lines. This file belongs to the platform kit (do not edit it; it is refreshed with the kit).
//
//   import { ask } from './class_ai.mjs';
//   const answer = await ask('Summarise this shopping list');
//
// How it works: when your teacher switches the class AI on, the platform gives your function three environment variables:
// OPENAI_API_KEY, OPENAI_BASE_URL and AI_MODEL. This module reads them and makes one request in the OpenAI-compatible format.
// Node 22 built-ins only: nothing to install.
//
//   ask(prompt, { system, maxTokens = 300, model, default, temperature, mock })   -> the answer text
//   chat(messages, { maxTokens = 300, model, default, temperature, mock })       -> the same, with a full message list
//                                                                                (for a conversation: send the earlier messages too,
//                                                                                the AI remembers nothing between calls)
//   available()                                                            -> true when an answer can be produced (real or mock)
//
// On any problem it throws AiError with a friendly `.message` you can show, and a `.kind`: not_enabled (the class AI is not on for
// you), cap_reached (your allowance is used up), disabled (your key is off), busy (overloaded, tried twice), timeout,
// provider_error. Pass { default: 'Sorry, the AI is resting' } to get that text back instead of an exception.
// On your own computer, with no variables set, it answers from a tiny OFFLINE MOCK (starts with "[mock AI]"), so you can build and
// test without spending anything. AI_MOCK=1 forces the mock anywhere, AI_MOCK=0 forbids it.
// { mock: '...' } is the text the offline mock returns instead of "[mock AI] ...". Use it when your code expects a certain shape,
// for example { mock: '[{"question": "2+2?", "choices": ["3", "4"], "answer": 1}]' }, so you can try the whole flow offline.
// It is ignored when the real AI answers.
//
// Safety: the key is never printed, logged or put in an error. Never send it to the browser: your page calls your own /api route.
// Your prompts go to the AI provider, so do not send private data.

export const KIT_HELPER_VERSION = 1;
const DEFAULT_TIMEOUT_SECONDS = 20;

export const MESSAGES = {
  not_enabled: 'The class AI is not switched on for you yet. Ask your teacher.',
  cap_reached: 'Your AI allowance is used up. Ask your teacher for more, or try again when it resets.',
  disabled: 'Your AI key is switched off (the AI was turned off, or your token is not active).',
  busy: 'The AI is busy right now. Please try again in a moment.',
  timeout: 'The AI took too long to answer. Try a shorter question.',
  provider_error: 'The AI service had a problem. Please try again.',
};

export class AiError extends Error {
  constructor(kind, status = 0) {
    super(MESSAGES[kind]);
    this.name = 'AiError';
    this.kind = kind;
    this.status = status;
  }
}

function mockMode() {
  const flag = process.env.AI_MOCK;
  if (flag === '1' || flag === 'true') return true;
  if (flag === '0' || flag === 'false') return false;
  // No key and not running on the platform (AWS Lambda): assume a laptop.
  return !process.env.OPENAI_API_KEY && !process.env.AWS_LAMBDA_FUNCTION_NAME;
}

export function available() {
  return mockMode() || Boolean(process.env.OPENAI_API_KEY && process.env.OPENAI_BASE_URL);
}

function classify(status) {
  if (status === 401) return 'disabled';
  if (status === 402 || status === 403) return 'cap_reached';
  if (status === 429 || status >= 500) return 'busy';
  return 'provider_error';
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function post(url, key, payload, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) throw new AiError(classify(response.status), response.status);
    return JSON.parse((await response.text()).trim());
  } catch (error) {
    if (error instanceof AiError) throw error;
    if (error?.name === 'AbortError') throw new AiError('timeout');
    throw new AiError('provider_error'); // network failure or an unreadable reply
  } finally {
    clearTimeout(timer);
  }
}

export async function chat(messages, { maxTokens = 300, model, default: fallback, temperature, mock } = {}) {
  try {
    if (mockMode()) {
      if (mock !== undefined) return String(mock);
      const last = [...messages].reverse().find((m) => m.role === 'user');
      return `[mock AI] You asked: ${String(last?.content ?? '').slice(0, 120)}`;
    }
    const key = process.env.OPENAI_API_KEY;
    const base = process.env.OPENAI_BASE_URL;
    if (!key || !base) throw new AiError('not_enabled');
    const payload = { model: model || process.env.AI_MODEL || '', messages, max_tokens: maxTokens };
    if (temperature !== undefined) payload.temperature = temperature;
    const url = `${base.replace(/\/+$/, '')}/chat/completions`;
    const timeoutMs = Number(process.env.AI_TIMEOUT || DEFAULT_TIMEOUT_SECONDS) * 1000;
    const started = Date.now();
    let data;
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        data = await post(url, key, payload, Math.max(timeoutMs - (Date.now() - started), 1000));
        break;
      } catch (error) {
        if (error.kind !== 'busy' || attempt === 2 || Date.now() - started > timeoutMs - 3000) throw error;
        await sleep(600);
      }
    }
    const content = data?.choices?.[0]?.message?.content;
    if (data?.choices?.[0]?.message == null) throw new AiError('provider_error'); // no usable answer in the reply
    return content == null ? '' : String(content);
  } catch (error) {
    if (!(error instanceof AiError)) throw error;
    console.log(`class_ai: ${error.kind} (status ${error.status})`); // kind and status only: never the key, prompt or answer
    if (fallback !== undefined) return fallback;
    throw error;
  }
}

export async function ask(prompt, { system, maxTokens = 300, model, default: fallback, temperature, mock } = {}) {
  const messages = [...(system ? [{ role: 'system', content: system }] : []), { role: 'user', content: String(prompt) }];
  return chat(messages, { maxTokens, model, default: fallback, temperature, mock });
}
