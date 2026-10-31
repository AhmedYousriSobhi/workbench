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
- The subnets are fixed, not configurable (observability.yml is a static file). `benchctl up` refuses with one message when
  the existing network has another subnet (run `benchctl down` once) or a host route (VPN, office network) overlaps.
- Phase 2 (issue 149): one small BMC container per node on `ipminet`, enabled or disabled per node in Settings to save
  memory, the login node attached to both networks.

## Consequences
Addresses are stable and realistic; the jump-host case works with `bmc-<node>`. A host that already uses 10.141/16 or
10.148/16 must free it. Changing the subnet of an existing cluster needs one `benchctl down`.
