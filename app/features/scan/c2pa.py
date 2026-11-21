"""Read Content Credentials (C2PA) and tell whether they declare an AI-generated image."""

import io
import json
import logging

log = logging.getLogger("artiko.c2pa")

# IPTC digital source types that C2PA uses for generative AI output.
AI_SOURCE_TYPES = ("trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia")


def ai_generator(data: bytes, mime: str) -> str | None:
    """The AI tool named by the file's Content Credentials, or None (no manifest, or not AI)."""
    try:
        import c2pa

        with c2pa.Reader(mime, io.BytesIO(data)) as reader:
            manifest_store = json.loads(reader.json())
    except Exception as e:  # noqa: BLE001  no manifest is the normal case, and the library raises
        log.debug("no C2PA manifest: %s", e)
        return None
    if not any(t in json.dumps(manifest_store) for t in AI_SOURCE_TYPES):
        return None
    active = manifest_store.get("manifests", {}).get(manifest_store.get("active_manifest"), {})
    info = active.get("claim_generator_info") or [{}]
    return info[0].get("name") or active.get("claim_generator") or "an AI tool"
