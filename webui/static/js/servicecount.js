// The one definition of "services" on the page (issue 861 A3, ADR 0089). A service is every part the page draws that has a
// reachability test: the snapshot's `services` map ({part: answers}) holds exactly those (the mocks, the real Jira, the Kubernetes API,
// the DCGM and real-GPU exporters, Teleport, the jump host and the controller), one probe each. Every place that shows a count
// (top-strip chip, Overview tiles and cards, header line, Services list, attention) calls this and nothing else. Pure.

/** {part: bool} -> { up, all, down: [part], stackDown, ratio: '7/7', words: '7 of 7' }; stackDown when parts exist and none answers. */
export function servicesCount(services) {
  const e = Object.entries(services || {}), down = e.filter(([, u]) => !u).map(([k]) => k), up = e.length - down.length;
  return { up, all: e.length, down, stackDown: e.length > 0 && up === 0, ratio: `${up}/${e.length}`, words: `${up} of ${e.length}` };
}
