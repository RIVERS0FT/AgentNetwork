# ADR-007：真实后端严格失败，不静默 fallback

> 状态：已接受

配置为 OpenCLAW 或 Claude Code 时，SDK/Gateway 缺失必须失败。`MOCK_LLM=1` 和显式 `direct_llm` 是独立模式，不得把 direct LLM 当作后端缺失时的透明替代。
