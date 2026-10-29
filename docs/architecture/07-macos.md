# macOS testing

[Architecture index](README.md) · [Project overview](../../README.md)

## 8. macOS testing (read this before planning)

- Apple's license permits macOS in a VM **only on Apple hardware**. Projects such as `dockur/macos` and Docker-OSX state this explicitly, and Docker-OSX has been the subject of a takedown request from Apple. Running macOS under KVM on this Ubuntu PC would violate the license, so **it's not a recommended or supported part of this design**.
- Practical alternative: treat macOS as an **external agent target**:
  1. a real Mac (even a spare Mac mini) on the LAN running the app's macOS agent, reporting to the cluster services on this host;
  2. a rented Mac (hosted Mac mini or cloud Mac instance) used on demand for performance runs;
  3. a hosted macOS CI runner for automated regression.
- Even if it were allowed, a 4-8 GB macOS VM would not coexist with the `std`/`full` profiles on 16 GB, so the `mac-agent` profile stays `min`-sized locally.
- Agent-side measurements (CPU, memory, wakeups, battery impact) come from the Mac itself; the sim only provides the "cluster" it talks to.
