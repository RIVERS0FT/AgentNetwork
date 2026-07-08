(function () {
  const normalizeBase = window.normalizeLogRecord;
  if (typeof normalizeBase !== 'function') return;

  const removedBehaviorEvents = new Set([
    'decide',
    'agent_decide',
    'act',
    'agent_action',
  ]);

  window.normalizeLogRecord = function normalizeApplicationEvent(record, origin) {
    const normalized = normalizeBase(record, origin);
    if (!normalized) return normalized;

    const event = record?.event || '';
    if (event === 'reasoning' || event === 'acting') {
      normalized.field = 'agent';
    } else if (removedBehaviorEvents.has(event)) {
      normalized.field = 'system';
      normalized.level = 'ERROR';
      normalized.eventText = `Unsupported application event: ${event}`;
    }
    return normalized;
  };
})();
