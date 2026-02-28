"""
DES Formulation Agent with ReasoningBank

This module implements the main agent for DES formulation design,
integrating ReasoningBank memory system with CoreRAG and LargeRAG tools.
"""

from typing import Dict, List, Optional, Callable, Tuple
import logging
from datetime import datetime
import asyncio
import json
import re

from .reasoningbank import (
    ReasoningBank,
    MemoryRetriever,
    MemoryExtractor,
    LLMJudge,
    MemoryItem,
    MemoryQuery,
    Trajectory,
    format_memories_for_prompt,
    # New: Async feedback components
    RecommendationManager,
    FeedbackProcessor,
    Recommendation,
    ExperimentResult
)

from .prompts import (
    OBSERVE_PROMPT,
    format_action_result_for_observe,
    parse_observe_output
)

logger = logging.getLogger(__name__)


class DESAgent:
    """
    Main agent for DES formulation design with asynchronous experimental feedback.

    NEW: The agent now supports real experimental feedback loop:
    1. Retrieve relevant memories from ReasoningBank
    2. Query CoreRAG for theoretical knowledge
    3. Query LargeRAG for literature precedents
    4. Generate DES formulation with reasoning
    5. Create persistent Recommendation record (status: PENDING)
    6. [Async] User performs experiment
    7. [Async] User submits ExperimentResult
    8. Extract data-driven memories and consolidate

    Attributes:
        llm_client: LLM for agent reasoning
        reasoning_bank: ReasoningBank instance
        retriever: MemoryRetriever instance
        extractor: MemoryExtractor instance
        judge: LLMJudge instance (optional, not used in v1)
        rec_manager: RecommendationManager for persistent storage
        feedback_processor: FeedbackProcessor for async feedback
        corerag_client: CoreRAG tool interface
        largerag_client: LargeRAG tool interface
        config: Configuration dictionary
    """

    def __init__(
        self,
        llm_client: Callable[[str], str],
        reasoning_bank: ReasoningBank,
        retriever: MemoryRetriever,
        extractor: MemoryExtractor,
        judge: LLMJudge,
        rec_manager: RecommendationManager,  # NEW: Required
        corerag_client: Optional[object] = None,
        largerag_client: Optional[object] = None,
        config: Optional[Dict] = None
    ):
        """
        Initialize DESAgent with async feedback support.

        Args:
            llm_client: Function for LLM calls
            reasoning_bank: ReasoningBank instance
            retriever: MemoryRetriever instance
            extractor: MemoryExtractor instance
            judge: LLMJudge instance (optional, for future use)
            rec_manager: RecommendationManager instance (NEW)
            corerag_client: CoreRAG tool (optional)
            largerag_client: LargeRAG tool (optional)
            config: Configuration dictionary
        """
        self.llm_client = llm_client
        self.memory = reasoning_bank
        self.retriever = retriever
        self.extractor = extractor
        self.judge = judge
        self.corerag = corerag_client
        self.largerag = largerag_client
        self.config = config or {}

        # NEW: Recommendation and feedback management
        self.rec_manager = rec_manager
        self.feedback_processor = FeedbackProcessor(self, rec_manager)

        logger.info("Initialized DESAgent with async experimental feedback support")

    # ===== ReAct Core Methods =====

    def _think(self, task: Dict, knowledge_state: Dict, iteration: int) -> Dict:
        """
        THINK phase: Analyze current knowledge state and decide next action.

        This is the planning/reasoning step where the LLM examines what information
        has been gathered so far and decides what to do next.

        Args:
            task: Task specification
            knowledge_state: Current accumulated knowledge
            iteration: Current iteration number

        Returns:
            Dict with:
                - action: Next action to take
                - reasoning: Explanation of the decision
                - information_gaps: What information is still missing
        """
        # Build thinking prompt
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        remaining_iterations = max_iterations - iteration
        progress_pct = int((iteration / max_iterations) * 100)

        # Determine iteration stage
        if progress_pct < 40:
            stage = "Early"
        elif progress_pct < 75:
            stage = "Mid"
        else:
            stage = "Late"

        # Summarize accumulated knowledge
        theory_summary = f"{knowledge_state['num_theory_queries']} queries made" if knowledge_state['num_theory_queries'] > 0 else "Not retrieved"
        literature_summary = f"{knowledge_state['num_literature_queries']} queries made" if knowledge_state['num_literature_queries'] > 0 else "Not retrieved"

        # NEW: Failure tracking summary
        failed_theory = knowledge_state.get('failed_theory_attempts', 0)
        failed_literature = knowledge_state.get('failed_literature_attempts', 0)

        # Format memory summary
        memory_summary = ""
        if knowledge_state['memories'] and len(knowledge_state['memories']) > 0:
            memory_summary = "\n**Retrieved Memories Summary**:\n"
            for i, mem in enumerate(knowledge_state['memories'][:3], 1):
                measurements = mem.metadata.get("measurements", [])
                max_eff = None
                unit = ""
                for m in measurements:
                    if m.get("leaching_efficiency") is not None:
                        max_eff = m.get("leaching_efficiency") if max_eff is None else max(max_eff, m.get("leaching_efficiency"))
                        unit = m.get("unit", unit)
                eff_text = f"浸出效率≈{max_eff} {unit}" if max_eff is not None else "无浸出数据"
                memory_summary += f"  {i}. {mem.title[:80]}... ({eff_text})\n"
            if len(knowledge_state['memories']) > 3:
                memory_summary += f"  ... and {len(knowledge_state['memories']) - 3} more\n"

        think_prompt = f"""You are a DES (Deep Eutectic Solvent) formulation expert planning your research approach.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Target Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Progress**: Iteration {iteration}/{max_iterations} ({progress_pct}% complete, {remaining_iterations} remaining) - **{stage} Stage**

**Current Knowledge State**:
- Memories retrieved: {knowledge_state['memories_retrieved']} ({len(knowledge_state['memories'] or [])} items)
{memory_summary}
- Theoretical knowledge (CoreRAG): {theory_summary} (failed attempts: {failed_theory})
- Literature knowledge (LargeRAG): {literature_summary} (failed attempts: {failed_literature})
- Formulation candidates generated: {len(knowledge_state['formulation_candidates'])}
- Previous observations: {len(knowledge_state['observations'])}

**Recent Observations**:
{self._format_observations(knowledge_state['observations'][-2:] if len(knowledge_state['observations']) > 0 else [])}

**Latest OBSERVE Analysis** (from previous iteration):
{self._format_latest_observe_recommendation(knowledge_state['observations'])}

**Available Actions**:
1. **retrieve_memories** - Get past experiences from ReasoningBank (validated experimental data). NOTE: If returned empty in last iteration, you may skip and proceed with other tools.
2. **query_theory** - Query CoreRAG ontology for theoretical principles
3. **query_literature** - Query LargeRAG for literature data
4. **query_parallel** - Query both CoreRAG and LargeRAG simultaneously
5. **generate_formulation** - Generate DES formulation from accumulated knowledge
6. **refine_formulation** - Refine existing formulation with more information
7. **finish** - Complete task (only if formulation is ready)

**Tool Characteristics**:
- **ReasoningBank (retrieve_memories)**: Instant retrieval of validated past experiments - **MOST RELIABLE WHEN AVAILABLE**. If empty, no relevant memories exist - this is acceptable, proceed with other tools.
- **LargeRAG (query_literature)**: Fast vector search (~1-2 seconds) across 10,000+ papers
- **CoreRAG (query_theory)**: Deep ontology reasoning (~5-10 minutes per query)

**Note: Use Memory to Guide Theory and Literature Queries**:
1. **retrieve memories first** (in iteration 1) if not yet retrieved - memories contain validated experimental data
2. **If retrieve_memories returns 0 results**: This is ACCEPTABLE - no relevant historical data exists. Immediately move on to theory/literature queries without retrying.
3. Memories from real experiments are the **MOST RELIABLE** knowledge source when available
4. Only query CoreRAG/LargeRAG if memories are insufficient or missing critical details

**Research Requirements**:
- **Preferred**: Memories + Theory (CoreRAG) + Literature (LargeRAG)
- **Acceptable**: Memories + LLM parametric knowledge (if tools unavailable)
- **Minimum**: Theory + Literature (if no relevant memories exist)
- **Fallback**: LLM parametric knowledge (if all tools fail)

**CoreRAG Usage Guidelines**:
- CoreRAG is NECESSARY (DES design needs theoretical basis), but takes 5-10 minutes
- **Use thoughtfully**: Craft comprehensive, well-structured queries to maximize information gain per query
- **Avoid repeated similar queries**: Plan what theoretical knowledge you need, then query ONCE with a complete question
- Good query: "What are the key principles for cellulose dissolution via DES? Include hydrogen bonding mechanisms, component selection criteria, and molar ratio considerations."
- Poor query: Multiple narrow queries like "What is hydrogen bonding?" then "What about molar ratios?" (wasteful)

**Decision Guidelines by Stage**:
- **Early ({stage == 'Early' and '✓' or '✗'})**:
  - **Priority 1**: Retrieve memories if not yet done. If returns 0, immediately proceed to Priority 2.
  - **Priority 2**: Query literature/theory (memories empty OR insufficient)
- **Mid ({stage == 'Mid' and '✓' or '✗'})**: Ensure sufficient knowledge from available sources (memories when available + tools)
- **Late ({stage == 'Late' and '✓' or '✗'})**: Must generate formulation soon. If you have any knowledge sources (memories/theory/literature) → generate now

**STRICT Anti-Loop Rules**:
- **STOP after 2 consecutive failures**: If a tool fails 2 times in a row → STOP trying, move to alternative action
- **Failed tool tracking**: CoreRAG failed {failed_theory} times, LargeRAG failed {failed_literature} times
- **If both tools unavailable ({failed_theory >= 2 and failed_literature >= 2})**: RELY ON MEMORIES (if available) + LLM parametric knowledge and generate formulation immediately
- **DO NOT repeat the same action if result is unchanged**:
  * If retrieve_memories returns 0 results → This means NO historical data exists. DO NOT retry. Immediately move to theory/literature queries.
  * If a query returns empty/unchanged results twice → move to alternative action
- **Progress awareness**: At {progress_pct}% complete, prioritize actions that move towards formulation generation

**Your Task**:
Given your current progress ({iteration}/{max_iterations}, {stage} stage), analyze the knowledge state and decide the SINGLE most valuable next action.

Output JSON:
{{
    "action": "action_name",
    "reasoning": "Why this action is the best next step (2-3 sentences)",
    "information_gaps": ["gap1", "gap2"]  // What critical info is still missing
}}
"""

        try:
            response = self.llm_client(think_prompt)
            thought = self._parse_json_response(response)

            # Validate action
            valid_actions = [
                "retrieve_memories", "query_theory", "query_literature",
                "query_parallel", "generate_formulation", "refine_formulation", "finish"
            ]

            if thought.get("action") not in valid_actions:
                logger.warning(f"Invalid action '{thought.get('action')}', defaulting to retrieve_memories")
                thought["action"] = "retrieve_memories"

            return thought

        except Exception as e:
            logger.error(f"Think phase failed: {e}")
            # Fallback: simple heuristic
            if not knowledge_state["memories_retrieved"]:
                return {
                    "action": "retrieve_memories",
                    "reasoning": "Starting with memory retrieval (fallback decision)",
                    "information_gaps": ["All information"]
                }
            elif not knowledge_state["theory_knowledge"] and not knowledge_state["literature_knowledge"]:
                return {
                    "action": "query_parallel",
                    "reasoning": "Need both theory and literature (fallback decision)",
                    "information_gaps": ["Theory", "Literature"]
                }
            else:
                return {
                    "action": "generate_formulation",
                    "reasoning": "Have sufficient information (fallback decision)",
                    "information_gaps": []
                }

    def _act(self, action: str, task: Dict, knowledge_state: Dict, tool_calls: List) -> Dict:
        """
        ACT phase: Execute the chosen action.

        Args:
            action: Action to execute
            task: Task specification
            knowledge_state: Current knowledge state (will be updated in-place)
            tool_calls: List to append tool call records

        Returns:
            Dict with:
                - action: Action that was executed
                - success: Whether action succeeded
                - data: Retrieved/generated data
                - summary: Human-readable summary
        """
        logger.info(f"[ACT] Executing action: {action}")

        if action == "retrieve_memories":
            memories = self._retrieve_memories(task)
            knowledge_state["memories"] = memories
            knowledge_state["memories_retrieved"] = True
            return {
                "action": "retrieve_memories",
                "success": True,
                "data": memories,
                "summary": f"Retrieved {len(memories)} relevant memories from past experiences"
            }

        elif action == "query_theory":
            theory = self._query_corerag(task, knowledge_state)
            if theory:
                knowledge_state["theory_knowledge"].append(theory)  # Accumulate
                knowledge_state["num_theory_queries"] += 1
                tool_calls.append({
                    "tool": "CoreRAG",
                    # Record the *actual* query sent to CoreRAG (LLM-generated), not the task description.
                    "query": (theory.get("_query_text") if isinstance(theory, dict) else task["description"]),
                    "result": theory
                })
            else:
                knowledge_state["failed_theory_attempts"] += 1  # Track failure
            return {
                "action": "query_theory",
                "success": theory is not None,
                "data": theory,
                "summary": f"Retrieved theoretical knowledge from CoreRAG ontology (query #{knowledge_state['num_theory_queries']})" if theory else "CoreRAG query failed"
            }

        elif action == "query_literature":
            literature = self._query_largerag(task, knowledge_state)
            if literature:
                knowledge_state["literature_knowledge"].append(literature)  # Accumulate
                knowledge_state["num_literature_queries"] += 1
                tool_calls.append({
                    "tool": "LargeRAG",
                    # Record the *actual* query sent to LargeRAG (LLM-generated), not the task description.
                    "query": (literature.get("_query_text") if isinstance(literature, dict) else task["description"]),
                    "result": literature
                })
            else:
                knowledge_state["failed_literature_attempts"] += 1  # Track failure
            return {
                "action": "query_literature",
                "success": literature is not None,
                "data": literature,
                "summary": f"Retrieved literature precedents from LargeRAG (query #{knowledge_state['num_literature_queries']})" if literature else "LargeRAG query failed"
            }

        elif action == "query_parallel":
            # Parallel query both tools
            theory, literature = self._query_tools_parallel(task, knowledge_state)

            if theory:
                knowledge_state["theory_knowledge"].append(theory)  # Accumulate
                knowledge_state["num_theory_queries"] += 1
                tool_calls.append({
                    "tool": "CoreRAG",
                    "query": (theory.get("_query_text") if isinstance(theory, dict) else task["description"]),
                    "result": theory
                })
            else:
                knowledge_state["failed_theory_attempts"] += 1

            if literature:
                knowledge_state["literature_knowledge"].append(literature)  # Accumulate
                knowledge_state["num_literature_queries"] += 1
                tool_calls.append({
                    "tool": "LargeRAG",
                    "query": (literature.get("_query_text") if isinstance(literature, dict) else task["description"]),
                    "result": literature
                })
            else:
                knowledge_state["failed_literature_attempts"] += 1

            return {
                "action": "query_parallel",
                "success": (theory is not None) or (literature is not None),
                "data": {"theory": theory, "literature": literature},
                "summary": f"Parallel query: CoreRAG {'✓ (query #' + str(knowledge_state['num_theory_queries']) + ')' if theory else '✗'}, LargeRAG {'✓ (query #' + str(knowledge_state['num_literature_queries']) + ')' if literature else '✗'}"
            }

        elif action == "generate_formulation":
            # Ensure memories are retrieved
            if not knowledge_state["memories_retrieved"]:
                knowledge_state["memories"] = self._retrieve_memories(task)
                knowledge_state["memories_retrieved"] = True

            formulation = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )

            knowledge_state["formulation_candidates"].append(formulation)
            return {
                "action": "generate_formulation",
                "success": True,
                "data": formulation,
                "summary": f"Generated formulation: {formulation['formulation'].get('HBD', '?')}:{formulation['formulation'].get('HBA', '?')} (confidence: {formulation.get('confidence', 0):.2f})"
            }

        elif action == "refine_formulation":
            # Generate additional candidate with current knowledge
            if not knowledge_state["formulation_candidates"]:
                # No formulation to refine, generate new one
                return self._act("generate_formulation", task, knowledge_state, tool_calls)

            formulation = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )

            knowledge_state["formulation_candidates"].append(formulation)
            return {
                "action": "refine_formulation",
                "success": True,
                "data": formulation,
                "summary": f"Refined formulation (now have {len(knowledge_state['formulation_candidates'])} candidates)"
            }

        else:
            logger.warning(f"Unknown action: {action}")
            return {
                "action": action,
                "success": False,
                "data": None,
                "summary": f"Unknown action: {action}"
            }

    def _observe(self, action_result: Dict, knowledge_state: Dict, task: Dict, iteration: int) -> Dict:
        """
        OBSERVE phase: LLM-based analysis of action results.

        NEW: Uses LLM to analyze action results, extract insights, identify gaps,
        and recommend next actions. Replaces hardcoded logic with intelligent analysis.

        Args:
            action_result: Result from _act method
            knowledge_state: Current knowledge state (for context)
            task: Task specification
            iteration: Current iteration number

        Returns:
            Dict with:
                - action: Action that was executed
                - success: Whether action succeeded
                - summary: LLM-generated observation summary
                - knowledge_updated: List of updated knowledge domains
                - key_insights: List of extracted insights (NEW)
                - information_gaps: List of identified gaps (NEW)
                - information_sufficient: Whether we have enough info
                - recommended_next_action: LLM's recommendation (NEW)
                - recommendation_reasoning: Reasoning for recommendation (NEW)
        """
        # Calculate progress context
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        progress_pct = int((iteration / max_iterations) * 100)

        if progress_pct < 40:
            stage = "Early"
        elif progress_pct < 75:
            stage = "Mid"
        else:
            stage = "Late"

        # Format action result details
        action_result_summary = format_action_result_for_observe(
            action_result["action"],
            action_result,
            knowledge_state
        )

        # Format recent observations
        recent_observations = self._format_observations(
            knowledge_state["observations"][-2:] if len(knowledge_state["observations"]) > 0 else []
        )

        # Build OBSERVE prompt
        observe_prompt = OBSERVE_PROMPT.format(
            task_description=task.get("description", ""),
            target_material=task.get("target_material", ""),
            target_temperature=task.get("target_temperature", 25),
            iteration=iteration,
            max_iterations=max_iterations,
            progress_pct=progress_pct,
            stage=stage,
            action=action_result["action"],
            success=action_result["success"],
            action_result_summary=action_result_summary,
            has_memories=knowledge_state["memories_retrieved"],
            num_memories=len(knowledge_state["memories"] or []),
            num_theory=knowledge_state["num_theory_queries"],
            failed_theory=knowledge_state["failed_theory_attempts"],
            num_literature=knowledge_state["num_literature_queries"],
            failed_literature=knowledge_state["failed_literature_attempts"],
            num_formulations=len(knowledge_state["formulation_candidates"]),
            num_observations=len(knowledge_state["observations"]),
            recent_observations=recent_observations
        )

        # Call LLM for observation analysis
        try:
            llm_output = self.llm_client(observe_prompt)
            observation = parse_observe_output(llm_output)

            # Add metadata
            observation["action"] = action_result["action"]
            observation["success"] = action_result["success"]

            logger.info(f"[OBSERVE] LLM analysis: {observation.get('summary', 'No summary')[:100]}...")

        except Exception as e:
            logger.error(f"OBSERVE LLM call failed: {e}, using fallback")
            # Fallback: minimal observation
            observation = {
                "action": action_result["action"],
                "success": action_result["success"],
                "summary": f"Action {action_result['action']} completed with status: {action_result['success']}",
                "knowledge_updated": [],
                "key_insights": [],
                "information_gaps": ["LLM observation failed"],
                "information_sufficient": False,
                "recommended_next_action": "generate_formulation",
                "recommendation_reasoning": "Fallback due to LLM error"
            }

        return observation

    # ===== Helper Methods =====

    def _format_observations(self, observations: List[Dict]) -> str:
        """
        Format recent observations for display in prompts.

        NEW: Includes key_insights and information_gaps from LLM analysis.
        """
        if not observations:
            return "(No observations yet)"

        formatted = []
        for i, obs in enumerate(observations, 1):
            obs_text = f"{i}. **Summary**: {obs.get('summary', 'No summary')}"

            # Add key insights if available
            if obs.get("key_insights"):
                insights = obs["key_insights"][:2]  # Show max 2 insights
                insights_text = "; ".join(insights)
                obs_text += f"\n   **Insights**: {insights_text}"

            # Add identified gaps if available
            if obs.get("information_gaps"):
                gaps = obs["information_gaps"][:2]  # Show max 2 gaps
                gaps_text = "; ".join(gaps)
                obs_text += f"\n   **Gaps**: {gaps_text}"

            formatted.append(obs_text)

        return "\n".join(formatted)

    def _format_latest_observe_recommendation(self, observations: List[Dict]) -> str:
        """
        Format the latest OBSERVE phase recommendation for THINK prompt.

        NEW: Shows the LLM-generated recommendation from previous iteration.
        """
        if not observations or len(observations) == 0:
            return "(No previous observations - this is iteration 1)"

        latest = observations[-1]

        # Extract recommendation if available
        recommended_action = latest.get("recommended_next_action", "N/A")
        recommendation_reasoning = latest.get("recommendation_reasoning", "No reasoning provided")

        formatted = f"- **Recommended Action**: {recommended_action}\n"
        formatted += f"- **Reasoning**: {recommendation_reasoning}\n"
        formatted += f"- **Note**: You may follow this recommendation or choose a different action based on the full context."

        return formatted

    def _parse_json_response(self, llm_output: str) -> Dict:
        """Parse JSON from LLM response with multiple fallback strategies."""
        # Try to extract JSON block
        json_match = re.search(r'```json\s*(.*?)\s*```', llm_output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find any JSON object
        json_match = re.search(r'\{.*?\}', llm_output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback: return empty dict
        logger.warning("Could not parse JSON from LLM output")
        return {}

    def _query_tools_parallel(self, task: Dict, knowledge_state: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        Query CoreRAG and LargeRAG in parallel for efficiency.

        Returns:
            (theory_knowledge, literature_knowledge)
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._query_tools_parallel_async(task, knowledge_state))
            loop.close()
            return result
        except Exception as e:
            logger.error(f"Parallel query failed: {e}")
            # Fallback to sequential
            theory = self._query_corerag(task, knowledge_state)
            literature = self._query_largerag(task, knowledge_state)
            return theory, literature

    async def _query_tools_parallel_async(self, task: Dict, knowledge_state: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """Async version of parallel tool query."""
        loop = asyncio.get_event_loop()

        # Helper coroutine to return None when tool is unavailable
        async def return_none():
            return None

        # Create tasks
        tasks = []
        if self.corerag:
            tasks.append(loop.run_in_executor(None, self._query_corerag, task, knowledge_state))
        else:
            tasks.append(return_none())

        if self.largerag:
            tasks.append(loop.run_in_executor(None, self._query_largerag, task, knowledge_state))
        else:
            tasks.append(return_none())

        # Execute in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle results
        theory = results[0] if not isinstance(results[0], Exception) else None
        literature = results[1] if not isinstance(results[1], Exception) else None

        return theory, literature

    def solve_task(self, task: Dict) -> Dict:
        """
        Main entry point for solving a DES formulation task using ReAct loop.

        NEW: ReAct (Reasoning-Acting) paradigm:
        - Think: Analyze current knowledge state and decide next action
        - Act: Execute the chosen action (query tools, generate formulation)
        - Observe: Summarize results and update knowledge state

        This creates a cumulative information-building process similar to deep research agents.

        Args:
            task: Task dictionary with keys:
                - task_id: Unique identifier
                - description: Natural language description
                - target_material: Material to dissolve
                - target_temperature: Target temperature (°C)
                - constraints: Additional constraints

        Returns:
            Dict with keys:
                - formulation: Proposed DES formulation
                - reasoning: Explanation of design choices
                - confidence: Confidence score (0-1)
                - supporting_evidence: Literature/theory references
                - status: "PENDING"
                - iterations_used: Number of ReAct iterations
        """
        task_id = task.get("task_id", f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        logger.info(f"[ReAct Agent] Starting task {task_id}: {task['description'][:50]}...")

        # Initialize knowledge state
        knowledge_state = {
            "memories": None,
            "memories_retrieved": False,
            "theory_knowledge": [],  # Changed: List to accumulate all theory queries
            "literature_knowledge": [],  # Changed: List to accumulate all literature queries
            "formulation_candidates": [],
            "observations": [],
            "information_gaps": [],  # Track what we still need to know
            "num_theory_queries": 0,  # Track number of CoreRAG queries
            "num_literature_queries": 0,  # Track number of LargeRAG queries
            "failed_theory_attempts": 0,  # NEW: Track failed CoreRAG attempts
            "failed_literature_attempts": 0,  # NEW: Track failed LargeRAG attempts
        }

        # Initialize trajectory tracking
        trajectory_steps = []
        tool_calls = []

        # ReAct loop parameters
        max_iterations = self.config.get("agent", {}).get("max_iterations", 8)
        iteration = 0
        task_complete = False

        # ===== ReAct Loop =====
        while iteration < max_iterations and not task_complete:
            iteration += 1
            logger.info(f"\n{'='*60}")
            logger.info(f"[ReAct Iteration {iteration}/{max_iterations}]")
            logger.info(f"{'='*60}")

            # THINK: Decide next action based on current knowledge state
            thought = self._think(task, knowledge_state, iteration)
            logger.info(f"[THINK] {thought['reasoning']}")
            logger.info(f"[THINK] Next action: {thought['action']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "think",
                "reasoning": thought["reasoning"],
                "action": thought["action"],
                "information_gaps": thought.get("information_gaps", [])
            })

            # Check if ready to finish
            if thought["action"] == "finish":
                logger.info("[THINK] Decision: Task complete, ready to finalize")
                task_complete = True
                break

            # ACT: Execute the chosen action
            action_result = self._act(thought["action"], task, knowledge_state, tool_calls)
            logger.info(f"[ACT] Executed: {thought['action']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "act",
                "action": thought["action"],
                "result_summary": action_result.get("summary", "")
            })

            # OBSERVE: Summarize and integrate new information
            observation = self._observe(action_result, knowledge_state, task, iteration)
            logger.info(f"[OBSERVE] {observation['summary']}")

            knowledge_state["observations"].append(observation)

            # NEW: Update information_gaps from observation
            if observation.get("information_gaps"):
                knowledge_state["information_gaps"] = observation["information_gaps"]
                logger.debug(f"[OBSERVE] Updated information gaps: {observation['information_gaps']}")

            trajectory_steps.append({
                "iteration": iteration,
                "phase": "observe",
                "observation": observation["summary"],
                "knowledge_updated": observation.get("knowledge_updated", []),
                "key_insights": observation.get("key_insights", []),
                "information_gaps": observation.get("information_gaps", [])
            })

        # ===== Finalize Formulation =====
        logger.info(f"\n[ReAct Agent] Finalizing after {iteration} iterations")

        # If no formulation generated yet, generate now
        if not knowledge_state.get("formulation_candidates"):
            logger.info("[Final] Generating formulation from accumulated knowledge")
            if not knowledge_state["memories_retrieved"]:
                knowledge_state["memories"] = self._retrieve_memories(task)
                knowledge_state["memories_retrieved"] = True

            formulation_result = self._generate_formulation(
                task,
                knowledge_state["memories"] or [],
                knowledge_state["theory_knowledge"],
                knowledge_state["literature_knowledge"]
            )
        else:
            # Use best candidate
            formulation_result = knowledge_state["formulation_candidates"][0]

        # Add memories_used to formulation_result for trajectory persistence
        formulation_result["memories_used"] = [m.title for m in (knowledge_state["memories"] or [])]

        # ===== Create Trajectory Record =====
        trajectory = Trajectory(
            task_id=task_id,
            task_description=task["description"],
            steps=trajectory_steps,
            outcome="pending_experiment",
            final_result=formulation_result,
            metadata={
                "target_material": task.get("target_material"),
                "target_temperature": task.get("target_temperature"),
                "constraints": task.get("constraints", {}),
                "tool_calls": tool_calls,
                "iterations_used": iteration,
                "react_mode": True,
                "final_knowledge_state": {
                    "had_memories": knowledge_state["memories_retrieved"],
                    "had_theory": len(knowledge_state["theory_knowledge"]) > 0,
                    "had_literature": len(knowledge_state["literature_knowledge"]) > 0,
                    "num_theory_queries": knowledge_state["num_theory_queries"],
                    "num_literature_queries": knowledge_state["num_literature_queries"],
                }
            }
        )

        # ===== Create Recommendation Record =====
        rec_id = f"REC_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{task_id}"

        recommendation = Recommendation(
            recommendation_id=rec_id,
            task=task,
            task_id=task_id,
            formulation=formulation_result["formulation"],
            reasoning=formulation_result.get("reasoning", ""),
            confidence=formulation_result.get("confidence", 0.0),
            trajectory=trajectory,
            status="PENDING",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )

        self.rec_manager.save_recommendation(recommendation)
        logger.info(f"[ReAct Agent] Saved recommendation {rec_id}")

        # ===== Prepare Return Result =====
        result = formulation_result.copy()
        result["recommendation_id"] = rec_id
        result["status"] = "PENDING"
        result["task_id"] = task_id
        result["iterations_used"] = iteration
        result["memories_used"] = [m.title for m in (knowledge_state["memories"] or [])]
        result["information_sources"] = {
            "memories": knowledge_state["memories_retrieved"],
            "theory": len(knowledge_state["theory_knowledge"]) > 0,
            "literature": len(knowledge_state["literature_knowledge"]) > 0
        }
        result["next_steps"] = (
            f"Recommendation {rec_id} is ready for experimental testing. "
            f"Submit feedback using agent.submit_experiment_feedback('{rec_id}', experiment_result)."
        )

        logger.info(f"[ReAct Agent] Task {task_id} completed in {iteration} iterations")
        return result

    def _retrieve_memories(self, task: Dict) -> List[MemoryItem]:
        """
        Retrieve relevant memories for the task.

        NEW: LLM decides how many memories to retrieve based on task complexity and memory availability.

        Args:
            task: Task dictionary

        Returns:
            List of relevant MemoryItem objects
        """
        # Get total number of memories in the bank
        total_memories = len(self.memory.get_all_memories())

        logger.info(f"[Memory Retrieval] Total memories in bank: {total_memories}")

        if total_memories == 0:
            logger.info("[Memory Retrieval] No memories available")
            return []

        # Let LLM decide how many memories to retrieve
        decision_prompt = f"""You are deciding how many memories to retrieve for a DES formulation task.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Target Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Memory Bank Status**:
- Total memories available: {total_memories}
- These memories contain validated experimental data from past DES formulations

**Your Decision**:
Decide how many memories to retrieve (top_k parameter) based on:
1. **Task complexity**: Simple binary DES → fewer memories (1-3); Complex multi-component DES → more memories (3-10)
2. **Available memories**: If total < 5, retrieve all; if total >= 5, be selective
3. **Material specificity**: Novel material → retrieve more for broader context; Common material → fewer targeted memories
4. **Temperature**: Non-standard temperature → retrieve more examples; Standard temperature → fewer examples

**Guidelines**:
- **Minimum**: 2 (always try to get at least two relevant memory)
- **Maximum**: min(10, total_memories) (don't overwhelm with too many)

Output ONLY a JSON object:
{{
    "top_k": <number>,
    "reasoning": "<1-2 sentences explaining your choice>"
}}
"""

        try:
            llm_response = self.llm_client(decision_prompt)
            decision = self._parse_json_response(llm_response)

            # Extract top_k with validation
            top_k = decision.get("top_k", 3)
            reasoning = decision.get("reasoning", "Default retrieval strategy")

            # Validate and constrain top_k
            top_k = max(1, min(top_k, total_memories, 10))

            logger.info(f"[Memory Retrieval] LLM decision: retrieve top_{top_k} memories")
            logger.info(f"[Memory Retrieval] Reasoning: {reasoning}")

        except Exception as e:
            logger.warning(f"[Memory Retrieval] LLM decision failed: {e}, using default top_k=3")
            top_k = min(3, total_memories)

        # Perform retrieval with LLM-decided top_k
        query = MemoryQuery(
            query_text=task["description"],
            top_k=top_k,
            min_similarity=self.config.get("memory", {}).get("min_similarity", 0.0)
        )

        memories = self.retriever.retrieve(query)
        logger.info(f"[Memory Retrieval] Retrieved {len(memories)} memories (requested: {top_k}, available: {total_memories})")

        return memories

    def _query_corerag(self, task: Dict, knowledge_state: Dict) -> Optional[Dict]:
        """
        Query CoreRAG for theoretical knowledge using LLM-generated query.

        Args:
            task: Task dictionary
            knowledge_state: Current knowledge state (to generate informed queries)

        Returns:
            Dict with theory knowledge, or None if unavailable
        """
        if not self.corerag:
            logger.warning("CoreRAG client not available")
            return None

        try:
            # Let LLM generate the query based on current knowledge state
            num_prev_queries = knowledge_state["num_theory_queries"]

            # Summarize what we already know
            prev_theory_summary = ""
            if knowledge_state["theory_knowledge"]:
                prev_theory_summary = f"\n**Previous theory queries ({num_prev_queries} total):**\n"
                for i, theory in enumerate(knowledge_state["theory_knowledge"][-2:], start=max(1, num_prev_queries-1)):
                    prev_theory_summary += f"Query {i}: Retrieved theoretical knowledge\n"

            literature_summary = ""
            if knowledge_state["literature_knowledge"]:
                literature_summary = f"\n**Literature knowledge acquired:** {len(knowledge_state['literature_knowledge'])} queries completed"

            query_gen_prompt = f"""You are generating a query for CoreRAG (theoretical ontology database) to support DES formulation design.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Current Knowledge State**:
- Theory queries completed: {num_prev_queries}
- Literature queries completed: {knowledge_state['num_literature_queries']}
{prev_theory_summary}
{literature_summary}

**Your Goal**: Generate a comprehensive, well-structured CoreRAG query to retrieve theoretical knowledge.

**Guidelines**:
- If this is the FIRST theory query (query #{num_prev_queries + 1}): Ask for comprehensive theoretical foundations (hydrogen bonding, component selection, molar ratios, temperature effects)
- If this is a SUBSEQUENT query: Ask for complementary theoretical insights not covered in previous queries
- Be specific and detailed to maximize information gain
- CoreRAG takes 5-10 minutes per query, so make it count!

Output ONLY the query text (no JSON, no explanation):"""

            query_text = self.llm_client(query_gen_prompt).strip()
            # Remove quotes if LLM added them
            query_text = query_text.strip('"').strip("'")

            logger.info(f"[CoreRAG Query #{num_prev_queries + 1}] LLM generated: {query_text[:100]}...")

            # Format query for CoreRAG
            query = {
                "query": query_text,
                "focus": ["hydrogen_bonding", "component_selection", "molar_ratio", "temperature_effects"]
            }

            # Call CoreRAG
            result = self.corerag.query(query)
            logger.debug(f"CoreRAG returned: {str(result)[:100]}...")

            # Preserve traceability: keep the actual query used alongside the result.
            if isinstance(result, dict):
                result.setdefault("_query_text", query_text)
                result.setdefault("_query_payload", query)
                return result

            # Defensive fallback: always return a dict-like structure upstream can log.
            return {
                "_query_text": query_text,
                "_query_payload": query,
                "raw_result": result,
            }

        except Exception as e:
            logger.error(f"CoreRAG query failed: {e}")
            return None

    def _query_largerag(self, task: Dict, knowledge_state: Dict) -> Optional[Dict]:
        """
        Query LargeRAG for literature precedents using LLM-generated query.

        Args:
            task: Task dictionary
            knowledge_state: Current knowledge state (to generate diverse queries)

        Returns:
            Dict with literature knowledge, or None if unavailable
        """
        if not self.largerag:
            logger.warning("LargeRAG client not available")
            return None

        try:
            num_prev_queries = knowledge_state["num_literature_queries"]

            # Summarize previous queries to avoid repetition
            prev_lit_summary = ""
            if knowledge_state["literature_knowledge"]:
                prev_lit_summary = f"\n**Previous literature queries ({num_prev_queries} total):**\n"
                prev_lit_summary += f"Already retrieved {num_prev_queries * 10} documents from literature.\n"
                prev_lit_summary += "Generate a DIFFERENT query to explore new angles (e.g., different keywords, component variations, property focus)."

            theory_summary = ""
            if knowledge_state["theory_knowledge"]:
                theory_summary = f"\n**Theoretical knowledge available:** {len(knowledge_state['theory_knowledge'])} theory queries completed"

            query_gen_prompt = f"""You are generating a query for LargeRAG (literature database with 10,000+ papers) to support DES formulation design.

**Task**: {task['description']}
**Target Material**: {task['target_material']}
**Temperature**: {task.get('target_temperature', 25)}°C
**Constraints**: {task.get('constraints', {})}

**Current Knowledge State**:
- Literature queries completed: {num_prev_queries}
- Theory queries completed: {knowledge_state['num_theory_queries']}
{prev_lit_summary}
{theory_summary}

**Your Goal**: Generate a literature search query to find relevant DES formulations and experimental data.

**Guidelines**:
- Query #{num_prev_queries + 1} of literature search
- If first query: Search for direct DES formulation examples
- If subsequent query: Explore DIFFERENT angles (e.g., component variations, property data, dissolution mechanisms, alternative formulations)
- **IMPORTANT**: Make each query DIFFERENT from previous ones to maximize information coverage
- Use specific keywords relevant to DES and {task['target_material']}

Output ONLY the query text (no JSON, no explanation):"""

            query_text = self.llm_client(query_gen_prompt).strip()
            # Remove quotes if LLM added them
            query_text = query_text.strip('"').strip("'")

            logger.info(f"[LargeRAG Query #{num_prev_queries + 1}] LLM generated: {query_text[:100]}...")

            # Format query for LargeRAG
            query = {
                "query": query_text,
                "filters": {
                    "material_type": task.get("material_category", "polymer"),
                    "temperature_range": [task.get("target_temperature", 25) - 10, task.get("target_temperature", 25) + 10]
                },
                "top_k": self.config.get("tools", {}).get("largerag", {}).get("max_results", 10)
            }

            # Call LargeRAG
            result = self.largerag.query(query)
            logger.debug(f"LargeRAG returned: {str(result)[:100]}...")

            # Preserve traceability: keep the actual query used alongside the result.
            if isinstance(result, dict):
                result.setdefault("_query_text", query_text)
                result.setdefault("_query_payload", query)
                return result

            return {
                "_query_text": query_text,
                "_query_payload": query,
                "raw_result": result,
            }

        except Exception as e:
            logger.error(f"LargeRAG query failed: {e}")
            return None

    def _generate_formulation(
        self,
        task: Dict,
        memories: List[MemoryItem],
        theory_list: List[Dict],  # Changed: Now a list of all theory queries
        literature_list: List[Dict]  # Changed: Now a list of all literature queries
    ) -> Dict:
        """
        Generate DES formulation using LLM with all available knowledge.

        Args:
            task: Task dictionary
            memories: Retrieved memory items
            theory_list: List of all CoreRAG theory knowledge retrieved
            literature_list: List of all LargeRAG literature knowledge retrieved

        Returns:
            Dict with formulation, reasoning, confidence, etc.
        """
        # Build comprehensive prompt
        prompt = self._build_formulation_prompt(task, memories, theory_list, literature_list)

        # Call LLM
        try:
            llm_output = self.llm_client(prompt)
            logger.debug(f"LLM formulation output: {llm_output[:200]}...")
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return {
                "formulation": {},
                "reasoning": f"Error: {str(e)}",
                "confidence": 0.0,
                "supporting_evidence": []
            }

        # Parse LLM output
        result = self._parse_formulation_output(llm_output)

        return result

    def _build_formulation_prompt(
        self,
        task: Dict,
        memories: List[MemoryItem],
        theory_list: List[Dict],  # Changed
        literature_list: List[Dict]  # Changed
    ) -> str:
        """
        Build comprehensive prompt for formulation generation.

        Args:
            task: Task dictionary
            memories: Retrieved memories
            theory_list: List of theory knowledge
            literature_list: List of literature knowledge

        Returns:
            Formatted prompt string
        """
        prompt = "# DES Formulation Design Task\n\n"

        # Task description
        prompt += f"## Task\n{task['description']}\n\n"
        prompt += f"**Target Material:** {task['target_material']}\n"
        prompt += f"**Target Temperature:** {task.get('target_temperature', 25)}°C\n"

        constraints = task.get("constraints", {})
        if constraints:
            prompt += f"**Constraints:** {constraints}\n"

        prompt += "\n"

        # Inject memories
        if memories:
            prompt += format_memories_for_prompt(memories)
            prompt += "\n"

        # Add all accumulated theory knowledge
        if theory_list:
            prompt += f"## Theoretical Knowledge (from CoreRAG - {len(theory_list)} queries)\n\n"
            for i, theory in enumerate(theory_list, 1):
                prompt += f"### Theory Query {i}:\n{theory}\n\n"

        # Add all accumulated literature knowledge
        if literature_list:
            prompt += f"## Literature Precedents (from LargeRAG - {len(literature_list)} queries)\n\n"
            for i, literature in enumerate(literature_list, 1):
                prompt += f"### Literature Query {i}:\n{literature}\n\n"

        # Instructions - Support both binary and multi-component DES
        num_components = task.get("num_components", 2)  # Default to binary (2-component) DES

        if num_components == 2:
            # Binary DES (traditional format)
            prompt += """## Instructions

Based on the above information, design a **binary DES formulation** (2 components). Your output must include:

1. **HBD (Hydrogen Bond Donor)**: Component name
2. **HBA (Hydrogen Bond Acceptor)**: Component name
3. **Molar Ratio**: e.g., "1:2" (HBD:HBA)
4. **Reasoning**: Explain your design choices (2-3 sentences)
5. **Confidence**: 0.0 to 1.0
6. **Supporting Evidence**: List key facts from memory/theory/literature

Format your response as JSON:
```json
{
    "formulation": {
        "HBD": "...",
        "HBA": "...",
        "molar_ratio": "..."
    },
    "reasoning": "...",
    "confidence": 0.0,
    "supporting_evidence": ["...", "..."]
}
```
"""
        else:
            # Multi-component DES (ternary, quaternary, etc.)
            prompt += f"""## Instructions

Based on the above information, design a **{num_components}-component DES formulation** (multi-component eutectic system). Your output must include:

1. **Components**: List of {num_components} components with their roles (HBD/HBA/neutral)
2. **Molar Ratio**: Ratio between all components (e.g., "1:2:1" for ternary)
3. **Reasoning**: Explain your design choices, especially why multiple components are beneficial (3-4 sentences)
4. **Confidence**: 0.0 to 1.0
5. **Supporting Evidence**: List key facts from memory/theory/literature. Left empty if none.
6. **Synergy Explanation**: How do the multiple components work together?

Format your response as JSON:
```json
{{
    "formulation": {{
        "components": [
            {{"name": "Component 1", "role": "HBD", "function": "Primary hydrogen bond donor"}},
            {{"name": "Component 2", "role": "HBA", "function": "Hydrogen bond acceptor"}},
            {{"name": "Component 3", "role": "HBD/modifier", "function": "Secondary donor to tune properties"}}
        ],
        "molar_ratio": "1:2:0.5",
        "num_components": {num_components}
    }},
    "reasoning": "...",
    "confidence": 0.0,
    "supporting_evidence": ["...", "..."],
    "synergy_explanation": "Component X and Y synergistically..."
}}
```

**Key Design Considerations for Multi-Component DES**:
- **Ternary DES (3 components)**: Often used to tune viscosity, melting point, or solubility
- **Quaternary DES (4+ components)**: Can provide fine-tuned properties but increase complexity
- **Synergy**: Multiple HBDs or HBAs can create cooperative effects
- **Literature precedent**: Check if similar multi-component systems have been reported
"""


        return prompt

    def _parse_formulation_output(self, llm_output: str) -> Dict:
        """
        Parse LLM output to extract formulation.

        Args:
            llm_output: Raw LLM output

        Returns:
            Structured formulation dict
        """
        import json
        import re

        # Try to extract JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', llm_output, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                return result
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON from LLM output")

        # Fallback: return minimal structure
        return {
            "formulation": {},
            "reasoning": llm_output[:500],
            "confidence": 0.5,
            "supporting_evidence": []
        }

    # ===== NEW: Asynchronous Experimental Feedback Methods =====

    def submit_experiment_feedback(
        self,
        recommendation_id: str,
        experiment_result: ExperimentResult
    ) -> Dict:
        """
        Submit experimental feedback for a recommendation (NEW: Async feedback loop).

        This method completes the async feedback loop:
        1. User performs experiment based on recommendation
        2. User submits ExperimentResult with lab measurements
        3. System extracts data-driven memories
        4. System consolidates new memories into ReasoningBank

        Args:
            recommendation_id: ID of the recommendation to update
            experiment_result: ExperimentResult object with lab measurements

        Returns:
            Dict with processing results:
                - status: "success" or "error"
                - recommendation_id: The updated recommendation ID
                - measurement_count: Number of measurement rows processed
                - is_liquid_formed: Whether DES liquid formed
                - memories_extracted: List of extracted memory titles
                - message: Human-readable status message

        Example:
            Typical usage:
                - Prepare ExperimentResult with measurements list
                - Call submit_experiment_feedback(rec_id, experiment_result)
                - Inspect returned measurement_count / memories_extracted
        """
        logger.info(f"Processing experimental feedback for recommendation {recommendation_id}")

        try:
            # Check if this is an update (recommendation already has feedback)
            existing_rec = self.rec_manager.get_recommendation(recommendation_id)
            is_update = (
                existing_rec is not None and
                existing_rec.experiment_result is not None and
                existing_rec.trajectory.metadata.get("feedback_processed_at") is not None
            )

            if is_update:
                logger.info(f"Detected feedback update for {recommendation_id}")

            # Step 1: Submit experimental feedback to recommendation manager
            self.rec_manager.submit_feedback(recommendation_id, experiment_result)

            # Step 2: Use FeedbackProcessor to extract memories and update ReasoningBank
            process_result = self.feedback_processor.process_feedback(
                recommendation_id,
                is_update=is_update
            )

            # Log using measurement count
            solubility_str = f"measurements={process_result.get('measurement_count', 0)}"
            logger.info(
                f"Feedback processing completed: {process_result['num_memories']} "
                f"memories extracted ({solubility_str})"
            )

            # Auto-save if configured
            if self.config.get("memory", {}).get("auto_save", False):
                save_path = self.config["memory"]["persist_path"]
                self.memory.save(save_path)
                logger.info(f"Auto-saved memory bank to {save_path}")

            message = (
                f"Experimental feedback processed successfully. "
                f"Measurements: {process_result.get('measurement_count', 0)}. "
            )
            if is_update:
                deleted = process_result.get("deleted_memories", 0)
                message += f"Updated feedback (deleted {deleted} old memories). "
            message += f"Extracted {process_result['num_memories']} new memories."

            return {
                "status": "success",
                "recommendation_id": recommendation_id,
                "is_liquid_formed": process_result.get("is_liquid_formed"),
                "memories_extracted": process_result["memories_extracted"],
                "measurement_count": process_result.get("measurement_count", 0),
                "is_update": is_update,
                "deleted_memories": process_result.get("deleted_memories", 0) if is_update else None,
                "message": message
            }

        except Exception as e:
            logger.error(f"Failed to process experimental feedback: {e}")
            return {
                "status": "error",
                "recommendation_id": recommendation_id,
                "message": f"Error processing feedback: {str(e)}"
            }

    def load_historical_recommendations(
        self,
        data_path: str,
        reprocess: bool = True
    ) -> Dict:
        """
        Load historical recommendations from another system instance (NEW: Cross-instance reuse).

        This enables transferring experimental knowledge between different system instances:
        - System A generates recommendations + collects experiments
        - System B loads System A's data and learns from it
        - Version-aware data format ensures backward compatibility

        Args:
            data_path: Path to directory containing recommendations.json or individual REC_*.json files
            reprocess: If True, re-extract memories with current extraction logic (default: True)
                      If False, only load existing memories without reprocessing

        Returns:
            Dict with loading results:
                - status: "success" or "error"
                - num_loaded: Number of recommendations loaded
                - num_reprocessed: Number re-processed with current logic
                - memories_added: Total memories added to ReasoningBank
                - message: Human-readable status

        Example:
            >>> # Load data from System A into System B
            >>> result = agent_B.load_historical_recommendations(
            ...     data_path="/path/to/system_A/recommendations/",
            ...     reprocess=True  # Re-extract with System B's logic
            ... )
            >>> print(f"Loaded {result['num_loaded']} recommendations")
            >>> print(f"Added {result['memories_added']} memories to System B")
        """
        logger.info(f"Loading historical recommendations from {data_path}")

        try:
            import os
            import json
            from pathlib import Path

            data_dir = Path(data_path)
            if not data_dir.exists():
                raise FileNotFoundError(f"Data path not found: {data_path}")

            num_loaded = 0
            num_reprocessed = 0
            total_memories = 0

            # Load all recommendation JSON files
            rec_files = list(data_dir.glob("REC_*.json"))

            for rec_file in rec_files:
                try:
                    with open(rec_file, "r", encoding="utf-8") as f:
                        rec_data = json.load(f)

                    # Convert to Recommendation object (version-aware deserialization)
                    rec = Recommendation.from_dict(rec_data)

                    # Only process COMPLETED recommendations with experimental feedback
                    if rec.status == "COMPLETED" and rec.experiment_result is not None:
                        num_loaded += 1

                        if reprocess:
                            # Re-extract memories with current extraction logic
                            logger.info(f"Reprocessing {rec.recommendation_id} with current logic")

                            new_memories = self.extractor.extract_from_experiment(
                                rec.trajectory,
                                rec.experiment_result
                            )

                            if new_memories:
                                self.memory.consolidate(new_memories)
                                total_memories += len(new_memories)
                                num_reprocessed += 1
                                logger.info(
                                    f"Extracted {len(new_memories)} memories from {rec.recommendation_id}"
                                )
                        else:
                            # Just load existing memories (if stored in trajectory metadata)
                            existing_memories = rec.trajectory.metadata.get("extracted_memories", [])
                            total_memories += len(existing_memories)
                            logger.info(
                                f"Loaded {len(existing_memories)} existing memories from {rec.recommendation_id}"
                            )

                    else:
                        logger.debug(
                            f"Skipping {rec.recommendation_id} (status={rec.status}, "
                            f"has_feedback={rec.experiment_result is not None})"
                        )

                except Exception as e:
                    logger.warning(f"Failed to load {rec_file}: {e}")
                    continue

            # Auto-save if configured
            if self.config.get("memory", {}).get("auto_save", False):
                save_path = self.config["memory"]["persist_path"]
                self.memory.save(save_path)
                logger.info(f"Auto-saved memory bank to {save_path}")

            logger.info(
                f"Historical data loading complete: {num_loaded} recommendations loaded, "
                f"{num_reprocessed} reprocessed, {total_memories} memories added"
            )

            return {
                "status": "success",
                "num_loaded": num_loaded,
                "num_reprocessed": num_reprocessed,
                "memories_added": total_memories,
                "message": (
                    f"Successfully loaded {num_loaded} recommendations. "
                    f"Reprocessed {num_reprocessed} with current logic. "
                    f"Added {total_memories} memories to ReasoningBank."
                )
            }

        except Exception as e:
            logger.error(f"Failed to load historical recommendations: {e}")
            return {
                "status": "error",
                "num_loaded": 0,
                "num_reprocessed": 0,
                "memories_added": 0,
                "message": f"Error loading historical data: {str(e)}"
            }


# Example usage and testing
if __name__ == "__main__":
    # This will be implemented in examples/example_des_task.py
    pass
