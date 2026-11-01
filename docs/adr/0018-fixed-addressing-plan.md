# ADR 0018: a fixed private addressing plan instead of 127.0.0.1 for everything

Status: accepted (issues 147, 148, 149)

## Context
Every endpoint the client used was `127.0.0.1` on a published port, and all BMCs shared one address (a single process
choosing the node by `Host` header). Through the login node (jump host) `127.0.0.1` is the login container itself, which
made BMC access fail with connection refused. Inside the cluster each container had a Docker-assigned IP that changed
between starts, and `cmsh` listed made-up addresses. A real cluster has distinct, stable addresses per node and per BMC.

## Decision
- The client runs on the same Ubuntu host, and Linux routes Docker bridge subnets, so containers get **fixed private IPs**
  and the client connects to them at standard ports (login 22, Grafana 3000, ...). No port publishing, no root.
- Two networks in the style of Bright Cluster Manager: `internalnet` 10.141.0.0/16 (nodes, services) and `ipminet`
  10.148.0.0/16 (BMCs, phase 2). Compute nodes 10.141.0.N, GPU nodes 10.141.100.N (numbered within their kind, so scaling one
  kind never renumbers the other), head node (login) 10.141.255.254, services 10.141.255.0/24.
  The plan is `benchctl/addressing.py`; `components/bcm-mock/bcm.py` repeats the node formula and a test keeps them equal, so
  `cmsh` and BCM show the real addresses. `compose/observability.yml` is static and repeats its three addresses.
- The ports published on 127.0.0.1 stay: they are the tool's own plumbing (the page talks to the mocks there) and the
  fallback (`benchctl export profile --loopback`) for a client that cannot route to the Docker network or that insists on
  HTTPS for non-local hosts (plain-HTTP Jira).
- Each network's gateway is pinned to 10.141.255.253 / 10.148.255.253. Docker's default is the first address (x.x.0.1), which is the
  first node's (and the first BMC's); the first live start failed with "Address already in use" before this was set.
- The subnets are fixed, not configurable (observability.yml is a static file). `benchctl up` refuses with one message when
  the existing network has another subnet (run `benchctl down` once) or a host route (VPN, office network) overlaps.
- Phase 2 (issue 149): every node's BMC has its own address on `ipminet`: 10.148.0.N (compute), 10.148.100.N (GPU) at the
  standard port 443. Each is a tiny forwarder container (`components/bmc-farm/forward.py`, 32 MB) that pipes TCP to its own port
  on the farm (9000 + the node's index), which keeps all BMC state; the farm picks the node by that port, so the Host header
  is not needed. A separate state container per BMC was rejected: it would have touched every tool path (scenario injection,
  the page, the traffic counters). The farm and the login node (the jump host) are on both networks. A node's BMC is switched
  on or off per node (`bmc_nodes` in the workbench selection, `--bmc-nodes`, the Cluster architecture sheet): no forwarder, no address,
  no export target, 32 MB saved. 127.0.0.1:8000/8443 on the farm stays for the tool and as the fallback.

## Consequences
Addresses are stable and realistic; the jump-host case works with `bmc-<node>`. A host that already uses 10.141/16 or
10.148/16 must free it. Changing the subnet of an existing cluster needs one `benchctl down`.
