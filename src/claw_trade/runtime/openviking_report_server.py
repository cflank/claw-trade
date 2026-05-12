"""OpenViking server entry point for claw-trade report runtime."""

from __future__ import annotations

import os
import sys
from typing import Any

from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV


_DISABLED_MESSAGE = (
    "OpenViking vectorization is disabled in claw-trade report runtime; "
    "report artifact writes must pass vectorize=false."
)


class _DisabledEmbedder:
    provider = "disabled"
    model_name = "claw-trade-report-no-vectorization"
    is_sparse = False

    def embed(self, text: str, is_query: bool = False) -> Any:
        raise RuntimeError(_DISABLED_MESSAGE)

    def embed_batch(self, texts: list[str], is_query: bool = False) -> list[Any]:
        raise RuntimeError(_DISABLED_MESSAGE)

    async def embed_async(self, text: str, is_query: bool = False) -> Any:
        raise RuntimeError(_DISABLED_MESSAGE)

    async def embed_batch_async(self, texts: list[str], is_query: bool = False) -> list[Any]:
        raise RuntimeError(_DISABLED_MESSAGE)

    def close(self) -> None:
        return None


def _preparse_config(argv: list[str]) -> None:
    for index, arg in enumerate(argv):
        if arg == "--config" and index + 1 < len(argv):
            os.environ[OPENVIKING_CONFIG_ENV] = argv[index + 1]
            return
        if arg.startswith("--config="):
            os.environ[OPENVIKING_CONFIG_ENV] = arg.split("=", 1)[1]
            return


def _install_report_runtime_patches() -> None:
    from openviking.core.directories import DirectoryInitializer
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking.storage.vikingdb_manager import VikingDBManager
    from openviking_cli.utils import get_logger
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig

    logger = get_logger(__name__)

    def get_disabled_embedder(self: EmbeddingConfig) -> _DisabledEmbedder:
        return _DisabledEmbedder()

    async def skip_directory_l0_l1_vectors(
        self: DirectoryInitializer,
        uri: str,
        parent_uri: str | None,
        defn: Any,
        owner_space: str,
        ctx: Any,
    ) -> None:
        return None

    def setup_disabled_standard_queues(
        self: QueueManager,
        vector_store: Any,
        start: bool = True,
    ) -> None:
        self.get_queue(self.EMBEDDING, allow_create=True)
        self.get_queue(self.SEMANTIC, allow_create=True)
        logger.info("claw-trade report runtime disabled OpenViking semantic/vector queues")

    def start_disabled_queues(self: QueueManager) -> None:
        self._started = True
        logger.info("claw-trade report runtime skipped OpenViking queue workers")

    async def fail_embedding_enqueue(self: VikingDBManager, embedding_msg: Any) -> bool:
        raise RuntimeError(_DISABLED_MESSAGE)

    EmbeddingConfig.get_embedder = get_disabled_embedder
    DirectoryInitializer._ensure_directory_l0_l1_vectors = skip_directory_l0_l1_vectors
    QueueManager.setup_standard_queues = setup_disabled_standard_queues
    QueueManager.start = start_disabled_queues
    VikingDBManager.enqueue_embedding_msg = fail_embedding_enqueue


def main() -> None:
    _preparse_config(sys.argv)
    _install_report_runtime_patches()

    from openviking_cli.server_bootstrap import main as openviking_server_main

    openviking_server_main()


if __name__ == "__main__":
    main()
