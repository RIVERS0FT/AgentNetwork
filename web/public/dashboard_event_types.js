(function () {
  const normalizeBase = window.normalizeLogRecord;
  const extractTokenUsageBase = window.extractTokenUsage;

  const removedApplicationEvents = new Set([
    'decide',
    'agent_decide',
    'act',
    'agent_action',
    'llm_cli_call',
  ]);
  const nativeApplicationEvents = new Set([
    'policy_check',
    'tool_call_requested',
    'tool_result_received',
    'subagent_lifecycle',
  ]);

  function compactHash(value) {
    const text = String(value || '');
    return text.length > 12 ? text.slice(0, 12) + '…' : text;
  }

  if (typeof normalizeBase === 'function') {
    window.normalizeLogRecord = function normalizeApplicationEvent(record, origin) {
      const normalized = normalizeBase(record, origin);
      if (!normalized) return normalized;

      const event = record?.event || '';
      if (event === 'reasoning' || event === 'acting') {
        normalized.field = 'agent';
      } else if (event === 'llm_api_call') {
        normalized.field = 'llm_api';
      } else if (nativeApplicationEvents.has(event)) {
        const payload = record?.payload || {};
        const result = record?.result || {};
        const action = record?.action || {};
        const tool = record?.tool || {};
        const target = record?.target || {};
        const backend = payload.backend || '';
        const status = action.status || result.status || result.decision || '';
        const policySha = compactHash(payload.policy_sha256 || result.policy_sha256);

        normalized.field = 'agent';
        normalized.status = status;

        if (event === 'subagent_lifecycle') {
          const parent = payload.parent_agent_id || record.agent_id || '';
          const child = target.agent_id || target.id || '';
          const session = payload.child_session_id || '';
          normalized.actor = parent;
          normalized.target = child;
          normalized.eventText = [parent, '→', child, 'subagent', status]
            .filter(Boolean)
            .join(' ');
          normalized.detailText = [
            backend && 'backend=' + backend,
            session && 'session=' + session,
            payload.run_id && 'run=' + payload.run_id,
            policySha && 'policy=' + policySha,
            result.reason && 'reason=' + result.reason,
          ].filter(Boolean).join(' ');
        } else if (event === 'policy_check') {
          const capability = tool.canonical_capability || '';
          const decision = result.decision || status;
          normalized.eventText = [
            record.agent_id || '',
            'policy',
            decision,
            tool.name || capability || action.name || '',
          ].filter(Boolean).join(' ');
          normalized.detailText = [
            backend && 'backend=' + backend,
            capability && 'capability=' + capability,
            policySha && 'policy=' + policySha,
            result.reason && 'reason=' + result.reason,
          ].filter(Boolean).join(' ');
        } else {
          const phase = event === 'tool_call_requested' ? 'tool request' : 'tool result';
          normalized.eventText = [
            record.agent_id || '',
            phase,
            tool.name || action.name || '',
            status,
          ].filter(Boolean).join(' ');
          normalized.detailText = [
            backend && 'backend=' + backend,
            tool.canonical_capability && 'capability=' + tool.canonical_capability,
            tool.tool_call_id && 'call=' + tool.tool_call_id,
            payload.session_id && 'session=' + payload.session_id,
            result.reason && 'reason=' + result.reason,
            result.error_message && 'error=' + result.error_message,
          ].filter(Boolean).join(' ');
        }
      } else if (removedApplicationEvents.has(event)) {
        normalized.field = 'system';
        normalized.level = 'ERROR';
        normalized.eventText = `Unsupported application event: ${event}`;
        normalized.detailText = '';
      }
      return normalized;
    };
  }

  if (typeof extractTokenUsageBase === 'function') {
    window.extractTokenUsage = function extractApiTokenUsage(record) {
      if (!record || record.event !== 'llm_api_call') return null;
      return extractTokenUsageBase(record);
    };
  }
})();
