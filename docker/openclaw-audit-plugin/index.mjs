import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const AUDIT_URL = process.env.NATIVE_AUDIT_URL || "http://127.0.0.1:8000";
const AUDIT_TOKEN = process.env.NATIVE_AUDIT_TOKEN || "";
const REQUIRED = (process.env.NATIVE_AUDIT_REQUIRED || "1") !== "0";

async function post(path, body) {
  const response = await fetch(`${AUDIT_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AUDIT_TOKEN}`,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    throw new Error(`AgentNetwork audit endpoint returned ${response.status}`);
  }
  return await response.json();
}

function identity(ctx, event) {
  return {
    agent_id: String(ctx?.agentId || process.env.AGENT_ID || "unknown").toLowerCase(),
    session_id: String(
      ctx?.sessionKey || ctx?.requesterSessionKey || event?.sessionKey || ""
    ),
    run_id: String(ctx?.runId || event?.runId || ""),
  };
}

async function auditedPost(path, body) {
  try {
    return await post(path, body);
  } catch (error) {
    if (REQUIRED) throw error;
    return { allowed: false, reason: String(error) };
  }
}

export default definePluginEntry({
  id: "agentnetwork-audit",
  name: "AgentNetwork Native Audit Bridge",
  register(api) {
    api.on("before_tool_call", async (event, ctx) => {
      const who = identity(ctx, event);
      const decision = await auditedPost("/internal/native/policy/check", {
        agent_id: who.agent_id,
        backend: "openclaw",
        tool_name: String(event.toolName || ""),
        tool_input: event.params || {},
        tool_call_id: String(event.toolCallId || who.run_id || ""),
        session_id: who.session_id,
        spawn_depth: (who.session_id.match(/:subagent:/g) || []).length,
      });
      if (!decision.allowed) {
        return {
          block: true,
          blockReason: decision.reason || "Denied by AgentNetwork native policy",
        };
      }
      return {};
    }, { priority: 1000 });

    api.on("after_tool_call", async (event, ctx) => {
      const who = identity(ctx, event);
      await auditedPost("/internal/native/audit", {
        kind: "tool_result",
        agent_id: who.agent_id,
        backend: "openclaw",
        tool_name: String(event.toolName || ""),
        tool_call_id: String(event.toolCallId || who.run_id || ""),
        output: event.result ?? event.output ?? null,
        error: event.error ? String(event.error) : "",
        duration_ms: Number(event.durationMs || 0),
        session_id: who.session_id,
      });
    });

    api.on("subagent_spawned", async (event, ctx) => {
      const who = identity(ctx, event);
      await auditedPost("/internal/native/audit", {
        kind: "subagent_lifecycle",
        agent_id: who.agent_id,
        backend: "openclaw",
        child_agent_id: String(event.childSessionKey || event.agentId || event.runId || ""),
        child_session_id: String(event.childSessionKey || ""),
        run_id: String(event.runId || ""),
        status: "running",
        agent_type: String(event.targetKind || "subagent"),
        model: String(event.resolvedModel || ""),
        provider: String(event.resolvedProvider || ""),
      });
    });

    api.on("subagent_ended", async (event, ctx) => {
      const who = identity(ctx, event);
      const outcome = String(event.outcome || "");
      const terminalOutcomes = new Set(["timeout", "killed", "reset", "deleted"]);
      const status =
        outcome === "ok"
          ? "completed"
          : outcome === "error"
            ? "failed"
            : terminalOutcomes.has(outcome)
              ? outcome
              : event.error
                ? "failed"
                : "completed";
      await auditedPost("/internal/native/audit", {
        kind: "subagent_lifecycle",
        agent_id: who.agent_id,
        backend: "openclaw",
        child_agent_id: String(event.targetSessionKey || event.runId || ""),
        child_session_id: String(event.targetSessionKey || ""),
        run_id: String(event.runId || ""),
        status,
        agent_type: String(event.targetKind || "subagent"),
        reason: event.error ? String(event.error) : String(event.reason || ""),
      });
    });
  },
});
