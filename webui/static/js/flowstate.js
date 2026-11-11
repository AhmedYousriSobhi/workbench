import { S } from './state.js';


// ---- traffic (#74, #118): real request counters from the mocks plus injected faults, over SSE, drawn with the nodes in the cluster ----
export const SVGNS = 'http:' + '//www.w3.org/2000/svg';  // an XML namespace name, not a fetch; split so the no-external-URL test stays strict
export const BASE_SERVICES = [['bmc-farm', 'BMC farm', 'Redfish'], ['bcm-mock', 'BCM', 'CMDaemon'], ['jira-mock', 'Jira', 'tickets'], ['gpu-exporter', 'GPU exporter', 'DCGM metrics']];
export const REAL_SERVICES = new Set(['jira-real', 'k8s-api', 'dcgm-exporter', 'real-gpu']);  // everything else the page draws is simulated: kind is colour, not a word in the box
export const TELEPORT_BOX = ['teleport', 'Teleport proxy', 'gateway, not a node'];
export const JUMP_BOX = ['jump', 'Jump host', 'bare ssh bastion'],
  CTL_BOX = ['controller', 'Controller', 'slurmctld host'],
  ACC_PORT = { jump: 2220, controller: 2221 };  // ADR 0036; the ports are the published fallbacks on 127.0.0.1
export const accExtra = () => [...(hasService('jump') ? [JUMP_BOX] : []), ...(hasService('controller') ? [CTL_BOX] : [])];  // drawn only when built: no phantom box (#420)
export const accUp = k => { try { return !!scoped().services[k]; } catch (e) { return false; } };
export const scoped = () => { const c = JSON.parse(S.lastCluster), m = S.activeCluster && c.clusters && c.clusters.find(x => x.name === S.activeCluster); return m ? { ...c, ...m } : c; };
export function hasTeleport() { try { return !!(S.lastCluster && 'teleport' in scoped().services); } catch (e) { return false; } }
export const JIRA_REAL_BOX = ['jira-real', 'Jira DC', 'Data Center'],
  K8S_API_BOX = ['k8s-api', 'Kubernetes API', 'kube-apiserver'],
  DCGM_BOX = ['dcgm-exporter', 'DCGM (real)', 'first GPU node'],
  REAL_GPU_BOX = ['real-gpu', 'GPU card', 'this machine\'s card'];  // the opt-in real exporter on a mock NVML (issue 426) and the host's own card (issue 428)  // the opt-in real exporter on a mock NVML (issue 426)
export function hasService(k) { try { return !!(S.lastCluster && k in scoped().services); } catch (e) { return false; } }
export function svcList() { return [...BASE_SERVICES, ...(hasService('jira-real') ? [JIRA_REAL_BOX] : []), ...(hasService('k8s-api') ? [K8S_API_BOX] : []), ...(hasService('dcgm-exporter') ? [DCGM_BOX] : []), ...(hasService('real-gpu') ? [REAL_GPU_BOX] : []), ...(hasTeleport() ? [TELEPORT_BOX] : [])]; }  // Teleport only when the part is built: no phantom box
export const G = { clients: { x: 8, y: 8, w: 164, h: 272 }, injector: { x: 8, y: 300, w: 164, h: 124 }, cluster: { x: 668, y: 8, w: 504 } };  // set by buildFlow from the panel's shape
export let svcFit = {};  // svcFit: the width a box's text may use
export const reduced = matchMedia('(prefers-reduced-motion: reduce)');
