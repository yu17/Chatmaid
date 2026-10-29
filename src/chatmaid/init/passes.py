"""Init passes 3 (style profile) and 4 (embeddings). Called through InitPipeline."""

import hashlib
import logging
from typing import TYPE_CHECKING

from chatmaid.init.chunking import detect_language
from chatmaid.llm import prompts
from chatmaid.llm.base import LLMError
from chatmaid.stop import StopRequested

if TYPE_CHECKING:
    from chatmaid.init.pipeline import InitOptions, InitPipeline

log = logging.getLogger(__name__)

MIN_STYLE_SAMPLES = 5
STYLE_MAX_PER_LANGUAGE = 60
STYLE_MIN_CHARS = 20
STYLE_SAMPLE_CHARS = 1500
EMBED_BATCH = 32
# Enough newest owner messages to fill 60 per language even when many are short.
_OWNER_SCAN = 2000


def run_style(
    pipeline: "InitPipeline", opts: "InitOptions", *, min_samples: int = MIN_STYLE_SAMPLES
) -> dict[str, int]:
    """Write a "default"-tier style profile per language from the owner's newest messages."""
    groups: dict[str, list[tuple[str, str]]] = {}
    for ref in pipeline.store.owner_message_refs(limit=_OWNER_SCAN):
        if all(len(g) >= STYLE_MAX_PER_LANGUAGE for g in groups.values()) and len(groups) >= 2:
            break
        try:
            text = pipeline.connector.fetch(ref).text.strip()
        except KeyError:  # moved or deleted in the mail client since ingest
            continue
        if len(text) < STYLE_MIN_CHARS:
            continue
        group = groups.setdefault(detect_language(text), [])
        if len(group) < STYLE_MAX_PER_LANGUAGE:
            group.append((ref.key(), text))

    prompt = prompts.load("style_profile")
    model = pipeline.cfg.lmstudio.model
    done: dict[str, int] = {}
    eligible = [(lang, samples) for lang, samples in groups.items() if len(samples) >= min_samples]
    progress = pipeline._progress("style", len(eligible))
    for n, (language, samples) in enumerate(eligible):
        progress.update(n, language)
        user = "\n".join(
            f"{i}. {text[:STYLE_SAMPLE_CHARS]}" for i, (_, text) in enumerate(samples, start=1)
        )
        try:
            out = pipeline._complete_json(prompt, user, model)
        except StopRequested:
            pipeline.stopped = True
            break
        except LLMError as e:
            log.warning("style profile for %s failed: %s", language, e)
            continue
        pipeline.store.set_style_profile(
            language, "default", out["profile"], [key for key, _ in samples]
        )
        done[language] = len(samples)
    head = "style stopped" if pipeline.stopped else "style done"
    progress.finish(f"{head}: {len(done)} of {len(eligible)} languages profiled")
    return done


def run_embed(pipeline: "InitPipeline", opts: "InitOptions") -> int:
    """Embed conversation summaries and memories whose text is new or changed."""
    store = pipeline.store
    items = [
        ("conversation", r["id"], r["summary"])
        for r in store.conversations(needing_summary=False, exclude_bulk=False)
        if r["summary"]
    ]
    items += [("memory", r["id"], r["text"]) for r in store.memories()]

    pending = []
    for kind, ref_id, text in items:
        digest = hashlib.sha256(text.encode()).hexdigest()
        if store.embedding_hash(kind, ref_id) != digest:
            pending.append((kind, ref_id, text, digest))

    dim = pipeline.cfg.lmstudio.embedding_dim
    model = pipeline.cfg.lmstudio.model_embedding
    starts = range(0, len(pending), EMBED_BATCH)
    progress = pipeline._progress("embed", len(starts))
    embedded = 0
    for n, start in enumerate(starts):
        progress.update(n, f"{embedded} of {len(pending)} texts")
        batch = pending[start : start + EMBED_BATCH]
        try:
            vectors = pipeline.llm.embed([text for _, _, text, _ in batch], model=model)
        except StopRequested:
            pipeline.stopped = True
            break
        if len(vectors) != len(batch) or any(len(v) != dim for v in vectors):
            raise LLMError(f"embedding model returned vectors that are not {dim}-dimensional")
        for (kind, ref_id, _, digest), vector in zip(batch, vectors):
            store.upsert_embedding(kind, ref_id, digest, vector)
        embedded += len(batch)
    head = "embed stopped" if pipeline.stopped else "embed done"
    progress.finish(f"{head}: {embedded} of {len(pending)} texts embedded")
    return embedded
