"""
Elasticsearch 日志客户端 — 对应架构文档 第十节：日志系统设计 / Elasticsearch索引

将 SimulationLogger 的 6 级日志索引导入 Elasticsearch，支持全文搜索和聚合分析。

使用方式:
    from agent_network.es_client import ESClient
    es = ESClient()
    await es.initialize()

    # 索引日志
    await es.index_log(entry_dict, "logs-agent")

    # 搜索日志
    results = await es.search("logs-agent", {"match": {"event": "task_received"}})

    # 批量索引
    await es.bulk_index([(entry1, "logs-system"), (entry2, "logs-agent")])

依赖: pip install elasticsearch

环境变量:
    ES_HOSTS         - ES 节点列表，逗号分隔 (默认 http://localhost:9200)
    ES_USER          - 用户名 (可选)
    ES_PASSWORD      - 密码 (可选)
    ES_INDEX_PREFIX  - 索引前缀 (默认 "")
"""

import os
import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("agent_network.es_client")

# 6 个逻辑索引（对应架构文档）
INDEX_NAMES = [
    "logs-system",
    "logs-agent",
    "logs-tool",
    "logs-prompt",
    "logs-packet",
    "logs-audit",
]


class ESClient:
    """
    Elasticsearch 客户端（单例）

    支持:
    - 自动创建索引和映射
    - 单条/批量索引
    - DSL 搜索
    - 索引统计
    - 优雅降级（ES 不可用时仍可运行）
    """

    _instance: Optional["ESClient"] = None
    _client = None
    _available: bool = False
    _index_prefix: str = ""

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_config()
        return cls._instance

    def _init_config(self):
        hosts_str = os.environ.get("ES_HOSTS", "http://localhost:9200")
        self._hosts = [h.strip() for h in hosts_str.split(",") if h.strip()]
        self._user = os.environ.get("ES_USER", "")
        self._password = os.environ.get("ES_PASSWORD", "")
        self._index_prefix = os.environ.get("ES_INDEX_PREFIX", "")

    # ── 索引映射 ────────────────────────────────

    @property
    def INDEX_MAPPINGS(self) -> Dict[str, dict]:
        """返回各索引的 Elasticsearch 映射"""
        pfx = self._index_prefix

        def idx(name):
            return self._index_name(name)

        return {
            idx("logs-system"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "details": {"type": "object", "enabled": False},
                        "message": {"type": "text"},
                    }
                }
            },
            idx("logs-agent"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "agent_id": {"type": "keyword"},
                        "agent_role": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "details": {"type": "object", "enabled": False},
                        "message": {"type": "text"},
                    }
                }
            },
            idx("logs-tool"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "tool_name": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "latency_ms": {"type": "float"},
                        "details": {"type": "object", "enabled": False},
                        "message": {"type": "text"},
                    }
                }
            },
            idx("logs-prompt"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "agent_id": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "prompt_text": {"type": "text"},
                        "response_text": {"type": "text"},
                        "details": {"type": "object", "enabled": False},
                    }
                }
            },
            idx("logs-packet"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "agent_id": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "packet_id": {"type": "keyword"},
                        "direction": {"type": "keyword"},
                        "source_agent": {"type": "keyword"},
                        "target_agent": {"type": "keyword"},
                        "message_type": {"type": "keyword"},
                        "latency": {"type": "float"},
                        "details": {"type": "object", "enabled": False},
                    }
                }
            },
            idx("logs-audit"): {
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "level": {"type": "keyword"},
                        "event": {"type": "keyword"},
                        "agent_id": {"type": "keyword"},
                        "details": {"type": "object", "enabled": False},
                        "message": {"type": "text"},
                    }
                }
            },
        }

    def _index_name(self, name: str) -> str:
        return f"{self._index_prefix}{name}" if self._index_prefix else name

    # ── 初始化 ──────────────────────────────────

    async def initialize(self) -> bool:
        """
        连接 ES 集群、验证健康状态、创建索引映射。
        返回 True 表示 ES 可用，False 表示不可用（优雅降级）。
        """
        try:
            from elasticsearch import AsyncElasticsearch
        except ImportError:
            logger.warning("[ESClient] elasticsearch package not installed, ES disabled")
            self._available = False
            return False

        # 构建连接参数
        kwargs = {"hosts": self._hosts}
        if self._user and self._password:
            kwargs["basic_auth"] = (self._user, self._password)

        self._client = AsyncElasticsearch(**kwargs)

        try:
            # 验证连接
            health = await self._client.cluster.health(timeout="5s")
            logger.info(f"[ESClient] Connected to ES, cluster: {health.get('cluster_name', 'unknown')}, "
                        f"status: {health.get('status', 'unknown')}")

            # 创建索引
            for index_name, body in self.INDEX_MAPPINGS.items():
                exists = await self._client.indices.exists(index=index_name)
                if not exists:
                    await self._client.indices.create(index=index_name, body=body)
                    logger.info(f"[ESClient] Created index: {index_name}")

            self._available = True
            return True

        except Exception as e:
            logger.warning(f"[ESClient] ES unavailable: {e}, running in memory-only mode")
            self._available = False
            self._client = None
            return False

    @property
    def available(self) -> bool:
        """ES 是否可用"""
        return self._available and self._client is not None

    # ── 索引操作 ────────────────────────────────

    async def index_log(self, entry: Dict[str, Any], index_name: str) -> bool:
        """
        索引单条日志

        Args:
            entry: 日志条目字典（LogEntry.to_dict()）
            index_name: 索引名（不带前缀）

        Returns:
            是否成功
        """
        if not self.available:
            return False

        try:
            full_index = self._index_name(index_name)
            await self._client.index(
                index=full_index,
                document=entry,
                # 使用 log 的 timestamp 作为文档时间
            )
            return True
        except Exception as e:
            logger.error(f"[ESClient] Index error: {e}")
            return False

    async def bulk_index(self, entries: List[Tuple[Dict[str, Any], str]]) -> int:
        """
        批量索引日志

        Args:
            entries: [(entry_dict, index_name), ...]

        Returns:
            成功索引的数量
        """
        if not self.available or not entries:
            return 0

        try:
            from elasticsearch.helpers import async_bulk

            actions = []
            for entry, index_name in entries:
                actions.append({
                    "_index": self._index_name(index_name),
                    "_source": entry,
                })

            success, errors = await async_bulk(self._client, actions, raise_on_error=False)
            if errors:
                logger.warning(f"[ESClient] Bulk index errors: {len(errors)}")
            return success
        except Exception as e:
            logger.error(f"[ESClient] Bulk index error: {e}")
            return 0

    # ── 搜索 ────────────────────────────────────

    async def search(
        self,
        index: str,
        query: Dict[str, Any] = None,
        size: int = 50,
        from_: int = 0,
        sort: List[Dict] = None,
    ) -> Dict[str, Any]:
        """
        DSL 搜索

        Args:
            index: 索引名（不带前缀），支持通配符 "logs-*"
            query: Elasticsearch query DSL (body)
            size: 返回条数
            from_: 分页偏移
            sort: 排序规则

        Returns:
            {"total": int, "hits": List[dict], "took_ms": int}
        """
        if not self.available:
            return {"total": 0, "hits": [], "took_ms": 0, "error": "ES unavailable"}

        try:
            full_index = self._index_name(index)
            body = {
                "query": query or {"match_all": {}},
                "size": size,
                "from": from_,
            }
            if sort:
                body["sort"] = sort
            else:
                body["sort"] = [{"timestamp": {"order": "desc"}}]

            result = await self._client.search(index=full_index, body=body)
            hits = [h["_source"] for h in result["hits"]["hits"]]
            return {
                "total": result["hits"]["total"]["value"],
                "hits": hits,
                "took_ms": result.get("took", 0),
            }
        except Exception as e:
            logger.error(f"[ESClient] Search error: {e}")
            return {"total": 0, "hits": [], "took_ms": 0, "error": str(e)}

    async def search_simple(
        self,
        index: str = "logs-*",
        keyword: str = "",
        agent_id: str = None,
        level: str = None,
        event: str = None,
        time_from: str = None,
        time_to: str = None,
        size: int = 50,
    ) -> Dict[str, Any]:
        """
        简单搜索（自动构建 Query DSL）

        对应架构文档 第十一节 日志搜索：
            agent_id = 'agent-001' AND level = 'ERROR' AND timestamp > now()-1h
        """
        must = []
        if keyword:
            must.append({"multi_match": {"query": keyword, "fields": ["message", "event", "prompt_text^2", "response_text"]}})
        must_filters = []
        if agent_id:
            must_filters.append({"term": {"agent_id": agent_id}})
        if level:
            must_filters.append({"term": {"level": level}})
        if event:
            must_filters.append({"term": {"event": event}})
        if time_from or time_to:
            ts_range = {}
            if time_from:
                ts_range["gte"] = time_from
            if time_to:
                ts_range["lte"] = time_to
            must_filters.append({"range": {"timestamp": ts_range}})

        query = {
            "bool": {
                "must": must if must else [{"match_all": {}}],
                "filter": must_filters,
            }
        }
        return await self.search(index=index, query=query, size=size)

    # ── 统计 ────────────────────────────────────

    async def get_index_stats(self) -> Dict[str, int]:
        """获取各索引文档计数"""
        if not self.available:
            return {name: 0 for name in INDEX_NAMES}

        try:
            stats = await self._client.indices.stats(index=self._index_name("logs-*"))
            result = {}
            for name in INDEX_NAMES:
                full_name = self._index_name(name)
                idx_stats = stats.get("indices", {}).get(full_name, {})
                result[name] = idx_stats.get("primaries", {}).get("docs", {}).get("count", 0)
            return result
        except Exception as e:
            logger.error(f"[ESClient] Stats error: {e}")
            return {name: 0 for name in INDEX_NAMES}

    async def health(self) -> Dict[str, Any]:
        """ES 集群健康检查"""
        if not self.available:
            return {"status": "unavailable", "available": False}

        try:
            h = await self._client.cluster.health()
            return {"available": True, **h}
        except Exception as e:
            return {"available": False, "error": str(e)}

    # ── 日志分析 ────────────────────────────────

    async def analyze_errors(
        self, agent_id: str = None, hours: int = 1
    ) -> Dict[str, Any]:
        """
        AI 日志分析预查询 — 对应架构文档 第十一节：AI日志分析

        聚合最近 N 小时的 ERROR/FATAL 日志，按事件分类。
        """
        from datetime import datetime, timedelta, timezone
        time_from = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        must = [
            {"terms": {"level": ["ERROR", "FATAL"]}},
            {"range": {"timestamp": {"gte": time_from}}},
        ]
        if agent_id:
            must.append({"term": {"agent_id": agent_id}})

        try:
            result = await self._client.search(
                index=self._index_name("logs-*"),
                body={
                    "query": {"bool": {"must": must}},
                    "size": 0,
                    "aggs": {
                        "by_index": {
                            "terms": {"field": "_index", "size": 10},
                            "aggs": {
                                "by_event": {
                                    "terms": {"field": "event", "size": 20},
                                    "aggs": {
                                        "latest": {
                                            "top_hits": {"size": 3, "sort": [{"timestamp": "desc"}]}
                                        }
                                    },
                                }
                            },
                        }
                    },
                },
            )
            return {
                "total_errors": result["hits"]["total"]["value"],
                "aggregations": result.get("aggregations", {}),
            }
        except Exception as e:
            return {"error": str(e)}

    # ── 清理 ────────────────────────────────────

    async def close(self):
        """关闭 ES 连接"""
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        if cls._instance:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(cls._instance.close())
            except Exception:
                pass
            cls._instance = None
