"""
Example: Using DESAgent with ReasoningBank for DES Formulation Design

This script demonstrates how to use the complete ReasoningBank framework
to solve DES formulation tasks with real API clients.

Prerequisites:
    - Set DASHSCOPE_API_KEY environment variable (or OPENAI_API_KEY)
    - Or configure API keys in .env file
    - Optional: Build LargeRAG index for literature retrieval
      (python src/tools/largerag/examples/1_build_index.py)

Components Status:
    ✅ LLM Client: Real (DashScope/OpenAI)
    ✅ Embedding Client: Real (DashScope/OpenAI)
    ✅ ReasoningBank: Real (memory system)
    ✅ LargeRAG: Real (literature retrieval)
    ✅ CoreRAG: Real (ontology-based theoretical knowledge) - NEW!
    ⚠️  Experimental Feedback: Mock (LLM-as-a-judge)
"""

import sys
import os
import logging
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent.reasoningbank import (
    ReasoningBank,
    MemoryRetriever,
    MemoryExtractor,
    LLMJudge,
)
from agent.des_agent import DESAgent
from agent.utils import LLMClient, EmbeddingClient
from agent.tools import create_largerag_adapter, create_corerag_adapter


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = None) -> dict:
    """Load configuration from YAML file"""
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "reasoningbank_config.yaml"

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    return config


# def create_mock_corerag_client():
#     """Create a mock CoreRAG client - NOW REPLACED WITH REAL CORERAG"""
#     class MockCoreRAG:
#         def query(self, query_dict):
#             return {
#                 "theory": "Cellulose dissolution requires disrupting hydrogen bonds between cellulose chains. DES with strong H-bond donors/acceptors can intercalate and solvate cellulose.",
#                 "key_factors": ["H-bond strength", "molar ratio", "temperature"],
#                 "recommended_hbd": ["Urea", "Thiourea", "Acetamide"],
#                 "recommended_hba": ["Choline chloride", "Quaternary ammonium salts"]
#             }
#     return MockCoreRAG()


# def create_mock_largerag_client():
#     """Create a mock LargeRAG client - NOW REPLACED WITH REAL LARGERAG"""
#     class MockLargeRAG:
#         def query(self, query_dict):
#             return {
#                 "papers": [
#                     {
#                         "title": "Deep eutectic solvents for cellulose dissolution",
#                         "finding": "ChCl:Urea (1:2) achieves 5-10% cellulose dissolution at 80°C",
#                         "year": 2020
#                     },
#                     {
#                         "title": "Tuning DES properties for biopolymer processing",
#                         "finding": "Higher urea content improves cellulose solubility but increases viscosity",
#                         "year": 2021
#                     }
#                 ],
#                 "common_formulations": [
#                     "ChCl:Urea (1:2)",
#                     "ChCl:Thiourea (1:2)",
#                     "ChCl:Glycerol (1:2)"
#                 ]
#             }
#     return MockLargeRAG()


def main():
    """Main example workflow"""

    print("="*70)
    print("DES Formulation Agent with ReasoningBank - Example Workflow")
    print("="*70)
    print()

    # Load configuration
    logger.info("Loading configuration...")
    try:
        config = load_config()
        logger.info("Configuration loaded successfully")
    except Exception as e:
        logger.warning(f"Could not load config: {e}, using defaults")
        config = {
            "memory": {
                "max_items": 1000,
                "retrieval_top_k": 3,
                "persist_path": "data/memory/reasoning_bank.json",
                "auto_save": True
            }
        }

    # Initialize ReasoningBank components
    logger.info("Initializing ReasoningBank components...")

    # Create real API clients
    logger.info("Creating LLM and Embedding clients...")
    try:
        # Create LLM client for agent reasoning
        llm_client = LLMClient(
            provider="dashscope",  # or "openai"
            model="qwen-plus",
            temperature=0.7,
            max_tokens=2000
        )
        logger.info(f"LLM client initialized: {llm_client.provider}/{llm_client.model}")

        # Create embedding client for memory retrieval
        embedding_client = EmbeddingClient(
            provider="dashscope",  # or "openai"
            model="text-embedding-v3"
        )
        logger.info(f"Embedding client initialized: {embedding_client.provider}/{embedding_client.model}")

    except Exception as e:
        logger.error(f"Failed to initialize API clients: {e}")
        logger.error("Please ensure DASHSCOPE_API_KEY or OPENAI_API_KEY is set in environment or .env file")
        raise

    # Create memory bank with real embedding function
    bank = ReasoningBank(
        embedding_func=embedding_client.embed,  # Use real embedding
        max_items=config["memory"]["max_items"]
    )

    # Create retriever
    retriever = MemoryRetriever(
        bank=bank,
        embedding_func=embedding_client.embed  # Use real embedding
    )

    # Create extractor
    extractor = MemoryExtractor(
        llm_client=llm_client,  # Use real LLM (callable via __call__)
        temperature=1.0,
        max_items_per_trajectory=3
    )

    # Create judge
    judge = LLMJudge(
        llm_client=llm_client,  # Use real LLM (callable via __call__)
        temperature=0.0
    )

    # Create tool clients
    # CoreRAG: Using real adapter
    logger.info("Initializing CoreRAG adapter...")
    try:
        corerag = create_corerag_adapter(max_workers=1)
        status = corerag.get_status()

        if status["status"] == "ready":
            logger.info("CoreRAG adapter initialized successfully")
        else:
            logger.warning(
                f"CoreRAG initialization status: {status['status']}\n"
                f"Message: {status.get('message', 'Unknown')}\n"
                "The agent will run without ontology-based theoretical knowledge."
            )
            # Keep the adapter even if not ready - it might work later
            # or provide graceful degradation

    except Exception as e:
        logger.error(f"Failed to initialize CoreRAG adapter: {e}")
        logger.warning("Continuing without CoreRAG (will use None)")
        corerag = None

    # LargeRAG: Using real adapter
    logger.info("Initializing LargeRAG adapter...")
    try:
        largerag = create_largerag_adapter()
        status = largerag.get_status()

        if status["status"] == "ready":
            logger.info("LargeRAG adapter initialized successfully")
        elif status["status"] == "no_index":
            logger.warning(
                "LargeRAG index not found. The agent will run without literature knowledge.\n"
                "To enable LargeRAG, build the index first:\n"
                "  python src/tools/largerag/examples/1_build_index.py"
            )
        else:
            logger.error(f"LargeRAG initialization failed: {status.get('message', 'Unknown error')}")
            logger.warning("Continuing without LargeRAG (will use None)")
            largerag = None

    except Exception as e:
        logger.error(f"Failed to initialize LargeRAG adapter: {e}")
        logger.warning("Continuing without LargeRAG (will use None)")
        largerag = None

    # Initialize DES Agent
    agent = DESAgent(
        llm_client=llm_client,  # Use real LLM (callable via __call__)
        reasoning_bank=bank,
        retriever=retriever,
        extractor=extractor,
        judge=judge,
        corerag_client=corerag,
        largerag_client=largerag,
        config=config
    )

    logger.info("All components initialized successfully!")
    print()

    # Define test tasks
    tasks = [
        {
            "task_id": "task_001",
            "description": "Design a DES to dissolve cellulose at room temperature (25°C)",
            "target_material": "cellulose",
            "target_temperature": 25,
            "constraints": {
                "viscosity": "< 500 cP",
                "toxicity": "low"
            }
        },
        {
            "task_id": "task_002",
            "description": "Design a DES for lignin extraction at 60°C",
            "target_material": "lignin",
            "target_temperature": 60,
            "constraints": {
                "sustainability": "bio-based components preferred"
            }
        },
        {
            "task_id": "task_003",
            "description": "Design a low-temperature DES for cellulose dissolution at -10°C",
            "target_material": "cellulose",
            "target_temperature": -10,
            "constraints": {
                "viscosity": "< 1000 cP",
                "low_temperature_stability": "required"
            }
        }
    ]

    # Solve tasks sequentially
    results = []

    for i, task in enumerate(tasks, 1):
        print("="*70)
        print(f"Task {i}/{len(tasks)}: {task['task_id']}")
        print("="*70)
        print(f"Description: {task['description']}")
        print(f"Target Material: {task['target_material']}")
        print(f"Target Temperature: {task['target_temperature']}°C")
        print(f"Constraints: {task['constraints']}")
        print()

        # Solve task
        result = agent.solve_task(task)
        results.append(result)

        # Display result
        print("-"*70)
        print("RESULT:")
        print("-"*70)
        print(f"Status: {result['status'].upper()}")

        if result.get('formulation'):
            formulation = result['formulation']
            print(f"\nFormulation:")
            print(f"  HBD: {formulation.get('HBD', 'N/A')}")
            print(f"  HBA: {formulation.get('HBA', 'N/A')}")
            print(f"  Molar Ratio: {formulation.get('molar_ratio', 'N/A')}")

        print(f"\nReasoning: {result.get('reasoning', 'N/A')[:200]}...")
        print(f"\nConfidence: {result.get('confidence', 0.0):.2f}")

        if result.get('memories_used'):
            print(f"\nMemories Used: {len(result['memories_used'])}")
            for mem in result['memories_used']:
                print(f"  - {mem}")

        if result.get('memories_extracted'):
            print(f"\nNew Memories Extracted: {len(result['memories_extracted'])}")
            for mem in result['memories_extracted']:
                print(f"  - {mem}")

        print()

    # Summary
    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Tasks Completed: {len(results)}")
    print(f"Successful: {sum(1 for r in results if r['status'] == 'success')}")
    print(f"Failed: {sum(1 for r in results if r['status'] == 'failure')}")
    print(f"\nMemory Bank Statistics:")
    stats = bank.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Save memory bank
    save_path = config["memory"]["persist_path"]
    try:
        bank.save(save_path)
        print(f"\nMemory bank saved to: {save_path}")
    except Exception as e:
        logger.error(f"Failed to save memory bank: {e}")

    print("\nExample workflow completed successfully!")
    print("="*70)


if __name__ == "__main__":
    main()
