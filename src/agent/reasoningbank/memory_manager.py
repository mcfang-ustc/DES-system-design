"""
ReasoningBank Memory Manager

This module implements the central memory storage and management system for
the ReasoningBank framework.
"""

from typing import List, Optional, Dict, Callable
import json
import os
import threading
from pathlib import Path
import logging

from .memory import MemoryItem, MemoryQuery

logger = logging.getLogger(__name__)


class ReasoningBank:
    """
    Central memory storage and retrieval system for agent reasoning strategies.

    ReasoningBank maintains a collection of MemoryItem objects that represent
    distilled reasoning strategies extracted from past experiences. It supports:
    - Adding new memories with automatic embedding
    - Retrieving relevant memories via similarity search
    - Persisting memories to disk for long-term storage
    - Simple consolidation (currently append-only)

    Attributes:
        memories: List of all stored MemoryItem objects
        embedding_func: Optional function to compute embeddings (query_text) -> List[float]
        max_items: Maximum number of memories to store (oldest removed if exceeded)
    """

    def __init__(
        self,
        embedding_func: Optional[Callable[[str], List[float]]] = None,
        max_items: int = 1000,
    ):
        """
        Initialize ReasoningBank.

        Args:
            embedding_func: Function that takes a string and returns an embedding vector
            max_items: Maximum capacity of the memory bank
        """
        self.memories: List[MemoryItem] = []
        self.embedding_func = embedding_func
        self.max_items = max_items
        # Protect in-memory mutations and on-disk persistence from concurrent requests
        # (e.g., async feedback processing with multiple worker threads).
        self._lock = threading.RLock()
        logger.info(f"Initialized ReasoningBank with max_items={max_items}")

    def add_memory(self, memory: MemoryItem, compute_embedding: bool = True) -> None:
        """
        Add a new memory item to the bank.

        Args:
            memory: MemoryItem to add
            compute_embedding: Whether to compute embedding if not already present

        Raises:
            ValueError: If memory validation fails
        """
        # Validate memory
        if not isinstance(memory, MemoryItem):
            raise ValueError("memory must be a MemoryItem instance")

        # Compute embedding outside lock (may be a network call); the item isn't in the bank yet.
        if compute_embedding and self.embedding_func and memory.embedding is None:
            try:
                # Use title + description for embedding
                embed_text = f"{memory.title}. {memory.description}"
                memory.embedding = self.embedding_func(embed_text)
                logger.debug(f"Computed embedding for memory: {memory.title}")
            except Exception as e:
                logger.warning(f"Failed to compute embedding: {e}")
                # Continue without embedding

        # Add to collection (thread-safe)
        with self._lock:
            self.memories.append(memory)
            logger.info(f"Added memory '{memory.title}' (total: {len(self.memories)})")

            # Enforce max_items limit (remove oldest)
            if len(self.memories) > self.max_items:
                removed = self.memories.pop(0)
                logger.info(
                    f"Removed oldest memory '{removed.title}' (limit: {self.max_items})"
                )

    def add_memories(
        self, memories: List[MemoryItem], compute_embeddings: bool = True
    ) -> None:
        """
        Add multiple memory items in batch.

        Args:
            memories: List of MemoryItem objects to add
            compute_embeddings: Whether to compute embeddings
        """
        for memory in memories:
            self.add_memory(memory, compute_embedding=compute_embeddings)

    def get_all_memories(self) -> List[MemoryItem]:
        """
        Get all stored memories.

        Returns:
            List of all MemoryItem objects
        """
        with self._lock:
            return self.memories.copy()

    def get_memory_by_title(self, title: str) -> Optional[MemoryItem]:
        """
        Retrieve a memory by its title.

        Args:
            title: Exact title to match

        Returns:
            MemoryItem if found, None otherwise
        """
        with self._lock:
            for memory in self.memories:
                if memory.title == title:
                    return memory
            return None

    def filter_memories(self, filters: Dict) -> List[MemoryItem]:
        """
        Filter memories by metadata criteria.

        Args:
            filters: Dictionary of criteria (e.g., {"is_from_success": True})

        Returns:
            List of matching MemoryItem objects
        """
        with self._lock:
            filtered = []
            for memory in self.memories:
                match = True
                for key, value in filters.items():
                    # Check top-level attributes
                    if hasattr(memory, key):
                        if getattr(memory, key) != value:
                            match = False
                            break
                    # Check metadata
                    elif key in memory.metadata:
                        if memory.metadata[key] != value:
                            match = False
                            break
                    else:
                        match = False
                        break

                if match:
                    filtered.append(memory)

        logger.debug(
            f"Filtered {len(filtered)}/{len(self.memories)} memories with {filters}"
        )
        return filtered

    def consolidate(self, new_memories: List[MemoryItem]) -> None:
        """
        Consolidate new memories into the bank.

        Currently implements simple append strategy. Future versions may include:
        - Duplicate detection and merging
        - Clustering similar memories
        - Forgetting low-utility memories

        Args:
            new_memories: List of MemoryItem objects to consolidate
        """
        logger.info(f"Consolidating {len(new_memories)} new memories")
        self.add_memories(new_memories, compute_embeddings=True)
        logger.info(f"Consolidation complete. Total memories: {len(self.memories)}")

    def save(self, filepath: str) -> None:
        """
        Persist memory bank to disk in JSON format.

        Args:
            filepath: Path to save file (will create parent directories if needed)

        Raises:
            IOError: If file cannot be written
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # Serialize memories
            data = {
                "version": "1.0",
                "max_items": self.max_items,
                "num_memories": len(self.memories),
                "memories": [memory.to_dict() for memory in self.memories],
            }

            # Atomic write to prevent partial files during crashes / concurrent writes.
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)

            logger.info(f"Saved {len(self.memories)} memories to {filepath}")

    def load(self, filepath: str) -> None:
        """
        Load memory bank from disk.

        Args:
            filepath: Path to load file

        Raises:
            FileNotFoundError: If file does not exist
            JSONDecodeError: If file is not valid JSON
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Memory file not found: {filepath}")

        with self._lock:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Validate version (future-proofing)
            version = data.get("version", "1.0")
            if version != "1.0":
                logger.warning(f"Loading memory file with version {version} (expected 1.0)")

            # Load memories
            memories_data = data.get("memories", [])
            self.memories = [MemoryItem.from_dict(m) for m in memories_data]

            # Update max_items if specified
            if "max_items" in data:
                self.max_items = data["max_items"]

            logger.info(f"Loaded {len(self.memories)} memories from {filepath}")

    def delete_by_title(self, title: str) -> bool:
        """
        Delete a memory by its title.

        Args:
            title: Exact title to match

        Returns:
            True if memory was deleted, False if not found
        """
        with self._lock:
            original_count = len(self.memories)
            self.memories = [m for m in self.memories if m.title != title]
            deleted = len(self.memories) < original_count

        if deleted:
            logger.info(f"Deleted memory with title: {title}")
        else:
            logger.warning(f"No memory found with title: {title}")

        return deleted

    def delete_by_recommendation_id(self, recommendation_id: str) -> int:
        """
        Delete all memories associated with a specific recommendation.

        This is used when updating experimental feedback to remove old memories
        before extracting new ones.

        Args:
            recommendation_id: Recommendation ID to filter by

        Returns:
            Number of memories deleted
        """
        with self._lock:
            original_count = len(self.memories)
            self.memories = [
                m for m in self.memories
                if m.metadata.get("recommendation_id") != recommendation_id
            ]
            deleted_count = original_count - len(self.memories)

        logger.info(
            f"Deleted {deleted_count} memories for recommendation {recommendation_id} "
            f"(remaining: {len(self.memories)})"
        )
        return deleted_count

    def clear(self) -> None:
        """Clear all memories from the bank."""
        with self._lock:
            count = len(self.memories)
            self.memories = []
        logger.info(f"Cleared {count} memories from ReasoningBank")

    def get_statistics(self) -> Dict:
        """
        Get statistics about the memory bank.

        Returns:
            Dictionary with statistics
        """
        with self._lock:
            if not self.memories:
                return {
                    "total_memories": 0,
                    "from_success": 0,
                    "from_failure": 0,
                    "with_embeddings": 0,
                }

            success_count = sum(1 for m in self.memories if m.is_from_success)
            with_embedding = sum(1 for m in self.memories if m.embedding is not None)

            return {
                "total_memories": len(self.memories),
                "from_success": success_count,
                "from_failure": len(self.memories) - success_count,
                "with_embeddings": with_embedding,
                "max_capacity": self.max_items,
                "utilization": f"{len(self.memories) / self.max_items * 100:.1f}%",
            }

    def __len__(self) -> int:
        """Return number of memories in the bank."""
        with self._lock:
            return len(self.memories)

    def __repr__(self) -> str:
        """String representation of ReasoningBank."""
        stats = self.get_statistics()
        return (
            f"ReasoningBank(total={stats['total_memories']}, "
            f"success={stats['from_success']}, "
            f"failure={stats['from_failure']})"
        )


# Example usage
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Create sample embedding function (mock)
    def mock_embedding(text: str) -> List[float]:
        # Simple mock: use hash to generate pseudo-random embedding
        import hashlib

        hash_val = int(hashlib.md5(text.encode()).hexdigest(), 16)
        return [(hash_val >> i) % 100 / 100.0 for i in range(8)]

    # Initialize bank
    bank = ReasoningBank(embedding_func=mock_embedding, max_items=10)

    # Add some sample memories
    memory1 = MemoryItem(
        title="Prioritize H-Bond Analysis",
        description="Analyze hydrogen bonding first for polar materials",
        content="For dissolving polar polymers, H-bond strength is the primary factor.",
        source_task_id="task_001",
        is_from_success=True,
    )

    memory2 = MemoryItem(
        title="Avoid Incompatible Pairs",
        description="Certain HBD-HBA combinations cause decomposition",
        content="Quaternary ammonium salts + acidic HBDs can lead to decomposition.",
        source_task_id="task_002",
        is_from_success=False,
    )

    bank.add_memory(memory1)
    bank.add_memory(memory2)

    # Get statistics
    print("\nBank Statistics:")
    print(json.dumps(bank.get_statistics(), indent=2))

    # Save to file
    save_path = "data/memory/test_reasoningbank.json"
    bank.save(save_path)
    print(f"\nSaved to {save_path}")

    # Load from file
    new_bank = ReasoningBank(embedding_func=mock_embedding)
    new_bank.load(save_path)
    print(f"\nLoaded bank: {new_bank}")

    # Filter memories
    successes = new_bank.filter_memories({"is_from_success": True})
    print(f"\nSuccessful memories: {len(successes)}")
    for mem in successes:
        print(f"  - {mem.title}")
