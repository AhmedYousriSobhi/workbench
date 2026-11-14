// The node summary at the top of the node inspector (screen 3) as data. Pure, so `node --test` covers it.
// Honesty rule: a value the page cannot read says so and names the command that can; it is never guessed or shown as zero.

const NOT_READ = 'not measured';

// one cluster-snapshot row `n`, the /api/nodes/{name} body (or null), and `slurm` ({reason, jobs: [{id, user}]} from the governor's slurm-stats, or
// null when those stats are unavailable), and `tickets` (the Jira mock's tickets for the node: [{key, summary, status, priority}], null when it does not
// answer, undefined before it was asked: no row) -> [{k, v, code?}] rows; `code` marks a value shown in monospace
export function nodeSummary(n, body, slurm = null, tickets) {
  if (!n) return [];
  const rows = [];
  if (n.kind === 'k8s') {
    rows.push({ k: 'Kind', v: `Kubernetes node (simulated by KWOK)${n.gpus ? `, ${n.gpus} GPUs` : ''}` });
    rows.push({ k: 'Reason', v: `not read here; run kubectl describe node ${n.name} from a login node that serves Kubernetes` });
    pushTickets(rows, tickets, n.name);
    return rows;
  }
  const gpu = n.kind === 'gpu' || n.kind === 'real-gpu';
  if (slurm) {  // one notice when Slurm cannot be read (the Reason row below); here its own words
    rows.push({ k: 'Reason', v: slurm.reason ? `${slurm.reason} (Slurm's reason)` : n.state === 'ok' ? 'none (healthy)' : 'Slurm gives none for this state' });
    rows.push({ k: 'Jobs', v: slurm.jobs.length ? slurm.jobs.map(j => `${j.id} (${j.user})`).join(', ') : 'none running' });
  } else rows.push({ k: 'Reason', v: n.state === 'ok' ? 'none (healthy)' : `not read here (Slurm stats are unavailable: the stack or governor is down or paused under memory pressure); run ./scripts/benchctl describe node ${n.name}` });
  const hw = [];
  if (n.gpus) hw.push(`${n.gpus} GPU${n.gpus === 1 ? '' : 's'}`);
  if (typeof n.temp === 'number') hw.push(`${Math.round(n.temp)} °C`);
  hw.push(n.fans_failed == null ? 'fans ' + NOT_READ : n.fans_failed > 0 ? `${n.fans_failed} fan${n.fans_failed === 1 ? '' : 's'} failed` : 'fans ok');
  rows.push({ k: 'Hardware', v: hw.join(' · ') });
  const faults = [];
  if (gpu && n.xid > 0) faults.push(`GPU XID ${n.xid}`);
  if (n.ib_down > 0) faults.push(`${n.ib_down} InfiniBand port${n.ib_down === 1 ? '' : 's'} down`);
  if (n.nic_flap) faults.push('NIC flapping');
  if (faults.length) rows.push({ k: 'Faults', v: faults.join(' · ') });
  rows.push({ k: 'Power', v: n.power || NOT_READ });
  rows.push({ k: 'Address', v: body && body.ip ? body.ip : NOT_READ, code: !!(body && body.ip) });
  rows.push({ k: 'BMC', v: body && body.bmc_ip ? `${body.bmc_ip}:443` : body ? 'none for this node' : NOT_READ, code: !!(body && body.bmc_ip) });
  pushTickets(rows, tickets, n.name);
  return rows;
}

// The Tickets row reads the Jira mock only (its ops integration labels a ticket node-<name>); whether a real Jira labels tickets that way is not known,
// so the row never claims it. Status is in words, never colour.
function pushTickets(rows, tickets, name) {
  if (tickets === undefined) return;
  if (tickets === null) rows.push({ k: 'Tickets', v: 'not read (the Jira mock does not answer, or it is not part of this cluster)' });
  else rows.push({ k: 'Tickets', v: tickets.length ? tickets.map(t => `${t.key} ${t.status}${t.priority ? `, ${t.priority}` : ''}: ${t.summary}`).join(' · ') : `none in the Jira mock (label node-${name})` });
}

// the lens chips, in order: [{id, label}]; `telemetry` is shown as Monitoring, the plane id is unchanged
export const LENSES = [
  { id: 'all', label: 'All' }, { id: 'access', label: 'Access' }, { id: 'management', label: 'Management' },
  { id: 'telemetry', label: 'Monitoring' }, { id: 'tickets', label: 'Tickets' }, { id: 'faults', label: 'Faults' }, { id: 'client', label: "Client's eye" },
];
