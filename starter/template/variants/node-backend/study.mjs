// EXAMPLE routes for the AI patterns (see PATTERNS.md): answer from books, feedback, and a tool-using agent that needs your approval before it saves.
// Read it, copy what your app needs, and delete the rest (and the `route(` lines in index.mjs) when you do not need it. It is YOUR file: edit it freely.
//
//   POST /study          { question, collections? }        -> answer from the books, with sources ("The books do not say." when unsure)
//   POST /feedback       { item, rating: 'up'|'down', comment? }   -> thumbs from your visitors
//   GET  /feedback                                         -> { up, down, recent_down }
//   POST /agent          { goal }                          -> a tool-using agent: status, answer, trace (and run_id when it waits for you)
//   POST /agent/approve  { run_id, approve }               -> continue a run that was waiting for approval
//
// Everything works on your computer too (npm run dev): books are read from .class-knowledge/ and the agent follows the scripted mockScript replies below, so it is free.
// On the platform it needs the teacher to have switched class knowledge (books), class storage (cache, feedback, waiting runs) and the class AI on.
import { randomBytes } from 'node:crypto';
import * as agent from './class_agent.mjs';
import { AiError } from './class_ai.mjs';
import * as kb from './class_knowledge.mjs';
import * as quality from './class_quality.mjs';
import * as storage from './class_storage.mjs';

const BOOKS = undefined; // undefined searches everything you can see; or a fixed list you choose, e.g. ['library/science']. NEVER take this from the visitor.
const RUNS = 'agent-runs';
const RUN_ID_LENGTH = 24;

/** What the agent may do. Keep it to 2 to 4 small, safe tools. A tool that changes something needs a human's approval first. */
function tools() {
  return [
    agent.tool('search_books', 'Find passages in the books', { query: 'text' },
      async (a) => (await kb.search(a.query, BOOKS, 3)).hits.map((h) => ({ title: h.title, page: h.page, text: h.text.slice(0, 300) }))),
    agent.tool('save_note', 'Save a short note for the student', { text: 'text' },
      async (a) => { await storage.put('notes', String(Date.now()).padStart(13, '0'), { text: a.text.slice(0, 200) }); return 'saved'; }, { needsApproval: true }),
  ];
}

/** What the agent 'hears' on your computer, where the class AI is only an offline mock: a fixed script, so you can try the whole flow for free. */
export function mockScript(goal) {
  if (goal.toLowerCase().includes('save')) return [{ tool: 'save_note', args: { text: goal.slice(0, 100) } }, { final: 'I saved your note.' }];
  return [{ tool: 'search_books', args: { query: goal.slice(0, 100) } }, { final: `[mock agent] I searched the books for: ${goal.slice(0, 60)}` }];
}

const fail = (error) => [503, { error: error.kind || 'error', message: error.message }];

/** What the page may see: never the internal state. A run that waits for approval is kept in class storage under a run id the page sends back. */
async function publish(result) {
  const { state, ...visible } = result;
  if (visible.status === 'needs_approval') {
    visible.run_id = randomBytes(RUN_ID_LENGTH / 2).toString('hex');
    await storage.put(RUNS, visible.run_id, state);
  }
  return visible;
}

/** [status, data] for a route of this file, or null when the path is not one of them. */
export async function route(method, path, body) {
  try {
    if (method === 'POST' && path === '/study') {
      const question = String(body.question ?? '').trim();
      if (!question || question.length > 500) return [400, { error: 'bad_request', message: 'Send a question of 1 to 500 characters.' }];
      const reply = await quality.answerCached(question, { collections: BOOKS, days: 7 }); // the same question is answered once; a changed book is never served an old answer
      return [200, { ...reply, answer_id: quality.cacheKey('answer', question) }];
    }
    if (method === 'POST' && path === '/feedback') {
      if (body.rating !== 'up' && body.rating !== 'down') return [400, { error: 'bad_request', message: 'rating must be "up" or "down".' }];
      return [200, { saved: await quality.feedback(String(body.item ?? '').slice(0, 80), body.rating, String(body.comment ?? '')) }];
    }
    if (method === 'GET' && path === '/feedback') return [200, await quality.feedbackSummary()];
    if (method === 'POST' && path === '/agent') {
      const goal = String(body.goal ?? '').trim();
      if (!goal || goal.length > 300) return [400, { error: 'bad_request', message: 'Send a goal of 1 to 300 characters.' }];
      return [200, await publish(await agent.run(goal, tools(), { maxSteps: 5, maxSeconds: 16, mock: mockScript(goal) }))];
    }
    if (method === 'POST' && path === '/agent/approve') {
      const runId = String(body.run_id ?? '');
      if (runId.length !== RUN_ID_LENGTH || !/^[a-z0-9]+$/i.test(runId)) return [400, { error: 'bad_request', message: 'Unknown run.' }];
      const state = await storage.get(RUNS, runId, null);
      if (!state) return [404, { error: 'not_found', message: 'That run is not waiting any more.' }];
      await storage.del(RUNS, runId); // a run is approved or declined once
      return [200, await publish(await agent.resume(state, Boolean(body.approve), tools(), { maxSteps: 5, mock: [{ final: 'Done.' }] }))];
    }
  } catch (error) {
    if (error instanceof AiError || error instanceof kb.KnowledgeError || error instanceof storage.StorageError) return fail(error); // not switched on, allowance used, full...
    throw error;
  }
  return null;
}
