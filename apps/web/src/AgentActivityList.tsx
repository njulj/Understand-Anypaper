import React from 'react';
import { Loader2 } from 'lucide-react';
import { AgentActivity } from './api';


export function appendAgentActivity(
  current: AgentActivity[],
  activity: AgentActivity,
): AgentActivity[] {
  if (activity.kind === 'thinking_done') {
    return current.filter((item) => item.id !== activity.id);
  }
  const next = current.filter((item) => item.id !== activity.id && item.kind !== 'thinking');
  return [...next, activity].slice(-12);
}

export function AgentActivityList({ activities }: { activities: AgentActivity[] }) {
  return (
    <ol className="agent-activities" aria-label="Graph agent activity">
      {activities.map((activity) => (
        <li className={`agent-activity agent-activity-${activity.kind}`} key={activity.id}>
          {activity.kind === 'thinking' ? (
            <Loader2 className="spin" size={14} />
          ) : activity.kind === 'thought' ? (
            <span aria-hidden="true">💡</span>
          ) : (
            <span className="agent-activity-dot" aria-hidden="true" />
          )}
          <span className="agent-activity-label">{activity.label}</span>
          {activity.kind === 'edit' ? (
            <span className="agent-activity-cards">
              <small className="diff-card">
                <span>+{activity.additions ?? 0}</span>{' '}
                <span>−{activity.deletions ?? 0}</span>
              </small>
              {activity.status !== 'streaming' && activity.status !== 'failed' ? (
                <>
                  <small>{activity.problem_count ?? 0} Problems</small>
                  <small>{activity.node_count ?? 0} Nodes</small>
                </>
              ) : null}
            </span>
          ) : null}
          {activity.kind === 'read' && activity.start_line != null ? (
            <span className="agent-activity-cards">
              <small>
                L{activity.start_line}
                {activity.end_line != null && activity.end_line !== activity.start_line
                  ? `–${activity.end_line}`
                  : ''}
              </small>
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
