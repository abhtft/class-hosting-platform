"""Real-service probe for the knowledge service's embedding client. Run it in YOUR terminal; the keys stay in your environment and are never printed.

  PowerShell:   $env:GOOGLE_API_KEY = "<your Gemini key>"
                $env:OPENROUTER_API_KEY = "<an OpenRouter inference key>"      (optional: only to compare the two providers)
                python .temp/probe-embeddings.py

It embeds a handful of study-book sentences and questions through lambda/embeddings.py exactly as the platform will, and prints only numbers:
sizes, unit length, which question lands closest to which passage, document-versus-question task types, and (if you gave the OpenRouter key) how
close the same text is through the two providers. Cost: a few hundred tokens, a fraction of a cent.
"""
import math
import os
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "lambda"))
os.environ.setdefault("AWS_REGION", "us-east-1")
import embeddings as em  # noqa: E402
import gemini  # noqa: E402

google, openrouter = os.environ.get("GOOGLE_API_KEY", ""), os.environ.get("OPENROUTER_API_KEY", "")
if not google:
    sys.exit("Set GOOGLE_API_KEY in this terminal first (the value is never printed).")
gemini._secret.update(value=google, at=time.time() + 10 ** 6)
if openrouter:
    em._openrouter_secret.update(value=openrouter, at=time.time() + 10 ** 6)

PASSAGES = [
    "Photosynthesis is the process by which green plants make their own food using sunlight, water and carbon dioxide.",
    "Newton's second law states that force equals mass multiplied by acceleration.",
    "Akbar was the greatest of the Mughal emperors and built Fatehpur Sikri.",
    "Water evaporates from oceans, condenses into clouds and falls back as rain: this is the water cycle.",
]
QUESTIONS = ["How do plants prepare their food?", "What is the formula for force?", "Which emperor founded Fatehpur Sikri?", "Why does it rain?", "Who won the football world cup?"]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def show(label, model, dims, task_types=False):
    started = time.time()
    p = em.embed(PASSAGES, model=model, dims=dims, task="document", task_types=task_types)
    q = em.embed(QUESTIONS, model=model, dims=dims, task="query", task_types=task_types)
    took = time.time() - started
    norm_ok = all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-6 for v in p["vectors"] + q["vectors"])
    print(f"\n== {label}: model {model}, {dims} numbers, task types {'on' if task_types else 'off'}  ({took:.2f} s, {p['tokens'] + q['tokens']} tokens, {p['cost_micro'] + q['cost_micro']} micro-dollars)")
    print(f"   vector length {len(p['vectors'][0])}, all unit length: {norm_ok}")
    for question, qv in zip(QUESTIONS, q["vectors"]):
        sims = [cos(qv, pv) for pv in p["vectors"]]
        best = max(range(len(sims)), key=sims.__getitem__)
        print(f"   {question:<42} best passage {best}  cosine {sims[best]:.3f}  (others {', '.join(f'{s:.2f}' for i, s in enumerate(sims) if i != best)})")
    return p["vectors"], q["vectors"]


try:
    g768 = show("Google", "gemini-embedding-001", 768)
    show("Google", "gemini-embedding-001", 768, task_types=True)
    show("Google", "gemini-embedding-001", 1536)
except em.EmbeddingError as e:
    sys.exit(f"Google answered: HTTP {e.status}: {e}")
if openrouter:
    try:
        o768 = show("OpenRouter", "google/gemini-embedding-001", 768)
        same = [cos(a, b) for a, b in zip(g768[0], o768[0])]
        print("\n== the same passages through both providers (no task types): cosine", ", ".join(f"{s:.3f}" for s in same), "(close to 1.000 means the vectors are interchangeable)")
    except em.EmbeddingError as e:
        print(f"\nOpenRouter answered: HTTP {e.status}: {e}")
else:
    print("\n(no OPENROUTER_API_KEY: the provider comparison was skipped)")
print("\nDone. Paste this output back; it contains no keys.")
