# Architecture overview

[Architecture index](README.md) · [Project overview](../../README.md)

## 3. Architecture overview

```mermaid
flowchart TB
  subgraph HOST["Ubuntu host, 16 GB (control plane lives here)"]
    GOV["hpcsim-governor<br/>systemd service, protected slice<br/>PSI + cgroup + MemAvailable"]
    CTL["simctl CLI + Web UI (hpcsim user)"]
    SD["systemd-oomd (backstop)"]
  end

  subgraph CORE["P1 core.slice"]
    APP["App under test"]
    PROM["Prometheus + Alertmanager"]
    GRAF["Grafana"]
    SCTLD["slurmctld + slurmrestd (+ slurmdbd/MariaDB)"]
  end

  subgraph MOCK["P2 mocks.slice"]
    BMC["bmc-farm: N virtual Redfish BMCs, 1 process"]
    BCMM["bcm-mock: CMDaemon REST + monitoring"]
    JIRA["jira-mock (default) or Jira Cloud free site"]
    GPUX["gpu-exporter-farm: fake DCGM metrics"]
  end

  subgraph COMP["P4 compute.slice"]
    SLURMD["slurmd containers x N (one shared base image)"]
  end

  subgraph K8S["P3 k8s"]
    K3D["k3d/kind: 1 server + 1 real agent"]
    KWOK["KWOK: fake nodes incl. fake GPUs"]
  end

  subgraph LOAD["P5 load.slice"]
    USERS["user-sim: personas and job arrivals"]
    TBOT["ticket-bot: fake engineers working Jira"]
    CHAOS["scenario-engine: fault injection"]
  end

  subgraph VM["P6 on-demand fidelity VM (libvirt/KVM)"]
    FVM["Real Ubuntu node: systemd, sshd, node_exporter, real kernel"]
  end

  CTL --> GOV
  CTL --> CHAOS
  GOV -. "throttle / freeze / reclaim / stop" .-> COMP
  GOV -. .-> K8S
  GOV -. .-> LOAD
  GOV -. .-> VM
  SD -. "last resort" .-> LOAD
  APP --> SCTLD
  APP --> BMC
  APP --> BCMM
  APP --> JIRA
  APP --> K3D
  PROM --> GPUX
  PROM --> SLURMD
  GRAF --> PROM
  USERS --> SCTLD
  TBOT --> JIRA
  CHAOS --> BMC
  CHAOS --> GPUX
  CHAOS --> KWOK
  CHAOS --> SLURMD
  KWOK --- K3D
  SCTLD --- SLURMD
  FVM --- SCTLD
```

macOS is deliberately **not** in this diagram: it runs as an external agent ([macOS testing](07-macos.md)).
