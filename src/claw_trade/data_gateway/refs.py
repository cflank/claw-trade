from __future__ import annotations

NORMALIZED_DATASET_REF_PREFIX = "dataset://normalized/"
PROVIDER_ATTEMPT_REF_PREFIX = "attempt://data-provider/"

LEGACY_NORMALIZED_REF_PREFIXES = (
    "normalized://mongo/normalized_datasets/",
    "mongo://normalized_datasets/",
)
_LEGACY_PROVIDER_ATTEMPT_PREFIX = "attempt://mongo/provider_attempts/"


def is_normalized_dataset_ref(ref: str) -> bool:
    return ref.strip().startswith(NORMALIZED_DATASET_REF_PREFIX)


def is_legacy_normalized_dataset_ref(ref: str) -> bool:
    text = ref.strip()
    return any(text.startswith(prefix) for prefix in LEGACY_NORMALIZED_REF_PREFIXES)


def normalize_legacy_normalized_dataset_ref(ref: object) -> str:
    text = str(ref).strip()
    if is_legacy_normalized_dataset_ref(text):
        return normalize_normalized_dataset_ref(text)
    return text


def normalize_normalized_dataset_ref(
    ref: str,
    *,
    market: str = "CN_A",
    dataset: str = "daily_bar",
    granularity: str = "daily",
) -> str:
    text = ref.strip()
    if text.startswith(NORMALIZED_DATASET_REF_PREFIX):
        return text
    for prefix in LEGACY_NORMALIZED_REF_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return f"{NORMALIZED_DATASET_REF_PREFIX}{market}/{dataset}/{granularity}/{_stable_ref_tail(text)}"


def normalize_provider_attempt_ref(ref: str) -> str:
    text = ref.strip()
    if text.startswith(PROVIDER_ATTEMPT_REF_PREFIX):
        return text
    if text.startswith(_LEGACY_PROVIDER_ATTEMPT_PREFIX):
        text = text[len(_LEGACY_PROVIDER_ATTEMPT_PREFIX) :]
    elif text.startswith("attempt://"):
        text = text[len("attempt://") :]
    return f"{PROVIDER_ATTEMPT_REF_PREFIX}{_stable_ref_tail(text)}"


def _stable_ref_tail(value: str) -> str:
    text = value.strip()
    if not text:
        return "unknown"
    return text.replace("/", ":")
