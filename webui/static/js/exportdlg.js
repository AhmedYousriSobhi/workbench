import { $ } from './util.js';


export function init() {
  $('expbtn').onclick = () => { $('exp-msg').textContent = ''; $('expdlg').showModal(); };
  $('exp-close').onclick = $('exp-cancel').onclick = () => $('expdlg').close();
  $('expdlg').addEventListener('click', e => { if (e.target === $('expdlg')) $('expdlg').close(); });
  $('exp-go').onclick = async () => {
    $('exp-msg').textContent = 'Preparing…';
    try {
      const r = await fetch('/api/export/profile' + ($('exp-bmc').checked ? '?bmc=1&bmc_via=' + $('exp-via').value : '?x=1') + '&addr=' + $('exp-addr').value);
      if (r.status === 404) { $('exp-msg').textContent = '✗ This page is newer than its backend. Restart it: ./scripts/benchctl stop && ./scripts/benchctl start'; return; }
      if (!r.ok) { $('exp-msg').textContent = '✗ ' + (await r.json()).detail; return; }
      const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = 'bench-profile.json';
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      $('exp-msg').textContent = '✓ Saved. Import it in the client, then set what a file never holds. SSH key: run ./scripts/benchctl ssh-key install in a terminal and pick ~/.ssh/workbench_ed25519 in the cluster\'s SSH section. Grafana token: cat compose/generated/secrets/grafana_viewer_token (new after every benchctl up).' + ($('exp-bmc').checked ? ' BMC password: the part after admin: in compose/generated/secrets/bmc_credentials.' : '') + ' Jira needs no token: any value is accepted.' + (r.headers.get('X-Warning') ? ' ⚠ ' + r.headers.get('X-Warning') : '');
    } catch (e) { $('exp-msg').textContent = '✗ Could not reach the page backend.'; }
  };

}
