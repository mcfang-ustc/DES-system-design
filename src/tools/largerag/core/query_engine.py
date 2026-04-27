"""
查询引擎模块
要求：
1. 两阶段检索：向量召回 → Reranker 精排
2. 支持自定义查询参数（top_k, threshold）
3. 返回格式化结果（含来源信息）
"""

from typing import List, Dict, Any, Optional
import os
from llama_index.core import VectorStoreIndex
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
from llama_index.llms.dashscope import DashScope
import logging

from ..config.settings import SETTINGS, DASHSCOPE_API_KEY

logger = logging.getLogger(__name__)


try:
    # We keep OpenAI optional: LargeRAG retrieval (get_similar_documents) does not require it.
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


class SimilarityThresholdFilter(BaseNodePostprocessor):
    """Reranker 之后的相似度阈值过滤器"""

    threshold: float  # Pydantic 字段声明

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        """
        过滤低于阈值的节点

        Args:
            nodes: 输入节点列表（已包含分数）
            query_bundle: 查询信息（未使用）

        Returns:
            过滤后的节点列表
        """
        if self.threshold <= 0:
            return nodes

        original_count = len(nodes)
        filtered_nodes = [n for n in nodes if n.score is not None and n.score >= self.threshold]

        if len(filtered_nodes) < original_count:
            logger.info(
                f"SimilarityThresholdFilter: Filtered {original_count - len(filtered_nodes)} nodes "
                f"(threshold: {self.threshold}, remaining: {len(filtered_nodes)})"
            )

        return filtered_nodes


class LargeRAGQueryEngine:
    """查询引擎封装"""

    def __init__(self, index: VectorStoreIndex):
        self.index = index
        self.settings = SETTINGS
        # DashScope key is still required because the embed_model used by the index is DashScope
        # (kept intentionally to avoid re-indexing large corpora).
        self.dashscope_api_key = DASHSCOPE_API_KEY

        if not self.dashscope_api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY is required for query engine. "
                "Please set it in .env file."
            )

        self.llm_provider = (self.settings.llm.provider or "dashscope").strip().lower()

        # LLM used only for `query()` (agent path uses retrieval only).
        self.llm = None
        self.openai_client = None

        if self.llm_provider == "dashscope":
            # 初始化 LLM（使用配置文件中的模型）
            self.llm = DashScope(
                model_name=self.settings.llm.model,
                api_key=self.dashscope_api_key,
                temperature=self.settings.llm.temperature,
                max_tokens=self.settings.llm.max_tokens,
            )
        elif self.llm_provider == "openai":
            if OpenAI is None:
                # Retrieval still works; only `query()` will fail.
                logger.warning(
                    "OpenAI python package is not available. LargeRAG.query() will be unavailable."
                )
            else:
                openai_api_key = os.getenv("OPENAI_API_KEY")
                if not openai_api_key:
                    logger.warning(
                        "OPENAI_API_KEY is not set. LargeRAG.query() will be unavailable."
                    )
                else:
                    # Optional override for OpenAI-compatible gateways.
                    base_url = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
                    self.openai_client = OpenAI(api_key=openai_api_key, base_url=base_url)
        else:
            raise ValueError(
                f"Unsupported LLM provider: {self.settings.llm.provider!r}. "
                f"Supported: 'dashscope', 'openai'."
            )

        # 初始化 Reranker
        self.reranker = None
        if self.settings.reranker.enabled:
            self.reranker = DashScopeRerank(
                model=self.settings.reranker.model,
                api_key=self.dashscope_api_key,
                top_n=self.settings.retrieval.rerank_top_n,
            )

        # 构建查询引擎
        self.query_engine = None
        if self.llm is not None:
            self._build_query_engine()

    def _build_query_engine(self):
        """构建查询引擎（含 Reranker 和阈值过滤）"""
        # Retriever（支持相似度阈值）
        retriever_kwargs = {
            "index": self.index,
            "similarity_top_k": self.settings.retrieval.similarity_top_k,
        }

        # 如果设置了相似度阈值（> 0），则在向量检索阶段启用过滤
        if self.settings.retrieval.similarity_threshold > 0:
            retriever_kwargs["similarity_cutoff"] = self.settings.retrieval.similarity_threshold
            logger.info(f"Vector retrieval threshold enabled: {self.settings.retrieval.similarity_threshold}")

        retriever = VectorIndexRetriever(**retriever_kwargs)

        # 构建 Node Postprocessors 流水线
        postprocessors = []

        # 1. Reranker（如果启用）
        if self.reranker:
            postprocessors.append(self.reranker)
            logger.info(f"Reranker enabled: {self.settings.reranker.model}")

        # 2. Reranker 之后的阈值过滤（如果启用 rerank_threshold）
        if self.settings.retrieval.rerank_threshold > 0:
            threshold_filter = SimilarityThresholdFilter(
                threshold=self.settings.retrieval.rerank_threshold
            )
            postprocessors.append(threshold_filter)
            logger.info(f"Post-rerank threshold filter enabled: {self.settings.retrieval.rerank_threshold}")

        # 构建 Query Engine
        self.query_engine = RetrieverQueryEngine.from_args(
            retriever=retriever,
            node_postprocessors=postprocessors,
            llm=self.llm,
        )

    def _format_docs_for_synthesis(self, docs: List[Dict[str, Any]], max_chars_per_doc: int = 1200) -> str:
        """Format retrieved docs into a compact context for LLM synthesis."""
        if not docs:
            return "(No retrieved documents)"

        parts: List[str] = []
        for i, doc in enumerate(docs, 1):
            metadata = doc.get("metadata", {}) or {}
            doc_hash = str(metadata.get("doc_hash", "unknown"))[:8]
            page = metadata.get("page_idx", "N/A")
            score = doc.get("score", 0.0)
            text = str(doc.get("text", ""))

            if len(text) > max_chars_per_doc:
                text = text[:max_chars_per_doc] + "..."

            parts.append(
                f"[Doc {i}] score={score:.3f} source={doc_hash} page={page}\n{text}"
            )

        return "\n\n".join(parts)

    def query(self, query_text: str, **kwargs) -> str:
        """
        执行查询

        Args:
            query_text: 查询文本
            **kwargs: 自定义参数（如 top_k）

        Returns:
            LLM 生成的回答
        """
        logger.info(f"Querying: {query_text}")

        if self.llm_provider == "dashscope":
            if self.query_engine is None:
                raise RuntimeError("Query engine not initialized (DashScope LLM unavailable).")
            response = self.query_engine.query(query_text)
            return str(response)

        if self.llm_provider == "openai":
            if self.openai_client is None:
                raise RuntimeError("OpenAI client not initialized. Check OPENAI_API_KEY / OPENAI_API_BASE.")

            top_k = kwargs.get("top_k") or self.settings.retrieval.rerank_top_n
            docs = self.get_similar_documents(query_text, top_k=int(top_k))
            context = self._format_docs_for_synthesis(docs)

            system_prompt = (
                "You are a scientific literature assistant for Deep Eutectic Solvents (DES). "
                "Answer the user's query using ONLY the provided retrieved documents. "
                "If the documents are insufficient, say what is missing."
            )
            user_prompt = (
                f"User query:\n{query_text}\n\n"
                f"Retrieved documents:\n{context}\n\n"
                "Write a concise, factual answer. Include any key experimental conditions if present."
            )

            # GPT-5.*: keep reasoning off here (LargeRAG is used for factual grounding, not deep planning).
            resp = self.openai_client.chat.completions.create(
                model=self.settings.llm.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.settings.llm.temperature,
                max_completion_tokens=self.settings.llm.max_tokens,
                reasoning_effort="none",
            )
            return (resp.choices[0].message.content or "").strip()

        raise RuntimeError(f"Unsupported llm_provider={self.llm_provider!r}")

    def get_similar_documents(
        self,
        query_text: str,
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取相似文档（不使用 LLM 生成）

        Args:
            query_text: 查询文本
            top_k: 最终返回数量（默认使用 rerank_top_n 配置值）

        Returns:
            文档列表，格式：[{"text": ..., "score": ..., "metadata": ...}]

        工作流程：
            1. 向量召回 similarity_top_k 个候选文档
            2. Reranker 精排（如果启用）
            3. 返回前 top_k 个结果
        """
        # 确定最终返回数量
        final_top_k = top_k or self.settings.retrieval.rerank_top_n

        # 动态调整候选池大小（确保足够）
        # 如果用户要的数量超过配置的候选池，自动扩大候选池
        required_candidates = max(
            final_top_k * 2,  # 候选池至少是最终返回数的2倍（给 Reranker 足够选择空间）
            self.settings.retrieval.similarity_top_k
        )

        # 向量召回（支持相似度阈值）
        retriever_kwargs = {
            "index": self.index,
            "similarity_top_k": required_candidates,
        }

        # 如果动态调整了候选池，记录日志
        if required_candidates > self.settings.retrieval.similarity_top_k:
            logger.info(
                f"Auto-adjusted similarity_top_k from {self.settings.retrieval.similarity_top_k} "
                f"to {required_candidates} to satisfy top_k={final_top_k}"
            )

        # 如果设置了相似度阈值（> 0），则启用过滤
        if self.settings.retrieval.similarity_threshold > 0:
            retriever_kwargs["similarity_cutoff"] = self.settings.retrieval.similarity_threshold

        retriever = VectorIndexRetriever(**retriever_kwargs)
        nodes = retriever.retrieve(query_text)

        # Reranker 精排（如果启用）
        if self.reranker:
            # 创建临��� reranker 实例，确保返回足够多的结果
            from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
            reranker = DashScopeRerank(
                model=self.settings.reranker.model,
                api_key=self.dashscope_api_key,
                top_n=max(final_top_k, self.settings.retrieval.rerank_top_n),
            )
            nodes = reranker.postprocess_nodes(nodes, query_str=query_text)

            # 如果启用了 rerank_threshold，对 Reranker 分数进行过滤
            if self.settings.retrieval.rerank_threshold > 0:
                original_count = len(nodes)
                nodes = [n for n in nodes if n.score >= self.settings.retrieval.rerank_threshold]
                if len(nodes) < original_count:
                    logger.info(
                        f"Filtered {original_count - len(nodes)} nodes by rerank score threshold "
                        f"(threshold: {self.settings.retrieval.rerank_threshold})"
                    )

        # 格式化结果并返回前 top_k 个
        results = []
        for node in nodes[:final_top_k]:
            results.append({
                "text": node.get_content(),
                "score": node.score,
                "metadata": node.metadata,
            })

        return results
